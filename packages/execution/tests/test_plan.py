"""Pricing, sizing and vetting one candidate — and every reason to refuse it."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from execution import guards
from execution.liquidity import Liquidity
from execution.plan import Market, OrderPlan, build


# ── stubs ───────────────────────────────────────────────────────────────────────────────────
# The candidate is read structurally (see ``plan``'s docstring), so this stands in for
# ``core.setups.Candidate`` without assembling an OrderBlock and a tuple of Views.

@dataclass(frozen=True)
class StubCandidate:
    asset: str = "ETH"
    direction: str = "long"
    entry: float = 3_200.0
    stop: float = 3_050.0
    target: float = 3_900.0
    key: str = "eth-long-abc123"


@dataclass(frozen=True)
class StubListing:
    """Mirrors ``oracle.venue_map.Listing`` — including ``is_proxy``."""
    canonical: str = "ETH"
    venue: str = "hyperliquid"
    symbol: str = "ETH"
    scale: float | None = None

    @property
    def is_proxy(self) -> bool:
        return self.scale is not None and self.scale != 1.0


ETH_MARKET = Market(coin="ETH", sz_decimals=4)
MARKETS = {
    "ETH": ETH_MARKET,
    "BTC": Market(coin="BTC", sz_decimals=5),
    "SOL": Market(coin="SOL", sz_decimals=2),
}


HEALTHY = Liquidity(coin="ETH", day_volume=50_000_000.0, open_interest=100_000_000.0,
                    bid_depth=500_000.0, ask_depth=500_000.0, spread=0.0001)


def _build(candidate=None, *, market=None, listing=None, markets=None, **kw):
    kw.setdefault("equity", 10_000.0)
    kw.setdefault("risk_pct", 0.01)
    kw.setdefault("liquidity", HEALTHY)
    if markets is None:
        # ``market`` is a convenience for overriding just ETH's lot size.
        markets = {**MARKETS, "ETH": market} if market is not None else MARKETS
    return build(
        candidate or StubCandidate(),
        markets=markets,
        listing=StubListing() if listing is None else listing,
        **kw,
    )


# ── the happy path ──────────────────────────────────────────────────────────────────────────

def test_builds_a_bracket():
    plan = _build()
    assert isinstance(plan, OrderPlan)
    assert plan.coin == "ETH"
    assert plan.is_buy is True
    assert plan.entry == pytest.approx(3_200.0)
    assert plan.stop == pytest.approx(3_050.0)
    assert plan.target == pytest.approx(3_900.0)


def test_size_matches_the_risk_budget():
    """1% of $10,000 over a $150 stop is 0.6666 ETH once floored to ETH's 4dp lot."""
    plan = _build()
    assert plan.size == pytest.approx(0.6666)
    assert plan.risk == pytest.approx(0.6666 * 150)
    assert plan.risk < 100  # floored, so always at or under budget


def test_a_short_sells():
    plan = _build(StubCandidate(direction="short", entry=3_200, stop=3_350, target=2_800))
    assert isinstance(plan, OrderPlan)
    assert plan.is_buy is False


def test_reports_leverage():
    plan = _build()
    assert plan.leverage == pytest.approx(plan.notional / 10_000.0)


# ── refusals ────────────────────────────────────────────────────────────────────────────────

def test_refuses_an_asset_with_no_listing():
    """``venue_map`` returns None for an asset the venue does not carry at all."""
    refusal = build(StubCandidate(), markets=MARKETS, listing=None,
                    equity=10_000.0, risk_pct=0.01, liquidity=HEALTHY)
    assert isinstance(refusal, guards.Refusal)
    assert refusal.code == guards.REFUSAL_UNLISTED


def test_refuses_a_symbol_absent_from_this_networks_universe():
    """The testnet-vs-mainnet trap: the venue map is right, the network is wrong."""
    refusal = _build(markets={k: v for k, v in MARKETS.items() if k != "ETH"})
    assert isinstance(refusal, guards.Refusal)
    assert refusal.code == guards.REFUSAL_UNLISTED
    assert "universe" in refusal.detail


