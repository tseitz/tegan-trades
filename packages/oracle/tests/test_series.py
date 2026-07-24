from datetime import date, timedelta

import pytest

from oracle.series import MAX_CARRY_DAYS, Bar, PriceSeries


def _series(*pairs, symbol="BTC-USD", source="coinbase"):
    """pairs: ('2025-01-02', close) — open/high/low derived, we mostly assert on close."""
    bars = tuple(
        Bar(date=date.fromisoformat(d), open=c, high=c * 1.01, low=c * 0.99, close=c)
        for d, c in pairs
    )
    return PriceSeries(symbol=symbol, source=source, bars=bars)


# ── construction invariants ─────────────────────────────────────────────────

def test_bars_are_sorted_and_deduped_on_construction():
    # Sources paginate and overlap; Coinbase returns newest-first. Normalize once, here,
    # so every consumer can assume ascending + unique.
    s = _series(("2025-01-03", 3.0), ("2025-01-01", 1.0), ("2025-01-03", 3.0), ("2025-01-02", 2.0))
    assert [b.date.isoformat() for b in s.bars] == ["2025-01-01", "2025-01-02", "2025-01-03"]


def test_empty_series_is_allowed_and_returns_none():
    s = PriceSeries(symbol="X-USD", source="coinbase", bars=())
    assert s.close_on(date(2025, 1, 1)) is None
    assert s.span is None


def test_span_reports_first_and_last_bar_dates():
    s = _series(("2025-01-01", 1.0), ("2025-03-01", 2.0))
    assert s.span == (date(2025, 1, 1), date(2025, 3, 1))


# ── close_on: exact hits ────────────────────────────────────────────────────

def test_close_on_exact_date():
    s = _series(("2025-01-01", 100.0), ("2025-01-02", 110.0))
    assert s.close_on(date(2025, 1, 2)) == pytest.approx(110.0)


# ── close_on: carry-forward (the weekend/holiday case) ──────────────────────

def test_close_on_carries_last_known_bar_forward():
    # Friday close is the honest price for a Saturday query.
    s = _series(("2025-01-03", 100.0))  # Friday
    assert s.close_on(date(2025, 1, 4)) == pytest.approx(100.0)  # Saturday
    assert s.close_on(date(2025, 1, 5)) == pytest.approx(100.0)  # Sunday


def test_close_on_never_looks_into_the_future():
    """The whole phase is a backtest — reading a later bar for an earlier date would
    leak information and silently inflate every score."""
    s = _series(("2025-01-01", 100.0), ("2025-01-10", 200.0))
    # 2025-01-05 must resolve to the Jan-1 bar, never the Jan-10 one.
    assert s.close_on(date(2025, 1, 5)) == pytest.approx(100.0)


def test_close_on_before_first_bar_is_none():
    s = _series(("2025-01-10", 100.0))
    assert s.close_on(date(2025, 1, 1)) is None


def test_close_on_refuses_to_carry_beyond_max_gap():
    """A long carry means the data is genuinely missing (delisting, outage). Inventing a
    price there would fabricate a return, so degrade to None instead."""
    s = _series(("2025-01-01", 100.0))
    last = date(2025, 1, 1)
    assert s.close_on(last + timedelta(days=MAX_CARRY_DAYS)) == pytest.approx(100.0)
    assert s.close_on(last + timedelta(days=MAX_CARRY_DAYS + 1)) is None


def test_close_on_max_carry_is_overridable():
    s = _series(("2025-01-01", 100.0))
    assert s.close_on(date(2025, 1, 20), max_carry_days=30) == pytest.approx(100.0)
    assert s.close_on(date(2025, 1, 20), max_carry_days=2) is None


# ── extremes over a window (Tier-2 path-dependent grading needs this) ───────

def test_extremes_between_is_inclusive_of_both_ends():
    s = _series(("2025-01-01", 10.0), ("2025-01-02", 30.0), ("2025-01-03", 20.0))
    lo, hi = s.extremes_between(date(2025, 1, 1), date(2025, 1, 3))
    assert lo == pytest.approx(10.0 * 0.99)
    assert hi == pytest.approx(30.0 * 1.01)


def test_extremes_between_empty_window_is_none():
    s = _series(("2025-01-01", 10.0))
    assert s.extremes_between(date(2025, 2, 1), date(2025, 2, 5)) is None
