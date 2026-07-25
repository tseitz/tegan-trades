from datetime import date

import pytest

from oracle.resample import to_weekly
from oracle.series import Bar, PriceSeries


def _bar(d: str, *, open: float, high: float, low: float, close: float) -> Bar:
    return Bar(date=date.fromisoformat(d), open=open, high=high, low=low, close=close)


def _flat_bar(d: str, price: float) -> Bar:
    """A bar where open/high/low/close all equal ``price`` — convenient filler for days
    whose OHLC don't matter to the assertion at hand."""
    return _bar(d, open=price, high=price, low=price, close=price)


# ── the look-ahead pin ───────────────────────────────────────────────────────

def test_weekly_close_on_never_leaks_the_current_week():
    """Exists to catch look-ahead leakage: a weekly bar dated at the week's start would
    make close_on(mid-week) return that same week's close — including days that, from the
    query date's point of view, haven't happened yet. Dating at the week's last bar is what
    prevents that."""
    week1 = [_flat_bar(d, 100.0) for d in
              ("2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09",
               "2025-01-10", "2025-01-11", "2025-01-12")]
    week2 = [_flat_bar(d, 200.0) for d in
              ("2025-01-13", "2025-01-14", "2025-01-15", "2025-01-16",
               "2025-01-17", "2025-01-18", "2025-01-19")]
    series = PriceSeries(symbol="BTC-USD", source="coinbase", bars=tuple(week1 + week2))

    weekly = to_weekly(series, include_partial=True)

    # 2025-01-15 is a Wednesday inside week 2. If we'd dated week 2's bar at its start
    # (2025-01-13), this would wrongly resolve to week 2's close.
    assert weekly.close_on(date(2025, 1, 15)) == pytest.approx(100.0)


# ── OHLC aggregation ─────────────────────────────────────────────────────────

def test_ohlc_aggregates_open_close_high_low_correctly():
    bars = (
        _bar("2025-01-06", open=10.0, high=12.0, low=9.0, close=11.0),
        _bar("2025-01-07", open=11.0, high=15.0, low=10.5, close=14.0),
        _bar("2025-01-08", open=14.0, high=14.5, low=8.0, close=9.0),
    )
    # Trailing bar in the next week so the first week is complete.
    series = PriceSeries(symbol="X-USD", source="coinbase",
                          bars=bars + (_flat_bar("2025-01-13", 9.5),))

    weekly = to_weekly(series, include_partial=False)

    assert len(weekly.bars) == 1
    bar = weekly.bars[0]
    assert bar.date == date(2025, 1, 8)  # last daily bar in the week
    assert bar.open == pytest.approx(10.0)   # first bar's open
    assert bar.close == pytest.approx(9.0)   # last bar's close
    assert bar.high == pytest.approx(15.0)   # max across the week
    assert bar.low == pytest.approx(8.0)     # min across the week


# ── include_partial ───────────────────────────────────────────────────────────

def test_include_partial_false_drops_trailing_incomplete_week():
    week1 = [_flat_bar(d, 100.0) for d in ("2025-01-06", "2025-01-07")]
    week2 = [_flat_bar(d, 200.0) for d in ("2025-01-13", "2025-01-14")]
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=tuple(week1 + week2))

    weekly = to_weekly(series, include_partial=False)

    assert len(weekly.bars) == 1
    assert weekly.bars[0].date == date(2025, 1, 7)


def test_include_partial_true_keeps_trailing_incomplete_week():
    week1 = [_flat_bar(d, 100.0) for d in ("2025-01-06", "2025-01-07")]
    week2 = [_flat_bar(d, 200.0) for d in ("2025-01-13", "2025-01-14")]
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=tuple(week1 + week2))

    weekly = to_weekly(series, include_partial=True)

    assert len(weekly.bars) == 2
    assert weekly.bars[-1].date == date(2025, 1, 14)
    assert weekly.bars[-1].close == pytest.approx(200.0)


# ── 5-day equity-style week ───────────────────────────────────────────────────

def test_five_day_equity_week_aggregates_and_counts_as_complete():
    week1 = (
        _bar("2025-01-06", open=100.0, high=105.0, low=99.0, close=101.0),  # Mon
        _bar("2025-01-07", open=101.0, high=103.0, low=100.0, close=102.0),  # Tue
        _bar("2025-01-08", open=102.0, high=104.0, low=101.0, close=103.0),  # Wed
        _bar("2025-01-09", open=103.0, high=106.0, low=102.0, close=104.0),  # Thu
        _bar("2025-01-10", open=104.0, high=107.0, low=103.0, close=98.0),   # Fri
    )
    # A later week (also 5-day, equities skip the weekend) makes week 1 complete.
    week2 = (_bar("2025-01-13", open=98.0, high=99.0, low=97.0, close=98.5),)
    series = PriceSeries(symbol="SPY", source="yahoo", bars=week1 + week2)

    weekly = to_weekly(series, include_partial=False)

    assert len(weekly.bars) == 1
    bar = weekly.bars[0]
    assert bar.date == date(2025, 1, 10)  # Friday, the week's last bar
    assert bar.open == pytest.approx(100.0)
    assert bar.close == pytest.approx(98.0)
    assert bar.high == pytest.approx(107.0)
    assert bar.low == pytest.approx(99.0)


# ── single-bar week ───────────────────────────────────────────────────────────

def test_week_with_a_single_daily_bar():
    bars = (
        _bar("2025-01-08", open=50.0, high=55.0, low=48.0, close=52.0),  # lone Wed
        _flat_bar("2025-01-13", 60.0),  # next week, makes the lone bar's week complete
    )
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=bars)

    weekly = to_weekly(series, include_partial=False)

    assert len(weekly.bars) == 1
    bar = weekly.bars[0]
    assert bar.date == date(2025, 1, 8)
    assert bar.open == pytest.approx(50.0)
    assert bar.high == pytest.approx(55.0)
    assert bar.low == pytest.approx(48.0)
    assert bar.close == pytest.approx(52.0)


# ── empty input ───────────────────────────────────────────────────────────────

def test_empty_series_returns_empty_series():
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=())

    weekly = to_weekly(series, include_partial=True)

    assert weekly.bars == ()
    assert weekly.symbol == "X-USD"
    assert weekly.source == "coinbase"


# ── year boundary ─────────────────────────────────────────────────────────────

def test_bars_spanning_a_year_boundary_do_not_collapse_into_one_week():
    # 2024-12-27 is ISO week (2024, 52); 2024-12-30 through 2025-01-02 is ISO week
    # (2025, 1). Grouping by week number alone (ignoring year) would wrongly merge these.
    bars = (
        _flat_bar("2024-12-27", 100.0),  # (2024, 52)
        _flat_bar("2024-12-30", 110.0),  # (2025, 1)
        _flat_bar("2025-01-02", 111.0),  # (2025, 1)
        _flat_bar("2025-01-06", 120.0),  # (2025, 2) - makes (2025, 1) complete
    )
    series = PriceSeries(symbol="X-USD", source="coinbase", bars=bars)

    weekly = to_weekly(series, include_partial=True)

    assert [b.date.isoformat() for b in weekly.bars] == [
        "2024-12-27", "2025-01-02", "2025-01-06",
    ]
    assert weekly.bars[1].open == pytest.approx(110.0)
    assert weekly.bars[1].close == pytest.approx(111.0)
