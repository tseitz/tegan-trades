"""Who gets searched on X, who each handle actually is, and where their posts land.

Two lists in ``cfg/watchlist.yaml`` do different jobs and must not be conflated:

- **``x_grok_digest``** is the *search set* — a curated, API-capped (20) list of handles worth
  spending a call on. It deliberately includes ``dormant`` and ``candidate`` people, because
  the entire reason X is worth having is reaching voices with no long-form feed.
- **``people[].channels[]`` with ``platform: x``** is the *attribution map* — which human a
  handle belongs to.

Keeping them separate is what makes agreement honest. Six of the seventeen digest handles are
people already ingested from YouTube, so resolving to the canonical ``person`` is the only
thing stopping Mayne's post and Mayne's livestream from reading as two voices agreeing —
against a metric that is 20% of a setup's score.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from ingestion.store import DATA_ROOT, TranscriptRecord, save
from ingestion.x_search import (
    XPost,
    group_by_author_day,
    render_document,
    source_id_for,
)

PLATFORM = "x"


def search_handles(watchlist: dict) -> list[str]:
    """The handles to pass as ``allowed_x_handles``, in configured order."""
    return list(watchlist.get("x_grok_digest") or [])


def handle_to_person(watchlist: dict) -> dict[str, str]:
    """Lower-cased handle -> canonical person name, from the per-person channel entries."""
    mapping: dict[str, str] = {}
    for person in watchlist.get("people") or []:
        for channel in person.get("channels") or []:
            if channel.get("platform") == PLATFORM and channel.get("id"):
                mapping[channel["id"].lower()] = person["name"]
    return mapping


def person_for(handle: str, watchlist: dict) -> str | None:
    """The person behind a handle, or None.

    None rather than a fallback label on purpose: ``distill`` would happily attach real theses
    to an invented person, and every downstream agreement count would inherit it. An
    unattributable post is dropped and reported instead — see ``unattributable``.
    """
    return handle_to_person(watchlist).get(handle.lower())


def unattributable(watchlist: dict) -> list[str]:
    """Digest handles with no person entry — searched but uncreditable.

    A config gap of exactly the kind that hides: without this, their posts would either vanish
    or be filed under a placeholder name that quietly joins the roster.
    """
    people = handle_to_person(watchlist)
    return [h for h in search_handles(watchlist) if h.lower() not in people]


EXCLUDED = "excluded"


def undigested(watchlist: dict) -> list[str]:
    """Handles declared on a person but absent from the search set, excluding deliberate drops.

    The inverse gap to ``unattributable``, and cheap to act on: the digest runs well under the
    API's cap of 20, so these are free slots going unused rather than a hard tradeoff.

    ``access: excluded`` channels are omitted. A report that lists decisions alongside gaps
    nags on every run and stops being read — which is precisely how the ``@RealVision`` typo
    survived. Deliberate removals are marked in the watchlist so this stays a list of things
    that are actually wrong.
    """
    digest = {h.lower() for h in search_handles(watchlist)}
    out: list[str] = []
    for person in watchlist.get("people") or []:
        for channel in person.get("channels") or []:
            if channel.get("platform") != PLATFORM or not channel.get("id"):
                continue
            if channel.get("access") == EXCLUDED:
                continue
            if channel["id"].lower() not in digest:
                out.append(channel["id"].lower())
    return out


# How far back the window may be widened automatically to close a gap. A day of the digest is
# roughly $0.25, so a week is ~$1.75 — worth paying unprompted, a quarter is not.
MAX_AUTO_LOOKBACK_DAYS = 7


def last_ingested_day(*, root: Path = DATA_ROOT) -> str | None:
    """The newest day already captured, or None if nothing has been.

    Exists because the nightly job's real cadence is "whenever the laptop next wakes", not
    06:15: launchd runs a missed `StartCalendarInterval` on wake and coalesces several missed
    days into **one** run. With a fixed `--from yesterday`, closing the lid Friday and opening
    it Monday would fetch Sunday→Monday and lose Saturday permanently — YouTube self-heals
    because `ingest-roster` looks back months and skips what exists, but X had no equivalent.
    """
    days = []
    for sidecar in (Path(root) / PLATFORM).glob("*.json"):
        try:
            day = json.loads(sidecar.read_text(encoding="utf-8")).get("published_at")
        except (OSError, json.JSONDecodeError):
            continue
        if day:
            days.append(day[:10])
    return max(days) if days else None


def resume_window(today: date, *, root: Path = DATA_ROOT,
                  max_lookback: int = MAX_AUTO_LOOKBACK_DAYS) -> tuple[str, bool]:
    """Where to start the next X pull, and whether the gap had to be truncated.

    Resumes *from* the last captured day rather than the day after it: a day captured mid-run
    is partial, and `store_posts` rewrites a day wholesale, so re-fetching it is both correct
    and idempotent.
    """
    floor = today - timedelta(days=max_lookback)
    last = last_ingested_day(root=root)
    if last is None:
        return (today - timedelta(days=1)).isoformat(), False
    start = date.fromisoformat(last)
    return (max(start, floor).isoformat(), start < floor)


def store_posts(posts, watchlist: dict, *, root: Path = DATA_ROOT) -> list[Path]:
    """Write one document per author per day, returning the paths written.

    Rewrites a day wholesale rather than appending, so re-running an overlapping window is
    idempotent and a late post lands in the day it belongs to. Cheap because a day's posts are
    small — and appending would risk duplicating a post that appeared in two windows.
    """
    people = handle_to_person(watchlist)
    written: list[Path] = []
    for (handle, day), day_posts in sorted(group_by_author_day(posts).items()):
        person = people.get(handle.lower())
        if person is None:
            continue
        ordered = sorted(day_posts, key=lambda p: p.post_id)
        record = TranscriptRecord(
            platform=PLATFORM,
            source_id=source_id_for(handle, day),
            text=render_document(handle, day, ordered),
            metadata={
                "person": person,
                "handle": handle,
                # A day of posts has no single permalink, so the profile is the honest URL.
                # Every post's own permalink is inside the document body.
                "url": f"https://x.com/{handle}",
                "published_at": day,
                "post_count": len(ordered),
                "post_urls": [p.url for p in ordered],
            },
        )
        written.append(save(record, root=root))
    return written


def dropped_unattributable(posts, watchlist: dict) -> list[XPost]:
    """Posts that survived harvesting but belong to nobody on the roster."""
    people = handle_to_person(watchlist)
    return [p for p in posts if p.handle.lower() not in people]
