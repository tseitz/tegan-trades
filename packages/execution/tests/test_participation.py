"""How much of a thin market one order would be, and the size that keeps it reasonable.

The numbers in these tests are real. ``INTL`` (Main International ETF) and ``FXI`` are both
in ``cfg/venue_map.yaml`` and both reachable on Alpaca, and the 30-session medians below were
measured on 2026-07-28 — see ``participation`` for why the median and not a single session.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from execution import guards
from execution.participation import (
    REFUSAL_NO_DEPTH,
    Depth,
    check_depth,
    depth_from_bars,
    max_shares,
)
from execution.plan import SHARE_GRID, Market, build
from execution.session import describe

# 30-session medians, Alpaca SIP, to 2026-07-28.
INTL = Depth(sessions=30, median_volume=24_707.0, median_trades=175.0,
             median_dollar_volume=736_268.0)
FXI = Depth(sessions=30, median_volume=24_211_596.0, median_trades=44_222.0,
            median_dollar_volume=860_000_000.0)


# ── depth_from_bars ─────────────────────────────────────────────────────────────────────────

def test_medians_not_means():
    """INTL's own volume spans 7,966 to 222,872 shares within one month. A mean over that is
    dominated by the two spike sessions and would clear a ceiling the typical session fails."""
    bars = [{"v": 7_966, "n": 146, "vw": 30.31},
            {"v": 12_088, "n": 154, "vw": 29.96},
            {"v": 24_749, "n": 212, "vw": 30.57},
            {"v": 106_910, "n": 186, "vw": 30.02},
            {"v": 222_872, "n": 165, "vw": 29.83}]
    depth = depth_from_bars(bars)
    assert depth.median_volume == 24_749
    assert depth.median_trades == 165
    assert depth.sessions == 5
    # Dollar volume is the median session's own volume x its own VWAP, not a product of two
    # independently-taken medians, which would describe a session that never happened.
    assert depth.median_dollar_volume == pytest.approx(24_749 * 30.57)


def test_no_bars_is_none_not_zero():
    """Distinguishable from a measured zero — the caller treats them differently."""
    assert depth_from_bars([]) is None
    assert depth_from_bars(None) is None


def test_malformed_bars_are_skipped_not_fatal():
    bars = [{"v": 1_000, "n": 10, "vw": 5.0}, {"v": None, "n": "x"}, {}]
    depth = depth_from_bars(bars)
    assert depth.sessions == 1
    assert depth.median_volume == 1_000


# ── max_shares ──────────────────────────────────────────────────────────────────────────────

def test_ceiling_is_a_fraction_of_the_median_session():
    assert max_shares(INTL, 0.01) == pytest.approx(247.07)
    assert max_shares(FXI, 0.01) == pytest.approx(242_115.96)


def test_a_ceiling_that_binds_nothing_real():
    """The 1% default was chosen to be inert on every candidate the engine has produced except
    the pathological one. SBSW is the worst of the rest at 0.04% participation."""
    sbsw = Depth(sessions=30, median_volume=5_358_410.0, median_trades=26_862.0,
                 median_dollar_volume=45_000_000.0)
    assert max_shares(sbsw, 0.01) > 2_023  # its risk-derived size


# ── check_depth ─────────────────────────────────────────────────────────────────────────────

def test_a_measured_zero_is_a_refusal():
    """No size is the right size in a market with no sessions that traded."""
    dead = Depth(sessions=30, median_volume=0.0, median_trades=0.0, median_dollar_volume=0.0)
    refusal = check_depth(dead)
    assert refusal is not None
    assert refusal.code == REFUSAL_NO_DEPTH


def test_unmeasured_is_not_a_refusal():
    """``None`` means the fetch did not answer — a transient network failure, most likely.

    Refusing on it would repeat the mistake that forced ``liquidity_enforced`` off for this
    venue: an unmeasured market became a refusal, so the gate refused every equity. A missing
    measurement must degrade to "no cap applied", never to "no trade".
    """
    assert check_depth(None) is None


def test_a_live_market_passes():
    assert check_depth(INTL) is None
    assert check_depth(FXI) is None


# ── through build() ─────────────────────────────────────────────────────────────────────────
# The real INTL candidate, approved on paper 2026-07-28: 1,639 shares risking $999.79, which
# is 6.6% of a median session in a fund that trades 175 times a day.

@dataclass(frozen=True)
class StubCandidate:
    asset: str = "INTL"
    direction: str = "long"
    entry: float = 29.80
    stop: float = 29.1935
    target: float = 32.34
    key: str = "intl-long-abc123"


@dataclass(frozen=True)
class StubListing:
    canonical: str = "INTL"
    venue: str = "alpaca"
    symbol: str = "INTL"
    scale: float | None = None

    @property
    def is_proxy(self) -> bool:
        return self.scale is not None and self.scale != 1.0


SHARE_MARKETS = {"INTL": Market(coin="INTL", sz_decimals=0, grid=SHARE_GRID)}


def _build(**kw):
    kw.setdefault("equity", 100_000.0)
    kw.setdefault("risk_pct", 0.01)
    kw.setdefault("enforce_liquidity", False)
    return build(StubCandidate(), markets=SHARE_MARKETS, listing=StubListing(), **kw)


def test_uncapped_is_the_size_that_was_actually_sent():
    """Guards the baseline: without a ceiling configured, nothing changes."""
    plan = _build()
    assert plan.size == 1639
    assert plan.capped_from is None


def test_the_ceiling_shrinks_the_order_and_says_so():
    plan = _build(depth=INTL, max_participation=0.01)
    assert plan.size == 247                       # floor(24,707 x 1%)
    assert plan.capped_from == 1639               # what the risk budget alone asked for
    assert plan.depth is INTL


def test_a_capped_order_reports_the_risk_it_actually_takes():
    """The whole argument for capping over refusing: the shrunken risk is the signal. A prompt
    still claiming 1% after a 6.6x cut would be worse than no cap at all."""
    plan = _build(depth=INTL, max_participation=0.01)
    assert plan.risk == pytest.approx(247 * 0.61, abs=0.01)
    assert plan.risk / plan.equity == pytest.approx(0.0015, abs=0.0001)


def test_a_ceiling_that_does_not_bind_leaves_no_trace():
    """``capped_from`` is None, not equal to size — the render keys off it to decide whether
    there is anything to explain."""
    plan = _build(depth=FXI, max_participation=0.01)
    assert plan.size == 1639
    assert plan.capped_from is None


def test_unmeasured_depth_does_not_cap_and_does_not_refuse():
    plan = _build(depth=None, max_participation=0.01)
    assert plan.size == 1639
    assert plan.capped_from is None


def test_a_dead_market_is_refused_before_it_is_sized():
    dead = Depth(sessions=30, median_volume=0.0, median_trades=0.0, median_dollar_volume=0.0)
    refusal = _build(depth=dead, max_participation=0.01)
    assert refusal.code == REFUSAL_NO_DEPTH


def test_a_cap_that_shrinks_below_one_share_refuses_rather_than_rounding_to_nothing():
    """No size is the right size here, and ``check_size`` already knows how to say that."""
    thin = Depth(sessions=30, median_volume=50.0, median_trades=3.0, median_dollar_volume=1_500.0)
    refusal = _build(depth=thin, max_participation=0.01)
    assert refusal.code == guards.REFUSAL_DUST


# ── what the person at the prompt sees ──────────────────────────────────────────────────────

def test_the_preview_explains_a_cap_it_applied():
    """Without this the risk line reads 0.15% where 1% was configured, and that looks like a
    bug rather than the market's own answer."""
    plan = _build(depth=INTL, max_participation=0.01)
    text = describe(plan)
    assert "capped from 1639 to 247" in text
    assert "1.0% of a median session" in text
    assert "175 trades/day" in text
    assert "24,707 sh/day" in text


