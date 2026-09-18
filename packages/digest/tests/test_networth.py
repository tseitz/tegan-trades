"""Summing the two pots #75 already loads into one net-worth figure. Pure.

Table-driven, one row per case — the shape `of()` and `delta()` have to get right is a short
list of states, not a long list of arithmetic.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from core.review import Holding, Location, Reading, RosterLean
from digest import networth
from oracle.portfolios import Mandate, Portfolio, Position
from review.cli import ReviewResult


def _reading(ticker, price, shares=1.0) -> Reading:
    return Reading(
        holding=Holding(ticker=ticker, shares=shares, cost=None),
        roster=RosterLean(lean="bullish", bulls=1, bears=0, people=1, newest=None,
                          age_days=None, voices=("A",)),
        location=Location(where="at_support", basis="range", position=0.1),
        verdict="HOLD", price=price, weekly_trend="uptrend",
    )


def _result(*, readings=(), cash=None, cash_by_account=None):
    book = SimpleNamespace(cash=cash, cash_by_account=cash_by_account or {})
    return SimpleNamespace(readings=list(readings), book=book)


# ── `of()` ────────────────────────────────────────────────────────────────────

def test_a_pot_with_cash_by_account_sums_every_account():
    result = _result(readings=[_reading("SPY", 100.0)],
                     cash_by_account={"roth": 50.0, "traditional": 25.0})
    net = networth.of([result], parked=0.0)
    assert net.total == 175.0
    assert net.pots == 1


def test_a_pot_with_bare_cash_counts_it():
    result = _result(readings=[_reading("SPY", 100.0)], cash=40.0)
    net = networth.of([result], parked=0.0)
    assert net.total == 140.0


def test_cash_by_account_wins_over_bare_cash_when_both_are_present():
    """Mirrors `treasury.book._idle_rows`'s precedence — one rule stops the two readers from
    disagreeing about the same dollars when they drift."""
    result = _result(readings=(), cash=999.0, cash_by_account={"roth": 10.0})
    net = networth.of([result], parked=0.0)
    assert net.total == 10.0


def test_a_pot_with_no_cash_at_all_counts_as_zero_not_unknown():
    """A hand-kept file with no broker behind it is the normal case, not a failure."""
    result = _result(readings=[_reading("SPY", 100.0)], cash=None)
    net = networth.of([result], parked=0.0)
    assert net.total == 100.0


def test_an_unpriced_holding_is_excluded_and_counted():
    result = _result(readings=[_reading("SPY", 100.0), _reading("PURR", None)], cash=0.0)
    net = networth.of([result], parked=0.0)
    assert net.total == 100.0
    assert net.unpriced == 1


def test_zero_pots_and_an_unknown_treasury_read_is_unknown():
    assert networth.of([], parked=None) is None


def test_zero_pots_and_a_genuinely_empty_treasury_is_nothing_to_sum():
    assert networth.of([], parked=0.0) is None


def test_zero_pots_and_a_real_parked_figure_is_treasury_only():
    net = networth.of([], parked=500.0)
    assert net.total == 500.0
    assert net.pots == 1


def test_a_failed_portfolio_pass_is_unknown_regardless_of_parked():
    assert networth.of(None, parked=500.0) is None


def test_pot_count_adds_treasury_only_when_it_holds_money():
    result = _result(readings=[_reading("SPY", 100.0)], cash=0.0)
    assert networth.of([result], parked=0.0).pots == 1
    assert networth.of([result], parked=500.0).pots == 2


def test_money_moved_from_a_pots_cash_into_treasury_leaves_the_total_unchanged():
    """The one real double-count vector this module guards against (AC 2)."""
    before = networth.of([_result(readings=[_reading("SPY", 100.0)], cash=200.0)], parked=0.0)
    after = networth.of([_result(readings=[_reading("SPY", 100.0)], cash=50.0)], parked=150.0)
    assert before.total == after.total


# ── `delta()` ─────────────────────────────────────────────────────────────────

def test_bootstrap_is_the_first_night_this_memory_has_ever_been_written():
    net = networth.of([_result(readings=[_reading("SPY", 100.0)], cash=0.0)], parked=0.0)
    d = networth.delta(net, None)
    assert d.bootstrap is True
    assert d.change is None
    assert d.change_pct is None


def test_a_previous_total_of_zero_has_no_percent_change():
    net = networth.of([_result(readings=[_reading("SPY", 100.0)], cash=0.0)], parked=0.0)
    d = networth.delta(net, 0.0)
    assert d.bootstrap is False
    assert d.change == 100.0
    assert d.change_pct is None


def test_change_and_change_pct_against_a_real_previous_total():
    net = networth.of([_result(readings=[_reading("SPY", 110.0)], cash=0.0)], parked=0.0)
    d = networth.delta(net, 100.0)
    assert d.change == pytest.approx(10.0)
    assert d.change_pct == pytest.approx(0.1)


def test_remember_returns_the_total():
    net = networth.of([_result(readings=[_reading("SPY", 100.0)], cash=0.0)], parked=0.0)
    assert networth.remember(net) == 100.0


# ── a real `ReviewResult` and `Portfolio`, not the duck-typed stand-in above ──

def test_against_a_real_review_result_and_portfolio():
    """`of()` reaches two attributes deep into types it never imports
    (`.readings[*].market_value` and `.book.cash`/`.book.cash_by_account`), which a table of
    `SimpleNamespace`s cannot catch drifting."""
    mandate = Mandate(name="retirement", benchmarks=(), horizon="macro",
                      risk_posture="conservative")
    position = Position(holding=Holding(ticker="SPY", shares=2.0, cost=90.0), domain="stock")
    book = Portfolio(name="retirement", mandate=mandate, positions=(position,), cash=25.0)
    result = ReviewResult(book=book, readings=[_reading("SPY", 100.0, shares=2.0)],
                          contexts=(None,), mismatched=(), levels=((), (), 0), chains=(),
                          macro=())

    net = networth.of([result], parked=0.0)
    assert net.total == 225.0
    assert net.pots == 1
    assert net.unpriced == 0
