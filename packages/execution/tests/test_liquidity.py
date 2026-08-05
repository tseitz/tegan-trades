"""The HIP-3 liquidity gate.

The fixtures are real markets measured on mainnet 2026-07-27, not invented numbers — the
whole argument for this gate is that the spread between a healthy builder market and a dead
one is enormous and visible, so the tests use the actual observations.
"""
from __future__ import annotations

import pytest
from execution import guards
from execution.liquidity import Liquidity, parse_book, parse_context

# ── measured profiles ───────────────────────────────────────────────────────────────────────

SP500 = Liquidity(coin="xyz:SP500", day_volume=457_161_100, open_interest=483_195_296,
                  bid_depth=1_456_600, ask_depth=1_620_621, spread=0.00001)
GOOGL = Liquidity(coin="xyz:GOOGL", day_volume=52_386_051, open_interest=123_273_744,
                  bid_depth=67_208, ask_depth=71_861, spread=0.00006)
RIVN = Liquidity(coin="xyz:RIVN", day_volume=1_205_821, open_interest=582_344,
                 bid_depth=92_941, ask_depth=33_008, spread=0.00018)
URNM = Liquidity(coin="xyz:URNM", day_volume=133_022, open_interest=959_545,
                 bid_depth=19_675, ask_depth=11_687, spread=0.00255)
DXY = Liquidity(coin="xyz:DXY")   # no volume, no OI, no book — spread stays None


# ── the healthy ones pass ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("market", [SP500, GOOGL])
def test_healthy_markets_pass(market):
    """A gate that blocks the good HIP-3 markets is useless — SP500 quotes tighter than
    core-book BTC."""
    assert guards.check_liquidity(market, notional=200.0) is None


# ── the dead one is a category of its own ───────────────────────────────────────────────────

def test_a_market_with_no_book_is_refused_distinctly():
    """``xyz:DXY`` is in cfg/venue_map.yaml and has nothing quoted on either side.

    Its own refusal code, not just 'illiquid': a stop here cannot fill at *any* price, which
    is different in kind from filling expensively.
    """
    refusal = guards.check_liquidity(DXY, notional=200.0)
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_NO_BOOK


def test_no_book_is_checked_before_the_volume_floor():
    """Order matters for the message: a book-less market should say so rather than report a
    volume number that is technically true but explains nothing."""
    refusal = guards.check_liquidity(DXY, notional=200.0)
    assert refusal is not None and "no two-sided book" in refusal.detail


# ── the floors ──────────────────────────────────────────────────────────────────────────────

def test_thin_volume_is_refused():
    """URNM has enough open interest to pass that floor but trades $133k a day."""
    refusal = guards.check_liquidity(URNM, notional=200.0)
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_ILLIQUID
    assert "24h" in refusal.detail


def test_thin_open_interest_is_refused():
    """RIVN clears the volume floor at $1.2M but has only $582k of open interest —
    a market people trade through rather than hold in."""
    refusal = guards.check_liquidity(RIVN, notional=200.0)
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_ILLIQUID
    assert "open interest" in refusal.detail


# ── the size-relative check ─────────────────────────────────────────────────────────────────

def test_depth_check_is_inert_at_a_small_account():
    """$200 against GOOGL's ~$67k thin side is 0.3% — under the 1% ceiling."""
    assert guards.check_liquidity(GOOGL, notional=200.0) is None


def test_depth_check_binds_as_the_account_grows():
    """The reason it exists: at 100x the size the same market is no longer big enough."""
    refusal = guards.check_liquidity(GOOGL, notional=20_000.0)
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_TOO_BIG


def test_depth_uses_the_thinner_side():
    """A stop can need either side, so the worse one governs."""
    lopsided = Liquidity(coin="X", day_volume=50_000_000, open_interest=50_000_000,
                         bid_depth=10_000_000, ask_depth=10_000, spread=0.0001)
    refusal = guards.check_liquidity(lopsided, notional=5_000.0)
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_TOO_BIG


# ── failing closed ──────────────────────────────────────────────────────────────────────────

def test_missing_liquidity_data_is_a_refusal_not_a_pass():
    """A failed fetch must not silently disable the gate — that would switch it off in
    exactly the venue conditions where it matters most."""
    refusal = guards.check_liquidity(None, notional=200.0)
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_NO_LIQUIDITY_DATA


# ── parsing ─────────────────────────────────────────────────────────────────────────────────

def test_parse_context_converts_open_interest_to_money():
    """Open interest arrives in contracts; unconverted, a $5 asset and a $500 one are not
    comparable against a dollar floor."""
    volume, open_interest = parse_context(
        {"dayNtlVlm": "52386051.0", "openInterest": "400.0", "markPx": "305.0"})
    assert volume == pytest.approx(52_386_051.0)
    assert open_interest == pytest.approx(122_000.0)


@pytest.mark.parametrize("ctx", [None, {}, {"dayNtlVlm": "oops"}])
def test_parse_context_survives_junk(ctx):
    assert parse_context(ctx) == (0.0, 0.0)


def test_parse_book_measures_depth_within_the_band():
    levels = [
        [{"px": "100.0", "sz": "10"}, {"px": "99.5", "sz": "10"}, {"px": "50.0", "sz": "999"}],
        [{"px": "100.1", "sz": "10"}, {"px": "150.0", "sz": "999"}],
    ]
    bid_depth, ask_depth, spread = parse_book(levels)
    assert bid_depth == pytest.approx(100 * 10 + 99.5 * 10)   # the 50.0 wall is out of band
    assert ask_depth == pytest.approx(100.1 * 10)             # the 150.0 wall is out of band
    assert spread == pytest.approx(0.1 / 100.05, rel=1e-3)


@pytest.mark.parametrize("levels", [
    None, [], [[], []],
    [[{"px": "100", "sz": "1"}], []],     # bids only
    [[], [{"px": "100", "sz": "1"}]],     # asks only
])
def test_parse_book_reports_no_spread_when_a_side_is_empty(levels):
    """A one-sided book has no mid, so any spread would be invented. None is the signal the
    guard turns into REFUSAL_NO_BOOK."""
    assert parse_book(levels) == (0.0, 0.0, None)
