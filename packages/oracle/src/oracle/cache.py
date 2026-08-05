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

from oracle.intraday import IntradayBar, IntradaySeries
from oracle.series import Bar, PriceSeries

# src/oracle/cache.py -> src/oracle -> src -> oracle -> packages -> <repo root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "prices"

# Intraday lives under its own subtree rather than beside the daily files, because the
# interval is part of a series' identity: BTC-USD hourly and BTC-USD daily are different data
# and one file name cannot hold both. Nesting it inside DATA_ROOT keeps "delete data/prices/"
# the single safe reset it has always been.
INTRADAY_DIR = "intraday"


def cache_path(source: str, symbol: str, root: Path = DATA_ROOT) -> Path:
    return Path(root) / source / f"{quote(symbol, safe='')}.json"


def intraday_path(source: str, interval: str, symbol: str, root: Path = DATA_ROOT) -> Path:
    return Path(root) / INTRADAY_DIR / source / interval / f"{quote(symbol, safe='')}.json"


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


def save_intraday(series: IntradaySeries, *, root: Path = DATA_ROOT) -> Path:
    path = intraday_path(series.source, series.interval, series.symbol, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "symbol": series.symbol,
        "source": series.source,
        "interval": series.interval,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # Short keys matter more here than for daily: an hourly series over the corpus span is
        # ~18,000 bars per symbol against the daily file's ~730.
        "bars": [
            {"t": b.date.isoformat(), "o": b.open, "h": b.high,
             "l": b.low, "c": b.close, "v": b.volume}
            for b in series.bars
        ],
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def load_intraday(
    source: str, interval: str, symbol: str, *, root: Path = DATA_ROOT,
) -> IntradaySeries | None:
    path = intraday_path(source, interval, symbol, root)
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    bars = tuple(
        IntradayBar(
            date=datetime.fromisoformat(b["t"]),
            open=b["o"], high=b["h"], low=b["l"], close=b["c"],
            # ``.get`` rather than ``[]``: an unmeasured bar is stored as null and a source
            # that never reports volume would otherwise fail to load at all.
            volume=b.get("v"),
        )
        for b in doc.get("bars", [])
    )
    return IntradaySeries(
        symbol=doc["symbol"], source=doc["source"], interval=doc["interval"], bars=bars,
    )


def merge_intraday(series: IntradaySeries, *, root: Path = DATA_ROOT) -> Path:
    """Union new bars into whatever is cached, incoming winning on timestamp conflicts.

    Incoming wins for a sharper reason than in the daily case: the newest bar of a live fetch
    is always still forming, so its volume and close are provisional and every subsequent run
    refetches a truer version of the same stamp.
    """
    existing = load_intraday(series.source, series.interval, series.symbol, root=root)
    if existing is None:
        return save_intraday(series, root=root)
    # IntradaySeries dedupes on construction keeping the last bar seen per stamp, so ordering
    # existing-then-incoming makes the fresh value win.
    combined = IntradaySeries(
        symbol=series.symbol,
        source=series.source,
        interval=series.interval,
        bars=existing.bars + series.bars,
    )
    return save_intraday(combined, root=root)
