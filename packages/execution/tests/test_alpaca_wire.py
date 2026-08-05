"""The exact Alpaca payload, and the reply. Pure, so all of it is testable without a key.

The counterpart to ``test_wire``. Where Hyperliquid takes three separate order requests under
a grouping, Alpaca takes one order carrying two nested exit legs — but the *intent* is
identical, and the tests here mirror the perp ones case for case so a divergence in behaviour
between the two venues shows up as a missing test rather than a surprise in production.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from execution.alpaca_wire import (
    ACCEPTED_STATUSES,
    ORDER_CLASS,
    TIME_IN_FORCE,
    bracket_order,
    parse_order,
)
from execution.plan import OrderPlan

LONG = OrderPlan(
    asset="MSFT", coin="MSFT", direction="long", size=4.0,
    entry=480.25, stop=455.0, target=560.0,
    risk=101.0, notional=1_921.0, equity=10_000.0, candidate_key="cbb6fa5d4548",
)
SHORT = OrderPlan(
    asset="PLTR", coin="PLTR", direction="short", size=12.0,
    entry=170.0, stop=182.0, target=140.0,
    risk=144.0, notional=2_040.0, equity=10_000.0, candidate_key="69e95dfead96",
)


# ── the order ───────────────────────────────────────────────────────────────────────────────

def test_entry_is_a_gtc_limit_bracket():
    order = bracket_order(LONG)
    assert order["symbol"] == "MSFT"
    assert order["type"] == "limit"
    assert order["limit_price"] == "480.25"
    assert order["side"] == "buy"
    assert order["order_class"] == ORDER_CLASS
    assert order["time_in_force"] == TIME_IN_FORCE


def test_a_short_sells_to_enter():
    assert bracket_order(SHORT)["side"] == "sell"


def test_quantity_is_a_whole_number_of_shares():
    """Bracket legs cannot be attached to a fractional order (Alpaca 42210000), so a size
    carrying a fraction here would be rejected by the venue, not silently truncated."""
    assert bracket_order(LONG)["qty"] == "4"


def test_a_fractional_size_is_refused_rather_than_sent():
    """Fail loudly at the boundary. ``shares.round_shares`` runs upstream, so a fraction
    arriving here means the wrong grid was applied — and the venue's own error names a code,
    not a cause."""
    with pytest.raises(ValueError, match="whole shares"):
        bracket_order(replace(LONG, size=4.5))


# ── the two exits ───────────────────────────────────────────────────────────────────────────

def test_take_profit_is_a_limit_at_the_target():
    """Same asymmetry as the perp bracket: the take-profit exists to capture a price, so it
    is a limit and gets the target or better."""
    assert bracket_order(LONG)["take_profit"] == {"limit_price": "560.0"}


def test_stop_loss_carries_no_limit_price():
    """A stop that fails to fill is not a stop. Omitting ``limit_price`` makes Alpaca queue a
    plain stop — a market order once triggered — which is the same choice ``wire`` makes by
    setting ``isMarket`` on the perp stop leg. Adding a limit here would turn it into a
    stop-limit that can gap straight through.
    """
    stop_loss = bracket_order(LONG)["stop_loss"]
    assert stop_loss == {"stop_price": "455.0"}
    assert "limit_price" not in stop_loss


def test_both_exits_are_present_on_every_order():
    """Alpaca requires both legs on a bracket, and a bracket is the only thing this package
    sends — an entry resting without a stop is the outcome the whole class exists to avoid."""
    for plan in (LONG, SHORT):
        order = bracket_order(plan)
        assert "take_profit" in order and "stop_loss" in order


def test_extended_hours_is_never_requested():
    """Brackets are not accepted with ``extended_hours``; sending it rejects the whole order."""
    assert "extended_hours" not in bracket_order(LONG)


# ── idempotency ─────────────────────────────────────────────────────────────────────────────

def test_the_candidate_key_becomes_the_client_order_id():
    """Venue-side duplicate protection. ``store.placed_keys`` guards across sessions using
    this repo's own log; this guards even when that log is lost or a session is run twice
    concurrently, because Alpaca rejects a re-used client_order_id outright.
    """
    assert bracket_order(LONG)["client_order_id"] == "cbb6fa5d4548"


# ── the reply ───────────────────────────────────────────────────────────────────────────────

def _accepted(status="new", leg_statuses=("held", "held")):
    return {
        "id": "9f2a-parent", "client_order_id": "cbb6fa5d4548", "status": status,
        "legs": [{"id": f"9f2a-leg{i}", "status": s} for i, s in enumerate(leg_statuses)],
    }


def test_an_accepted_bracket_reports_ok_with_every_order_id():
    placement = parse_order(_accepted())
    assert placement.ok
    assert placement.order_ids == ("9f2a-parent", "9f2a-leg0", "9f2a-leg1")


@pytest.mark.parametrize("status", sorted(ACCEPTED_STATUSES))
def test_every_accepted_status_is_a_success(status):
    assert parse_order(_accepted(status=status)).ok


def test_a_rejected_parent_is_not_ok():
    assert not parse_order(_accepted(status="rejected")).ok


def test_a_rejected_leg_fails_the_whole_placement():
    """The worst outcome available is an entry that rested while its stop was rejected, so
    any leg erroring makes the whole placement not-ok — exactly as ``parse_placement`` does
    for a partially rejected perp bracket."""
    placement = parse_order(_accepted(leg_statuses=("held", "rejected")))
    assert not placement.ok
    assert "rejected" in (placement.error or "")


def test_ids_are_recorded_even_on_failure():
    """So a half-placed bracket can be found and cancelled."""
    placement = parse_order(_accepted(leg_statuses=("held", "rejected")))
    assert "9f2a-parent" in placement.order_ids


def test_an_error_body_is_reported_with_its_message():
    raw = {"code": 42210000, "message": "fractional orders must be simple orders"}
    placement = parse_order(raw)
    assert not placement.ok
    assert "fractional" in (placement.error or "")
    assert "42210000" in (placement.error or "")


@pytest.mark.parametrize("raw", [None, [], "ok", 3])
def test_an_unrecognised_reply_is_a_failure_not_a_crash(raw):
    """Fail closed. An unparseable reply must never read as a placed order."""
    placement = parse_order(raw)
    assert not placement.ok
    assert placement.error


def test_an_unknown_status_fails_closed():
    """A status Alpaca adds later is treated as failure rather than assumed benign — the same
    rule ``parse_placement`` applies to unrecognised perp statuses."""
    assert not parse_order(_accepted(status="something_new")).ok


def test_the_raw_reply_is_kept_verbatim():
    """``store`` writes this. A reply this code misread is exactly the case where the original
    text is the only way to find out."""
    raw = _accepted()
    assert parse_order(raw).raw == raw
