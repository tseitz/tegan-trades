"""Thin JSON-over-HTTP helper shared by the source adapters.

Kept deliberately small: the adapters inject ``get_json`` in tests, so this module is the
only place that touches the network and is never exercised by the unit suite.
"""
from __future__ import annotations

import time
from typing import Any

import requests

# Yahoo serves an empty body to obviously-scripted clients.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) tegan-trades/0.1"

TIMEOUT = 20
RETRIES = 3
BACKOFF = 1.5


class FetchError(RuntimeError):
    """Non-recoverable fetch failure — the caller records the symbol as unfetchable
    rather than aborting a multi-hundred-symbol backfill."""


def get_json(url: str, params: dict | None = None, *, timeout: int = TIMEOUT) -> Any:
    """GET + parse JSON, retrying rate limits and transient 5xx with linear backoff."""
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(
                url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT}
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(BACKOFF * (attempt + 1))
                last = FetchError(f"{resp.status_code} for {url}")
                continue
            if resp.status_code == 404:
                # A symbol the source doesn't carry — a normal outcome for the long tail,
                # not an error. Adapters parse this into "no bars".
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last = exc
            time.sleep(BACKOFF * (attempt + 1))
    raise FetchError(f"failed after {RETRIES} attempts: {url}") from last
