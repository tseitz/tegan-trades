"""The venue's tick/lot rules, which are the difference between a resting order and a
rejection with no useful message.

The expected values in ``test_matches_live_book_precision`` are not invented — they were
read off live L2 books on 2026-07-27 (BTC 64772.0, ETH 1941.9, SOL 75.509) and the rule was
reverse-engineered to reproduce them. If this test ever fails, re-read a book before
"fixing" it.
"""
from __future__ import annotations

import pytest
from hyperliquid.utils.signing import float_to_wire

from execution.rounding import round_price, round_size


# ── size ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, sz_decimals, expected", [
    (0.6666666, 4, 0.6666),      # ETH: 4dp
    (0.123456, 5, 0.12345),      # BTC: 5dp
    (12.3456, 2, 12.34),         # SOL: 2dp
    (5.0, 0, 5.0),               # whole-lot markets
    (5.9, 0, 5.0),
])
def test_round_size_truncates_toward_zero(raw, sz_decimals, expected):
    """Sizes round DOWN, never up.

    Rounding up would spend more than the risk budget authorised — small per trade, but it
    is the one direction the error must never go, so it is a floor rather than a nearest.
    """
    assert round_size(raw, sz_decimals) == pytest.approx(expected)


def test_round_size_can_reach_zero():
    """A size below one lot rounds to zero rather than to the minimum lot.

    Zero is a refusal signal the guards act on. Silently promoting it to one lot would place
    an order many times larger than the risk budget asked for.
    """
    assert round_size(0.4, 0) == 0.0


# ── price ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, sz_decimals, expected", [
    # (asset, szDecimals) -> the precision live books actually show
    (64772.04, 5, 64772.0),      # BTC: 5 sig figs and 6-5=1dp both give 0-1dp
    (1941.8532, 4, 1941.9),      # ETH: 5 sig figs binds (1dp) before 6-4=2dp
    (75.50942, 2, 75.509),       # SOL: 5 sig figs binds (3dp) before 6-2=4dp
])
def test_matches_live_book_precision(raw, sz_decimals, expected):
    assert round_price(raw, sz_decimals) == pytest.approx(expected)


def test_significant_figures_bind_before_decimal_places():
    """The two limits are independent and the tighter one wins.

    SOL allows 4 decimal places by the szDecimals rule, but 75.50942 has 7 significant
    figures; 5-sig-figs cuts it to 3dp first. Applying only the szDecimals rule would send
    75.5094 and be rejected.
    """
    assert round_price(75.50942, 2) == pytest.approx(75.509)


def test_decimal_places_bind_before_significant_figures():
    """And the reverse: a small price has sig figs to spare but runs out of decimals.

    0.00123456 has plenty of room under 5 sig figs (0.0012345) but a szDecimals of 2 caps it
    at 4 decimal places.
    """
    assert round_price(0.00123456, 2) == pytest.approx(0.0012)


def test_integers_are_always_allowed():
    """Integer prices bypass the significant-figure limit entirely.

    123456 is 6 sig figs and would otherwise be illegal; the venue permits any integer. This
    matters for exactly the assets most likely to be traded — a six-figure BTC price.
    """
    assert round_price(123456.0, 5) == pytest.approx(123456.0)
    assert round_price(123456.4, 5) == pytest.approx(123456.0)


def test_large_non_integer_collapses_to_an_integer():
    """A price too large to carry decimals under 5 sig figs becomes a legal integer."""
    assert round_price(1234567.8, 5) == pytest.approx(1234568.0)


def test_rejects_non_positive_price():
    with pytest.raises(ValueError):
        round_price(0.0, 2)
    with pytest.raises(ValueError):
        round_price(-10.0, 2)


# ── the constraint that actually bites ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, sz_decimals", [
    (1941.85321, 4), (75.509428, 2), (64772.0432, 5), (0.000123456, 2), (3.14159265, 3),
])
def test_output_always_survives_the_sdk_wire_encoder(raw, sz_decimals):
    """Every rounded price must pass ``float_to_wire``, which RAISES above 8 decimals.

    This is the failure this module exists to prevent: ``Candidate.entry`` is a float derived
    from bar arithmetic and routinely carries full float precision, so handing it to the SDK
    unrounded throws before the order is ever sent.
    """
    float_to_wire(round_price(raw, sz_decimals))
    float_to_wire(round_size(raw, sz_decimals))


def test_unrounded_price_would_have_failed():
    """Proves the premise rather than asserting it in a comment."""
    with pytest.raises(ValueError, match="rounding"):
        float_to_wire(1941.853216549871)
