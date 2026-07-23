from __future__ import annotations

import json
from pathlib import Path

from core.thesis import SCHEMA_VERSION, Thesis

# Repo root: src/distill/store.py -> src/distill -> src -> distill -> packages -> <root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "theses"


def thesis_path(platform: str, source_id: str, root: Path = DATA_ROOT) -> Path:
    return root / platform / f"{source_id}.json"


def exists(platform: str, source_id: str, root: Path = DATA_ROOT) -> bool:
    return thesis_path(platform, source_id, root).exists()


def save_theses(
    platform: str,
    source_id: str,
    theses: list[Thesis],
    *,
    model: str,
    distilled_at: str,
    root: Path = DATA_ROOT,
) -> Path:
    path = thesis_path(platform, source_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "transcript_ref": f"{platform}/{source_id}",
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "distilled_at": distilled_at,
        "theses": [t.model_dump() for t in theses],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def load_theses(platform: str, source_id: str, root: Path = DATA_ROOT) -> list[Thesis]:
    doc = json.loads(thesis_path(platform, source_id, root).read_text(encoding="utf-8"))
    return [Thesis.model_validate(t) for t in doc["theses"]]
