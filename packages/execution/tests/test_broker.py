"""The broker's decidable parts — namespace parsing, market filtering, network refusal.

Placing an actual order needs a key and a funded account, so that lives behind
``@pytest.mark.integration`` in ``test_live.py``. Everything here runs offline.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from execution.broker import (
    MAINNET,
    NETWORKS,
    PORTFOLIO_MARGIN,
    SPOT_COLLATERAL_MODES,
    TESTNET,
    UNIFIED_ACCOUNT,
    Credentials,
    HyperliquidBroker,
    dex_of,
    margin_committed,
    perp_markets,
    spot_collateral,
)

# ── HIP-3 namespaces ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("coin, expected", [
    ("ETH", ""), ("BTC", ""),
    ("xyz:GOLD", "xyz"), ("xyz:NVDA", "xyz"),
    ("test:FOO", "test"),
])
def test_dex_of_splits_the_namespace(coin, expected):
    """The namespace selects both which meta to load and which margin pool backs the trade,
    so it is load-bearing rather than cosmetic."""
    assert dex_of(coin) == expected


# ── market filtering ────────────────────────────────────────────────────────────────────────

def test_perp_markets_keeps_core_and_hip3_and_drops_spot():
    """Spot is indexed from 10000, builder perps from 110000."""
    markets = perp_markets(
        coin_to_asset={"ETH": 1, "PURR/USDC": 10_000, "xyz:GOLD": 110_004},
        asset_to_sz_decimals={1: 4, 10_000: 2, 110_004: 2},
    )
    assert set(markets) == {"ETH", "xyz:GOLD"}
    assert markets["ETH"].sz_decimals == 4
    assert markets["xyz:GOLD"].coin == "xyz:GOLD"


def test_perp_markets_skips_coins_with_no_lot_size():
    """A coin the SDK can name but cannot size is unusable; including it would produce an
    order with no valid size rather than a clear refusal."""
    markets = perp_markets(coin_to_asset={"ETH": 1, "GHOST": 2}, asset_to_sz_decimals={1: 4})
    assert set(markets) == {"ETH"}


# ── network safety ──────────────────────────────────────────────────────────────────────────

def test_rejects_an_unknown_network_before_touching_the_wallet():
    """Validation runs first, so a typo cannot get as far as loading a key or opening a
    connection."""
    with pytest.raises(ValueError, match="unknown network"):
        HyperliquidBroker(Credentials("0xabc", "0xdef"), network="paper")


def test_the_two_networks_have_distinct_urls():
    """Guards against a copy-paste that would silently point testnet at real money."""
    assert NETWORKS[TESTNET] != NETWORKS[MAINNET]
    assert "testnet" in NETWORKS[TESTNET]
    assert "testnet" not in NETWORKS[MAINNET]


# ── unified-account collateral ──────────────────────────────────────────────────────────────
# Regression cover for a real misread on 2026-07-27: an account holding 999 USDC reported
# accountValue 0.0, because under a unified account the SPOT balance is the perps collateral
# and clearinghouseState no longer reflects it.

SPOT_999 = {"balances": [
    {"coin": "USDC", "total": "999.0", "hold": "0.0"},
    {"coin": "TZERO", "total": "0.0", "hold": "0.0"},
]}


def test_spot_collateral_reads_the_collateral_token():
    assert spot_collateral(SPOT_999) == pytest.approx(999.0)


@pytest.mark.parametrize("state", [None, {}, {"balances": []}, {"balances": [
    {"coin": "TZERO", "total": "5.0"}]}])
def test_spot_collateral_is_zero_when_the_token_is_absent(state):
    """Zero becomes a refusal downstream, never an order sized against nothing."""
    assert spot_collateral(state) == 0.0


def test_margin_committed_is_zero_with_no_positions():
    """The ordinary case — a fresh account must not have its balance reduced."""
    assert margin_committed({"crossMaintenanceMarginUsed": "0.0", "assetPositions": []}) == 0.0


def test_margin_committed_counts_cross_maintenance_and_isolated_margin():
    """Both, because they tie up the same shared pool under a unified account."""
    committed = margin_committed({
        "crossMaintenanceMarginUsed": "12.5",
        "assetPositions": [
            {"position": {"leverage": {"type": "isolated"}, "marginUsed": "40.0"}},
            {"position": {"leverage": {"type": "cross"}, "marginUsed": "99.0"}},
        ],
    })
    # 12.5 cross maintenance + 40.0 isolated. The cross position's marginUsed is NOT added
    # again — crossMaintenanceMarginUsed already accounts for it.
    assert committed == pytest.approx(52.5)


@pytest.mark.parametrize("mode", [UNIFIED_ACCOUNT, PORTFOLIO_MARGIN])
def test_spot_collateral_modes_cover_both_unified_variants(mode):
    assert mode in SPOT_COLLATERAL_MODES


def test_an_unrecognised_mode_is_not_treated_as_spot_collateralised():
    """Fails closed. An unknown mode reads the perps balance, so a wrong guess reports 0 and
    refuses to trade rather than sizing against a balance that may not be collateral."""
    assert "" not in SPOT_COLLATERAL_MODES
    assert "manual" not in SPOT_COLLATERAL_MODES


# ── Hyperliquid CAN be asked what became of a candidate ──────────────────────────────────────
#
# It was long held that it could not: no ``cloid``, so the venue knows an order only by an oid
# this repo would have to index itself. The repo already indexes it — ``store.record_placement`` has
# written ``order_ids`` on every placement — and ``query_order_by_oid`` answers on that oid.
# Verified live on testnet 2026-08-08: all seven logged HL oids resolved.

class FakeInfo:
    """The two Info calls the close pass needs, canned."""

    def __init__(self, orders=None, fills=None, funding=None):
        self.orders = orders or {}
        self._fills = fills if fills is not None else []
        self._funding = funding if funding is not None else []
        self.asked = []

    def query_order_by_oid(self, user, oid):
        self.asked.append(oid)
        return self.orders.get(int(oid))

    def user_fills_by_time(self, user, start_time, end_time=None, aggregate_by_time=False):
        self.start_time = start_time
        return self._fills

    def user_funding_history(self, user, startTime, endTime=None):
        self.funding_window = (startTime, endTime)
        return self._funding


def _hl(info):
    """A broker with no network in its constructor — only the two attributes these methods use."""
    b = object.__new__(HyperliquidBroker)
    b._info = info
    b.account_address = "0xabc"
    return b


# The real SOL bracket, as the venue reports it.
SOL_ORDER = {
    "status": "order",
    "order": {
        "status": "filled",
        "statusTimestamp": 1785253286303,
        "order": {
            "coin": "SOL", "side": "A", "limitPx": "74.4", "sz": "0.0", "oid": 57089713957,
            "origSz": "3.81", "orderType": "Limit", "reduceOnly": False,
            "children": [
                {"oid": 57089713958, "orderType": "Take Profit Limit", "triggerPx": "68.0",
                 "sz": "3.81", "origSz": "3.81", "reduceOnly": True},
                {"oid": 57089713959, "orderType": "Stop Market", "triggerPx": "77.02",
                 "sz": "3.81", "origSz": "3.81", "reduceOnly": True},
            ],
        },
    },
}


def test_a_candidate_is_asked_about_by_its_recorded_oid():
    info = FakeInfo(orders={57089713957: SOL_ORDER})
    states = _hl(info).states(["64bcf4f9ca01"],
                              order_ids={"64bcf4f9ca01": ["57089713957"]})
    state = states["64bcf4f9ca01"]
    assert state is not None
    assert state.status == "filled"
    assert state.filled_qty == 3.81
    assert info.asked == [57089713957]


def test_the_bracket_legs_are_discovered_from_the_entrys_children():
    """The entry query returns both exits with their oids and types, so a close on this venue can
    name the leg that took it rather than settling for ``unknown``. Ordered take-profit then stop
    to match ``store.record_placement``'s convention — by ``orderType``, never by position."""
    state = _hl(FakeInfo(orders={57089713957: SOL_ORDER})).states(
        ["k"], order_ids={"k": ["57089713957"]})["k"]
    assert state.leg_order_ids == ("57089713958", "57089713959")


