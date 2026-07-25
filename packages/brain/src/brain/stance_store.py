from __future__ import annotations

import json
import sys
from pathlib import Path

from core.stance import SCHEMA_VERSION, Stance

# Repo root: src/brain/stance_store.py -> src/brain -> src -> brain -> packages -> <root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "stances"


def stance_path(platform: str, source_id: str, root: Path = DATA_ROOT) -> Path:
    return root / platform / f"{source_id}.json"


def exists(platform: str, source_id: str, root: Path = DATA_ROOT) -> bool:
    return stance_path(platform, source_id, root).exists()


def save_stances(
    platform: str,
    source_id: str,
    stances: list[Stance],
    *,
    model: str,
    extracted_at: str,
    root: Path = DATA_ROOT,
) -> Path:
    path = stance_path(platform, source_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "transcript_ref": f"{platform}/{source_id}",
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "extracted_at": extracted_at,
        "stances": [s.model_dump() for s in stances],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def load_stances(platform: str, source_id: str, root: Path = DATA_ROOT) -> list[Stance]:
    doc = json.loads(stance_path(platform, source_id, root).read_text(encoding="utf-8"))
    return [Stance.model_validate(s) for s in doc["stances"]]


def load_all_stances(root: Path = DATA_ROOT) -> list[Stance]:
    """Every stance in the corpus, flattened.

    The stance corpus is built incrementally by `brain-extract`, so a missing root is a
    normal early state, not an error — it degrades to an empty list. Individual documents
    that fail to parse are skipped rather than taking the whole corpus down, matching the
    per-item tolerance the extraction path is built around.
    """
    if not root.exists():
        return []
    stances: list[Stance] = []
    for path in sorted(root.glob("*/*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            stances.extend(Stance.model_validate(s) for s in doc.get("stances", []))
        except Exception as exc:  # noqa: BLE001 - a corrupt document must not hide the rest
            # Skipping is right; skipping *quietly* is not. A silently-dropped document
            # shrinks a consensus count with no trace, which is worse than a loud error.
            print(f"[brain] skipped unreadable stance file {path.name}: {exc!r}",
                  file=sys.stderr)
    return stances
