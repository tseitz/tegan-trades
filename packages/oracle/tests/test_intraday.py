"""The intraday series — the timeframe below the daily, carrying volume and a clock.

Two things are being pinned here, and they are the whole reason this type exists rather than
reusing ``oracle.series``:

1. **Bars carry volume.** The trigger's own gate — refuse a candidate whose entry timeframe
   has no trade in it — cannot be answered by ``execution.participation``, which measures
   *daily* equity depth through an open broker. It has to come off the bars themselves.
2. **Bars carry a datetime, and it still flows through ``core``.** ``core.structure`` and
   ``core.imbalance`` are duck-typed on ``.date`` and use it only as an orderable token, so
   the primitives run unchanged on intraday bars. The last two tests hold that claim honest;
   if either breaks, the H1 trigger's premise is gone and this is where it should be found.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from core import imbalance, structure
from oracle.intraday import H1, H12, IntradayBar, IntradaySeries


def _at(hour: int, day: int = 1) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


def _bar(hour: int, close: float, *, day: int = 1, volume: float | None = 10.0) -> IntradayBar:
    return IntradayBar(
        date=_at(hour, day), open=close, high=close + 1, low=close - 1, close=close,
        volume=volume,
    )


def _series(*bars, interval: str = H1) -> IntradaySeries:
    return IntradaySeries(symbol="BTC-USD", source="coinbase", interval=interval, bars=bars)


# ── the bar ─────────────────────────────────────────────────────────────────────────────────

def test_a_bar_carries_volume():
    assert _bar(0, 100.0, volume=42.5).volume == 42.5


def test_unmeasured_volume_is_none_not_zero():
    """Same distinction ``execution.participation.check_depth`` already draws: a market that
    traded nothing is a refusal, a market nobody measured is merely uncapped. Collapsing the
    two here would make every source that omits volume look like a dead market."""
    assert IntradayBar(date=_at(0), open=1, high=1, low=1, close=1).volume is None
    assert _bar(0, 100.0, volume=0.0).volume == 0.0


def test_a_bar_is_immutable():
    with pytest.raises(FrozenInstanceError):
        _bar(0, 100.0).close = 1.0


# ── the series ──────────────────────────────────────────────────────────────────────────────

def test_bars_are_sorted_ascending_on_construction():
    """Coinbase returns newest-first, exactly as it does for daily bars."""
    series = _series(_bar(2, 102.0), _bar(0, 100.0), _bar(1, 101.0))
    assert [b.close for b in series.bars] == [100.0, 101.0, 102.0]


def test_duplicate_timestamps_collapse_with_the_last_one_winning():
    """Paginated fetches overlap at the seams and a refetch of a forming bar is a correction —
    the same reasoning ``oracle.cache.merge`` relies on for daily bars."""
    series = _series(_bar(0, 100.0), _bar(0, 111.0))
    assert len(series.bars) == 1
    assert series.bars[0].close == 111.0


def test_naive_timestamps_are_refused_at_construction():
    """A naive/aware mix raises deep inside ``sorted`` with a message that names neither the
    symbol nor the source. Rejecting at the boundary is the difference between a fixable
    error and a confusing one — and a naive UTC-looking stamp from a source that meant
    local time is a silent several-hour shift, which is worse than either."""
    # DTZ001 is the rule this test exists to enforce at runtime — the naive stamp is the
    # input under test, not an oversight.
    naive = IntradayBar(date=datetime(2026, 8, 1, 0), open=1, high=1, low=1, close=1)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        _series(naive)


def test_span_is_first_and_last_stamp():
    series = _series(_bar(0, 100.0), _bar(5, 105.0))
    assert series.span == (_at(0), _at(5))


def test_span_of_an_empty_series_is_none():
    assert _series().span is None


def test_latest_is_the_newest_bar_or_none():
    assert _series().latest is None
    assert _series(_bar(0, 100.0), _bar(3, 103.0)).latest.close == 103.0


def test_bars_between_is_inclusive_at_both_ends():
    series = _series(*[_bar(h, 100.0 + h) for h in range(6)])
    window = series.bars_between(_at(1), _at(3))
    assert [b.date.hour for b in window] == [1, 2, 3]


def test_the_interval_is_carried_so_h1_and_h12_cannot_be_confused():
    """The two timeframes have different jobs — H12 is the setup zone, H1 is the trigger —
    and a resampled series is otherwise indistinguishable from a fetched one."""
    assert _series(_bar(0, 1.0)).interval == H1
    assert _series(_bar(0, 1.0), interval=H12).interval == H12


# ── the premise: core's primitives run on these bars unchanged ───────────────────────────────

def test_core_structure_finds_swings_in_intraday_bars():
    """``core.structure`` never names a bar type and treats ``.date`` as an orderable token
    only. If this fails, the H1 trigger cannot be built on the existing primitives."""
    closes = [100, 101, 105, 101, 100, 99, 95, 99, 100]
    bars = [_bar(h, float(c)) for h, c in enumerate(closes)]
    found = structure.swings(bars, width=2)
    assert found, "no swings found in a series with an obvious high and low"
    assert {s.kind for s in found} == {structure.SWING_HIGH, structure.SWING_LOW}
    assert all(isinstance(s.date, datetime) for s in found)


def test_core_imbalance_finds_a_gap_in_intraday_bars():
    """A displacement candle leaving a void, on the hourly clock."""
    quiet = [
        IntradayBar(date=_at(h), open=100.0, high=100.5, low=99.5, close=100.0, volume=1.0)
        for h in range(16)
    ]
    push = [
        IntradayBar(date=_at(16), open=100.0, high=101.0, low=99.9, close=100.9, volume=9.0),
        IntradayBar(date=_at(17), open=101.0, high=112.0, low=100.9, close=111.5, volume=9.0),
        IntradayBar(date=_at(18), open=111.5, high=113.0, low=105.0, close=112.0, volume=9.0),
    ]
    gaps = imbalance.fair_value_gaps(quiet + push)
    assert gaps, "no FVG found across an 11-point hourly displacement"
    assert gaps[0].kind == imbalance.BULLISH
    assert isinstance(gaps[0].date, datetime)


def test_swings_stay_ordered_across_a_day_boundary():
    """Hours wrap and dates do not — sorting on the stamp rather than the hour is what keeps
    23:00 before the next day's 00:00."""
    bars = [_bar(23, 100.0, day=1), _bar(0, 101.0, day=2), _bar(1, 102.0, day=2)]
    series = _series(*bars)
    assert [b.date for b in series.bars] == sorted(b.date for b in bars)
    assert series.span == (_at(23, 1), _at(1, 2))


def test_the_series_reports_its_own_hourly_coverage():
    """The gate needs "does this candidate have a trigger timeframe at all". A series with
    three bars over a fortnight is not an answer, and it must be distinguishable from a
    series that is merely quiet."""
    series = _series(*[_bar(h, 100.0) for h in range(6)])
    assert series.traded_bars == 6
    assert _series(*[_bar(h, 100.0, volume=0.0) for h in range(6)]).traded_bars == 0
    assert _series(*[_bar(h, 100.0, volume=None) for h in range(6)]).traded_bars is None


def test_bars_between_on_an_empty_series_is_empty():
    assert _series().bars_between(_at(0), _at(1)) == ()


def test_a_series_spanning_a_gap_keeps_the_bars_it_has():
    """Equities do not trade overnight and the resample drops empty buckets — a series with
    holes is the normal case, not a defect, so nothing here may try to fill them."""
    series = _series(_bar(14, 100.0, day=1), _bar(14, 101.0, day=2))
    assert len(series.bars) == 2
    assert series.span[1] - series.span[0] == timedelta(days=1)
