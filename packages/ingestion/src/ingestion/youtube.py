from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests
from youtube_transcript_api import RequestBlocked, YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig

from ingestion.store import DATA_ROOT, TranscriptRecord, save

# Transient failures worth retrying (a rotating proxy gives a fresh IP each try):
# connection drops, 429-redirects to google /sorry, chunked-encoding aborts,
# and hung sockets (surfaced as Timeout by the session below).
_TRANSIENT = (requests.exceptions.RequestException,)

# (connect, read) seconds. Without a timeout a hung proxy socket stalls the whole
# run indefinitely; on timeout the request errors (transient) and we retry a fresh IP.
_HTTP_TIMEOUT = (10.0, 30.0)


class _TimeoutSession(requests.Session):
    """A requests Session that applies a default timeout to every request.

    `requests` has no session-level default timeout, and youtube-transcript-api
    calls `.get()` without one — so a hung connection never returns. We pass this
    as the library's `http_client`; it still layers the proxy config on top.
    """

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", _HTTP_TIMEOUT)
        return super().request(*args, **kwargs)


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
    # Pass our timeout session as http_client; the library still applies the
    # proxy config (proxies, Connection: close, 429-retry adapters) to it.
    return YouTubeTranscriptApi(proxy_config=_proxy_config(), http_client=_TimeoutSession())


def fetch_transcript(
    video_id: str,
    *,
    retries: int = 4,
    backoff: float = 1.5,
    sleep=time.sleep,
) -> str:
    """Fetch a transcript, retrying transient failures with backoff.

    Block handling depends on whether a proxy is configured:
    - **No proxy (single IP):** a `RequestBlocked` is systemic — every later
      fetch will fail too — so it's raised as `TranscriptBlocked` for the batch
      loop to abort on (fail-fast).
    - **Proxy (rotating IPs):** a block or a flaky-exit-IP error affects only
      this attempt; retry with a fresh IP, and if all retries fail let the raw
      error bubble as a per-video failure (no abort).
    """
    proxied = _proxy_config() is not None
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            fetched = _build_api().fetch(video_id)  # v1.x instance API; snippet.text
            return " ".join(snippet.text for snippet in fetched)
        except RequestBlocked as exc:
            if not proxied:
                raise TranscriptBlocked(str(exc)) from exc
            last_exc = exc  # rotating proxy — a fresh IP may not be blocked
        except _TRANSIENT as exc:
            last_exc = exc  # flaky proxy IP / dropped connection
        if attempt < retries:
            sleep(backoff * attempt)
    assert last_exc is not None
    raise last_exc


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
