from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from youtube_transcript_api import (
    AgeRestricted,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from ingestion import channel as channel_mod
from ingestion import deadletters
from ingestion.store import DATA_ROOT
from ingestion.store import exists as _store_exists
from ingestion.youtube import TranscriptBlocked
from ingestion.youtube import fetch_transcript as _fetch_transcript
from ingestion.youtube import ingest_video as _ingest_video

DEFAULT_MAX_VIDEOS = 50
DEFAULT_MAX_AGE_DAYS = 730

# Not a dead-letter reason: "not yet, ask again tomorrow". A scheduled stream airs on its own
# and a fresh upload gets its captions on its own, so retrying is correct in both cases. It is
# split out of ``failed`` only so that "nobody expected this" stays true there.
PENDING = "pending"

# How long after publication a missing transcript is still assumed to be *pending* rather than
# absent. YouTube generates automatic captions minutes to hours after an upload, so the same
# video answers ``TranscriptsDisabled`` at 06:30 and returns a transcript by mid-morning.
#
# **Measured, not guessed:** TTrades' ``Je7cd9HJUBE`` ("Morning Q&A", published 2026-07-27)
# raised ``TranscriptsDisabled`` in the 06:30 nightly and ingested cleanly at 11:00 the same
# day. Without this grace the first version of this code would have buried a same-day video
# from an active roster member permanently — the exact false-positive the registry is supposed
# to be too careful to make. Two days is generous against an observed gap of about four hours;
# the cost of being generous is one retry per night per video. TUNE.
CAPTION_GRACE_DAYS = 2

# Transcript-side outcomes that do not change on a retry *once the video has had time to
# settle*. Whitelisted by exception type rather than by message, and deliberately *not*
# including anything block-shaped: a ``RequestBlocked`` is systemic and temporary, it has its
# own abort path, and marking a video dead over one would discard it permanently for a bad
# night's egress. See ``deadletters``.
_PERMANENT_TRANSCRIPT: tuple[tuple[type[Exception], str], ...] = (
    (TranscriptsDisabled, deadletters.NO_CAPTIONS),
    (NoTranscriptFound, deadletters.NO_CAPTIONS),
    (AgeRestricted, deadletters.AGE_RESTRICTED),
    # Last: several of the above subclass it, and the first match wins.
    (VideoUnavailable, deadletters.UNAVAILABLE),
)


def _transcript_verdict(exc: Exception, *, age_days: int | None) -> str | None:
    """``deadletters`` reason, ``PENDING``, or None for "still a real failure".

    ``isinstance`` rather than an exact type lookup: ``youtube_transcript_api`` raises through
    a hierarchy, so keying on ``type(exc)`` would quietly stop matching the day the library
    introduces a subclass — and "quietly stops matching" here means back to a permanent
    non-zero ``failed``, which is the bug this whole path exists to fix.

    An unknown age is treated as young. Burying something whose date could not be read would
    be deciding permanently on the strength of a fact we do not have.
    """
    for exc_type, reason in _PERMANENT_TRANSCRIPT:
        if isinstance(exc, exc_type):
            if age_days is None or age_days < CAPTION_GRACE_DAYS:
                return PENDING
            return reason
    return None


# yt-dlp reports metadata failures as prose on a ``DownloadError``, with no typed variants, so
# these have to be matched on text.
#
# **Nothing here is ever buried, and that asymmetry is deliberate.** A metadata failure happens
# *before* hydration succeeds, so there is no ``published_at`` to age-gate against — the grace
# period that makes a transcript verdict safe cannot be applied. Rather than bury on a prose
# match alone, both shapes route to ``PENDING`` and get retried forever. The cost is a couple
# of wasted hydrates a night; the alternative risks discarding a video permanently because
# yt-dlp phrased something unexpectedly. ``failed`` still ends up clean, which was the point.
_UNAVAILABLE_MARKERS = ("This video is not available", "Video unavailable", "Private video")
_UPCOMING_MARKERS = ("This live event will begin", "Premieres in", "premiere")


def _metadata_verdict(message: str) -> str | None:
    """``PENDING``, or None for "still a real failure"."""
    if any(marker in message for marker in _UNAVAILABLE_MARKERS + _UPCOMING_MARKERS):
        return PENDING
    return None


# Repo root: src/ingestion/roster.py -> ... -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_WATCHLIST = _REPO_ROOT / "cfg" / "watchlist.yaml"


@dataclass(frozen=True)
class ChannelTarget:
    person: str
    channel: str
    max_videos: int
    max_age_days: int


def load_watchlist(path: Path = DEFAULT_WATCHLIST) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def active_targets(watchlist: dict) -> list[ChannelTarget]:
    targets: list[ChannelTarget] = []
    for person in watchlist.get("people", []):
        if person.get("status") != "active":
            continue
        backfill = person.get("backfill") or {}
        max_videos = backfill.get("max_videos", DEFAULT_MAX_VIDEOS)
        max_age_days = backfill.get("max_age_days", DEFAULT_MAX_AGE_DAYS)
        for ch in person.get("channels", []):
            if ch.get("platform") == "youtube" and ch.get("access") == "ok":
                targets.append(ChannelTarget(
                    person=person["name"],
                    channel=ch["id"],
                    max_videos=max_videos,
                    max_age_days=max_age_days,
                ))
    return targets


@dataclass(frozen=True)
class SkippedPerson:
    """An `active` roster member the sweep cannot reach, and why."""
    person: str
    reason: str


def unreachable_active(watchlist: dict) -> list[SkippedPerson]:
    """Active people that yield no ingest target.

    ``active_targets`` filters to youtube+ok and says nothing about what it discarded, so a
    person marked active with only a podcast/paid/dormant channel disappears from every run
    without a word. That silence is the same failure shape as the Phase-1 stub bug: the
    config looks right, the sweep looks clean, and the data is simply absent.
    """
    skipped: list[SkippedPerson] = []
    for person in watchlist.get("people", []):
        if person.get("status") != "active":
            continue
        channels = person.get("channels") or []
        if any(c.get("platform") == "youtube" and c.get("access") == "ok" for c in channels):
            continue
        detail = ", ".join(
            f"{c.get('platform')}/{c.get('id')} ({c.get('access')})" for c in channels
        ) or "no channels configured"
        skipped.append(SkippedPerson(person=person["name"], reason=detail))
    return skipped


@dataclass
class ChannelResult:
    """One channel's sweep, bucketed by *why* each video did or didn't land.

    ``dead`` and ``pending`` were split out of ``failed`` on 2026-07-27 because that counter
    had stopped meaning anything. The nightly reported ``10 failed`` and all ten were expected
    — 6 with captions disabled, 2 deleted, 2 livestreams that had not aired yet — so nothing
    short of reading every line could have surfaced an eleventh, genuinely new failure. The
    three now answer different questions: ``dead`` is "never going to work, stop asking",
    ``pending`` is "not yet, ask again tomorrow", and ``failed`` is "nobody expected this".
    """
    person: str
    channel: str
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    dead: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # The registry as it stood when this channel finished, so a run that aborts mid-sweep on a
    # block still carries out what it learned instead of rediscovering it tomorrow.
    dead_registry: dict[str, dict] = field(default_factory=dict)


class RunAborted(Exception):
    """Stop the whole ingest run early (e.g. a YouTube IP block).

    Carries the partial ChannelResult accumulated before the abort so the
    caller can still report what was ingested up to that point.
    """

    def __init__(self, result: ChannelResult):
        super().__init__("ingestion run aborted")
        self.result = result


def _default_save(root: Path):
    def save_video(video_id: str, metadata: dict, text: str) -> None:
        _ingest_video(video_id, metadata, text=text, root=root)
    return save_video


def ingest_channel(
    target: ChannelTarget,
    *,
    today: date | None = None,
    root: Path = DATA_ROOT,
    resolve=None,
    hydrate=None,
    fetch_transcript=None,
    exists=None,
    save_video=None,
    dead: dict[str, dict] | None = None,
) -> ChannelResult:
    today = today or date.today()
    resolve = resolve or channel_mod.resolve_recent
    hydrate = hydrate or channel_mod.hydrate
    fetch_transcript = fetch_transcript or _fetch_transcript
    exists = exists or (lambda platform, vid: _store_exists(platform, vid, root))
    save_video = save_video or _default_save(root)
    # Passed in and returned via ``result``: one registry is shared across every channel in a
    # sweep and written once at the end, so a run that aborts on a block still persists what it
    # learned rather than re-deriving it tomorrow.
    registry: dict[str, dict] = {} if dead is None else dead

    result = ChannelResult(person=target.person, channel=target.channel)

    def bury(vid: str, reason: str) -> None:
        nonlocal registry
        registry = deadletters.record(vid, reason, registry=registry, today=today)
        result.dead.append(vid)

    for stub in resolve(target.channel, target.max_videos):
        vid = stub.video_id
        if exists("youtube", vid):
            result.skipped.append(vid)
            continue
        if deadletters.is_dead(vid, registry):
            # Counted, not silent. A dead video that stopped appearing anywhere would make the
            # registry unfalsifiable — you could never tell "correctly skipped" from "quietly
            # lost", which is the same failure ``verify-roster`` exists to catch upstream.
            result.dead.append(vid)
            continue
        try:
            meta = hydrate(vid)
        except Exception as exc:  # noqa: BLE001 - log-and-continue per spec
            if _metadata_verdict(str(exc)) == PENDING:
                result.pending.append(vid)
                continue
            print(f"[ingest_channel] {target.channel}/{vid} metadata: {exc!r}", file=sys.stderr)
            result.failed.append((vid, f"metadata: {exc}"))
            continue
        if not meta.published_at:
            result.failed.append((vid, "missing published_at"))
            continue
        if not channel_mod.is_recent_enough(meta.published_at, target.max_age_days, today=today):
            result.stale.append(vid)
            continue
        try:
            text = fetch_transcript(vid)
        except TranscriptBlocked as exc:
            # Systemic IP block — every later fetch will fail too. Record this
            # video, then abort the run rather than hammering the blocked endpoint.
            print(
                f"[ingest_channel] {target.channel}/{vid} BLOCKED by YouTube "
                f"(IP ban on transcript endpoint) — aborting run: {exc!r}",
                file=sys.stderr,
            )
            result.failed.append((vid, f"transcript: {exc} [BLOCKED — run aborted]"))
            result.dead_registry = registry
            raise RunAborted(result) from exc
        except Exception as exc:  # noqa: BLE001 - log-and-continue per spec
            verdict = _transcript_verdict(
                exc, age_days=channel_mod.age_in_days(meta.published_at, today=today)
            )
            if verdict == PENDING:
                result.pending.append(vid)
                continue
            if verdict is not None:
                bury(vid, verdict)
                continue
            print(f"[ingest_channel] {target.channel}/{vid} transcript: {exc!r}", file=sys.stderr)
            result.failed.append((vid, f"transcript: {exc}"))
            continue
        metadata = {
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": meta.title,
            "published_at": meta.published_at,
            "duration": meta.duration,
            "channel_id": meta.channel_id,
            "person": target.person,
            "was_live": meta.was_live,
        }
        save_video(vid, metadata, text)
        result.ingested.append(vid)
    result.dead_registry = registry
    return result


def ingest_roster(
    watchlist: dict,
    *,
    root: Path = DATA_ROOT,
    today: date | None = None,
    _ingest_channel=None,
    dead_path: Path | None = None,
) -> list[ChannelResult]:
    run = _ingest_channel or ingest_channel
    results: list[ChannelResult] = []
    dead_path = deadletters.DEAD_FILE if dead_path is None else dead_path
    dead = deadletters.load(dead_path)
    for skipped in unreachable_active(watchlist):
        # Loud, every run: an active person contributing nothing must never be invisible.
        print(f"[ingest_roster] SKIPPED active person {skipped.person!r} — "
              f"no reachable channel: {skipped.reason}", file=sys.stderr)
    for target in active_targets(watchlist):
        try:
            result = run(target, root=root, today=today, dead=dead)
            dead = result.dead_registry or dead
            results.append(result)
        except RunAborted as exc:
            dead = exc.result.dead_registry or dead
            results.append(exc.result)
            print(
                "[ingest_roster] YouTube IP block detected — stopping sweep; "
                "remaining channels not processed. Retry from a residential IP "
                "or configure a proxy.",
                file=sys.stderr,
            )
            break
    # Written once, at the end, rather than per channel: the sweep is the unit of work, and a
    # partial file from a crashed run would be indistinguishable from a complete one.
    deadletters.save(dead, dead_path)
    return results


def format_summary(results: list[ChannelResult]) -> str:
    lines: list[str] = []
    for r in results:
        lines.append(
            f"{r.person} ({r.channel}): "
            f"{len(r.ingested)} ingested, {len(r.skipped)} skipped, "
            f"{len(r.stale)} stale, {len(r.dead)} dead, "
            f"{len(r.pending)} pending, {len(r.failed)} failed"
        )
        for vid, reason in r.failed:
            lines.append(f"    ! {vid}: {reason}")
    totals = {
        "ingested": sum(len(r.ingested) for r in results),
        "skipped": sum(len(r.skipped) for r in results),
        "stale": sum(len(r.stale) for r in results),
        "dead": sum(len(r.dead) for r in results),
        "pending": sum(len(r.pending) for r in results),
        "failed": sum(len(r.failed) for r in results),
    }
    lines.append(
        f"TOTAL: {totals['ingested']} ingested, {totals['skipped']} skipped, "
        f"{totals['stale']} stale, {totals['dead']} dead, "
        f"{totals['pending']} pending, {totals['failed']} failed"
    )
    return "\n".join(lines)
