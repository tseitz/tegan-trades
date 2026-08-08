"""What is holding the budget, and which of it is safe to cancel.

The load-bearing test in this file is ``test_an_exit_leg_is_never_offered_for_cancellation``.
Everything else here costs a wrong number on a screen; that one costs a live position losing
its stop.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from execution import book
from execution.book import (
    Position,
    RestingOrder,
    filled_at_by_symbol,
    parse_positions,
    parse_resting,
    stale,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

# The BE bracket as Alpaca returned it on 2026-07-29 — a resting entry with both exits held.
ENTRY = {
    "id": "a20305f3",
    "client_order_id": "196cc1ef1e5c",
    "symbol": "BE",
    "qty": "31",
    "limit_price": "140.3",
    "side": "buy",
    "position_intent": "buy_to_open",
    "status": "new",
    "submitted_at": "2026-07-29T08:00:28.624702Z",
    "legs": [{"id": "ace31801"}, {"id": "1aec22ed"}],
}

# The take-profit leg of a bracket whose entry has already filled. Same shape, same open
# status, opposite meaning.
EXIT_LEG = {
    "id": "1fa9b483",
    "client_order_id": "40ed1b39-d970-470b-ad43-edd1ab1b34db",
    "symbol": "INTL",
    "qty": "1639",
    "limit_price": "32.34",
    "side": "sell",
    "position_intent": "sell_to_close",
    "status": "new",
    "submitted_at": "2026-07-29T13:34:57.176948Z",
    "legs": None,
}


def test_a_resting_entry_is_listed_with_what_it_reserves():
    (order,) = parse_resting([ENTRY])
    assert order.order_id == "a20305f3"
    assert order.candidate_key == "196cc1ef1e5c"
    assert order.symbol == "BE"
    assert order.notional == pytest.approx(31 * 140.3)


def test_an_exit_leg_is_never_offered_for_cancellation():
    """The one that matters. A working take-profit or stop is an ordinary open order at the
    venue and looks exactly like an entry. Cancelling it frees no budget — it strips the
    protection off a live position. The venue labels the difference; this reads the label."""
    assert parse_resting([EXIT_LEG]) == ()
    assert parse_resting([ENTRY, EXIT_LEG]) == parse_resting([ENTRY])


def test_a_short_entry_is_not_mistaken_for_an_exit():
    """Side is the wrong signal and would be exactly backwards here: a short opens by selling.
    ``position_intent`` is the only field that answers the question asked."""
    short = {**ENTRY, "side": "sell", "position_intent": "sell_to_open", "symbol": "CRM"}
    (order,) = parse_resting([short])
    assert order.symbol == "CRM"


def test_an_order_with_no_intent_falls_back_to_having_legs():
    """A bracket parent has legs and a leg does not, which is the same distinction stated
    structurally — for the case where the venue omits the field."""
    parent = {k: v for k, v in ENTRY.items() if k != "position_intent"}
    leg = {k: v for k, v in EXIT_LEG.items() if k != "position_intent"}
    assert len(parse_resting([parent])) == 1
    assert parse_resting([leg]) == ()


@pytest.mark.parametrize("status", ["canceled", "expired", "rejected", "filled"])
def test_orders_the_venue_is_done_with_are_not_resting(status):
    assert parse_resting([{**ENTRY, "status": status}]) == ()


def test_an_unrecognised_status_is_still_listed():
    """Fail-closed, matching the duplicate guard: showing a dead order costs a line, hiding a
    live one costs the budget it is silently holding."""
    assert len(parse_resting([{**ENTRY, "status": "something_new"}])) == 1


def test_a_malformed_order_is_skipped_not_fatal():
    assert parse_resting([None, {}, {"id": "x"}, ENTRY]) == parse_resting([ENTRY])


def test_oldest_first():
    """The list exists to decide what to retire, and age is the reason."""
    older = {**ENTRY, "id": "older", "submitted_at": "2026-07-01T00:00:00Z"}
    orders = parse_resting([ENTRY, older])
    assert [o.order_id for o in orders] == ["older", "a20305f3"]


# ── age ─────────────────────────────────────────────────────────────────────────────────────

def test_age_is_measured_from_submission():
    (order,) = parse_resting([{**ENTRY, "submitted_at": "2026-07-15T12:00:00Z"}])
    assert order.age_days(NOW) == pytest.approx(14.0)


def test_an_unparseable_timestamp_costs_the_age_not_the_listing():
    (order,) = parse_resting([{**ENTRY, "submitted_at": "yesterday", "created_at": None}])
    assert order.age_days(NOW) is None


def test_stale_is_at_or_past_the_ceiling():
    fresh = parse_resting([{**ENTRY, "submitted_at": "2026-07-20T12:00:00Z"}])
    old = parse_resting([{**ENTRY, "id": "old", "submitted_at": "2026-07-15T12:00:00Z"}])
    assert stale(fresh + old, 14.0, NOW) == old


def test_an_unknown_age_is_never_stale():
    """Unmeasured is not old. Retiring an order because its timestamp failed to parse is the
    wrong direction of error — the same asymmetry ``check_depth`` is built on."""
    unknown = parse_resting([{**ENTRY, "submitted_at": None, "created_at": None}])
    assert stale(unknown, 0.0, NOW) == ()


# ── positions ───────────────────────────────────────────────────────────────────────────────

POSITION = {
    "symbol": "INTL", "qty": "1639", "side": "long",
    "market_value": "48219.38", "unrealized_pl": "-329.82",
}


def test_a_position_carries_its_value_and_pl():
    (position,) = parse_positions([POSITION])
    assert position == Position(
        symbol="INTL", side="long", qty=1639.0, market_value=48_219.38,
        unrealised_pl=-329.82, opened_at=None,
    )


def test_position_age_comes_from_the_order_that_opened_it():
    """``/v2/positions`` carries no timestamp, so the age has to be joined from the fill."""
    opened = filled_at_by_symbol([
        {"symbol": "INTL", "status": "filled", "position_intent": "buy_to_open",
         "filled_at": "2026-07-15T13:34:57Z"},
    ])
    (position,) = parse_positions([POSITION], opened)
    assert position.age_days(NOW) == pytest.approx(13.94, abs=0.01)


def test_a_closing_fill_does_not_date_a_position():
    """The exit of an earlier round trip would report a months-old position as opened today."""
    opened = filled_at_by_symbol([
        {"symbol": "INTL", "status": "filled", "position_intent": "sell_to_close",
         "filled_at": "2026-07-28T13:34:57Z"},
    ])
    assert opened == {}


def test_the_most_recent_opening_fill_wins():
    """A re-entered symbol is genuinely ambiguous. The newest fill reports the position as
    younger than it may be, which is the direction that retires nothing on a guess."""
    opened = filled_at_by_symbol([
        {"symbol": "INTL", "status": "filled", "filled_at": "2026-05-01T00:00:00Z"},
        {"symbol": "INTL", "status": "filled", "filled_at": "2026-07-15T00:00:00Z"},
    ])
    assert opened["INTL"] == datetime(2026, 7, 15, tzinfo=UTC)


def test_positions_are_largest_first():
    small = {**POSITION, "symbol": "BE", "market_value": "4349.30"}
    assert [p.symbol for p in parse_positions([small, POSITION])] == ["INTL", "BE"]


def test_a_short_position_sorts_by_size_not_sign():
    short = {**POSITION, "symbol": "CRM", "side": "short", "market_value": "-90000"}
    assert [p.symbol for p in parse_positions([POSITION, short])] == ["CRM", "INTL"]


def test_malformed_positions_are_skipped():
    assert parse_positions([None, {}, {"symbol": "X"}, POSITION]) == parse_positions([POSITION])


def test_a_resting_order_is_hashable_and_frozen():
    """Both types are values — the CLI selects from them by identity and must not be able to
    edit one on the way to cancelling it."""
    (order,) = parse_resting([ENTRY])
    assert isinstance(order, RestingOrder)
    with pytest.raises(AttributeError):
        order.qty = 1  # type: ignore[misc]


# ── Hyperliquid's status vocabulary is open-ended ────────────────────────────────────────────
#
# ``TERMINAL_STATUSES`` is Alpaca's list. Hyperliquid has a dozen ``*Canceled`` and ``*Rejected``
# variants and adds more (siblingFilledCanceled, liquidatedCanceled, minTradeNtlRejected...), so
# it is classified by suffix rather than enumerated — an unrecognised status must not read as
# working when the venue has plainly finished with it.

def test_hyperliquids_canceled_variants_are_terminal():
    for status in ("canceled", "marginCanceled", "liquidatedCanceled",
                   "siblingFilledCanceled", "reduceOnlyCanceled"):
        assert book.is_terminal_status(status), status


def test_hyperliquids_rejected_variants_are_terminal_and_never_traded():
    for status in ("rejected", "minTradeNtlRejected", "perpMarginRejected"):
        assert book.is_terminal_status(status), status
        assert book.is_failed_status(status), status


def test_alpacas_own_vocabulary_still_classifies():
    for status in ("canceled", "expired", "rejected", "done_for_day", "replaced"):
        assert book.is_terminal_status(status), status
    assert not book.is_terminal_status("filled")
    assert not book.is_terminal_status("new")


def test_an_unrecognised_status_is_still_treated_as_working():
    """The asymmetry ``TERMINAL_STATUSES`` was built on: reading a live order as dead releases
    the duplicate guard onto a live bracket, which is worse than one missed re-entry."""
    assert not book.is_terminal_status("someNewStatusHyperliquidAdded")


# ── a filled parent whose legs are unknown must read as LIVE ─────────────────────────────────

def test_a_filled_entry_with_no_leg_information_is_live():
    """Hyperliquid reports no per-leg status, so ``leg_statuses`` is empty there. An empty tuple
    made ``any(...)`` false and the position read as closed — which would release the duplicate
    guard onto an OPEN position and allow a second bracket on it. Unknown must fail closed."""
    state = book.OrderState(candidate_key="k", status="filled", filled_qty=3.81,
                            filled_avg_price=74.4, leg_statuses=())
    assert book.is_live(state) is True


def test_a_filled_entry_whose_legs_are_all_done_is_not_live():
    """Alpaca's path, unchanged: both exits finished means the position is flat and the
    candidate is free again."""
    state = book.OrderState(candidate_key="k", status="filled", filled_qty=1.0,
                            filled_avg_price=1.0, leg_statuses=("canceled", "filled"))
    assert book.is_live(state) is False


def test_a_filled_entry_with_one_working_leg_is_live():
    state = book.OrderState(candidate_key="k", status="filled", filled_qty=1.0,
                            filled_avg_price=1.0, leg_statuses=("new", "held"))
    assert book.is_live(state) is True
