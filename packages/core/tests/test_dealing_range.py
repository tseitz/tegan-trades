from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from core.dealing_range import (
    DISCOUNT,
    EQUILIBRIUM,
    PREMIUM,
    dealing_range,
)

START = date(2025, 1, 1)


def _bars(highs_lows, *, start: date = START):
    """Consecutive daily bars from (high, low) pairs.

    Copied from ``test_structure.py`` rather than imported — swing and dealing-range
    detection read only high and low, and a midpoint open/close keeps these fixtures usable
    where candle direction is irrelevant.
    """
    out = []
    for i, (high, low) in enumerate(highs_lows):
        mid = (high + low) / 2
        out.append(
            SimpleNamespace(
                date=start + timedelta(days=i),
                open=mid, high=high, low=low, close=mid,
            )
        )
    return tuple(out)


# Swing high 20 forms at index 3 (2025-01-04), confirmed at 2025-01-06.
# Swing low 3 forms at index 10 (2025-01-11), confirmed at 2025-01-13.
# The second run's highs are held flat at 9 — below the first run's tail — so it contributes
# no swing high of its own and never displaces the first run's as "most recent".
RANGE_BARS = _bars([
    (10, 1), (11, 1), (12, 1), (20, 1), (12, 1), (11, 1), (10, 1),
    (9, 8), (9, 7), (9, 5), (9, 3), (9, 5), (9, 7), (9, 8),
])

HIGH_CONFIRMED_AT = date(2025, 1, 6)
LOW_CONFIRMED_AT = date(2025, 1, 13)


# ── building the range ──────────────────────────────────────────────────────

def test_range_is_bounded_by_the_most_recent_swing_high_and_low():
    dr = dealing_range(RANGE_BARS)
    assert dr is not None
    assert dr.low == 3
    assert dr.high == 20


# ── equilibrium / position_at ───────────────────────────────────────────────

def test_equilibrium_is_the_midpoint():
    dr = dealing_range(RANGE_BARS)
    assert dr.equilibrium == 11.5


def test_position_at_spans_zero_to_one():
    dr = dealing_range(RANGE_BARS)
    assert dr.position_at(3) == 0.0
    assert dr.position_at(20) == 1.0
    assert dr.position_at(11.5) == 0.5


def test_position_at_is_none_outside_the_range_rather_than_clamped():
    """Price outside the range has no position *in* it, and answering 0.0 or 1.0 invents one.

    Measured: 5 of 52 live candidates had price outside their own range, headed by a TSLA long
    at 321.55 against a range of 368.60-432.86 — 0.73 widths BELOW the low, reported as a
    maximally deep discount. See ``scripts/probe_external_target.py``.
    """
    dr = dealing_range(RANGE_BARS)
    assert dr.position_at(2.99) is None
    assert dr.position_at(20.01) is None


# ── zone_at ──────────────────────────────────────────────────────────────────

def test_zone_at_is_discount_below_and_premium_above_equilibrium():
    dr = dealing_range(RANGE_BARS)
    assert dr.zone_at(3) == DISCOUNT
    assert dr.zone_at(20) == PREMIUM


def test_zone_at_is_equilibrium_exactly_at_the_midpoint():
    """Exactly at equilibrium is neither cheap nor expensive — folding it into one side
    would let a coin-flip location satisfy a rule meant to require an edge."""
    dr = dealing_range(RANGE_BARS)
    assert dr.zone_at(11.5) == EQUILIBRIUM


def test_zone_at_is_none_outside_the_range():
    """Once price has left the range there is no premium/discount reading to give. The range
    is stale and is meant to be redrawn — "as price breaks out of this range, it forms a new
    range" — so the honest answer is that this range cannot say."""
    dr = dealing_range(RANGE_BARS)
    assert dr.zone_at(2.99) is None
    assert dr.zone_at(20.01) is None


# ── permits ──────────────────────────────────────────────────────────────────

def test_permits_long_only_in_discount():
    dr = dealing_range(RANGE_BARS)
    assert dr.permits("long", 3) is True
    assert dr.permits("long", 20) is False


def test_permits_short_only_in_premium():
    dr = dealing_range(RANGE_BARS)
    assert dr.permits("short", 20) is True
    assert dr.permits("short", 3) is False


def test_permits_refuses_price_outside_the_range():
    """The TSLA case: price below the range low used to clamp to 0.0 and read as DISCOUNT, so
    a long was permitted on a fabricated reading. ``permits`` already fails closed on a None
    zone, so this needs no new refusal reason."""
    dr = dealing_range(RANGE_BARS)
    assert dr.permits("long", 2.99) is False
    assert dr.permits("short", 20.01) is False


def test_permits_fails_closed_on_neutral_and_unrecognised_directions():
    """An unrecognised direction must never be read as permission — this encodes the
    manifesto rule directly, so failing closed matters more here than elsewhere."""
    dr = dealing_range(RANGE_BARS)
    assert dr.permits("neutral", 3) is False
    assert dr.permits("sideways", 3) is False


# ── confirmed_at: the no-look-ahead invariant ───────────────────────────────

def test_confirmed_at_is_the_later_of_the_two_legs():
    """The range is not knowable until BOTH legs are. The low here confirms a week after the
    high (2025-01-13 vs 2025-01-06); taking the earlier date would let a caller use the range
    a week before it actually existed."""
    dr = dealing_range(RANGE_BARS)
    assert dr.high_swing.confirmed_at == HIGH_CONFIRMED_AT
    assert dr.low_swing.confirmed_at == LOW_CONFIRMED_AT
    assert dr.confirmed_at == LOW_CONFIRMED_AT


def test_as_of_before_the_second_leg_confirms_yields_none():
    """As of 2025-01-10 the swing high is knowable but the swing low is not — the range
    genuinely did not exist yet, so this must not fall back to a partial range."""
    assert dealing_range(RANGE_BARS, as_of=date(2025, 1, 10)) is None


def test_as_of_after_both_legs_confirm_yields_the_full_range():
    dr = dealing_range(RANGE_BARS, as_of=LOW_CONFIRMED_AT)
    assert dr is not None
    assert dr.low == 3
    assert dr.high == 20


# ── missing legs ─────────────────────────────────────────────────────────────

def test_missing_the_low_leg_yields_none():
    only_high = _bars([(10, 1), (11, 1), (12, 1), (20, 1), (12, 1), (11, 1), (10, 1)])
    assert dealing_range(only_high) is None


def test_missing_both_legs_yields_none():
    flat = _bars([(10, 5), (10, 5), (10, 5), (10, 5), (10, 5)])
    assert dealing_range(flat) is None


# ── degenerate range ─────────────────────────────────────────────────────────

def test_an_inverted_range_yields_none():
    """The most recent swing low (15) sits above the most recent swing high (10) here — an
    inverted range has no interior, and position_in_range already refuses that input. This
    mirrors that refusal rather than inventing a range."""
    inverted = _bars([
        (32, 30), (27, 25), (22, 20), (17, 15), (22, 20), (27, 25), (32, 30),
        (5, 1), (5.5, 1), (6, 1), (10, 1), (6, 1), (5.5, 1), (5, 1),
    ])
    assert dealing_range(inverted) is None


# ── immutability ─────────────────────────────────────────────────────────────

def test_dealing_range_is_frozen():
    dr = dealing_range(RANGE_BARS)
    with pytest.raises(FrozenInstanceError):
        dr.low = 0.0
