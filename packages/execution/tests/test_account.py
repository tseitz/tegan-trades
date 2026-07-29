"""What one account read settles, and what it must refuse to guess.

Three facts were being inferred from ``equity`` alone, and each inference was wrong in a way
that surfaced only as a venue rejection hours after the order was reported placed. These tests
pin the parse against the shape Alpaca actually returned on 2026-07-29 — see the module
docstring in ``execution.account`` for the numbers.
"""
from __future__ import annotations

import pytest

from execution.account import Account, parse_account

# The paper account as it stood at 2026-07-29T13:40Z, trimmed to the fields that are read.
# Kept verbatim rather than idealised: the exact arithmetic below is the evidence that
# ``initial_margin`` already contains both open positions and resting orders.
PAPER = {
    "equity": "99674.47",
    "buying_power": "24971.52",
    "initial_margin": "74702.95",
    "multiplier": "1",
    "shorting_enabled": False,
    "cash": "51455.09",
    "long_market_value": "48219.38",
}


def test_reads_the_four_facts():
    account = parse_account(PAPER)
    assert account == Account(
        equity=99_674.47,
        buying_power=24_971.52,
        committed=74_702.95,
        multiplier=1.0,
        can_short=False,
    )


def test_committed_is_positions_plus_resting_orders():
    """The relationship that makes one field enough.

    ``initial_margin`` on 2026-07-29 was the single open INTL position ($48,219.38) plus the
    three resting bracket entries ($26,483.57) — so "what have I already spoken for" needs no
    enumeration of orders and positions, and cannot drift out of step with the venue's own
    view of it. ``equity - committed == buying_power`` holds exactly at multiplier 1.
    """
    account = parse_account(PAPER)
    assert account.equity - account.committed == pytest.approx(account.buying_power, abs=0.01)


def test_a_cash_account_reports_it_cannot_short():
    """The fact that cost two orders. $99,674 of equity clears Reg T's $2,000 margin minimum
    by 50x, and this account still cannot short — the equity proxy was never the question."""
    assert parse_account(PAPER).can_short is False


def test_a_margin_account_reports_it_can_short():
    account = parse_account({**PAPER, "shorting_enabled": True, "multiplier": "2"})
    assert account.can_short is True
    assert account.multiplier == 2.0


@pytest.mark.parametrize("payload", [None, [], "", {"message": "forbidden"}])
def test_an_unreadable_account_is_none_not_a_zeroed_one(payload):
    """None and a zeroed ``Account`` mean opposite things downstream: the first disables the
    budget gate, the second refuses every order for having no buying power. A failed read
    must never look like an empty account."""
    assert parse_account(payload) is None


def test_a_missing_field_is_unknown_rather_than_zero():
    """Absent buying power means "the venue did not say", which leaves the gate off. Reading
    it as $0 would refuse everything on the strength of a field Alpaca renamed."""
    account = parse_account({"equity": "1000"})
    assert account is not None
    assert account.equity == 1_000.0
    assert account.buying_power is None
    assert account.committed is None
    assert account.can_short is None


def test_a_malformed_number_is_unknown_rather_than_fatal():
    account = parse_account({"equity": "1000", "buying_power": "not a number"})
    assert account is not None
    assert account.buying_power is None


def test_negative_equity_floors_to_zero():
    """Matches the pre-existing ``account_equity`` behaviour; a debit balance is not a budget."""
    assert parse_account({"equity": "-500"}).equity == 0.0


def test_headroom_is_none_when_the_venue_did_not_say():
    assert parse_account({"equity": "1000"}).headroom() is None


def test_headroom_subtracts_what_this_session_already_committed():
    """The reason a local total is needed at all.

    Alpaca accepted eight brackets between 03:24 and 04:01 ET on 2026-07-29 and rejected
    three of them at the open — so buying power does NOT necessarily decrement while the
    market is shut, and re-reading it per candidate would have shown the same $24,971 eight
    times. Subtracting what this session has placed is what makes the eighth order see the
    first seven.
    """
    account = parse_account(PAPER)
    assert account.headroom() == pytest.approx(24_971.52)
    assert account.headroom(placed=10_000.0) == pytest.approx(14_971.52)


def test_headroom_never_goes_negative():
    """An over-committed account has no room, not negative room — the caller compares an
    order's notional against this and a negative would read as a very large deficit."""
    assert parse_account(PAPER).headroom(placed=99_999.0) == 0.0