def test_a_candidate_with_no_recorded_oid_cannot_be_asked():
    """``None`` is not a status — it means no answer is available, and every reader already
    treats it as "assume the order is still out there"."""
    states = _hl(FakeInfo()).states(["k"], order_ids={})
    assert states == {"k": None}


def test_no_oid_mapping_at_all_leaves_every_key_unanswered():
    """The old behaviour, and still the right fallback: a caller that cannot supply the index
    gets the conservative answer rather than a crash."""
    assert _hl(FakeInfo()).states(["a", "b"]) == {"a": None, "b": None}


def test_an_order_the_venue_does_not_know_is_unanswered():
    assert _hl(FakeInfo(orders={57089713957: {"status": "unknownOid"}})).states(
        ["k"], order_ids={"k": ["57089713957"]})["k"] is None


def test_fills_carry_the_venues_own_open_or_close_verdict():
    """``dir`` states it, so this venue never has to infer a close from the side."""
    info = FakeInfo(fills=[
        {"coin": "SOL", "px": "74.4", "sz": "3.81", "side": "A", "time": 1785253286303,
         "dir": "Open Short", "closedPnl": "0.0", "oid": 57089713957, "fee": "0.0343"},
        {"coin": "SOL", "px": "75.888", "sz": "3.41", "side": "B", "time": 1786300000000,
         "dir": "Close Short", "closedPnl": "-5.074", "oid": 57591394123, "fee": "0.1164"},
    ])
    fills = _hl(info).fills(since="2026-07-28")
    assert fills is not None
    assert [f.closing for f in fills] == [False, True]
    assert [f.side for f in fills] == ["sell", "buy"]
    assert fills[1].fee == 0.1164


