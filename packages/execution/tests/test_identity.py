"""The identity gate: refuse an order against an instrument that priced out as the wrong asset.

``core.identity.compare`` does the arithmetic; this only turns its verdict into a refusal or a
pass. The ratios below are the real collisions the module docstring cites (measured 2026-07-31),
not invented numbers — the whole argument for this gate is that a wrong instrument is wrong by
orders of magnitude, and these are the orders of magnitude observed on this repo's own data.
"""
from __future__ import annotations

from core.identity import DIFFERS, IN_RANGE, MATCH, NO_PRICE, Comparison
from execution import guards

# ── the passing case ────────────────────────────────────────────────────────────────────────

def test_a_matching_mark_is_not_refused():
    """Agreement is the only case that clears the gate."""
    comparison = Comparison(verdict=MATCH, ratio=1.02)
    assert guards.check_identity("BTC", comparison, symbol="BTC", venue="hyperliquid") is None


# ── the wrong-instrument case ───────────────────────────────────────────────────────────────

def test_a_mark_that_disagrees_refuses_before_the_order():
    """Yahoo's bare WTI prices at 3.51 — W&T Offshore, the E&P company — while crude trades at
    81.75. An order meant for crude sent against that ratio would be against the wrong market
    entirely, not a proxy and not a timing gap."""
    comparison = Comparison(verdict=DIFFERS, ratio=3.51 / 81.75)
    refusal = guards.check_identity("WTI", comparison, symbol="WTI", venue="yahoo")
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_IDENTITY_MISMATCH


def test_the_mismatch_detail_carries_the_ratio_so_a_proxy_and_a_collision_are_distinguishable():
    """10.03 is an undeclared proxy (SPY against the S&P); 4e-5 is a memecoin collision under
    SPX. The verdict alone can't tell a reader which one happened — the ratio can."""
    comparison = Comparison(verdict=DIFFERS, ratio=0.33 / 7403)
    refusal = guards.check_identity("SPX", comparison, symbol="SPX", venue="hyperliquid")
    assert refusal is not None
    assert "4.458e-05" in refusal.detail


def test_the_jpy_collision_also_refuses_as_a_mismatch():
    """Yahoo's bare JPY prices at 37.27 while the yen itself trades at 159.18 — a 4.3x gap,
    orders of magnitude apart from the noise two correct sources can disagree by."""
    comparison = Comparison(verdict=DIFFERS, ratio=37.27 / 159.18)
    refusal = guards.check_identity("JPY", comparison, symbol="JPY", venue="yahoo")
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_IDENTITY_MISMATCH


# ── the unconfirmed cases ───────────────────────────────────────────────────────────────────

def test_a_mark_inside_our_recent_range_is_refused_but_not_as_a_mismatch():
    """IN_RANGE means the venue's mark sits inside our own recent trading band — probably a
    timing gap, since a venue quoting a session we haven't cached yet is right and we're
    behind. That's a different fact from a wrong instrument, so it gets a different code."""
    comparison = Comparison(verdict=IN_RANGE, ratio=1.18)
    refusal = guards.check_identity("ETH", comparison, symbol="ETH", venue="hyperliquid")
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_IDENTITY_UNCONFIRMED
    assert refusal.code != guards.REFUSAL_IDENTITY_MISMATCH


def test_no_price_is_refused_as_unconfirmed_not_as_a_mismatch():
    """NO_PRICE is 'no comparison was possible', not disagreement — PURR cached at exactly
    100x Hyperliquid's mark would be this shape if either side came back as zero rather than
    scaled wrong. Conflating it with DIFFERS would blame the venue for silence."""
    comparison = Comparison(verdict=NO_PRICE, ratio=None)
    refusal = guards.check_identity("PURR", comparison, symbol="PURR", venue="hyperliquid")
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_IDENTITY_UNCONFIRMED


# ── failing closed ──────────────────────────────────────────────────────────────────────────

def test_a_missing_comparison_is_a_refusal_not_a_pass():
    """Mirrors check_liquidity's ``liquidity is None`` branch: the check runs on a price
    fetched live, so a failed fetch must not silently become a pass — that would turn the gate
    off in exactly the venue conditions where it matters most."""
    refusal = guards.check_identity("BTC", None, symbol="BTC", venue="hyperliquid")
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_IDENTITY_UNCONFIRMED


def test_the_missing_comparison_detail_names_the_symbol_and_venue():
    """The human reading the refusal needs to know what failed to fetch, not just that
    something did."""
    refusal = guards.check_identity("BTC", None, symbol="BTC", venue="hyperliquid")
    assert refusal is not None
    assert "BTC" in refusal.detail
    assert "hyperliquid" in refusal.detail


# ── the two codes stay distinct ─────────────────────────────────────────────────────────────

def test_mismatch_and_unconfirmed_are_different_codes():
    """'Refusals name themselves' — a wrong instrument and an unverifiable one are different
    facts about the world, per guards.py's own docstring rule, and must not collapse to one
    code an audit log can't tell apart."""
    assert guards.REFUSAL_IDENTITY_MISMATCH != guards.REFUSAL_IDENTITY_UNCONFIRMED
