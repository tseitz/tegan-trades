"""Check what ``cfg/watchlist.yaml`` claims against what is actually there.

Every ``access:`` and ``status:`` value in the watchlist is a hand-recorded research finding
with no expiry and nothing that re-checks it. On 2026-07-26 three of those findings were tested
against reality and **two were wrong** — both recorded as verified:

- ``@RealVision``, ``access: ok`` — does not exist. No videos tab, no streams tab. The roster
  sweep reported it as ``0 ingested, 0 skipped, 0 stale, 0 failed``, which is the same line a
  healthy up-to-date channel prints.
- ``@TraderSZ``, ``status: dormant`` since ~2022 — actively livestreaming. The finding was
  *right about the uploads tab* (newest 2023-10-01) and wrong about the channel; he moved to
  ``/streams``. ``channel.resolve_recent`` already reads both tabs, so nothing but the marker
  was suppressing a whole voice.

Both failed silently, and in opposite directions: one claimed a source we did not have, the
other hid a source we did. Neither is detectable from the sweep's own output, which is why this
lives in a separate command that asserts nothing and only compares.

**It is free.** ``channel.list_tab`` needs no API key and no proxy, so this can run as often as
you like — and should run before the nightly loop, which will otherwise reproduce whatever the
roster gets wrong, silently, every night.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ingestion.channel import _TABS, hydrate, list_tab

# Verdicts. Ordered by how much they should worry you.
BROKEN = "BROKEN"                        # claimed reachable, isn't
EMPTY = "EMPTY"                          # reachable, claimed usable, has nothing
REVIVED = "REVIVED"                      # written off, but posting recently
UNDATED = "UNDATED"                      # written off, has videos, no date fetched — unknown
OK = "OK"
CONFIRMED_DORMANT = "dormant (confirmed)"

_PROBLEM_VERDICTS = (BROKEN, EMPTY, REVIVED, UNDATED)

# How recent a video has to be for a written-off channel to count as revived. Generous on
# purpose: @TraderSZ streams every few weeks and Phase 0 declared him dead for four years, so
# the cost of a wide window is one line to read and the cost of a narrow one is a lost voice.
REVIVAL_WINDOW_DAYS = 180


def _revival_cutoff(*, today: date | None = None) -> str:
    return ((today or date.today()) - timedelta(days=REVIVAL_WINDOW_DAYS)).isoformat()

# Access values that assert we can ingest this channel now.
_CLAIMS_USABLE = frozenset({"ok"})
# Access values that assert we cannot or will not.
_CLAIMS_UNUSABLE = frozenset({"dormant", "excluded"})


@dataclass(frozen=True)
class ChannelProbe:
    person: str
    channel: str
    recorded_access: str
    tabs: dict[str, int]      # tab name -> how many videos it returned; absent tab = didn't resolve
    newest_title: str
    error: str                # "" when at least one tab resolved
    newest_at: str | None = None   # ISO date of the newest video, when it was worth fetching

    @property
    def reachable(self) -> bool:
        """Whether any tab resolved.

        Deliberately *any*, not all: most channels have no ``/streams`` tab and
        ``resolve_recent`` already treats a missing one as empty rather than an error. Requiring
        both would flag almost every healthy channel, and a report that cries wolf is how the
        RealVision typo survived a whole sweep in the first place.
        """
        return bool(self.tabs)

    @property
    def video_count(self) -> int:
        return sum(self.tabs.values())

    @property
    def verdict(self) -> str:
        if self.recorded_access in _CLAIMS_UNUSABLE:
            # Written off. The interesting case is being wrong about that — but "has videos" is
            # not the test. Mark Newton's UC… channel holds an archive whose newest item is
            # "Cnbc interview 3/28/17"; the first version of this check called that REVIVED,
            # which is a false alarm on a marker that was correct. An archive is not a feed, so
            # revival needs a *recent* video. Without a date we cannot tell, and cannot claim to.
            if not self.video_count:
                return CONFIRMED_DORMANT
            if self.newest_at is None:
                return UNDATED
            return REVIVED if self.newest_at >= _revival_cutoff() else CONFIRMED_DORMANT
        if self.recorded_access in _CLAIMS_USABLE:
            if not self.reachable:
                return BROKEN
            return OK if self.video_count else EMPTY
        # guest / manual / paid / unknown: no claim we can test from here.
        return OK


# ── structural checks (no network) ───────────────────────────────────────────────

def _channels(watchlist: dict):
    for person in watchlist.get("people") or []:
        for channel in person.get("channels") or []:
            if channel.get("id"):
                yield person, channel


def duplicate_channels(watchlist: dict) -> list[tuple[str, str, list[str]]]:
    """Channels claimed by more than one person, as ``(platform, id, [people])``.

    The mirror image of the bad merge that put ``TraderSZ`` and ``Z$1`` in one entry: there, two
    people shared a name; here, two names share a feed. Both corrupt the same thing — agreement
    counts *people*, so a feed owned twice either splits one voice or doubles it.
    """
    owners: dict[tuple[str, str], list[str]] = defaultdict(list)
    for person, channel in _channels(watchlist):
        key = (channel.get("platform", ""), channel["id"].lower())
        if person["name"] not in owners[key]:
            owners[key].append(person["name"])
    return [(platform, cid, people)
            for (platform, cid), people in owners.items() if len(people) > 1]


def alias_collisions(watchlist: dict) -> list[tuple[str, str]]:
    """Aliases that name a *different* person, as ``(alias owner, person collided with)``.

    ``aliases`` exists so one speaker's several labels collapse into one voice — the extractor
    calls Nadeau by show name as often as by name. An alias matching someone else does the exact
    opposite and merges two people, which is unrecoverable once it reaches agreement counts.
    """
    names = {p["name"].lower(): p["name"] for p in watchlist.get("people") or []}
    out: list[tuple[str, str]] = []
    for person in watchlist.get("people") or []:
        for alias in person.get("aliases") or []:
            other = names.get(alias.lower())
            if other and other != person["name"]:
                out.append((person["name"], other))
    return out


def channel_less(watchlist: dict) -> list[str]:
    """People we intend to ingest that have no route to any content.

    ``dormant`` is exempt — it means kept for the record, so having no feed is the point.
    """
    return [
        person["name"]
        for person in watchlist.get("people") or []
        if person.get("status") != "dormant" and not (person.get("channels") or [])
    ]


# ── the probe ────────────────────────────────────────────────────────────────────

def probe_channel(person: str, channel: dict, *, limit: int = 5, _list_tab=None,
                  _hydrate=None) -> ChannelProbe:
    """Ask YouTube what is actually on a channel. Never raises — an unreachable channel is a
    *finding*, which is the whole point of this command.

    The newest video's **date** is fetched only when it can change the verdict, i.e. for a
    channel recorded as written-off that turns out to have videos. That is a couple of channels
    rather than all seventeen, and `hydrate` is a full yt-dlp extract per video — cheap once,
    slow times seventeen.
    """
    lister = _list_tab or list_tab
    tabs: dict[str, int] = {}
    newest_title, newest_id = "", ""
    errors: list[str] = []
    for tab in _TABS:
        try:
            stubs = lister(channel["id"], tab, limit)
        except Exception as exc:  # noqa: BLE001 - any failure is a finding, not a crash
            errors.append(f"{tab}: {str(exc)[:80]}")
            continue
        tabs[tab] = len(stubs)
        if stubs and not newest_title:
            newest_title, newest_id = stubs[0].title, stubs[0].video_id

    newest_at = None
    if newest_id and channel.get("access") in _CLAIMS_UNUSABLE:
        try:
            newest_at = (_hydrate or hydrate)(newest_id).published_at
        except Exception:  # noqa: BLE001 - falls through to UNDATED, which is honest
            newest_at = None

    return ChannelProbe(
        person=person, channel=channel["id"],
        recorded_access=channel.get("access", "unknown"),
        tabs=tabs, newest_title=newest_title,
        error="" if tabs else "; ".join(errors),
        newest_at=newest_at,
    )


def probe_youtube(watchlist: dict, *, limit: int = 5, _list_tab=None,
                  _hydrate=None) -> list[ChannelProbe]:
    """Probe every declared YouTube channel. X, telegram and podcast feeds are skipped — only
    YouTube can be checked for free, and a check that costs money would not get run."""
    return [
        probe_channel(person["name"], channel, limit=limit, _list_tab=_list_tab,
                      _hydrate=_hydrate)
        for person, channel in _channels(watchlist)
        if channel.get("platform") == "youtube"
    ]


@dataclass(frozen=True)
class Report:
    probes: list[ChannelProbe]
    duplicates: list[tuple[str, str, list[str]]] = field(default_factory=list)
    alias_collisions: list[tuple[str, str]] = field(default_factory=list)
    channel_less: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[ChannelProbe]:
        order = {v: i for i, v in enumerate(_PROBLEM_VERDICTS)}
        return sorted((p for p in self.probes if p.verdict in _PROBLEM_VERDICTS),
                      key=lambda p: order[p.verdict])

    @property
    def exit_code(self) -> int:
        """Non-zero when anything disagrees, so a scheduled run surfaces instead of scrolling by."""
        structural = self.duplicates or self.alias_collisions or self.channel_less
        return 1 if (self.problems or structural) else 0

    def format(self) -> str:
        lines: list[str] = []
        if self.problems:
            lines.append(f"{len(self.problems)} channel(s) disagree with the watchlist:")
            for p in self.problems:
                lines.append(f"  {p.verdict:20} {p.person} — {p.channel} "
                             f"(recorded: {p.recorded_access})")
                detail = p.error or (f"newest: {p.newest_title!r}" if p.newest_title else
                                     "no videos on any tab")
                lines.append(f"  {'':20} {detail}")
        for platform, cid, people in self.duplicates:
            lines.append(f"  DUPLICATE CHANNEL    {platform}/{cid} claimed by: {', '.join(people)}")
        for owner, other in self.alias_collisions:
            lines.append(f"  ALIAS COLLISION      {owner!r} has an alias matching {other!r}")
        for name in self.channel_less:
            lines.append(f"  NO CHANNELS          {name} is not dormant but has no feed")

        counts: dict[str, int] = defaultdict(int)
        for p in self.probes:
            counts[p.verdict] += 1
        summary = " · ".join(f"{v} {n}" for v, n in sorted(counts.items()))
        lines.append("")
        lines.append(f"{len(self.probes)} youtube channels probed: {summary}")
        return "\n".join(lines)


def verify_roster(watchlist: dict, *, limit: int = 5, _list_tab=None,
                  _hydrate=None) -> Report:
    return Report(
        probes=probe_youtube(watchlist, limit=limit, _list_tab=_list_tab, _hydrate=_hydrate),
        duplicates=duplicate_channels(watchlist),
        alias_collisions=alias_collisions(watchlist),
        channel_less=channel_less(watchlist),
    )
