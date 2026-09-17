from datetime import date

import pytest
from core.held_flat import (
    Basket,
    Flow,
    anchor_shares,
    build,
    flow_from,
    return_over,
    state_on,
    unclassified,
)
from core.transactions import InvestmentTransaction

D = date(2026, 1, 1)


def _txn(d, type_, subtype, *, amount=0.0, quantity=None, ticker=None, security_id=None):
    return InvestmentTransaction(
        id="t", account_id="a", security_id=security_id, ticker=ticker, date=d,
        quantity=quantity, price=None, amount=amount, fees=None, type=type_, subtype=subtype,
    )


# ── flow_from ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("txn", "expected"), [
    (_txn(D, "cash", "deposit", amount=-100.0), Flow(date=D, amount=100.0, kind="external")),
    (_txn(D, "cash", "withdrawal", amount=50.0), Flow(date=D, amount=-50.0, kind="external")),
    (_txn(D, "cash", "dividend", amount=-5.0), Flow(date=D, amount=5.0, kind="income")),
    (_txn(D, "cash", "interest", amount=-1.0), Flow(date=D, amount=1.0, kind="income")),
    (_txn(D, "buy", "buy", amount=200.0, quantity=2.0, ticker="AAA"), None),
    (_txn(D, "sell", "sell", amount=-200.0, quantity=-2.0, ticker="AAA"), None),
    (_txn(D, "fee", "miscellaneous fee", amount=10.0), None),
    (_txn(D, "transfer", "transfer", amount=-300.0), Flow(date=D, amount=300.0, kind="external")),
    (_txn(D, "transfer", "transfer", amount=300.0), Flow(date=D, amount=-300.0, kind="external")),
    (_txn(D, "transfer", "transfer", amount=-300.0, security_id="s1"), None),
])
def test_flow_from(txn, expected):
    assert flow_from(txn) == expected


def test_unclassified_picks_up_the_other_row_flow_from_declined():
    t = _txn(D, "transfer", "transfer", amount=-300.0, security_id="s1")
    assert unclassified((t,), anchor=D) == (t,)


def test_unclassified_ignores_rows_before_the_anchor():
    t = _txn(date(2025, 1, 1), "transfer", "transfer", amount=-300.0, security_id="s1")
    assert unclassified((t,), anchor=D) == ()


# ── anchor_shares ──────────────────────────────────────────────────────────

ANCHOR = date(2025, 1, 1)


def test_anchor_shares_held_throughout_with_no_trades_since_anchor():
    assert anchor_shares({"NVDA": 10.0}, (), anchor=ANCHOR) == {"NVDA": 10.0}


def test_anchor_shares_position_opened_and_closed_since_anchor_drops_out():
    since_anchor = (
        _txn(date(2025, 2, 1), "buy", "buy", quantity=5.0, ticker="TSLA", amount=500.0),
        _txn(date(2025, 3, 1), "sell", "sell", quantity=-5.0, ticker="TSLA", amount=-500.0),
    )
    assert anchor_shares({}, since_anchor, anchor=ANCHOR) == {}


def test_anchor_shares_bought_after_the_anchor_is_excluded_from_the_basket():
    bought_after = (_txn(date(2025, 2, 1), "buy", "buy", quantity=5.0, ticker="MSFT", amount=500.0),)
    assert anchor_shares({"MSFT": 5.0}, bought_after, anchor=ANCHOR) == {}


def test_anchor_shares_negative_reconstruction_is_none():
    overstated_buys = (_txn(date(2025, 2, 1), "buy", "buy", quantity=5.0, ticker="IBM", amount=500.0),)
    assert anchor_shares({"IBM": 2.0}, overstated_buys, anchor=ANCHOR) is None


def test_build_combines_reconstructed_shares_with_flows_since_the_anchor():
    transactions = (
        _txn(date(2025, 6, 1), "cash", "deposit", amount=-100.0),
        _txn(date(2025, 6, 2), "buy", "buy", quantity=1.0, ticker="AAA", amount=100.0),
    )
    basket = build({"AAA": 10.0}, transactions, anchor=ANCHOR)
    assert basket == Basket(
        anchor=ANCHOR, shares={"AAA": 9.0},
        flows=(Flow(date=date(2025, 6, 1), amount=100.0, kind="external"),),
    )


# ── return_over / state_on ──────────────────────────────────────────────────

START = date(2026, 1, 1)
MID = date(2026, 3, 1)
END = date(2026, 6, 1)


def _prices(mapping):
    return lambda ticker, day: mapping.get((ticker, day))


def _basket(flows=()):
    return Basket(anchor=START, shares={"AAA": 10.0}, flows=flows)


def test_return_over_flat_basket_is_zero():
    price_on = _prices({("AAA", START): 100.0, ("AAA", END): 100.0})
    assert return_over(_basket(), price_on, START, END) == pytest.approx(0.0)


def test_return_over_known_two_price_basket_matches_hand_computed_pct():
    price_on = _prices({("AAA", START): 100.0, ("AAA", END): 110.0})
    assert return_over(_basket(), price_on, START, END) == pytest.approx(0.10)


def test_deposit_on_the_window_start_leaves_the_return_unchanged():
    price_on = _prices({("AAA", START): 100.0, ("AAA", END): 110.0})
    deposit = Flow(date=START, amount=500.0, kind="external")
    without = return_over(_basket(), price_on, START, END)
    with_deposit = return_over(_basket((deposit,)), price_on, START, END)
    assert with_deposit == pytest.approx(without)


def test_withdrawal_leaves_the_return_unchanged_but_units_fall():
    price_on = _prices({("AAA", START): 100.0, ("AAA", END): 110.0})
    withdrawal = Flow(date=START, amount=-200.0, kind="external")
    basket = _basket((withdrawal,))
    without = return_over(_basket(), price_on, START, END)
    with_withdrawal = return_over(basket, price_on, START, END)
    assert with_withdrawal == pytest.approx(without)
    units, _ = state_on(basket, price_on, END)
    assert units < 1.0


def test_dividend_inside_the_window_strictly_beats_the_same_window_without_it():
    price_on = _prices({("AAA", START): 100.0, ("AAA", MID): 100.0, ("AAA", END): 110.0})
    dividend = Flow(date=MID, amount=50.0, kind="income")
    without = return_over(_basket(), price_on, START, END)
    with_dividend = return_over(_basket((dividend,)), price_on, START, END)
    assert with_dividend > without


def test_unpriced_member_on_either_end_of_the_window_is_none():
    price_on = _prices({("AAA", START): 100.0})  # no price at END
    assert return_over(_basket(), price_on, START, END) is None


def test_a_window_extending_before_the_anchor_is_none_not_a_backcast():
    """A price series can predate the basket's own anchor even though the basket cannot —
    without a guard, this would price *today's* holdings on a day before the account existed
    instead of reporting the window as too new."""
    price_on = _prices({("AAA", date(2025, 1, 1)): 100.0, ("AAA", END): 110.0})
    assert return_over(_basket(), price_on, date(2025, 1, 1), END) is None