def test_refuses_a_scaled_proxy():
    """No Hyperliquid listing is scaled today; this is the guard for the first one added."""
    refusal = _build(listing=StubListing(symbol="ETH", scale=10.03))
    assert isinstance(refusal, guards.Refusal)
    assert refusal.code == guards.REFUSAL_PROXY


@pytest.mark.parametrize("candidate", [
    StubCandidate(direction="long", entry=3_200, stop=3_300, target=3_900),   # stop above entry
    StubCandidate(direction="long", entry=3_200, stop=3_050, target=3_100),   # target below entry
    StubCandidate(direction="short", entry=3_200, stop=3_050, target=2_800),  # stop below entry
    StubCandidate(direction="sideways", entry=3_200, stop=3_050, target=3_900),
])
def test_refuses_incoherent_geometry(candidate):
    refusal = _build(candidate)
    assert isinstance(refusal, guards.Refusal)
    assert refusal.code == guards.REFUSAL_GEOMETRY


def test_refuses_when_rounding_collapses_the_zone():
    """Geometry is re-checked AFTER rounding, not just before.

    A zone narrower than one tick survives the raw check and then rounds to entry == stop,
    which would divide by zero in sizing. On a 0-decimal market 3200.4 and 3200.1 both round
    to 3200.
    """
    coarse = Market(coin="ETH", sz_decimals=6)  # 6-6 = 0 decimal places allowed
    refusal = _build(
        StubCandidate(entry=3_200.4, stop=3_200.1, target=3_900),
        market=coarse,
    )
    assert isinstance(refusal, guards.Refusal)
    assert refusal.code == guards.REFUSAL_GEOMETRY


def test_refuses_dust():
    """A tiny account on a coarse-lot market floors to zero contracts."""
    refusal = _build(market=Market(coin="ETH", sz_decimals=0), equity=100.0, risk_pct=0.01)
    assert isinstance(refusal, guards.Refusal)
    assert refusal.code == guards.REFUSAL_DUST


def test_refuses_below_the_venue_minimum_notional():
    """Distinct from dust: the size is non-zero, the order is just too small to accept.

    $40 at 1% risk is $0.40 over a $150 stop = 0.0026 ETH = $8.32 notional, under the venue's
    $10 floor. Reaching the venue with this would return a generic error; the guard says
    which of the two size problems it actually is.
    """
    refusal = _build(equity=40.0, risk_pct=0.01)
    assert isinstance(refusal, guards.Refusal)
    assert refusal.code == guards.REFUSAL_MIN_NOTIONAL


# ── the cap ─────────────────────────────────────────────────────────────────────────────────

def test_notional_cap_is_inert_on_realistic_zones():
    """Measured, not assumed: across all 77 recorded decisions the tightest stop is 0.51% of
    entry, implying at most 2.0x leverage at a 1% risk budget. A 3x cap must therefore change
    nothing on an ordinary candidate, or it is quietly resizing every trade."""
    uncapped = _build()
    capped = _build(max_notional_frac=3.0)
    assert capped.size == pytest.approx(uncapped.size)


def test_notional_cap_binds_on_a_pathologically_tight_stop():
    """Deep book on purpose, to isolate the leverage cap from the depth guard.

    At the default stub depth this candidate is refused as ``too_big_for_book`` instead —
    which is correct behaviour (a 3x position genuinely is too large for a $500k book) but
    would mean this test no longer exercises the cap it is named for.
    """
    deep = Liquidity(coin="ETH", day_volume=500_000_000.0, open_interest=500_000_000.0,
                     bid_depth=50_000_000.0, ask_depth=50_000_000.0, spread=0.0001)
    plan = _build(StubCandidate(entry=3_200, stop=3_199, target=3_900),
                  max_notional_frac=3.0, liquidity=deep)
    assert plan.leverage <= 3.0 + 1e-9
