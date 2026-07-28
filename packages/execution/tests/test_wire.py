"""The exact payload, and the reply. Pure, so all of it is testable without a key."""
from __future__ import annotations

import pytest
from hyperliquid.utils.signing import float_to_wire

from execution.plan import OrderPlan
from execution.wire import (
    TAKE_PROFIT_IS_MARKET,
    order_requests,
    parse_placement,
    stop_limit_price,
)

LONG = OrderPlan(
    asset="ETH", coin="ETH", direction="long", size=0.6666,
    entry=3_200.0, stop=3_050.0, target=3_900.0,
    risk=99.99, notional=2_133.12, equity=10_000.0, candidate_key="k",
)
SHORT = OrderPlan(
    asset="ETH", coin="ETH", direction="short", size=0.6666,
    entry=3_200.0, stop=3_350.0, target=2_800.0,
    risk=99.99, notional=2_133.12, equity=10_000.0, candidate_key="k",
)


# ── the stop's slippage side ────────────────────────────────────────────────────────────────

def test_long_stop_tolerates_filling_below_the_trigger():
    """A long exits by selling, so its limit must sit BELOW the trigger."""
    assert stop_limit_price(LONG, 4) < LONG.stop


def test_short_stop_tolerates_filling_above_the_trigger():
    """A short exits by buying, so its limit must sit ABOVE the trigger.

    This is the sign that, reversed, yields a stop which can never fill — a bracket that
    looks correct until the moment it is needed.
    """
    assert stop_limit_price(SHORT, 4) > SHORT.stop


# ── the three legs ──────────────────────────────────────────────────────────────────────────

def test_sends_exactly_three_legs():
    assert len(order_requests(LONG, 4)) == 3


def test_entry_is_a_resting_gtc_limit():
    entry = order_requests(LONG, 4)[0]
    assert entry["order_type"] == {"limit": {"tif": "Gtc"}}
    assert entry["limit_px"] == pytest.approx(3_200.0)
    assert entry["is_buy"] is True
    assert entry["reduce_only"] is False


def test_exits_take_the_opposite_side_and_reduce_only():
    """Without reduce_only a trigger firing on an unfilled entry opens a new position in the
    opposite direction instead of closing anything."""
    _, tp, sl = order_requests(LONG, 4)
    for leg in (tp, sl):
        assert leg["is_buy"] is False       # opposite of the long entry
        assert leg["reduce_only"] is True
        assert leg["sz"] == pytest.approx(LONG.size)


def test_take_profit_is_a_limit_trigger_and_stop_is_a_market_trigger():
    """The asymmetry is the design: capture a price vs guarantee an exit."""
    _, tp, sl = order_requests(LONG, 4)
    assert tp["order_type"]["trigger"] == {
        "triggerPx": 3_900.0, "isMarket": TAKE_PROFIT_IS_MARKET, "tpsl": "tp"
    }
    assert TAKE_PROFIT_IS_MARKET is False   # the documented default; see ``wire``
    assert sl["order_type"]["trigger"]["isMarket"] is True
    assert sl["order_type"]["trigger"]["tpsl"] == "sl"
    assert sl["order_type"]["trigger"]["triggerPx"] == pytest.approx(3_050.0)


def test_every_price_on_the_wire_survives_the_sdk_encoder():
    """Including the derived slippage price, which is the one nothing else rounds."""
    for leg in order_requests(LONG, 4) + order_requests(SHORT, 4):
        float_to_wire(leg["limit_px"])
        float_to_wire(leg["sz"])
        trigger = leg["order_type"].get("trigger")
        if trigger:
            float_to_wire(trigger["triggerPx"])


# ── the reply ───────────────────────────────────────────────────────────────────────────────

def _ok(statuses):
    return {"status": "ok", "response": {"type": "order", "data": {"statuses": statuses}}}


def test_parses_a_fully_resting_bracket():
    placement = parse_placement(_ok([
        {"resting": {"oid": 111}}, {"resting": {"oid": 222}}, {"resting": {"oid": 333}},
    ]))
    assert placement.ok is True
    assert placement.order_ids == (111, 222, 333)
    assert placement.error is None


def test_parses_the_real_testnet_bracket_reply():
    """Verbatim reply from the first live placement, 2026-07-27 (GOOGL on the xyz builder).

    Regression cover for a real defect: the trigger legs come back as **bare strings**, not
    objects, and treating them as unrecognised reported a perfectly good resting bracket as
    REJECTED. All three legs were confirmed on the book afterwards.
    """
    placement = parse_placement(_ok([
        {"resting": {"oid": 57081383964}}, "waitingForFill", "waitingForFill",
    ]))
    assert placement.ok is True
    assert placement.error is None
    assert placement.order_ids == (57081383964,)
    assert placement.statuses == ("resting", "waitingForFill", "waitingForFill")


def test_an_unknown_string_status_still_fails_closed():
    """The waiting states are whitelisted rather than 'any string is fine', so a status the
    venue adds later cannot silently pass as success."""
    placement = parse_placement(_ok([{"resting": {"oid": 1}}, "somethingNew"]))
    assert placement.ok is False
    assert placement.error is not None and "somethingNew" in placement.error


def test_parses_an_immediately_filled_entry():
    placement = parse_placement(_ok([
        {"filled": {"oid": 111, "totalSz": "0.6666", "avgPx": "3200.0"}},
        {"resting": {"oid": 222}}, {"resting": {"oid": 333}},
    ]))
    assert placement.ok is True
    assert placement.statuses[0] == "filled"


def test_a_partially_rejected_bracket_is_not_ok():
    """The case this parser exists for.

    The venue returns ``status: ok`` at the top level even when a leg was rejected. An entry
    resting with no stop behind it is the worst outcome available, so any leg erroring fails
    the whole placement rather than being averaged away.
    """
    placement = parse_placement(_ok([
        {"resting": {"oid": 111}},
        {"resting": {"oid": 222}},
        {"error": "Order price cannot be more than 95% away from the reference price"},
    ]))
    assert placement.ok is False
    assert placement.error is not None and "95%" in placement.error
    assert placement.order_ids == (111, 222)   # still recorded, so they can be cancelled


def test_top_level_failure_is_not_ok():
    placement = parse_placement({"status": "err", "response": "insufficient margin"})
    assert placement.ok is False
    assert placement.error is not None and "insufficient margin" in placement.error


@pytest.mark.parametrize("raw", [None, "boom", {}, {"status": "ok"}, _ok([])])
def test_unrecognised_replies_fail_closed(raw):
    """Never read an unparseable reply as success."""
    assert parse_placement(raw).ok is False
