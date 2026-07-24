"""Price cache — regenerable ore under ``data/prices/``, same contract as ``data/theses/``.

Mirrors ``distill.store``: a ``DATA_ROOT`` resolved off this file's location, flat JSON,
no migrations. Deleting the tree is always safe; a refetch rebuilds it.

Symbols are percent-encoded into filenames because Yahoo's namespace is filesystem-hostile
(``^GSPC``, ``GC=F``, ``DX-Y.NYB``). Encoding is reversible and injective, so two distinct
instruments can never collide onto one cache file.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

from oracle.series import Bar, PriceSeries

# src/oracle/cache.py -> src/oracle -> src -> oracle -> packages -> <repo root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "prices"


def cache_path(source: str, symbol: str, root: Path = DATA_ROOT) -> Path:
    return Path(root) / source / f"{quote(symbol, safe='')}.json"


def save(series: PriceSeries, *, root: Path = DATA_ROOT) -> Path:
    path = cache_path(series.source, series.symbol, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "symbol": series.symbol,
        "source": series.source,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # Short keys: ~730 bars per symbol across a few hundred symbols.
        "bars": [
            {"d": b.date.isoformat(), "o": b.open, "h": b.high, "l": b.low, "c": b.close}
            for b in series.bars
        ],
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def load(source: str, symbol: str, *, root: Path = DATA_ROOT) -> PriceSeries | None:
    path = cache_path(source, symbol, root)
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    bars = tuple(
        Bar(
            date=date.fromisoformat(b["d"]),
            open=b["o"], high=b["h"], low=b["l"], close=b["c"],
        )
        for b in doc.get("bars", [])
    )
    return PriceSeries(symbol=doc["symbol"], source=doc["source"], bars=bars)


def merge(series: PriceSeries, *, root: Path = DATA_ROOT) -> Path:
    """Union new bars into whatever is cached, incoming winning on date conflicts.

    Backfills are resumable and their request windows overlap at the seams, so merge —
    not overwrite — is the right default. Incoming wins because a refetch of a date is a
    correction (e.g. a partial final bar now closed).
    """
    existing = load(series.source, series.symbol, root=root)
    if existing is None:
        return save(series, root=root)
    # PriceSeries dedupes on construction keeping the last bar seen per date, so ordering
    # existing-then-incoming makes the fresh value win.
    combined = PriceSeries(
        symbol=series.symbol,
        source=series.source,
        bars=existing.bars + series.bars,
    )
    return save(combined, root=root)
