"""US equity tick and lot rules — the equity-side counterpart to ``test_rounding``.

The perp rules were reverse-engineered from live books. These are published instead: SEC Rule
612 sets the minimum *quoting* increment at $0.01 for stocks priced at or above $1.00 and
$0.0001 below it, and Alpaca rejects a limit price finer than the instrument allows.

The whole-share rule is not a tick rule at all — it is an Alpaca order-class constraint. A
fractional or notional order cannot carry bracket legs (error ``42210000``), and this package
only ever sends brackets, so every equity size must be a whole number of shares.
"""
from __future__ import annotations

import pytest

from execution.shares import round_share_price, round_shares


# ── size ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    (4.4, 4.0),
    (2.45, 2.0),
    (1.0, 1.0),
    (99.999, 99.0),
])
def test_shares_floor_to_whole(raw, expected):
    """DOWN, never to nearest — the same direction as ``round_size`` and for the same reason.

    Rounding 2.45 up to 3 spends 22% more than the risk budget authorised. Measured against
    the 38 approved decisions the cost of flooring is 0.2-0.8% of intended size, which is the
    cheap side of the trade.
    """
    assert round_shares(raw) == expected


def test_a_sub_one_share_size_is_zero_not_one():
    """Zero is a refusal the guards act on; promoting it to one share would place an order
    larger than the risk budget allows on exactly the most expensive instruments."""
    assert round_shares(0.9) == 0.0
    assert round_shares(0.0) == 0.0


def test_negative_size_is_zero():
    assert round_shares(-3.0) == 0.0


# ── price ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    (28.8512, 28.85),
    (500.006, 500.01),
    (1.0, 1.0),
    (29.024999618530273, 29.02),   # a real DOW entry, carried at float64 noise
    (52386.0, 52386.0),
])
def test_prices_at_or_above_a_dollar_round_to_the_penny(raw, expected):
    """A penny is always a legal increment. The 2024 amendment to Rule 612 introduced a
    half-penny tier for some highly-liquid names, but $0.01 is a multiple of $0.005, so
    rounding to the penny stays valid everywhere and needs no per-symbol tick lookup.
    """
    assert round_share_price(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    (0.98765, 0.9877),
    (0.00012345, 0.0001),
    (0.5, 0.5),
])
def test_prices_below_a_dollar_round_to_the_sub_penny(raw, expected):
    """Rule 612's second tier. A penny grid under $1.00 would move a price by up to 50%."""
    assert round_share_price(raw) == expected


def test_the_dollar_boundary_is_inclusive_of_the_penny_grid():
    """Exactly $1.00 is on the penny grid, not the sub-penny one — the rule reads 'priced at
    or above $1.00', and a boundary written the other way would quote 1.0000."""
    assert round_share_price(1.004) == 1.0
    assert round_share_price(0.9999) == 0.9999


def test_a_non_positive_price_raises():
    """Matches ``round_price``: a zero or negative price is a bug upstream, and substituting
    a 'reasonable' number would place a real order on it."""
    with pytest.raises(ValueError, match="positive"):
        round_share_price(0.0)
    with pytest.raises(ValueError, match="positive"):
        round_share_price(-5.0)


def test_rounding_a_price_never_moves_it_more_than_half_a_tick():
    """The property the two tiers exist to guarantee, checked across both."""
    for raw in (0.123456, 0.9999, 1.0, 1.005, 47.777, 500.006, 52386.4):
        tick = 0.01 if raw >= 1.0 else 0.0001
        assert abs(round_share_price(raw) - raw) <= tick / 2 + 1e-12


# ── the margin floor ────────────────────────────────────────────────────────────────────────

def test_a_short_below_the_margin_floor_is_refused():
    """Reg T: shorting needs a margin account, and a margin account needs $2,000 of equity.
    Below it Alpaca caps the account at 1x buying power and rejects the order — so this is a
    fact about the world the guards should state, not an error to discover from the venue.
    """
    from execution import guards
    refusal = guards.check_shortable("short", equity=1_000.0)
    assert refusal is not None
    assert refusal.code == guards.REFUSAL_NO_MARGIN
    assert "2,000" in refusal.detail


def test_a_long_below_the_margin_floor_is_fine():
    """A cash account buys perfectly well. Only the short leg needs margin."""
    from execution import guards
    assert guards.check_shortable("long", equity=1_000.0) is None


def test_a_short_at_or_above_the_floor_passes():
    from execution import guards
    assert guards.check_shortable("short", equity=2_000.0) is None
    assert guards.check_shortable("short", equity=50_000.0) is None
