from datetime import date

import pytest

from oracle import cache
from oracle.series import Bar, PriceSeries


def _series(*pairs, symbol="BTC-USD", source="coinbase"):
    bars = tuple(
        Bar(date=date.fromisoformat(d), open=c, high=c, low=c, close=c) for d, c in pairs
    )
    return PriceSeries(symbol=symbol, source=source, bars=bars)


def test_roundtrip_preserves_bars(tmp_path):
    original = _series(("2025-01-01", 100.0), ("2025-01-02", 110.5))
    cache.save(original, root=tmp_path)
    loaded = cache.load("coinbase", "BTC-USD", root=tmp_path)
    assert loaded.bars == original.bars
    assert loaded.symbol == "BTC-USD"
    assert loaded.source == "coinbase"


def test_load_missing_returns_none(tmp_path):
    assert cache.load("coinbase", "NOPE-USD", root=tmp_path) is None


@pytest.mark.parametrize("symbol", ["^GSPC", "GC=F", "DX-Y.NYB", "BTC-USD", "EURUSD=X"])
def test_filesystem_hostile_symbols_roundtrip(tmp_path, symbol):
    """Yahoo symbols carry ^, = and . — these must survive the path encoding without
    colliding, or two different instruments would share one cache file."""
    cache.save(_series(("2025-01-01", 1.0), symbol=symbol, source="yahoo"), root=tmp_path)
    loaded = cache.load("yahoo", symbol, root=tmp_path)
    assert loaded is not None and loaded.symbol == symbol


def test_distinct_symbols_never_share_a_path(tmp_path):
    assert cache.cache_path("yahoo", "GC=F", tmp_path) != cache.cache_path("yahoo", "GC-F", tmp_path)


def test_merge_extends_without_losing_or_duplicating(tmp_path):
    """Backfills are resumable and their windows overlap at the seams."""
    cache.save(_series(("2025-01-01", 1.0), ("2025-01-02", 2.0)), root=tmp_path)
    cache.merge(_series(("2025-01-02", 2.0), ("2025-01-03", 3.0)), root=tmp_path)
    loaded = cache.load("coinbase", "BTC-USD", root=tmp_path)
    assert [b.date.isoformat() for b in loaded.bars] == ["2025-01-01", "2025-01-02", "2025-01-03"]


def test_merge_prefers_incoming_bar_on_conflict(tmp_path):
    """A re-fetch of the same date is a correction (a partial bar filled in), so the
    fresh value must win rather than being silently discarded."""
    cache.save(_series(("2025-01-01", 1.0)), root=tmp_path)
    cache.merge(_series(("2025-01-01", 9.0)), root=tmp_path)
    assert cache.load("coinbase", "BTC-USD", root=tmp_path).bars[0].close == pytest.approx(9.0)


def test_merge_into_empty_cache_is_a_plain_save(tmp_path):
    cache.merge(_series(("2025-01-01", 1.0)), root=tmp_path)
    assert cache.load("coinbase", "BTC-USD", root=tmp_path) is not None
