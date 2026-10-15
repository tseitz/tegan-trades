"""Summing the two pots #75 already loads into one net-worth figure. Pure.

Table-driven, one row per case — the shape `of()` and `delta()` have to get right is a short
list of states, not a long list of arithmetic.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from core import networth
from core.review import Holding, Location, Reading, RosterLean


def _reading(ticker, price, shares=1.0) -> Reading:
    return Reading(
        holding=Holding(ticker=ticker, shares=shares, cost=None),
        roster=RosterLean(lean="bullish", bulls=1, bears=0, people=1, newest=None,
                          age_days=None, voices=("A",)),
        location=Location(where="at_support", basis="range", position=0.1),
        verdict="HOLD", price=price, weekly_trend="uptrend",
    )


def _result(*, readings=(), cash=None, cash_by_account=None, mismatched=()):
    book = SimpleNamespace(name="book", cash=cash, cash_by_account=cash_by_account or {})
    return SimpleNamespace(readings=list(readings), book=book, mismatched=mismatched)


# ── `of()` ────────────────────────────────────────────────────────────────────

def test_a_pot_with_cash_by_account_sums_every_account():
    result = _result(readings=[_reading("SPY", 100.0)],
                     cash_by_account={"roth": 50.0, "traditional": 25.0})
    net = networth.of([result], parked=0.0)
    assert net.total == 175.0
    assert net.pots == 1


def test_a_price_far_from_the_files_own_mark_is_left_out_and_named():
    """2026-10-15: SPX6900 tokens priced as the S&P 500 put net worth at $41.8M."""
    spx = (("SPX", 7718.94, 0.35),)
    one = _result(readings=[_reading("SPX", 7718.94, shares=5400.0), _reading("ETH", 2000.0)],
                  mismatched=spx)
    two = _result(readings=[_reading("SPX", 7718.94, shares=10.0)], mismatched=spx)
    net = networth.of([one, two], parked=0.0)
    assert net.total == 2000.0
    assert net.mismatched == ("SPX",)
    assert net.unpriced == 2


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


# ── the ledger row ────────────────────────────────────────────────────────────

TODAY = date(2026, 10, 20)


def _row(on, total, *, at=None, unpriced=0, mismatched=(), stale=False) -> networth.Row:
    return networth.Row(
        date=on, recorded_at=at or datetime(on.year, on.month, on.day, 11, tzinfo=UTC),
        total=total, parked=0.0, by_book={"a": total}, unpriced=unpriced,
        mismatched=tuple(mismatched), prices_fetched_at=None, stale=stale)


def test_by_book_and_parked_add_up_to_the_total():
    results = [_result(readings=[_reading("SPY", 100.0)], cash=5.0),
               _result(readings=[_reading("BTC", 50.0)])]
    results[1].book.name = "other"
    net = networth.of(results, parked=20.0)
    assert sum(net.by_book.values()) + net.parked == net.total == 175.0


def test_a_row_survives_its_own_json():
    r = _row(TODAY, 100.0, mismatched=("SPX",))
    assert networth.parse(r.to_json()) == r


@pytest.mark.parametrize("missing", ["date", "total", "by_book", "stale", "prices_fetched_at"])
def test_a_row_missing_a_field_is_refused_not_defaulted(missing):
    raw = _row(TODAY, 100.0).to_json()
    del raw[missing]
    with pytest.raises(networth.RowError, match=missing):
        networth.parse(raw)


def test_a_same_day_rerun_wins_by_recorded_at_not_file_position():
    early = _row(TODAY, 100.0, at=datetime(2026, 10, 20, 11, tzinfo=UTC))
    late = _row(TODAY, 120.0, at=datetime(2026, 10, 20, 21, tzinfo=UTC))
    assert networth.latest_per_date([late, early]) == [late]


@pytest.mark.parametrize(("days", "on", "found"), [
    (1, date(2026, 10, 19), True),
    (1, date(2026, 10, 18), False),     # a missed night is not "1d"
    (7, date(2026, 10, 12), True),      # one day of slack
    (7, date(2026, 10, 11), False),
    (30, date(2026, 9, 17), True),
    (365, date(2025, 10, 6), True),
])
def test_a_baseline_must_fall_inside_its_horizons_window(days, on, found):
    assert (networth.baseline([_row(on, 100.0)], today=TODAY, days=days) is not None) == found


def test_tonights_own_row_is_never_its_baseline():
    assert networth.baseline([_row(TODAY, 100.0)], today=TODAY, days=1) is None


@pytest.mark.parametrize("bad", [{"stale": True}, {"mismatched": ("SPX",)}])
def test_an_untrusted_night_is_skipped_as_a_baseline(bad):
    rows = [_row(date(2026, 10, 19), 100.0, **bad)]
    assert networth.baseline(rows, today=TODAY, days=1) is None


def test_horizons_answer_only_what_the_ledger_can():
    rows = [_row(date(2026, 10, 19), 100.0), _row(date(2026, 10, 13), 80.0, unpriced=1)]
    changes = networth.horizons(110.0, 0, rows, today=TODAY)
    assert set(changes) == {1, 7}
    assert changes[1].pct == pytest.approx(0.10) and not changes[1].uneven
    assert changes[7].amount == 30.0 and changes[7].uneven


def test_an_untrusted_tonight_marks_every_change_uneven():
    rows = [_row(date(2026, 10, 19), 100.0)]
    assert networth.horizons(110.0, 0, rows, today=TODAY, trusted=False)[1].uneven


def test_a_naive_recorded_at_is_refused():
    raw = _row(TODAY, 100.0).to_json()
    raw["recorded_at"] = "2026-10-20T11:00:00"
    with pytest.raises(networth.RowError, match="timezone"):
        networth.parse(raw)
