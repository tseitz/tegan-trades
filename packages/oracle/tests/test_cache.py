from datetime import date, datetime

import pytest
from oracle import cache, intraday
from oracle.intraday import IntradayBar
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


# ── intraday ────────────────────────────────────────────────────────────────────────────────
# Stored under their own tree because the interval is part of a series' identity: BTC-USD on
# the hourly and BTC-USD on the daily are different data, and one file name cannot hold both.

def _intraday(*triples, symbol="BTC-USD", source="coinbase", interval=intraday.H1):
    bars = tuple(
        IntradayBar(
            date=datetime.fromisoformat(t), open=c, high=c, low=c, close=c, volume=v,
        )
        for t, c, v in triples
    )
    return intraday.IntradaySeries(
        symbol=symbol, source=source, interval=interval, bars=bars,
    )


def test_intraday_roundtrip_preserves_stamps_and_volume(tmp_path):
    original = _intraday(
        ("2026-08-01T00:00:00+00:00", 100.0, 12.5),
        ("2026-08-01T01:00:00+00:00", 101.0, 0.0),
    )
    cache.save_intraday(original, root=tmp_path)
    loaded = cache.load_intraday("coinbase", intraday.H1, "BTC-USD", root=tmp_path)
    assert loaded.bars == original.bars
    assert loaded.interval == intraday.H1
    assert loaded.bars[0].date.tzinfo is not None


def test_intraday_unmeasured_volume_survives_the_roundtrip(tmp_path):
    """None must not come back as 0.0 — see ``test_intraday`` for why the two differ."""
    cache.save_intraday(_intraday(("2026-08-01T00:00:00+00:00", 100.0, None)), root=tmp_path)
    loaded = cache.load_intraday("coinbase", intraday.H1, "BTC-USD", root=tmp_path)
    assert loaded.bars[0].volume is None


def test_intraday_load_missing_returns_none(tmp_path):
    assert cache.load_intraday("coinbase", intraday.H1, "NOPE-USD", root=tmp_path) is None


def test_intervals_never_share_a_path(tmp_path):
    """The whole reason the interval is in the path: H1 and H12 for one symbol are different
    series, and writing both to one file would have the second silently eat the first."""
    assert (cache.intraday_path("coinbase", intraday.H1, "BTC-USD", tmp_path)
            != cache.intraday_path("coinbase", intraday.H12, "BTC-USD", tmp_path))


def test_intraday_never_collides_with_the_daily_cache(tmp_path):
    cache.save(_series(("2025-01-01", 1.0)), root=tmp_path)
    cache.save_intraday(_intraday(("2026-08-01T00:00:00+00:00", 9.0, 1.0)), root=tmp_path)
    assert cache.load("coinbase", "BTC-USD", root=tmp_path).bars[0].close == pytest.approx(1.0)
    loaded = cache.load_intraday("coinbase", intraday.H1, "BTC-USD", root=tmp_path)
    assert loaded.bars[0].close == pytest.approx(9.0)


def test_intraday_merge_extends_and_prefers_incoming(tmp_path):
    """The forming bar is refetched on every run and its volume grows until the hour closes,
    so incoming must win — the daily cache's reasoning, on a faster clock."""
    cache.save_intraday(_intraday(
        ("2026-08-01T00:00:00+00:00", 100.0, 5.0),
        ("2026-08-01T01:00:00+00:00", 101.0, 1.0),
    ), root=tmp_path)
    cache.merge_intraday(_intraday(
        ("2026-08-01T01:00:00+00:00", 101.5, 8.0),
        ("2026-08-01T02:00:00+00:00", 102.0, 3.0),
    ), root=tmp_path)
    loaded = cache.load_intraday("coinbase", intraday.H1, "BTC-USD", root=tmp_path)
    assert [b.date.hour for b in loaded.bars] == [0, 1, 2]
    assert loaded.bars[1].volume == pytest.approx(8.0)


def test_intraday_merge_into_empty_cache_is_a_plain_save(tmp_path):
    cache.merge_intraday(_intraday(("2026-08-01T00:00:00+00:00", 1.0, 1.0)), root=tmp_path)
    assert cache.load_intraday("coinbase", intraday.H1, "BTC-USD", root=tmp_path) is not None