def test_the_fill_window_is_sent_as_epoch_milliseconds():
    """The Protocol's ``since`` is a date string because Alpaca takes one; this venue takes ms."""
    info = FakeInfo(fills=[])
    _hl(info).fills(since="2026-07-28")
    assert info.start_time == 1785196800000  # 2026-07-28T00:00:00Z


def test_an_unreadable_fill_feed_is_none_not_empty():
    class Boom(FakeInfo):
        def user_fills_by_time(self, *a, **kw):
            raise TimeoutError("no answer")
    assert _hl(Boom()).fills(since="2026-07-28") is None


def test_funding_paid_over_a_hold_is_summed_for_one_coin():
    """209 events on the SOL short. Summed rather than sampled — the nightly snapshot in
    ``data/funding`` is an estimate of the rate, this is what was actually charged."""
    info = FakeInfo(funding=[
        {"delta": {"coin": "SOL", "usdc": "-0.01"}},
        {"delta": {"coin": "SOL", "usdc": "-0.02"}},
        {"delta": {"coin": "ONDO", "usdc": "-9.99"}},
    ])
    got = _hl(info).funding_paid("SOL", start=datetime(2026, 7, 28, tzinfo=UTC),
                                 end=datetime(2026, 8, 8, tzinfo=UTC))
    assert round(got, 4) == -0.03


def test_unreadable_funding_is_none_so_the_row_is_not_called_evidence():
    class Boom(FakeInfo):
        def user_funding_history(self, *a, **kw):
            raise TimeoutError("no answer")
    assert _hl(Boom()).funding_paid("SOL", start=datetime(2026, 7, 28, tzinfo=UTC),
                                    end=None) is None


def test_funding_is_summed_over_the_positions_own_window():
    """Not since some global start. Passing the earliest placement across all open candidates
    summed every funding event the coin ever had — on SOL that pulled in one event from before
    the position existed and moved the total by 0.019. A coin held twice would charge the first
    hold's funding to the second close."""
    info = FakeInfo(funding=[{"delta": {"coin": "SOL", "usdc": "-0.5"}}])
    opened = datetime(2026, 7, 28, 15, 41, 26, tzinfo=UTC)
    closed = datetime(2026, 8, 8, 14, 36, 12, tzinfo=UTC)
    _hl(info).funding_paid("SOL", start=opened, end=closed)
    assert info.funding_window == (1785253286000, 1786199772000)


def test_an_open_ended_window_runs_to_now():
    info = FakeInfo(funding=[])
    _hl(info).funding_paid("SOL", start=datetime(2026, 7, 28, tzinfo=UTC), end=None)
    assert info.funding_window[1] is None
