"""Carry folded into cross_reference: what it costs to *hold* the setup it just produced.

The scorer is deliberately untouched — ``_score`` never sees these fields, so ranking risk is
zero by construction. That is what IMPROVEMENTS §21 asks for before any weighting decision.

Fixtures mirror ``test_setups.py`` rather than reinventing structure.
"""
from datetime import date
from types import SimpleNamespace

import pytest
from core.dealing_range import DealingRange
from core.funding import FundingOutlook
from core.setups import (
    CARRY_HOLD_DAYS,
    ZONE_LEVEL_REASONS,
    Context,
    NotASetup,
    Setup,
    Zone,
    collapse,
    cross_reference,
)
from core.structure import (
    BEARISH,
    BULLISH,
    DOWNTREND,
    SWING_HIGH,
    SWING_LOW,
    UPTREND,
    Break,
    OrderBlock,
    Swing,
)

AS_OF = date(2025, 1, 10)
PUBLISHED = "2025-01-05"


def _swing(price, kind, *, index=0, day=1):
    when = date(2025, 1, day)
    return Swing(date=when, price=price, kind=kind, confirmed_at=when, index=index)


def _block(kind=BULLISH, *, top=110.0, bottom=100.0, invalidation=90.0):
    if kind == BULLISH:
        broken, origin_kind = _swing(120.0, SWING_HIGH), SWING_LOW
    else:
        broken, origin_kind = _swing(80.0, SWING_LOW), SWING_HIGH
    origin = _swing(invalidation, origin_kind, index=3, day=4)
    confirmed_at = date(2025, 1, 6)
    bos = Break(kind=kind, level=broken.price, swing=broken, origin=origin,
                date=confirmed_at, index=5)
    return OrderBlock(kind=kind, top=top, bottom=bottom, date=date(2025, 1, 5), index=4,
                      confirmed_at=confirmed_at, bos=bos, invalidation=invalidation)


def _range(low=80.0, high=200.0):
    return DealingRange(
        low=low, high=high,
        low_swing=_swing(low, SWING_LOW), high_swing=_swing(high, SWING_HIGH),
        confirmed_at=date(2025, 1, 6),
    )


def _ctx(**overrides):
    base = {
        "as_of": AS_OF, "price": 105.0, "weekly_trend": UPTREND, "daily_trend": UPTREND,
        "dealing_range": _range(),
        "zones": (Zone(block=_block(), structural_target=140.0),),
        "atr": 5.0,
    }
    base.update(overrides)
    return Context(**base)


def _short_ctx(**overrides):
    base = {
        "as_of": AS_OF, "price": 175.0, "weekly_trend": DOWNTREND, "daily_trend": DOWNTREND,
        "dealing_range": _range(),
        "zones": (Zone(block=_block(BEARISH, top=180.0, bottom=170.0, invalidation=190.0),
                    structural_target=140.0),),
        "atr": 5.0,
    }
    base.update(overrides)
    return Context(**base)


def _row(**overrides):
    base = {"id": "t1", "asset": "NVDA", "person": "TraderMayne", "direction": "long",
                "timeframe": "swing", "published_at": PUBLISHED, "key_levels": []}
    base.update(overrides)
    return SimpleNamespace(**base)


def _outlook(median=0.1186, p90=0.3144, n=501):
    return FundingOutlook(venue="hyperliquid", median=median, p90=p90, n=n)


# ── skipped, never guessed, when funding is unknown ──────────────────────────

def test_no_funding_leaves_every_carry_field_none():
    got = cross_reference(_row(), _ctx(), published_close=100.0)
    assert isinstance(got, Setup)
    # Same precedent as `atr`: an absent input skips the check rather than letting a zero
    # stand in for "measured, and it was free".
    assert got.funding_annual is None
    assert got.carry is None
    assert got.carry_reward_risk is None
    assert got.carry_reward_risk_p90 is None


def test_zero_funding_is_distinguishable_from_unknown_funding():
    got = cross_reference(_row(), _ctx(funding=_outlook(median=0.0, p90=0.0)), published_close=100.0)
    assert got.funding_annual == 0.0
    assert got.carry == 0.0
    # Aster really does charge nothing on equities. That is a measurement, not a gap.
    assert got.carry_reward_risk == pytest.approx(got.reward_risk)


# ── the point: carry moves R:R, and its sign follows direction ───────────────

def test_a_long_pays_positive_funding_and_its_ratio_falls():
    got = cross_reference(_row(), _ctx(funding=_outlook()), published_close=100.0)
    assert isinstance(got, Setup)
    assert got.carry > 0
    assert got.carry_reward_risk < got.reward_risk


