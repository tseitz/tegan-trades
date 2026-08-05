from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from yt_dlp import YoutubeDL


def channel_base_url(channel: str) -> str:
    """Normalize a watchlist channel identifier to its YouTube base URL.

    Accepts: '@handle', bare 'handle', 'UC...' channel id, or a full URL.
    """
    c = channel.strip().rstrip("/")
    if c.startswith("http"):
        return c
    if c.startswith("@"):
        return f"https://www.youtube.com/{c}"
    if c.startswith("UC") and len(c) == 24:
        return f"https://www.youtube.com/channel/{c}"
    return f"https://www.youtube.com/@{c}"


def tab_url(channel: str, tab: str) -> str:
    """URL for a channel's tab ('videos' | 'streams')."""
    return f"{channel_base_url(channel)}/{tab}"


_TABS = ("videos", "streams")


@dataclass(frozen=True)
class VideoStub:
    video_id: str
    title: str
    tab: str  # "videos" | "streams"


def _flat_entries(url: str, limit: int) -> list[dict]:
    opts = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "playlistend": limit,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("entries") or []


def list_tab(channel: str, tab: str, limit: int, *, _entries=None) -> list[VideoStub]:
    fetch = _entries or _flat_entries
    rows = fetch(tab_url(channel, tab), limit)
    stubs: list[VideoStub] = []
    for row in rows:
        vid = row.get("id")
        if not vid:
            continue
        stubs.append(VideoStub(video_id=vid, title=row.get("title") or "", tab=tab))
    return stubs


def resolve_recent(channel: str, max_videos: int, *, _list_entries=None) -> list[VideoStub]:
    """Enumerate a channel's recent uploads and livestreams (newest-first),
    merged and deduped by video id. Count cap applies per tab."""
    seen: set[str] = set()
    out: list[VideoStub] = []
    for tab in _TABS:
        try:
            stubs = list_tab(channel, tab, max_videos, _entries=_list_entries)
        except Exception as exc:
            # tab may not exist (e.g. no /streams) — but could also be a real
            # config/network error, so surface it rather than failing silently.
            print(f"[resolve_recent] {channel}/{tab} failed: {exc!r}", file=sys.stderr)
            stubs = []
        for stub in stubs:
            if stub.video_id in seen:
                continue
            seen.add(stub.video_id)
            out.append(stub)
    return out


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    title: str
    published_at: str | None   # ISO "YYYY-MM-DD"
    duration: int | None
    channel_id: str | None
    was_live: bool


def _published_at(info: dict) -> str | None:
    upload_date = info.get("upload_date")
    if upload_date and len(upload_date) == 8:
        candidate = f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass  # malformed/invalid calendar date — fall through to timestamp
    ts = info.get("timestamp")
    if ts is None:
        ts = info.get("release_timestamp")
    if ts is not None:
        return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()
    return None


def _full_extract(url: str) -> dict:
    with YoutubeDL({"skip_download": True, "quiet": True}) as ydl:
        return ydl.extract_info(url, download=False)


def hydrate(video_id: str, *, _extract=None) -> VideoMeta:
    """Full-extract a single video for reliable metadata."""
    extract = _extract or _full_extract
    info = extract(f"https://www.youtube.com/watch?v={video_id}")
    return VideoMeta(
        video_id=video_id,
        title=info.get("title") or "",
        published_at=_published_at(info),
        duration=info.get("duration"),
        channel_id=info.get("channel_id"),
        was_live=bool(info.get("was_live")),
    )


def is_recent_enough(published_at: str | None, max_age_days: int, *, today: date) -> bool:
    if not published_at:
        return False
    cutoff = today - timedelta(days=max_age_days)
    return date.fromisoformat(published_at) >= cutoff


def age_in_days(published_at: str | None, *, today: date) -> int | None:
    """Days since publication, or None when the date is missing or unparseable.

    None rather than a sentinel like -1 or 0: callers decide permanently on this (see
    ``roster.CAPTION_GRACE_DAYS``), and a number that silently means "don't know" is exactly
    how "unknown" gets treated as "old".
    """
    if not published_at:
        return None
    try:
        return (today - date.fromisoformat(published_at)).days
    except ValueError:
        return None
