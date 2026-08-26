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
    parse_hl_positions,
    parse_hl_resting,
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


# ── Hyperliquid's book listing ───────────────────────────────────────────────────────────────
#
# The ONDO bracket exactly as testnet returned it on 2026-08-25. Two things differ from Alpaca
# and both are traps:
#
# 1. ``frontend_open_orders`` returns each exit leg TWICE — nested under the entry's
#    ``children`` and again as a top-level order of its own. Alpaca's ``nested=true`` hides
#    them; this venue does not. So the flat list of three below is one cancellable entry, not
#    three, and the filter has to work on the top-level rows.
# 2. There is no ``position_intent`` and no ``client_order_id``. The entry is identified by the
#    venue's own two flags (``reduceOnly`` and ``isTrigger``), and the candidate it belongs to
#    is joined from the order log's recorded oid — see ``store.order_ids_by_key``.

HL_ENTRY = {
    "coin": "ONDO", "side": "B", "limitPx": "0.31213", "sz": "427.0", "oid": 57480565786,
    "timestamp": 1785970217766, "triggerCondition": "N/A", "isTrigger": False,
    "triggerPx": "0.0", "isPositionTpsl": False, "reduceOnly": False,
    "orderType": "Limit", "origSz": "427.0", "tif": "Gtc", "cloid": None,
}

HL_TAKE_PROFIT = {
    "coin": "ONDO", "side": "A", "limitPx": "0.4271", "sz": "427.0", "oid": 57480565787,
    "timestamp": 1785970217766, "triggerCondition": "Price above 0.4271", "isTrigger": True,
    "triggerPx": "0.4271", "isPositionTpsl": False, "reduceOnly": True,
    "orderType": "Take Profit Limit", "origSz": "427.0", "tif": None, "cloid": None,
    "children": [],
}

HL_STOP = {
    "coin": "ONDO", "side": "A", "limitPx": "0.27466", "sz": "427.0", "oid": 57480565788,
    "timestamp": 1785970217766, "triggerCondition": "Price below 0.28912", "isTrigger": True,
    "triggerPx": "0.28912", "isPositionTpsl": False, "reduceOnly": True,
    "orderType": "Stop Market", "origSz": "427.0", "tif": None, "cloid": None,
    "children": [],
}

# The order the venue actually returned them in, with the entry carrying its own legs as well.
HL_BOOK = [HL_STOP, HL_TAKE_PROFIT, {**HL_ENTRY, "children": [HL_TAKE_PROFIT, HL_STOP]}]


def test_a_hyperliquid_entry_is_listed_with_what_it_reserves():
    (order,) = parse_hl_resting(HL_BOOK)
    assert order.order_id == "57480565786"
    assert order.symbol == "ONDO"
    assert order.side == "buy"
    assert order.qty == pytest.approx(427.0)
    assert order.notional == pytest.approx(427 * 0.31213)


def test_a_hyperliquid_exit_leg_is_never_offered_for_cancellation():
    """The load-bearing one, and sharper here than at Alpaca. This venue lists both exits as
    top-level orders, so a listing that filtered nothing would put a live stop on a numbered
    menu one keystroke away from ``all``."""
    assert [o.order_id for o in parse_hl_resting(HL_BOOK)] == ["57480565786"]


def test_a_hyperliquid_short_entry_is_not_mistaken_for_an_exit():
    """A sell that opens exposure and a sell that closes it are the same ``side`` here. Only
    ``reduceOnly`` tells them apart, which is why the side is never read for this."""
    short = {**HL_ENTRY, "side": "A", "oid": 1}
    (order,) = parse_hl_resting([short])
    assert order.side == "sell"


def test_a_hyperliquid_entry_joins_back_to_its_candidate():
    """No ``cloid`` goes out, so the candidate comes from the oid the order log recorded."""
    (order,) = parse_hl_resting(HL_BOOK, {"57480565786": "abc123"})
    assert order.candidate_key == "abc123"


def test_an_unrecorded_hyperliquid_entry_is_still_listed():
    """``data/`` is gitignored and unbacked, so the join can genuinely be missing. An order the
    log cannot name is still holding margin and must still be cancellable."""
    (order,) = parse_hl_resting(HL_BOOK, {})
    assert order.candidate_key == ""


def test_hyperliquid_age_is_measured_from_its_millisecond_stamp():
    (order,) = parse_hl_resting(HL_BOOK)
    assert order.submitted_at == datetime(2026, 8, 5, 22, 50, 17, 766_000, tzinfo=UTC)


def test_a_malformed_hyperliquid_order_is_skipped_not_fatal():
    assert parse_hl_resting([None, {}, {"coin": "X"}, HL_ENTRY]) == parse_hl_resting([HL_ENTRY])


def test_hyperliquid_orders_are_oldest_first():
    newer = {**HL_ENTRY, "oid": 99, "timestamp": 1785970217766 + 86_400_000}
    assert [o.order_id for o in parse_hl_resting([newer, HL_ENTRY])] == ["57480565786", "99"]


# ── Hyperliquid positions ────────────────────────────────────────────────────────────────────
#
# ``assetPositions`` states size as a signed ``szi`` rather than a ``side`` word, and reports
# ``positionValue`` unsigned. The sign is the only thing saying which way the trade is facing.

HL_SHORT = {
    "coin": "SOL", "szi": "-3.81", "entryPx": "74.4", "positionValue": "283.46",
    "unrealizedPnl": "-1.94", "returnOnEquity": "-0.19", "marginUsed": "28.35",
    "leverage": {"type": "cross", "value": 10}, "maxLeverage": 20,
}

HL_POSITION = {"type": "oneWay", "position": HL_SHORT}


def test_a_hyperliquid_short_is_read_from_the_sign_of_its_size():
    (position,) = parse_hl_positions([HL_POSITION])
    assert position.side == "short"
    assert position.qty == pytest.approx(3.81)
    assert position.market_value == pytest.approx(283.46)
    assert position.unrealised_pl == pytest.approx(-1.94)


def test_a_hyperliquid_long_is_read_the_same_way():
    long = {"position": {**HL_SHORT, "szi": "3.81"}}
    (position,) = parse_hl_positions([long])
    assert position.side == "long"
    assert position.qty == pytest.approx(3.81)


def test_a_flat_hyperliquid_row_is_not_a_position():
    """The venue keeps returning a coin briefly after it goes flat. Zero size is no position,
    and listing one would invite a cancel against nothing."""
    flat = {"position": {**HL_SHORT, "szi": "0.0"}}
    assert parse_hl_positions([flat]) == ()


def test_hyperliquid_position_age_comes_from_the_opening_fill():
    """Nothing in ``assetPositions`` carries a timestamp, so the age is joined from the fills —
    the same shape as Alpaca's, with a different source."""
    opened = {"SOL": datetime(2026, 7, 15, tzinfo=UTC)}
    (position,) = parse_hl_positions([HL_POSITION], opened)
    assert position.age_days(NOW) == pytest.approx(14.5, abs=0.01)


def test_malformed_hyperliquid_positions_are_skipped():
    assert parse_hl_positions([None, {}, {"position": {}}, HL_POSITION]) == parse_hl_positions(
        [HL_POSITION]
    )