def test_a_short_collects_positive_funding_and_its_ratio_rises():
    got = cross_reference(_row(direction="short"), _short_ctx(funding=_outlook()),
                          published_close=185.0)
    assert isinstance(got, Setup)
    assert got.carry < 0            # the crowd is long; the short is paid to wait
    assert got.carry_reward_risk > got.reward_risk


def test_the_same_zone_scores_differently_long_versus_short():
    """The asymmetry the queue currently cannot see."""
    long_side = cross_reference(_row(), _ctx(funding=_outlook()), published_close=100.0)
    short_side = cross_reference(_row(direction="short"), _short_ctx(funding=_outlook()),
                                 published_close=185.0)
    assert long_side.carry > 0 > short_side.carry


def test_the_stress_case_is_carried_alongside_the_central_one():
    got = cross_reference(_row(), _ctx(funding=_outlook()), published_close=100.0)
    # p90 is where positions actually die, so it is reported rather than re-derived later.
    assert got.carry_reward_risk_p90 < got.carry_reward_risk


def test_carry_uses_the_costing_constant():
    outlook = _outlook(median=0.365, p90=0.365)
    got = cross_reference(_row(), _ctx(funding=outlook), published_close=100.0)
    assert got.carry == pytest.approx(0.365 * CARRY_HOLD_DAYS / 365.0)


def test_the_costing_constant_does_not_vary_by_timeframe():
    outlook = _outlook()
    swing = cross_reference(_row(timeframe="swing"), _ctx(funding=outlook), published_close=100.0)
    macro = cross_reference(_row(timeframe="macro"), _ctx(funding=outlook), published_close=100.0)
    # §2 killed per-timeframe horizons. Costing must not smuggle them back in.
    assert swing.carry == macro.carry


def test_carry_is_a_fraction_of_notional_not_a_price():
    got = cross_reference(_row(), _ctx(funding=_outlook()), published_close=100.0)
    # 11.86%/yr over 21 days is well under 1%. A carry expressed in price units would be
    # ~100x larger here and would silently swamp the ratio.
    assert 0 < got.carry < 0.02


# ── the one gate: a trade that loses money at its own target ─────────────────

def test_carry_exceeding_the_target_refuses_the_setup():
    got = cross_reference(_row(), _ctx(funding=_outlook(median=20.0, p90=25.0)),
                          published_close=100.0)
    assert isinstance(got, NotASetup)
    assert got.reason == "carry_dominates"


def test_the_carry_gate_is_classified_as_a_zone_level_refusal():
    # A thesis is cross-referenced once per timeframe, and carry is a property of the zone's
    # trade rather than of the thesis, so it must not double-count in the tally.
    assert "carry_dominates" in ZONE_LEVEL_REASONS


def test_the_gate_cannot_fire_when_funding_is_unknown():
    # An unmeasured asset must not be refused for a cost nobody measured.
    assert isinstance(cross_reference(_row(), _ctx(), published_close=100.0), Setup)


def test_ordinary_funding_does_not_trip_the_gate():
    assert isinstance(
        cross_reference(_row(), _ctx(funding=_outlook()), published_close=100.0), Setup
    )


# ── collapse carries it through, and the score does not move ─────────────────

def test_collapse_preserves_the_carry_fields_from_its_representative():
    setup = cross_reference(_row(), _ctx(funding=_outlook()), published_close=100.0)
    (candidate,) = collapse([setup])
    assert candidate.funding_annual == setup.funding_annual
    assert candidate.carry == setup.carry
    assert candidate.carry_reward_risk == setup.carry_reward_risk
    assert candidate.carry_reward_risk_p90 == setup.carry_reward_risk_p90


def test_the_score_is_untouched_by_carry():
    """The whole point of shipping display-only: ranking cannot move."""
    free = cross_reference(_row(), _ctx(funding=_outlook(median=0.0, p90=0.0)), published_close=100.0)
    costly = cross_reference(_row(), _ctx(funding=_outlook(median=0.30, p90=0.60)), published_close=100.0)
    (a,) = collapse([free])
    (b,) = collapse([costly])
    assert a.score == b.score
    # ...but the number a human reads differs, which is what makes it minable.
    assert a.carry_reward_risk != b.carry_reward_risk


def test_a_candidate_without_funding_still_collapses_and_scores():
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    (candidate,) = collapse([setup])
    assert candidate.carry_reward_risk is None
    assert candidate.score > 0
