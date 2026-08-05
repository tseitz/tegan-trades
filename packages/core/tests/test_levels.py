from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from core.levels import (
    NEAREST,
    NO_LEVELS,
    NO_REFERENCE,
    NO_TARGET_SIDE,
    STATED,
    UNDIRECTED,
    TargetReading,
    read_target,
)


def _row(*, direction="long", key_levels=()):
    return SimpleNamespace(direction=direction, key_levels=list(key_levels))


# The real corpus example from the phase 3.3 plan: a HYPE long whose levels mix a trigger,
# a target and three downside levels with nothing to distinguish them.
HYPE = (40, 50, 30, 21, 19, 20)


# ── the resolving cases ─────────────────────────────────────────────────────

def test_a_single_level_beyond_entry_is_the_target():
    reading = read_target(_row(key_levels=[50]), 45)
    assert reading.target == 50
    assert reading.source == STATED
    assert not reading.abstained


def test_a_short_reads_the_level_below_entry():
    reading = read_target(_row(direction="short", key_levels=[30]), 45)
    assert reading.target == 30
    assert reading.source == STATED


def test_a_messy_level_bag_still_resolves_when_only_one_lies_beyond_entry():
    """The point of reading levels at all. Six unlabeled floats look hopeless, but at a
    reference price of 45 only one of them is on the profit side, so the target is not
    actually ambiguous — no model required."""
    reading = read_target(_row(key_levels=HYPE), 45)
    assert reading.target == 50
    assert reading.source == STATED


def test_duplicate_levels_are_not_multiple_options():
    reading = read_target(_row(key_levels=[50, 50.0]), 45)
    assert reading.target == 50


# ── the abstaining cases ────────────────────────────────────────────────────
#
# Abstention is the designed fallback, not a failure: the caller drops to direction +
# conviction and takes every number from price structure instead. That is what lets this
# module stay deterministic and free.

def test_several_levels_beyond_entry_resolve_to_the_nearest():
    """At 35, both 40 and 50 are above — a trigger and a target. The nearest is the smallest
    claim their own numbers support, and NEAREST marks it inferred rather than clean.

    Strict abstention here was measured on the live corpus first: it forfeited 390 of 1,621
    rows and held stated coverage at 15% where this reaches 39%."""
    reading = read_target(_row(key_levels=HYPE), 35)
    assert reading.target == 40
    assert reading.source == NEAREST
    assert not reading.abstained


def test_the_nearest_for_a_short_is_the_highest_level_below_entry():
    reading = read_target(_row(direction="short", key_levels=[30, 21, 19]), 45)
    assert reading.target == 30
    assert reading.source == NEAREST


def test_no_levels_at_all_abstains():
    reading = read_target(_row(key_levels=[]), 45)
    assert reading.abstained
    assert reading.source == NO_LEVELS


def test_levels_entirely_on_the_wrong_side_abstain():
    """A long whose only levels sit below entry has stated stops, not targets."""
    reading = read_target(_row(key_levels=[30, 21, 19]), 45)
    assert reading.abstained
    assert reading.source == NO_TARGET_SIDE


def test_a_neutral_call_has_no_profit_side():
    reading = read_target(_row(direction="neutral", key_levels=[50]), 45)
    assert reading.abstained
    assert reading.source == UNDIRECTED


def test_an_unknown_direction_abstains_rather_than_guessing():
    reading = read_target(_row(direction="sideways", key_levels=[50]), 45)
    assert reading.abstained
    assert reading.source == UNDIRECTED


def test_no_reference_price_abstains():
    """Without a price at publication there is no 'beyond entry' to measure against."""
    reading = read_target(_row(key_levels=[50]), None)
    assert reading.abstained
    assert reading.source == NO_REFERENCE


def test_a_level_exactly_at_the_reference_price_is_not_a_target():
    reading = read_target(_row(key_levels=[45]), 45)
    assert reading.abstained
    assert reading.source == NO_TARGET_SIDE


# ── shape ───────────────────────────────────────────────────────────────────

def test_reading_is_immutable():
    reading = TargetReading(target=1.0, source=STATED)
    with pytest.raises(FrozenInstanceError):
        reading.target = 2.0


def test_abstained_is_derived_from_the_target_not_stored_separately():
    """Two fields that can disagree is a bug waiting to happen."""
    assert read_target(_row(key_levels=[50]), 45).abstained is False
    assert read_target(_row(key_levels=[]), 45).abstained is True