def test_the_preview_stays_quiet_when_nothing_was_capped():
    """A *warning* that prints on every order is a warning nobody reads.

    Narrowed from "a line that prints on every order" — see the test below, which requires the
    market line on every equity order. The distinction is whether the line carries a number
    that separates one market from another: `! capped` on a market that was not capped says
    nothing, whereas `175 trades/day` against `44,222 trades/day` is the whole answer.
    """
    assert "capped" not in describe(_build(depth=FXI, max_participation=0.01))
    assert "capped" not in describe(_build())


def test_the_preview_shows_the_market_even_when_no_cap_bound():
    """The thin market that does not trip the ceiling is the one worth seeing (§36, §49).

    FXI clears 1% by four orders of magnitude, so nothing caps and — before this — nothing
    printed. It then read identically to INTL, which trades 175 times a day. The whole point of
    the line is to distinguish markets the ceiling treats the same.
    """
    text = describe(_build(depth=FXI, max_participation=0.01))
    assert "capped" not in text
    assert "24,211,596 sh/day" in text
    assert "44,222 trades/day" in text


def test_the_market_line_names_this_order_s_share_of_a_session():
    """The continuum the ceiling is a threshold on, so the threshold can be read off orders
    rather than picked. 1,639 shares of a 24,707-share session is 6.63%."""
    assert "6.63% of one" in describe(_build(depth=INTL))


def test_perps_get_no_market_line():
    """``HyperliquidBroker.depth`` is None by construction — ``check_liquidity`` answers this
    live and better there, and a blank shares-per-day line would imply a measurement failure."""
    assert "sh/day" not in describe(_build())
