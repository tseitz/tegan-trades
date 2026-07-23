from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ingestion import channel as channel_mod
from ingestion.store import DATA_ROOT
from ingestion.store import exists as _store_exists

DEFAULT_MAX_VIDEOS = 50
DEFAULT_MAX_AGE_DAYS = 730

# Repo root: src/ingestion/roster.py -> ... -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_WATCHLIST = _REPO_ROOT / "config" / "watchlist.yaml"


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
