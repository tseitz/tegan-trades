from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone

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
