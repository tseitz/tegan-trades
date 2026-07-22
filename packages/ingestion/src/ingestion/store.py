from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Repo root = five parents up from this file:
# src/ingestion/store.py -> src/ingestion -> src -> ingestion -> packages -> <root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "transcripts"


@dataclass(frozen=True)
class TranscriptRecord:
    platform: str          # "youtube" | "podcast" | "x" | ...
    source_id: str         # natural key, e.g. youtube video id
    text: str
    metadata: dict


def path_for(platform: str, source_id: str, root: Path = DATA_ROOT) -> Path:
    return root / platform / f"{source_id}.txt"


def exists(platform: str, source_id: str, root: Path = DATA_ROOT) -> bool:
    return path_for(platform, source_id, root).exists()


def save(record: TranscriptRecord, root: Path = DATA_ROOT) -> Path:
    text_path = path_for(record.platform, record.source_id, root)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(record.text, encoding="utf-8")

    meta_path = text_path.with_suffix(".json")
    meta = {**record.metadata,
            "platform": record.platform,
            "source_id": record.source_id}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return text_path


def load(platform: str, source_id: str, root: Path = DATA_ROOT) -> str:
    return path_for(platform, source_id, root).read_text(encoding="utf-8")
