"""Pricing, sizing and vetting one candidate — and every reason to refuse it."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from execution import guards
from execution.liquidity import Liquidity
from execution.plan import SHARE_GRID, Market, OrderPlan, build


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


# ── the equity margin floor ─────────────────────────────────────────────────────────────────

MSFT_MARKET = Market(coin="MSFT", sz_decimals=0, grid=SHARE_GRID)
SHARE_MARKETS = {"MSFT": MSFT_MARKET}
MSFT_LISTING = StubListing(canonical="MSFT", venue="alpaca", symbol="MSFT")


def test_an_equity_short_is_refused_below_the_margin_floor():
    """The account-type cliff, caught in the plan rather than by the venue. Shorting needs a
    margin account and a margin account needs $2,000 — under it the account is long-only, and
    no amount of resizing changes that."""
    short = StubCandidate(asset="MSFT", direction="short",
                          entry=100.0, stop=110.0, target=80.0, key="k")
    refused = build(short, markets=SHARE_MARKETS, listing=MSFT_LISTING, equity=1_000.0,
                    risk_pct=0.01, enforce_liquidity=False)
    assert isinstance(refused, guards.Refusal)
    assert refused.code == guards.REFUSAL_NO_MARGIN


def test_the_same_short_is_allowed_once_the_account_clears_the_floor():
    short = StubCandidate(asset="MSFT", direction="short",
                          entry=100.0, stop=110.0, target=80.0, key="k")
    plan = build(short, markets=SHARE_MARKETS, listing=MSFT_LISTING, equity=5_000.0,
                 risk_pct=0.01, enforce_liquidity=False)
    assert isinstance(plan, OrderPlan)
    assert plan.size == 5.0     # $50 budget / $10 stop


def test_an_equity_long_is_unaffected_by_the_margin_floor():
    """A cash account buys perfectly well; only the borrow needs margin."""
    long = StubCandidate(asset="MSFT", direction="long",
                         entry=100.0, stop=95.0, target=140.0, key="k")
    plan = build(long, markets=SHARE_MARKETS, listing=MSFT_LISTING, equity=1_000.0,
                 risk_pct=0.01, enforce_liquidity=False)
    assert isinstance(plan, OrderPlan)
    assert plan.size == 2.0     # $10 budget / $5 stop


def test_a_perp_short_never_consults_the_margin_floor():
    """A perp shorts by taking the sell side of the same contract — no borrow, no margin
    account, so the question does not arise however small the balance."""
    short = StubCandidate(direction="short", entry=3_200.0, stop=3_350.0, target=2_800.0)
    plan = build(short, markets=MARKETS, listing=StubListing(), equity=500.0,
                 risk_pct=0.01, enforce_liquidity=False)
    assert isinstance(plan, OrderPlan)


def test_the_venues_own_answer_beats_the_equity_proxy():
    """The failure that cost two orders on 2026-07-29. The paper account holds $99,674 —
    fifty times the Reg T minimum — and cannot short, so the equity test says yes and the
    venue says no six hours later at the open."""
    short = StubCandidate(asset="MSFT", direction="short",
                          entry=100.0, stop=110.0, target=80.0, key="k")
    refused = build(short, markets=SHARE_MARKETS, listing=MSFT_LISTING, equity=99_674.0,
                    risk_pct=0.01, enforce_liquidity=False, can_short=False)
    assert isinstance(refused, guards.Refusal)
    assert refused.code == guards.REFUSAL_NO_MARGIN
    assert "cash account" in refused.detail


def test_the_venue_can_also_overrule_the_proxy_the_other_way():
    """A margin account under $2,000 is unusual but not impossible, and the proxy would refuse
    a trade the venue would take. Whichever way it points, the venue is the authority."""
    short = StubCandidate(asset="MSFT", direction="short",
                          entry=100.0, stop=110.0, target=80.0, key="k")
    plan = build(short, markets=SHARE_MARKETS, listing=MSFT_LISTING, equity=1_000.0,
                 risk_pct=0.01, enforce_liquidity=False, can_short=True)
    assert isinstance(plan, OrderPlan)


# ── concentration ───────────────────────────────────────────────────────────────────────────

def test_concentration_binds_where_leverage_does_not():
    """The two ceilings share an arithmetic and nothing else. The median approved candidate
    wants 17% of equity, which is nowhere near 3x — so ``max_notional_frac`` is inert on it
    and only a concentration ceiling limits how many such positions fit in the account."""
    plan = _build(max_notional_frac=3.0, max_position_frac=0.20)
    assert isinstance(plan, OrderPlan)
    assert plan.leverage <= 0.20 + 1e-9
    assert plan.cap_reason == "concentration"
    # ...and the risk actually taken falls with it, which is the honest consequence.
    assert plan.risk < 100.0


def test_concentration_is_inert_on_a_wide_zone():
    """A 20% ceiling must not touch a candidate whose stop is wide enough to size small, or
    it has stopped being a ceiling and become the sizing rule."""
    wide = StubCandidate(entry=3_200.0, stop=2_600.0, target=4_500.0)
    plan = _build(wide, max_position_frac=0.20)
    assert plan.cap_reason is None
    assert plan.capped_from is None


def test_the_tighter_of_the_two_ceilings_is_the_one_reported():
    """A 0.5x leverage ceiling is tighter than a 20% concentration one, and the preview has to
    name the one that actually bound or it sends the reader to the wrong setting."""
    plan = _build(max_notional_frac=0.5, max_position_frac=0.20)
    assert plan.cap_reason == "concentration"
    tighter = _build(max_notional_frac=0.1, max_position_frac=0.20)
    assert tighter.cap_reason == "leverage"


# ── the portfolio budget ────────────────────────────────────────────────────────────────────

def test_an_order_that_fits_is_untouched():
    """The gate must be invisible on an empty account, or it is resizing every trade."""
    plain = _build()
    with_room = _build(headroom=1_000_000.0)
    assert with_room.size == pytest.approx(plain.size)
    assert with_room.cap_reason is None


def test_an_order_is_shrunk_to_the_room_that_is_left():
    """$2,133 of notional wanted against $1,500 of room — 70% of the intended size, which
    clears the fill floor, so it is trimmed rather than refused."""
    plan = _build(headroom=1_500.0)
    assert isinstance(plan, OrderPlan)
    assert plan.notional <= 1_500.0
    assert plan.cap_reason == "budget"
    assert plan.capped_from is not None and plan.capped_from > plan.size


def test_a_nearly_full_account_refuses_rather_than_sending_a_token_position():
    """Below the fill floor the size is decided by where the candidate fell in tonight's queue
    rather than by anything about the trade."""
    refused = _build(headroom=20.0)
    assert isinstance(refused, guards.Refusal)
    assert refused.code == "no_headroom"


def test_the_budget_is_judged_after_the_market_has_had_its_say():
    """A participation-capped order must not then be refused for a budget shortfall it does
    not have. The fraction the floor tests is what the *budget* cost, not what thinness did."""
    from execution.participation import Depth
    thin = Depth(sessions=30, median_volume=50.0, median_trades=100.0,
                 median_dollar_volume=160_000.0)
    plan = _build(depth=thin, max_participation=0.01, headroom=1_000_000.0)
    assert isinstance(plan, OrderPlan)
    assert plan.cap_reason == "participation"


def test_capped_from_keeps_naming_what_the_risk_budget_asked_for():
    """When two ceilings bind in turn, the reader wants the distance from the intended trade —
    not from an intermediate number nothing ever proposed to send."""
    plan = _build(max_position_frac=0.20, headroom=1_500.0)
    assert isinstance(plan, OrderPlan)
    assert plan.cap_reason == "budget"
    # 1% of $10,000 over a $150 stop is 0.6666 ETH after the lot — the risk-derived size,
    # not the 0.625 the concentration ceiling had already cut it to.
    assert plan.capped_from == pytest.approx(0.6666, abs=1e-3)
