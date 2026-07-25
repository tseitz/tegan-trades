"""Minimal reader over the thesis firehose on disk.

Deliberately does *not* import ``distill`` — the oracle only needs the handful of fields
that drive routing and fetching, and coupling the price layer to the LLM layer would drag
its dependencies along for no benefit. ``data/theses/*/*.json`` is a stable on-disk shape.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from core.canon import Registry, resolve

# src/oracle/corpus.py -> src/oracle -> src -> oracle -> packages -> <repo root>
THESES_ROOT = Path(__file__).resolve().parents[4] / "data" / "theses"


@dataclass(frozen=True)
class CorpusRow:
    """One thesis, flattened to what pricing and grading actually read."""
    id: str
    asset: str              # canonical
    asset_raw: str
    domain: str
    direction: str
    timeframe: str
    conviction: str
    confidence: float
    person: str             # canonical
    published_at: str
    summary: str
    thesis_type: str
    url: str
    # A tuple, not a list, so the row stays genuinely immutable. Unlabeled by design — see
    # core.levels for how a target is read out of it, and why that can decline.
    key_levels: tuple[float, ...] = ()


def iter_rows(registry: Registry, *, root: Path = THESES_ROOT) -> Iterator[CorpusRow]:
    for path in sorted(Path(root).rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for t in doc.get("theses", []):
            resolved = resolve(_Lens(t), registry)
            yield CorpusRow(
                id=t["id"],
                asset=resolved.asset_canonical,
                asset_raw=t["asset"],
                domain=t["domain"],
                direction=t["direction"],
                timeframe=t["timeframe"],
                conviction=t["conviction"],
                confidence=(t.get("extraction") or {}).get("confidence", 0.0),
                person=resolved.person_canonical,
                published_at=(t.get("source") or {}).get("published_at") or "",
                summary=t.get("summary", ""),
                thesis_type=t.get("thesis_type", ""),
                url=(t.get("source") or {}).get("url", ""),
                key_levels=tuple(t.get("key_levels") or ()),
            )


class _Lens:
    """Adapts a raw thesis dict to the duck-typed shape ``canon.resolve`` expects."""

    def __init__(self, raw: dict):
        self.asset = raw["asset"]
        self.source = type("S", (), {"person": (raw.get("source") or {}).get("person", "")})()
