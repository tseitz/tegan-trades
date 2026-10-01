"""What each roster person said lately, one block per video. Pure.

Folds stances and transcript sidecars into per-person blocks and renders them as plain text.
Windowed on ``source.published_at``, never ``extracted_at``: extraction lags publication by
days and a backfill would otherwise read as fresh views (see ``roster``).

Lines are never hard-wrapped: the reader wraps them. ``htmlmail`` hangs a wrapped line under the
text after a two-space label gap, so labels stay padded with at least two spaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.rank import parse_date
from core.stance import Stance

WINDOW_DAYS = 3
WATCHING_MAX = 3
WATCHING_CHARS = 240

UNTITLED = "(untitled)"
_LABEL = 9
_CONVICTION_RANK = {"high": 0, "med": 1, None: 2, "low": 3}


@dataclass(frozen=True, slots=True)
class Video:
    ref: str
    title: str
    published_at: str
    url: str | None
    distilled: bool
    stances: tuple[Stance, ...]


@dataclass(frozen=True, slots=True)
class PersonView:
    person: str
    collapsed: bool
    videos: tuple[Video, ...]


def fold(stances: list[Stance], sidecars: list[dict], *, today: date,
         collapsed: frozenset[str], window_days: int = WINDOW_DAYS,
         distilled: frozenset[str] = frozenset()) -> list[PersonView]:
    """Per-person video blocks inside the window, newest first.

    ``distilled`` is the set of refs that have a stance file. A ref with stances counts as
    distilled regardless, so the set only matters for files holding zero stances.
    """
    cutoff = today - timedelta(days=window_days - 1)
    by_ref: dict[str, list[Stance]] = {}
    for s in stances:
        by_ref.setdefault(s.source.transcript_ref, []).append(s)

    found: dict[str, tuple[str, Video]] = {}
    for sc in sidecars:
        item = _from_sidecar(sc, by_ref, distilled, cutoff)
        if item is not None:
            found.setdefault(item[1].ref, item)
    for ref, group in by_ref.items():
        if ref not in found and (item := _from_stances(ref, group, cutoff)) is not None:
            found[ref] = item

    people: dict[str, list[Video]] = {}
    for person, video in found.values():
        people.setdefault(person, []).append(video)

    views = [PersonView(person=name, collapsed=name in collapsed, videos=_newest_first(vids))
             for name, vids in people.items()]
    return sorted(views, key=lambda v: (v.collapsed, _negdate(v.videos[0].published_at), v.person))


def _from_sidecar(sc, by_ref, distilled, cutoff) -> tuple[str, Video] | None:
    if not isinstance(sc, dict):
        return None
    published, platform, source_id = sc.get("published_at"), sc.get("platform"), sc.get("source_id")
    if not published or not platform or not source_id:
        return None
    when = parse_date(published)
    if when is None or when < cutoff:
        return None
    ref = f"{platform}/{source_id}"
    group = tuple(by_ref.get(ref, ()))
    if sc.get("title"):
        title = str(sc["title"])
    elif sc.get("handle"):
        n = int(sc.get("post_count") or 0)
        title = f"@{sc['handle']} · {n} post{'' if n == 1 else 's'}"
    else:
        title = UNTITLED
    person = sc.get("person") or (group[0].source.person if group else "(unknown)")
    return person, Video(ref=ref, title=title, published_at=str(published), url=sc.get("url"),
                         distilled=bool(group) or ref in distilled, stances=group)


def _from_stances(ref, group, cutoff) -> tuple[str, Video] | None:
    first = group[0]
    when = parse_date(first.source.published_at)
    if when is None or when < cutoff:
        return None
    return first.source.person, Video(
        ref=ref, title=UNTITLED, published_at=first.source.published_at,
        url=first.source.url, distilled=True, stances=tuple(group))


def _negdate(published_at: str) -> int:
    d = parse_date(published_at)
    return -d.toordinal() if d else 0


def _newest_first(videos: list[Video]) -> tuple[Video, ...]:
    return tuple(sorted(videos, key=lambda v: (_negdate(v.published_at), v.ref)))


def _day(published_at: str) -> str:
    d = parse_date(published_at)
    return f"{d:%b} {d.day}" if d else published_at


def _label(video: Video) -> str:
    if video.ref.startswith("x/") or video.title == UNTITLED:
        return video.title
    return f'"{video.title}"'


def _buckets(stances: tuple[Stance, ...]) -> dict[str, list[str]]:
    """Assets per bucket, high conviction first, deduped within each. Empty buckets omitted."""
    ordered = sorted(stances, key=lambda s: s.conviction != "high")
    out: dict[str, list[str]] = {"Bullish": [], "Bearish": [], "Unsure": [], "Punts": []}
    seen: dict[str, set[str]] = {k: set() for k in out}
    for s in ordered:
        if s.lean in ("bullish", "bearish"):
            name = "Punts" if s.conviction == "low" else s.lean.capitalize()
        else:
            name = "Unsure"
        key = s.asset.strip().casefold()
        if not key or key in seen[name]:
            continue
        seen[name].add(key)
        out[name].append(f"{s.asset.strip()} (high)" if s.conviction == "high"
                         and name in ("Bullish", "Bearish") else s.asset.strip())
    return {k: v for k, v in out.items() if v}


def _watching(stances: tuple[Stance, ...]) -> list[str]:
    ordered = sorted(stances, key=lambda s: _CONVICTION_RANK[s.conviction])
    out: list[str] = []
    for s in ordered:
        text = " ".join((s.watching or "").split())
        if text and text not in out:
            out.append(_truncate(text))
        if len(out) == WATCHING_MAX:
            break
    return out


def _truncate(text: str) -> str:
    if len(text) <= WATCHING_CHARS:
        return text
    cut = text[:WATCHING_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return cut + "…"


def _line(text: str, indent: int) -> list[str]:
    return [" " * indent + text]


def _fields(video: Video, indent: int, big_picture: str | None) -> list[str]:
    out: list[str] = []
    if big_picture:
        out += _line(big_picture, indent)
    for name, assets in _buckets(video.stances).items():
        out += _line(f"{name:<{_LABEL}}{', '.join(assets)}", indent)
    watching = _watching(video.stances)
    if watching:
        out += _line("Watching", indent)
    for text in watching:
        out += _line(f"• {text}", indent + 2)
    return out


def _has_views(video: Video) -> bool:
    return bool(video.stances)


def lines(views: list[PersonView], *, big_picture: dict[str, str] | None = None) -> list[str]:
    """The indented block, without a heading."""
    big_picture = big_picture or {}
    out: list[str] = []
    for view in views:
        if out:
            out.append("")
        out += (_collapsed_lines(view) if view.collapsed
                else _full_lines(view, big_picture.get(view.person)))
    return out


def _full_lines(view: PersonView, big_picture: str | None) -> list[str]:
    videos = view.videos
    out: list[str] = []
    undistilled = sum(1 for v in videos if not v.distilled)
    shown = [v for v in videos if v.distilled]
    if shown and _has_views(shown[0]):
        head = shown[0]
        out += _line(f"{view.person} · {_day(head.published_at)} · {_label(head)}", 2)
        out += _fields(head, 4, big_picture)
        rest = shown[1:]
    else:
        out += _line(view.person, 2)
        if big_picture:
            out += _line(big_picture, 4)
        rest = shown
    for video in rest:
        if _has_views(video):
            out += _line(f"{_day(video.published_at)} · {_label(video)}", 4)
            out += _fields(video, 6, None)
        else:
            out += _line(f"{_day(video.published_at)} · {_label(video)} — no market views", 4)
    if undistilled:
        out += _line(f"{undistilled} video{'' if undistilled == 1 else 's'} not yet distilled", 4)
    return out


def _collapsed_lines(view: PersonView) -> list[str]:
    n = len(view.videos)
    out = _line(f"{view.person} · {n} video{'' if n == 1 else 's'}", 2)
    quiet = 0
    for video in view.videos:
        buckets = _buckets(video.stances)
        parts = [f"{name} {', '.join(a.removesuffix(' (high)') for a in buckets[name])}"
                 for name in ("Bullish", "Bearish") if name in buckets]
        if not parts:
            quiet += 1
            continue
        out += _line(f"{_day(video.published_at)} · {_label(video)} — {' · '.join(parts)}", 4)
    if quiet:
        out += _line(f"+{quiet} with no views or not yet distilled", 4)
    return out
