"""The broker's decidable parts — namespace parsing, market filtering, network refusal.

Placing an actual order needs a key and a funded account, so that lives behind
``@pytest.mark.integration`` in ``test_live.py``. Everything here runs offline.
"""
from __future__ import annotations

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
