from __future__ import annotations

from datetime import date

from oracle.derived import DERIVED_SOURCE, ratio
from oracle.series import Bar, PriceSeries


def _series(symbol, rows):
    """``rows`` is (day, open, high, low, close) tuples in January 2026."""
    return PriceSeries(symbol=symbol, source="coinbase", bars=tuple(
        Bar(date=date(2026, 1, d), open=o, high=h, low=lo, close=c) for d, o, h, lo, c in rows
    ))


def test_open_and_close_are_exact_because_both_legs_are_quoted_at_that_instant():
    num = _series("ETH", [(1, 2000.0, 2100.0, 1950.0, 2050.0)])
    den = _series("BTC", [(1, 40000.0, 44000.0, 39000.0, 41000.0)])
    bar = ratio(num, den, symbol="ETH/BTC").bars[0]
    assert bar.open == 2000.0 / 40000.0
    assert bar.close == 2050.0 / 41000.0


def test_high_and_low_are_the_body_not_a_bound_built_from_opposing_extremes():
    """``n.high / d.low`` is a true upper bound and a wildly loose one for correlated legs: it
    assumes ETH peaked exactly when BTC bottomed, every single day. That would inflate every
    range the structure layer measures. The body uses only instants that really existed."""
    num = _series("ETH", [(1, 2000.0, 2100.0, 1950.0, 2050.0)])
    den = _series("BTC", [(1, 40000.0, 44000.0, 39000.0, 41000.0)])
    bar = ratio(num, den, symbol="ETH/BTC").bars[0]
    assert bar.high == max(bar.open, bar.close)
    assert bar.low == min(bar.open, bar.close)
    assert bar.high < 2100.0 / 39000.0        # far below the opposing-extremes bound


def test_a_bar_is_never_inverted():
    """``n.high / d.high`` against ``n.low / d.low`` is not merely loose, it is malformed — it
    can put the high under the low. Asserted across a leg pair where that inversion happens."""
    num = _series("ETH", [(1, 2000.0, 2010.0, 1900.0, 1950.0)])
    den = _series("BTC", [(1, 40000.0, 50000.0, 39000.0, 41000.0)])
    assert 2010.0 / 50000.0 < 1900.0 / 39000.0        # the naive form inverts here
    bar = ratio(num, den, symbol="ETH/BTC").bars[0]
    assert bar.high >= bar.low


def test_bars_have_height_whenever_the_ratio_moved():
    """Collapsing to closes would be equally honest and useless: a zero-height order block is
    refused as ``degenerate_zone``, so every candidate on the series would die."""
    num = _series("ETH", [(1, 2000.0, 2100.0, 1950.0, 2200.0)])
    den = _series("BTC", [(1, 40000.0, 44000.0, 39000.0, 40000.0)])
    bar = ratio(num, den, symbol="ETH/BTC").bars[0]
    assert bar.high > bar.low


def test_only_dates_present_in_both_legs_produce_a_bar():
    """Carrying a leg forward would manufacture a move in the ratio that neither leg made —
    yesterday's BTC against today's ETH is not a price anyone could have traded."""
    num = _series("ETH", [(1, 2000.0, 2100.0, 1950.0, 2050.0),
                          (2, 2050.0, 2150.0, 2000.0, 2100.0)])
    den = _series("BTC", [(1, 40000.0, 44000.0, 39000.0, 41000.0)])
    out = ratio(num, den, symbol="ETH/BTC")
    assert [b.date for b in out.bars] == [date(2026, 1, 1)]


def test_a_non_positive_denominator_is_skipped_rather_than_raising():
    num = _series("ETH", [(1, 2000.0, 2100.0, 1950.0, 2050.0),
                          (2, 2050.0, 2150.0, 2000.0, 2100.0)])
    den = _series("BTC", [(1, 0.0, 0.0, 0.0, 0.0),
                          (2, 40000.0, 44000.0, 39000.0, 41000.0)])
    out = ratio(num, den, symbol="ETH/BTC")
    assert [b.date for b in out.bars] == [date(2026, 1, 2)]


def test_the_source_says_derived_so_it_cannot_be_mistaken_for_a_fetched_series():
    out = ratio(_series("ETH", [(1, 2.0, 2.0, 2.0, 2.0)]),
                _series("BTC", [(1, 1.0, 1.0, 1.0, 1.0)]), symbol="ETH/BTC")
    assert out.source == DERIVED_SOURCE
    assert out.symbol == "ETH/BTC"


def test_an_empty_leg_yields_an_empty_series_rather_than_failing():
    out = ratio(_series("ETH", []), _series("BTC", [(1, 1.0, 1.0, 1.0, 1.0)]), symbol="ETH/BTC")
    assert out.bars == ()
