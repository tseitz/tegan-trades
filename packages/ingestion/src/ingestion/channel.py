from __future__ import annotations

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
