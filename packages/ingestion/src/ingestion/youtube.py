from __future__ import annotations

import os
import re
from pathlib import Path

from youtube_transcript_api import RequestBlocked, YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig

from ingestion.store import DATA_ROOT, TranscriptRecord, save


class TranscriptBlocked(Exception):
    """YouTube has IP-blocked the transcript (timedtext) endpoint.

    Systemic, not per-video — every subsequent fetch from this IP will also
    fail, so callers should stop the run rather than keep hammering.
    """

_ID_PATTERNS = [
    r"youtu\.be/([A-Za-z0-9_-]{11})",
    r"[?&]v=([A-Za-z0-9_-]{11})",
    r"/shorts/([A-Za-z0-9_-]{11})",
]


def extract_video_id(url: str) -> str:
    for pattern in _ID_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract a YouTube video id from: {url}")


def _proxy_config() -> WebshareProxyConfig | None:
    """Build a Webshare residential proxy config from env vars, or None.

    Opt-in: set WEBSHARE_PROXY_USERNAME + WEBSHARE_PROXY_PASSWORD to route
    transcript fetches through rotating residential IPs (needed when YouTube
    IP-blocks the caller's own IP). Absent → direct, un-proxied fetch.
    """
    user = os.environ.get("WEBSHARE_PROXY_USERNAME")
    password = os.environ.get("WEBSHARE_PROXY_PASSWORD")
    if user and password:
        return WebshareProxyConfig(proxy_username=user, proxy_password=password)
    return None


def _build_api() -> YouTubeTranscriptApi:
    config = _proxy_config()
    return YouTubeTranscriptApi(proxy_config=config) if config else YouTubeTranscriptApi()


def fetch_transcript(video_id: str) -> str:
    # v1.x instance API; each snippet exposes `.text`
    try:
        fetched = _build_api().fetch(video_id)
    except RequestBlocked as exc:
        # IP-level block on the timedtext endpoint — surface as a domain
        # signal so the batch loop can abort instead of retrying every video.
        raise TranscriptBlocked(str(exc)) from exc
    return " ".join(snippet.text for snippet in fetched)


def ingest_video(
    video_id: str,
    metadata: dict,
    *,
    text: str | None = None,
    root: Path = DATA_ROOT,
) -> str:
    """Persist a transcript + metadata for a known video id. Fetches the
    transcript when `text` is not supplied. Returns the video id."""
    if text is None:
        text = fetch_transcript(video_id)
    save(
        TranscriptRecord(
            platform="youtube",
            source_id=video_id,
            text=text,
            metadata=metadata,
        ),
        root=root,
    )
    return video_id


def ingest(url: str, *, root: Path = DATA_ROOT) -> str:
    """Fetch a YouTube transcript from a URL and persist it. Returns the video id."""
    video_id = extract_video_id(url)
    return ingest_video(video_id, {"url": url}, root=root)
