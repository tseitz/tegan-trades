from __future__ import annotations

import re
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi

from ingestion.store import DATA_ROOT, TranscriptRecord, save

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


def fetch_transcript(video_id: str) -> str:
    # v1.x instance API; each snippet exposes `.text`
    fetched = YouTubeTranscriptApi().fetch(video_id)
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
