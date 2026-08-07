from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest
from core.dealing_range import DealingRange
from core.exits import EXTERNAL, EXTREME, INTERNAL, OPPOSING, RANGE_BOUND, ExitLevel
from core.funding import FundingOutlook
from core.levels import NEAREST, STATED
from core.setups import (
    ARRIVAL,
    DAILY,
    H12,
    MIN_REWARD_RISK,
    PROXIMITY_SPAN,
    RR_HALF,
    STOP_PAD_ATR,
    TIER_LARGE,
    TIER_MAJOR,
    TIER_NONCRYPTO,
    TIER_SMALL,
    TIER_UNRANKED,
    WEEKLY,
    ZONE_LEVEL_REASONS,
    ZONE_TIMEFRAMES,
    Candidate,
    Context,
    HalfLife,
    NotASetup,
    Setup,
    View,
    Zone,
    approach_to,
    build_context,
    collapse,
    cross_reference,
    freshness_signal,
    reward_risk_signal,
    tier_for,
)
from core.structure import (
    BEARISH,
    BULLISH,
    DOWNTREND,
    RANGING,
    SWING_HIGH,
    SWING_LOW,
    UPTREND,
    UPTREND_FAILED_BREAKOUT,
    Break,
    OrderBlock,
    Swing,
)

AS_OF = date(2025, 1, 10)
PUBLISHED = "2025-01-05"


def _swing(price, kind, *, index=0, day=1):
    when = date(2025, 1, day)
    return Swing(date=when, price=price, kind=kind, confirmed_at=when, index=index)


def _block(kind=BULLISH, *, top=110.0, bottom=100.0, invalidation=90.0, confirmed_day=6):
    """A hand-built order block. Constructing the Break by hand keeps these tests focused on
    the gating logic rather than on re-deriving structure from a 100-bar fixture."""
    if kind == BULLISH:
        broken, origin_kind = _swing(120.0, SWING_HIGH), SWING_LOW
    else:
        broken, origin_kind = _swing(80.0, SWING_LOW), SWING_HIGH
    origin = None if invalidation is None else _swing(invalidation, origin_kind, index=3, day=4)
    confirmed_at = date(2025, 1, confirmed_day)
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
    """A context in which a long is fully permitted: weekly and daily both up, price at 105
    which is discount in an 80-200 range, and one live bullish zone at 100-110."""
    base = {
        "as_of": AS_OF,
        "price": 105.0,
        "weekly_trend": UPTREND,
        "daily_trend": UPTREND,
        "dealing_range": _range(),
        "zones": (Zone(block=_block(), structural_target=140.0),),
        "atr": 5.0,   # so the plausibility ceiling sits at 20 x 5 = 100 beyond entry
    }
    base.update(overrides)
    return Context(**base)


def _row(**overrides):
    base = {
        "id": "t1", "asset": "BTC", "person": "TraderMayne", "direction": "long",
        "timeframe": "swing", "published_at": PUBLISHED, "key_levels": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _reason(outcome):
    assert isinstance(outcome, NotASetup), f"expected a rejection, got {outcome!r}"
    return outcome.reason


def _nearest(setup):
    """The first level price meets on the way out — ``core.exits``' ``levels[0]``.

    Since v8 that is no longer the target: an internal entry targets the range boundary and
    everything nearer is a partial, so the nearest level is the head of ``ladder``. Rebuilt
    from both fields rather than read off one, because which of them holds it depends on the
    entry's liquidity, and the tests below are about *which level wins the nearest contest* —
    a question that outlived the target moving away from it."""
    rungs = [*setup.ladder,
             ExitLevel(price=setup.target, kind=setup.target_source,
                       reward_risk=setup.reward_risk)]
    return min(rungs, key=lambda level: abs(level.price - setup.entry))


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_permitted_long_becomes_a_setup():
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    assert isinstance(setup, Setup)
    assert setup.direction == "long"
    assert setup.entry_top == 110.0 and setup.entry_bottom == 100.0


def test_the_stop_and_invalidation_answer_two_different_questions():
    """The stop is where this *trade* is wrong; invalidation is where the *zone* itself dies.
    Conflating them was measured and rejected — pricing risk down to the origin swing put 17 of
    18 live candidates under 1.0 RR, and it also implied a stopped-out trade had destroyed the
    zone, which it hasn't.

    Three levels, not two, since v7: the raw far edge stays on ``block``, the trade's stop sits
    ``STOP_PAD_ATR`` ATRs beyond it, and invalidation is further out still.
    """
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    assert setup.block.stop == 100.0    # the zone's far edge, structural
    assert setup.stop == 95.0           # one ATR beyond it, where the trade is wrong
    assert setup.invalidation == 90.0   # the origin swing low, where the zone dies


def test_entry_is_the_near_edge_of_the_zone():
    """The near edge is the shallowest fill in the zone, so pricing reward-to-risk off it is
    the conservative choice — a deeper fill only ever improves the real trade."""
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    assert setup.entry == 110.0            # bullish zone approached from above
    bear = cross_reference(
        _row(direction="short"),
        _ctx(weekly_trend=DOWNTREND, daily_trend=DOWNTREND, price=180.0,
             zones=(Zone(block=_block(BEARISH, top=190.0, bottom=180.0, invalidation=200.0),
                         structural_target=150.0),)),
        published_close=185.0,
    )
    assert bear.entry == 180.0             # bearish zone approached from below


def test_reward_risk_is_measured_from_entry_to_stop():
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    # entry 110, stop 95 (far edge 100 padded by one ATR), target 200 -> risk 15, reward 90
    assert setup.reward_risk == 6.0


def test_the_scored_reward_risk_is_measured_from_price_not_entry():
    """§19(d): with a structural target — the post-break extreme — ``|target - entry|`` is
    literally how far price ran away from the zone, so distance *inflates* the ratio that is
    supposed to rank the trade. Measuring the remaining move from where the market actually
    is removes that. Both numbers are kept; only the scored one changes."""
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    # entry 110, price 105, target 200, risk 15 -> 95/15, against reward_risk's 90/15
    assert setup.reward_risk_from_price == pytest.approx(95 / 15)
    assert setup.reward_risk == 6.0


def test_a_zone_price_has_run_far_from_keeps_its_headline_rr_and_loses_its_scored_one():
    """The SPX case that opened §19: a legitimate zone 26% below price, whose R:R of 9.06 was
    earned by being unreachable. The displayed number is still the trade's real reward:risk —
    you would make that if filled — but it no longer buys rank."""
    far = cross_reference(_row(), _ctx(price=138.0), published_close=100.0)
    assert far.reward_risk == 6.0                               # unchanged: |200 - 110| / 15
    assert far.reward_risk_from_price == pytest.approx(62 / 15)  # |200 - 138| / 15


def test_the_reward_risk_gate_still_judges_the_trade_not_the_journey():
    """``MIN_REWARD_RISK`` is a *rule* — "a trade risking more than it stands to make is not a
    setup" — and per the gates-vs-scores split a rule is gated while a measurement on a
    continuum is scored. Reachability is a continuum, so it must not reach this gate: a
    candidate whose remaining move is thin is demoted, never refused."""
    # A wide zone (110 down to 75) so the boundary at 200 still pays 2.25R from entry while
    # paying only 1.55R from a price that has already run to 138. Since v8 the target is the
    # boundary, so the gap between the two ratios has to come from the risk leg.
    wide = (Zone(block=_block(top=110.0, bottom=75.0, invalidation=65.0),
                 structural_target=140.0),)
    far = cross_reference(_row(), _ctx(price=138.0, zones=wide), published_close=100.0)
    assert isinstance(far, Setup)
    assert far.reward_risk >= MIN_REWARD_RISK
    assert far.reward_risk_from_price < MIN_REWARD_RISK


def test_the_scored_reward_risk_never_saturates():
    """It was ``min(rr / 3.0, 1.0)``, which made 3.0, 9.06, 14.19 and 23.24 contribute
    identically — pinned at 1.0 for 12 of 18 weekly rows (§4). Same hyperbola as agreement."""
    contributions = [reward_risk_signal(rr) for rr in (1.0, 3.0, 9.06, 14.19, 23.24)]
    assert contributions == sorted(contributions)
    assert len(set(contributions)) == 5
    assert reward_risk_signal(RR_HALF) == pytest.approx(0.5)
    assert all(c < 1.0 for c in contributions)


def test_depth_reflects_how_far_price_has_travelled_into_the_zone():
    """The half of the ramp the sidecar could never see: v2 recorded ``proximity``, which
    saturates at 1.0 for every price inside the zone, so two candidates differing only in
    depth were indistinguishable in the decision record."""
    shallow = cross_reference(_row(), _ctx(price=109.0), published_close=100.0)
    deep = cross_reference(_row(), _ctx(price=101.0), published_close=100.0)
    assert deep.approach > shallow.approach
    assert deep.score > shallow.score


# ── the weekly gate ─────────────────────────────────────────────────────────

def test_weekly_downtrend_refuses_a_long():
    outcome = cross_reference(
        _row(), _ctx(weekly_trend=DOWNTREND, daily_trend=DOWNTREND), published_close=100.0
    )
    assert _reason(outcome) == "weekly_disagrees"


def test_ranging_weekly_is_permitted_but_unaligned():
    """A ranging weekly is the *absence* of a macro opinion, not a macro opinion against —
    two opposite situations that used to share the ``weekly_disagrees`` label and the same
    fate. Measured on the live corpus: 630 rows died to ranging versus 1,617 to a genuine
    contradiction. Rule 8 discards what fights the macro; it says nothing about what the
    macro declines to answer, so that is scored down rather than thrown away."""
    setup = cross_reference(_row(), _ctx(weekly_trend=RANGING), published_close=100.0)
    assert isinstance(setup, Setup)
    assert setup.trend_alignment == 0.0


def test_an_aligned_weekly_scores_above_a_ranging_one():
    aligned = cross_reference(_row(), _ctx(), published_close=100.0)
    ranging = cross_reference(_row(), _ctx(weekly_trend=RANGING), published_close=100.0)
    assert aligned.trend_alignment == 1.0
    assert aligned.score > ranging.score


def test_a_failed_weekly_breakout_still_permits_a_long():
    """'Uptrend: assume higher prices until breakout of resistance fails — then look for a
    counter-trend move down to a higher low.' That pullback IS the long entry, so a failed
    breakout is permissive, not disqualifying."""
    setup = cross_reference(
        _row(), _ctx(weekly_trend=UPTREND_FAILED_BREAKOUT), published_close=100.0
    )
    assert isinstance(setup, Setup)


def test_daily_opposing_weekly_is_a_conflict_and_kills_the_setup():
    """Weekly wins outright — a daily setup against weekly is discarded, not ranked lower."""
    outcome = cross_reference(_row(), _ctx(daily_trend=DOWNTREND), published_close=100.0)
    assert _reason(outcome) == "timeframe_conflict"


def test_daily_ranging_is_not_a_conflict():
    setup = cross_reference(_row(), _ctx(daily_trend=RANGING), published_close=100.0)
    assert isinstance(setup, Setup)


def test_a_ranging_weekly_leaves_no_macro_view_for_the_daily_to_contradict():
    """The mirror of ``test_a_ranging_weekly_is_unaligned_not_disagreeing``, one gate down.

    ``timeframe_conflict`` means "the two timeframes disagree", which requires two opinions.
    A ranging weekly has none, so there is nothing for the daily to conflict *with* and the
    refusal was measuring the daily leg alone. Measured 2026-07-28 (§27): 256 of 1,017
    refusals were this case, and releasing them recovers 23 candidates.

    The trade is still permitted rather than merely tolerated because a range has its own
    thesis — price is assumed to travel to the previous high or low until invalidated — so a
    weekly without a trend is not a weekly without a reason to act.
    """
    setup = cross_reference(
        _row(), _ctx(weekly_trend=RANGING, daily_trend=DOWNTREND), published_close=100.0
    )
    assert isinstance(setup, Setup)
    # Still unaligned: releasing the gate must not silently promote it to macro agreement.
    assert setup.trend_alignment == 0.0


def test_a_daily_conflict_under_an_agreeing_weekly_is_still_refused():
    """The half of the gate that survives §27. Two opinions that genuinely disagree is the
    case the reason was named for, and it is untouched."""
    outcome = cross_reference(
        _row(), _ctx(weekly_trend=UPTREND, daily_trend=DOWNTREND), published_close=100.0
    )
    assert _reason(outcome) == "timeframe_conflict"


# ── the premium/discount gate ───────────────────────────────────────────────

def test_a_long_in_premium_is_refused():
    """'Only ever TRULY buy the discount of the range.'"""
    outcome = cross_reference(_row(), _ctx(price=180.0), published_close=100.0)
    assert _reason(outcome) == "wrong_side_of_range"


def test_no_dealing_range_means_no_setup():
    outcome = cross_reference(_row(), _ctx(dealing_range=None), published_close=100.0)
    assert _reason(outcome) == "no_dealing_range"


# ── the zone gate ───────────────────────────────────────────────────────────

def test_no_zone_in_the_thesis_direction_means_no_setup():
    bearish_only = (Zone(block=_block(BEARISH), structural_target=60.0),)
    outcome = cross_reference(_row(), _ctx(zones=bearish_only), published_close=100.0)
    assert _reason(outcome) == "no_live_zone"


def test_a_zone_that_can_never_be_invalidated_is_refused():
    """A break with no origin swing has no level at which the zone dies, so it would stay live
    forever. That is worse than reporting nothing — note this is about the zone's *lifetime*,
    not about missing risk, since the stop is always available as the far edge."""
    endless = (Zone(block=_block(invalidation=None), structural_target=140.0),)
    outcome = cross_reference(_row(), _ctx(zones=endless), published_close=100.0)
    assert _reason(outcome) == "no_invalidation"


def test_the_most_recently_confirmed_zone_wins():
    """Structure is defined by the most recent level, so an older zone must not shadow a
    newer one just by appearing first in the sequence."""
    older = Zone(block=_block(top=110.0, bottom=100.0, confirmed_day=6), structural_target=140.0)
    newer = Zone(block=_block(top=115.0, bottom=105.0, confirmed_day=9), structural_target=150.0)
    setup = cross_reference(_row(), _ctx(zones=(older, newer)), published_close=100.0)
    assert setup.entry_bottom == 105.0
    # ...and the order they arrive in must not change the answer.
    reversed_ = cross_reference(_row(), _ctx(zones=(newer, older)), published_close=100.0)
    assert reversed_.entry_bottom == 105.0


# ── freshness ───────────────────────────────────────────────────────────────

def test_freshness_is_one_half_at_the_half_life():
    """The curve's whole contract: the configured window is where confidence halves, not
    where the view dies."""
    assert freshness_signal(0, 21) == 1.0
    assert freshness_signal(21, 21) == pytest.approx(0.5)
    assert freshness_signal(42, 21) == pytest.approx(1 / 3)


def test_freshness_decays_forever_without_reaching_zero():
    """Age must never be able to eliminate a view on its own — that is the cliff we removed.
    Something said two years ago ranks near the bottom; it does not vanish."""
    ages = [0, 7, 30, 90, 365, 1000]
    scores = [freshness_signal(age, 21) for age in ages]
    assert scores == sorted(scores, reverse=True)
    assert scores[-1] > 0.0


def test_a_view_older_than_its_window_still_becomes_a_setup():
    """Was ``stale``, the single biggest killer in the corpus: 2,913 of 3,459 rejections. Age
    is a measurement on a continuum, so it belongs in the score, not in a gate."""
    setup = cross_reference(_row(published_at="2024-01-01"), _ctx(), published_close=100.0)
    assert isinstance(setup, Setup)
    assert 0.0 < setup.freshness < 0.1


def test_a_fresher_view_outscores_an_older_one():
    fresh = cross_reference(_row(published_at="2025-01-09"), _ctx(), published_close=100.0)
    old = cross_reference(_row(published_at="2024-01-01"), _ctx(), published_close=100.0)
    assert fresh.freshness > old.freshness
    assert fresh.score > old.score


def test_freshness_scales_with_the_stated_timeframe():
    """One rule, different durations: a swing call ages in weekly candles and a position call
    in monthly ones, so the same elapsed days cost them different amounts of confidence."""
    swing = cross_reference(_row(published_at="2024-10-01"), _ctx(), published_close=100.0)
    position = cross_reference(
        _row(published_at="2024-10-01", timeframe="position"), _ctx(), published_close=100.0
    )
    assert position.freshness > swing.freshness


def test_the_half_life_is_configurable():
    impatient = cross_reference(
        _row(), _ctx(), published_close=100.0, half_life=HalfLife(swing=1)
    )
    patient = cross_reference(
        _row(), _ctx(), published_close=100.0, half_life=HalfLife(swing=1000)
    )
    assert impatient.freshness < patient.freshness


def test_a_view_published_after_the_as_of_date_is_not_more_than_fresh():
    """Clamped rather than allowed to exceed 1.0, so a clock skew or a mis-parsed date cannot
    manufacture a score above the scale everything else is measured on."""
    setup = cross_reference(_row(published_at="2025-06-01"), _ctx(), published_close=100.0)
    assert setup.freshness == 1.0


def test_an_undated_view_is_refused_rather_than_assumed_fresh():
    """Still a gate: a missing date is an absent fact, not a low measurement. There is
    nothing to score."""
    outcome = cross_reference(_row(published_at=None), _ctx(), published_close=100.0)
    assert _reason(outcome) == "undated"


# ── target resolution ───────────────────────────────────────────────────────
#
# ``_ctx()`` puts entry at 110, the structural extreme at 140 and the range top at 200, so the
# nearest thing structure offers a long is 30 away. An authored number is believed only when it
# beats that — see ``core.exits``.


def _far_ctx(**overrides):
    """A context whose structure sits *beyond* any target under test, so ``_reasonable``'s own
    verdict is what decides. Needed because the nearest-wins rule otherwise masks it: a stated
    number that structure undercuts is dropped whether or not it was plausible, which would
    leave the plausibility tests passing for the wrong reason."""
    base = {"dealing_range": _range(high=500.0),
            "zones": (Zone(block=_block(), structural_target=400.0),)}
    base.update(overrides)
    return _ctx(**base)


def test_a_stated_target_nearer_than_structure_is_believed():
    """'If the person has a target, listen to that' — but only when it is the first thing price
    meets. 130 is 20 from entry against structure's 30, so it leads."""
    setup = cross_reference(_row(key_levels=[130.0]), _ctx(), published_close=100.0)
    assert _nearest(setup).price == 130.0
    assert _nearest(setup).kind == STATED


def test_a_stated_target_beyond_the_nearest_structural_level_is_dropped():
    """The LINK case in miniature: a stated 150 against an extreme at 140. The engine used to
    print 150 and never mention 140; now 140 is the target and 150 is gone entirely, because a
    number nobody has to trade through is not a level."""
    setup = cross_reference(_row(key_levels=[150.0]), _ctx(), published_close=100.0)
    assert _nearest(setup).price == 140.0
    assert _nearest(setup).kind == EXTREME
    assert 150.0 not in [level.price for level in setup.ladder]


def test_overhead_supply_caps_the_target_and_the_rest_becomes_the_ladder():
    """The LINK defect end to end. A long whose zone ran to 140 with a live *bearish* block at
    130 was targeting straight through supply the engine had already found; now 130 is the
    target and 140/200 are runners the trade can be held for once it pays."""
    overhead = Zone(block=_block(BEARISH, top=145.0, bottom=130.0, invalidation=160.0),
                    structural_target=None)
    ctx = _ctx(zones=(*_ctx().zones, overhead))
    setup = cross_reference(_row(), ctx, published_close=100.0)
    assert setup.target == 200.0
    assert setup.target_source == RANGE_BOUND
    assert [level.price for level in setup.ladder] == [130.0, 140.0]
    assert [level.kind for level in setup.ladder] == [OPPOSING, EXTREME]


def test_an_abstained_reading_falls_back_to_the_structural_target():
    """No levels at all means structure supplies the number. 'That's generally where it's going
    whether they call it that way or not.'"""
    setup = cross_reference(_row(key_levels=[]), _ctx(), published_close=100.0)
    assert _nearest(setup).price == 140.0
    assert _nearest(setup).kind == EXTREME


def test_an_inferred_nearest_target_is_used_and_labelled_as_such():
    """Several levels beyond entry resolve to the nearest rather than abstaining, and the
    source records that it was inferred so it stays separable from a clean read."""
    setup = cross_reference(_row(key_levels=[130.0, 160.0]), _ctx(), published_close=100.0)
    assert _nearest(setup).price == 130.0
    assert _nearest(setup).kind == NEAREST


def test_a_stated_target_below_entry_is_unreasonable():
    """Stated at 105 it was above the publish price of 100, but the zone's near edge is 110 —
    so by the time there's an entry, the 'target' is behind it."""
    setup = cross_reference(_row(key_levels=[105.0]), _ctx(), published_close=100.0)
    assert _nearest(setup).kind == EXTREME


def test_a_stated_target_price_has_already_reached_is_unreasonable():
    """Measured on the live queue: 5 of 69 candidates carried a target price was already past,
    every one of them author-supplied. SPX showed a target of 6000 against a price of 7403,
    read from a call published at 5842 — the author's claim had been satisfied 14 months
    earlier, but 6000 still sits beyond the zone's near edge, which was the only thing checked.

    Here the zone entry is 110 and price is 130, so a stated 125 clears the entry and the R:R
    floor and would once have been believed. It is not a target any more; it is history."""
    setup = cross_reference(_row(key_levels=[125.0]), _ctx(price=130.0), published_close=100.0)
    assert _nearest(setup).price == 140.0
    assert _nearest(setup).kind == EXTREME


def test_a_short_target_price_has_already_reached_is_unreasonable():
    """The same rule on the other side, where 'already reached' means price is *below* the
    target. Asserted separately because the check is sign-dependent, and a sign error would
    leave the long case green while inverting the short one into rejecting live targets."""
    ctx = _ctx(
        weekly_trend=DOWNTREND, daily_trend=DOWNTREND, price=150.0,
        zones=(Zone(block=_block(BEARISH, top=190.0, bottom=180.0, invalidation=200.0),
                    structural_target=140.0),),
    )
    setup = cross_reference(_row(direction="short", key_levels=[160.0]), ctx,
                            published_close=185.0)
    assert _nearest(setup).price == 140.0
    assert _nearest(setup).kind == EXTREME


def test_a_stated_target_still_ahead_of_price_is_untouched():
    """The guard rejects reached targets, not distant ones. Price at 130 is past the entry and
    well into the zone, and a stated 150 is still ahead of it — 'if they call something, we
    listen' has to keep firing for the ordinary case, or the fix would quietly delete the
    stated leg of target selection instead of cleaning it."""
    setup = cross_reference(_row(key_levels=[135.0]), _ctx(price=130.0), published_close=100.0)
    assert _nearest(setup).price == 135.0
    assert _nearest(setup).kind == STATED


def test_a_stated_target_with_reward_risk_below_one_is_unreasonable():
    # entry 110, stop 95 (the far edge padded by one ATR) -> risk 15. A target at 115 is 5 of
    # reward. It is nearer than structure's 140, so only the R:R floor can reject it.
    setup = cross_reference(_row(key_levels=[115.0]), _ctx(), published_close=100.0)
    assert _nearest(setup).kind == EXTREME


def test_an_implausibly_distant_stated_target_is_unreasonable():
    """ATR is 5, so anything beyond 100 past the entry is further than this instrument travels.
    A call for 500 is not a target, it's a vibe. Structure is pushed out to 400 so the ceiling
    is what rejects it rather than the nearest-wins rule."""
    setup = cross_reference(_row(key_levels=[500.0]), _far_ctx(), published_close=100.0)
    assert _nearest(setup).price == 400.0
    assert _nearest(setup).kind == EXTREME


def test_the_distance_ceiling_scales_with_volatility_not_with_the_structural_target():
    """Measured: judging against the post-break extreme rejected 78 of 135 live ETH readings,
    because a recent break leaves a tiny structural distance. A target 190 beyond entry is
    implausible on ATR 5 and entirely ordinary on ATR 50 — the structural target is identical
    in both cases, so it cannot be the yardstick."""
    quiet = cross_reference(_row(key_levels=[300.0]), _far_ctx(atr=5.0), published_close=100.0)
    volatile = cross_reference(_row(key_levels=[300.0]), _far_ctx(atr=50.0),
                               published_close=100.0)
    assert _nearest(quiet).kind == EXTREME
    assert _nearest(volatile).price == 300.0 and _nearest(volatile).kind == STATED


def test_an_unknown_atr_skips_the_distance_check_rather_than_failing_it():
    """Inability to judge must not read as a verdict — the rule imbalance.is_displacement
    follows too."""
    setup = cross_reference(_row(key_levels=[300.0]), _far_ctx(atr=None), published_close=100.0)
    assert _nearest(setup).price == 300.0


def test_a_candidate_below_the_reward_risk_floor_is_refused():
    """A trade risking more than it stands to make is not a setup. Left to scoring alone, a
    0.32-RR candidate still surfaced mid-list on live data."""
    # The range has to be tight for this to bite now: the boundary is the target on an internal
    # entry, so a thin post-break extreme no longer sets the ratio. Entry 110 against a boundary
    # at 115 over a padded risk of 15 is 0.33R.
    thin = (Zone(block=_block(), structural_target=115.0),)
    outcome = cross_reference(
        _row(), _ctx(zones=thin, dealing_range=_range(low=102.0, high=115.0)),
        published_close=100.0,
    )
    assert _reason(outcome) == "reward_risk_too_low"


# ── the stop is padded by the instrument's own noise ────────────────────────
#
# ``OrderBlock.stop`` is the zone's far edge exactly, so risk is the zone's height and nothing
# else — a fact about one candle on the day it formed, carrying no information about how much
# the instrument moves today. Measured on the live queue 2026-07-28: 41 of 93 candidates had a
# stop under 1 ATR, 39 of them daily zones (67% of the daily population against 6% of weekly).


def _short_ctx(**overrides):
    """The mirror of ``_ctx``: weekly and daily both down, price 180 in the premium half, and
    one live bearish zone at 180-190 whose near edge is therefore its *bottom*."""
    base = {
        "weekly_trend": DOWNTREND, "daily_trend": DOWNTREND, "price": 180.0,
        "zones": (Zone(block=_block(BEARISH, top=190.0, bottom=180.0, invalidation=200.0),
                    structural_target=150.0),),
    }
    base.update(overrides)
    return _ctx(**base)


def test_the_stop_is_padded_away_from_entry_by_a_multiple_of_atr():
    """entry 110, raw stop 100, ATR 5 — one ATR of padding puts the stop at 95 and risk at 15.

    The point of the ATR yardstick rather than a fraction of the zone's own height: a
    percentage hands the *tightest* zone the smallest cushion, which is backwards.
    """
    setup = cross_reference(_row(), _ctx(), published_close=100.0, stop_pad_atr=1.0)
    assert setup.stop == 95.0
    assert setup.reward_risk == 6.0     # reward 90 over risk 15, was 9.0 unpadded


def test_padding_moves_a_short_stop_the_other_way():
    """A bearish zone is entered at its bottom and stopped at its top, so the cushion goes
    *up*. Signing this off ``family`` rather than the raw direction label matters — the corpus
    vocabulary is wider than long/short."""
    setup = cross_reference(_row(direction="short"), _short_ctx(),
                            published_close=185.0, stop_pad_atr=1.0)
    assert setup.stop == 195.0          # raw far edge 190 plus one ATR
    # A short entered at 180 targets the range LOW at 80 — reward 100 over risk 15.
    assert setup.reward_risk == pytest.approx(100 / 15)


def test_the_shipped_default_pads_by_one_atr():
    """``k`` was swept against the live 93-candidate population before being chosen: 1.0 is the
    only value that empties the sub-1-ATR band outright, and it costs 5 candidates. Pinned here
    so a change to the constant has to be deliberate."""
    assert STOP_PAD_ATR == 1.0
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    assert setup.stop == 95.0


def test_padding_can_be_switched_off_per_call():
    """The zero case has to keep working — every probe that reconciles against a pre-v7 number
    reaches for it, and ``scripts/probe_stop_padding.py`` sweeps through it."""
    setup = cross_reference(_row(), _ctx(), published_close=100.0, stop_pad_atr=0.0)
    assert setup.stop == 100.0          # the raw far edge
    assert setup.reward_risk == 9.0


def test_an_unknown_atr_skips_padding_rather_than_failing_it():
    """Inability to judge must not read as a verdict — the same rule ``_reasonable`` and
    ``imbalance.is_displacement`` already follow for the distance ceiling."""
    setup = cross_reference(_row(), _ctx(atr=None), published_close=100.0, stop_pad_atr=1.0)
    assert setup.stop == 100.0
    assert setup.reward_risk == 9.0


def test_a_degenerate_zone_is_refused_on_its_raw_height_not_its_padded_one():
    """Ordering pin, and the reason the predicted test breakage does not happen.

    Padding a zero-height zone would give it a positive risk and make this refusal
    unreachable. A zone whose edges coincide is structural nonsense whatever the volatility,
    so degeneracy is a question about the *zone* and is settled before the trade's cushion is
    applied.
    """
    flat = (Zone(block=_block(top=110.0, bottom=110.0), structural_target=140.0),)
    outcome = cross_reference(_row(), _ctx(zones=flat), published_close=100.0,
                              stop_pad_atr=1.0)
    assert _reason(outcome) == "degenerate_zone"


def test_price_past_the_raw_far_edge_is_refused_even_when_padding_would_cover_it():
    """``price_past_stop`` asks whether price traded clean out of the *zone*, which is a
    structural fact. Re-pointing it at the padded stop would quietly release candidates whose
    price is already out the far side — a behaviour change well beyond adding a cushion.

    Price 96 is past the raw far edge of 100 and still inside a stop padded to 95.
    """
    outcome = cross_reference(_row(), _ctx(price=96.0), published_close=100.0,
                              stop_pad_atr=1.0)
    assert _reason(outcome) == "price_past_stop"


def test_the_zone_keeps_its_raw_edges_when_the_stop_is_padded():
    """The cushion belongs to the trade, not to the structure. ``block`` and the printed entry
    band must keep describing the zone as it actually is, or the queue would show a zone that
    no chart agrees with."""
    setup = cross_reference(_row(), _ctx(), published_close=100.0, stop_pad_atr=1.0)
    assert (setup.entry_top, setup.entry_bottom) == (110.0, 100.0)
    assert setup.block.stop == 100.0    # the structural fact, unpadded
    assert setup.stop == 95.0           # the trade's stop, padded


def test_the_reward_risk_floor_is_configurable():
    thin = (Zone(block=_block(), structural_target=115.0),)
    setup = cross_reference(
        _row(), _ctx(zones=thin, dealing_range=_range(low=102.0, high=115.0)),
        published_close=100.0, min_reward_risk=0.1,
    )
    assert isinstance(setup, Setup)
    assert setup.reward_risk == pytest.approx(5 / 15)   # reward 5 over a padded risk of 15


def test_no_target_from_any_source_is_refused():
    """Nothing beyond entry anywhere: no post-break extreme, nothing stated, and a range whose
    high the zone already sits above.

    That last part is what it takes to reach this refusal now, and it is a real configuration
    rather than a contrivance: the range is bounded by the most recent confirmed swings, and an
    order block can perfectly well have formed above the newest swing high. Price at 105 in a
    102-109 range is still a discount, so the long is permitted; its entry at 110 simply has no
    external liquidity left above it."""
    no_structure = (Zone(block=_block(), structural_target=None),)
    outcome = cross_reference(
        _row(), _ctx(zones=no_structure, dealing_range=_range(low=102.0, high=109.0)),
        published_close=100.0,
    )
    assert _reason(outcome) == "no_target"


def test_a_structural_target_behind_entry_falls_through_to_the_range_boundary():
    """A zone whose break never ran leaves the extreme behind the entry. That kills *that*
    level, not the trade: the range high above is still external liquidity, and targeting it is
    what the roster means by 'targets come from the high time frame'."""
    behind = (Zone(block=_block(), structural_target=105.0),)
    setup = cross_reference(_row(), _ctx(zones=behind), published_close=100.0)
    assert setup.target == 200.0
    assert setup.target_source == RANGE_BOUND


# ── where you entered decides where you exit ────────────────────────────────
#
# See ``core.exits``' module docstring for the doctrine and the measurement. In short: an order
# block is internal range liquidity, so an entry on one targets *external* range liquidity —
# the range boundary — and every level in between is a partial, not the destination.


def test_an_internal_entry_targets_the_range_boundary_over_a_nearer_obstruction():
    """The LNGX shape: a live opposing block at 130 was being quoted as the target at 1.33R
    while the boundary sat at 200 for 6.0R, with the block a partial on the way."""
    blocked = (
        Zone(block=_block(), structural_target=None),
        Zone(block=_block(BEARISH, top=140.0, bottom=130.0, invalidation=150.0),
             structural_target=None),
    )
    setup = cross_reference(_row(), _ctx(zones=blocked), published_close=100.0)
    assert setup.target == 200.0
    assert setup.target_source == RANGE_BOUND
    assert setup.reward_risk == pytest.approx(90 / 15)
    # The obstruction survives as a rung rather than being discarded — it is where a partial
    # would go, which is the whole reason to keep it.
    assert [level.price for level in setup.ladder] == [130.0]
    assert setup.ladder[0].kind == OPPOSING


def test_an_internal_entry_records_its_liquidity_and_its_distance():
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    assert setup.entry_liquidity == INTERNAL
    # Entry 110 in an 80-200 range: 30 above the low, a quarter of a 120-wide range.
    assert setup.entry_outside_widths == pytest.approx(-0.25)


def test_an_external_sweep_entry_targets_the_nearest_internal_level_instead():
    """The inverse leg: "if you're entering based on external, ultimately you're targeting
    internal". Entry 75 is 0.04 widths below an 80-200 range's low — a poke, not a regime
    change — so the extreme at 140 is the destination and the boundary is a runner beyond it."""
    swept = (Zone(block=_block(top=75.0, bottom=65.0, invalidation=55.0),
                  structural_target=140.0),)
    setup = cross_reference(_row(), _ctx(zones=swept), published_close=70.0)
    assert setup.entry == 75.0
    assert setup.entry_liquidity == EXTERNAL
    assert setup.target == 140.0
    assert setup.target_source == EXTREME
    assert [level.price for level in setup.ladder] == [200.0]


def test_an_entry_far_outside_the_range_is_refused_rather_than_retargeted():
    """SOL: a short whose entry sat 3.92 widths above a range price was trading inside. Filling
    it requires the breakout that redraws the range, so the target would be computed from a
    range that no longer exists. Not a sweep, and not a trade."""
    stranded = (Zone(block=_block(top=45.0, bottom=35.0, invalidation=25.0),
                     structural_target=140.0),)
    outcome = cross_reference(_row(), _ctx(zones=stranded), published_close=40.0)
    assert _reason(outcome) == "entry_outside_range"


def test_the_sweep_threshold_is_the_line_between_those_two():
    """Same shape either side of ``MAX_SWEEP_WIDTHS`` — 0.25 of an 80-200 range is 30, so an
    entry at 50 is the last one still treated as a sweep."""
    def _at(entry_top):
        zones = (Zone(block=_block(top=entry_top, bottom=entry_top - 10.0,
                                   invalidation=entry_top - 20.0),
                      structural_target=140.0),)
        return cross_reference(_row(), _ctx(zones=zones), published_close=entry_top - 5)

    assert _at(50.0).entry_liquidity == EXTERNAL
    assert _reason(_at(49.0)) == "entry_outside_range"


def test_an_internal_entry_with_levels_but_no_boundary_is_refused_as_having_no_external():
    """Distinct from ``no_target``, which means structure offered nothing at all. Here there is
    a level — an opposing block above — but no external range liquidity, and Mayne's
    disqualifier #3 refuses on that rather than falling back to the internal one.

    Degenerate by construction: it takes a block spanning from below the range's midpoint to
    above its high, which is the only way price can be in discount while the entry sits past
    the boundary."""
    straddling = (
        Zone(block=_block(top=210.0, bottom=100.0, invalidation=90.0), structural_target=None),
        Zone(block=_block(BEARISH, top=350.0, bottom=340.0, invalidation=360.0),
             structural_target=None),
    )
    outcome = cross_reference(_row(), _ctx(zones=straddling), published_close=100.0)
    assert _reason(outcome) == "no_external_target"


def test_price_outside_its_own_range_is_refused_rather_than_read_as_a_deep_discount():
    """TSLA: price 321.55 against a 368.60-432.86 range clamped to 0.0 and passed the
    manifesto's discount gate as a *maximally deep* one. The strongest possible signal from the
    weakest possible evidence — see ``DealingRange.position_at``."""
    outcome = cross_reference(
        _row(), _ctx(price=70.0, dealing_range=_range(low=80.0, high=200.0)),
        published_close=100.0,
    )
    assert _reason(outcome) == "wrong_side_of_range"


# ── tiering ─────────────────────────────────────────────────────────────────

def test_tier_from_asset_rank():
    assert tier_for(1) == TIER_MAJOR
    assert tier_for(10) == TIER_MAJOR
    assert tier_for(11) == TIER_LARGE
    assert tier_for(100) == TIER_LARGE
    assert tier_for(101) == TIER_SMALL
    assert tier_for(None) == TIER_UNRANKED


def test_setup_carries_its_tier():
    setup = cross_reference(_row(), _ctx(), published_close=100.0, asset_rank=1)
    assert setup.tier == TIER_MAJOR


def test_a_non_crypto_domain_never_takes_a_crypto_market_cap_rank():
    """Measured on live data: SPX — the S&P 500, correctly priced at ~7147 — resolved to
    CoinGecko rank 124 and was labelled a small-cap, because a memecoin shares the ticker.
    Price routing already refuses to guess across domains; tiering has to as well, or the
    collision walks back in through the ranking."""
    assert tier_for(124, domain="macro") == TIER_NONCRYPTO
    assert tier_for(124, domain="stock") == TIER_NONCRYPTO
    assert tier_for(124, domain="crypto") == TIER_SMALL


def test_non_crypto_is_distinct_from_unresolved():
    """'This is a stock' and 'this is crypto we couldn't resolve' are different facts, and
    merging them would hide resolution failures inside a legitimate category."""
    assert tier_for(None, domain="stock") != tier_for(None, domain="crypto")


def test_setup_tiers_a_stock_by_domain_not_by_a_colliding_rank():
    setup = cross_reference(
        _row(domain="stock"), _ctx(), published_close=100.0, asset_rank=124
    )
    assert setup.tier == TIER_NONCRYPTO


# ── scoring ─────────────────────────────────────────────────────────────────

def test_roster_agreement_raises_the_score():
    alone = cross_reference(_row(), _ctx(), published_close=100.0, agreement_count=0)
    agreed = cross_reference(_row(), _ctx(), published_close=100.0, agreement_count=5)
    assert agreed.score > alone.score


def test_score_stays_within_zero_and_one():
    setup = cross_reference(
        _row(), _ctx(price=100.0), published_close=100.0, agreement_count=99, asset_rank=1
    )
    assert 0.0 <= setup.score <= 1.0


# ── approach ────────────────────────────────────────────────────────────────

def test_approach_rises_monotonically_from_a_span_away_to_the_far_edge():
    """One ramp, three regimes: travelling, arrived, traversing. The old two-term form split
    this across ``proximity`` and ``depth``, whose domains were disjoint — so neither ever
    varied while the other did, and the pair was already this function written twice."""
    block = _block()  # bullish, 100-110, approached from above
    ramp = [approach_to(block, price) for price in (118.0, 111.0, 110.0, 105.0, 100.0)]
    assert ramp == sorted(ramp)
    assert ramp[0] > 0.0
    assert approach_to(block, 110.0) == pytest.approx(ARRIVAL)   # the near edge
    assert approach_to(block, 100.0) == pytest.approx(1.0)       # the far edge


def test_approach_keeps_falling_beyond_the_span_instead_of_flooring():
    """The span is a half-distance now, not a cliff — same change ``freshness_signal`` made,
    for the same reason: a bounded term that saturates stops measuring.

    Measured on the live queue 2026-07-27: **16 of 69 candidates sat at exactly 0.00**, which
    made SPX 26% from its zone and SOL *135%* from its zone indistinguishable from each other
    and from one 10.01% away. Those are not the same trade, and the queue could not say so."""
    block = _block()  # bullish, 100-110, approached from above
    one_span = approach_to(block, 110.0 / (1 - PROXIMITY_SPAN))
    two_spans = approach_to(block, 110.0 / (1 - 2 * PROXIMITY_SPAN))
    far = approach_to(block, 400.0)
    assert one_span > two_spans > far > 0.0
    # Never reaches zero: distance alone must not be able to eliminate a candidate, exactly as
    # age alone cannot. Only ``price_past_stop`` and the gates end a candidate outright.
    assert approach_to(block, 1e9) > 0.0


def test_the_span_is_the_distance_at_which_approach_is_worth_half_of_arrival():
    """What ``PROXIMITY_SPAN`` now names. Pinning it here because the constant's meaning
    changed without its name changing — it used to be where the ramp hit zero."""
    block = _block()  # near edge 110
    assert approach_to(block, 110.0 / (1 - PROXIMITY_SPAN)) == pytest.approx(ARRIVAL / 2)


def test_approach_is_continuous_across_the_near_edge():
    """The travelling and traversing branches meet at ``ARRIVAL`` exactly. A discontinuity here
    would put a cliff back in a different place — a hair outside the zone scoring materially
    differently from a hair inside it."""
    block = _block()  # bullish, 100-110
    assert approach_to(block, 110.0) == pytest.approx(ARRIVAL)
    assert approach_to(block, 110.0001) == pytest.approx(ARRIVAL, abs=1e-5)


def test_approach_is_zero_once_price_has_traded_through_the_zone():
    """The defect this collapse exists to make unwriteable. ``depth_at`` clamped, so a bullish
    zone price had crashed through reported depth 1.0 — the maximum — and the old pair could
    hold ``proximity 0.00, depth 1.00`` simultaneously. On the live queue that state was 13 of
    82 candidates, and they outscored everything else."""
    block = _block()  # bullish, 100-110
    assert approach_to(block, 95.0) == 0.0
    bear = _block(BEARISH, top=110.0, bottom=100.0, invalidation=120.0)
    assert approach_to(bear, 115.0) == 0.0


def test_approach_is_scale_free():
    """Expressed as a fraction of price so a 1%-away BTC zone and a 1%-away SOL zone score the
    same — an absolute distance would make every cheap asset look imminent."""
    big = _block(top=110000.0, bottom=100000.0, invalidation=90000.0)
    small = _block(top=1.10, bottom=1.00, invalidation=0.90)
    assert approach_to(big, 111000.0) == pytest.approx(approach_to(small, 1.11))


def test_an_approaching_zone_outscores_a_distant_one():
    """The finding that motivated the ramp: on real data every candidate sat outside its zone
    with depth pinned at 0, so a zone 1% away and one 30% away scored identically."""
    approaching = cross_reference(_row(), _ctx(price=111.0), published_close=100.0)
    distant = cross_reference(_row(), _ctx(price=118.0), published_close=100.0)
    assert approaching.approach > distant.approach
    assert approaching.score > distant.score


def test_a_zone_price_has_traded_out_the_far_side_is_refused():
    """``block.stop`` is the far edge, so price beyond it means an entry at the near edge would
    already have been stopped. The zone itself is untouched — it dies at ``invalidation`` (90
    here), further out still — which is why this is a refusal about the trade and not a reason
    to drop the zone."""
    outcome = cross_reference(_row(), _ctx(price=95.0), published_close=100.0)
    assert _reason(outcome) == "price_past_stop"


def test_price_past_the_stop_is_refused_rather_than_scored_as_maximally_deep():
    """The regression proper. Under v2 this same candidate scored *higher* than one price had
    genuinely reached, because the clamp in ``position_in_range`` read "past the far edge" as
    "at the far edge" and paid it full depth."""
    through = cross_reference(_row(), _ctx(price=95.0), published_close=100.0)
    arrived = cross_reference(_row(), _ctx(price=105.0), published_close=100.0)
    assert isinstance(through, NotASetup)
    assert isinstance(arrived, Setup)


# ── collapsing to one candidate per zone ────────────────────────────────────

def _setups_for(people):
    ctx = _ctx()
    return [
        cross_reference(_row(id=f"t{i}", person=person), ctx, published_close=100.0)
        for i, person in enumerate(people)
    ]


def test_many_theses_on_one_zone_collapse_into_a_single_candidate():
    """A setup is an (asset, direction, zone); the roster is evidence for it, not a multiplier
    of it. Real data emitted eight identical ETH longs differing only by author."""
    candidates = collapse(_setups_for(["Mayne", "Cred", "DonAlt", "Mayne"]))
    assert len(candidates) == 1
    assert isinstance(candidates[0], Candidate)
    assert set(candidates[0].people) == {"Cred", "DonAlt", "Mayne"}   # deduped
    assert candidates[0].agreement == 3
    assert len(candidates[0].thesis_ids) == 4


# ── dates on candidates ─────────────────────────────────────────────────────
#
# A bare agreement count hides that one of four people last spoke months ago. The triage queue
# had to be fixed once for exactly this ("no date shown"), so the setups queue carries dates
# from the start.

def test_each_supporter_carries_their_own_latest_date_newest_first():
    ctx = _ctx()
    old = cross_reference(_row(id="a", person="Cowen", published_at="2026-07-05"), ctx,
                          published_close=100.0)
    recent = cross_reference(_row(id="b", person="Mayne", published_at="2026-07-20"), ctx,
                             published_close=100.0)
    candidate = collapse([old, recent])[0]
    assert candidate.views[0] == View(person="Mayne", published_at="2026-07-20")
    assert candidate.views[1] == View(person="Cowen", published_at="2026-07-05")
    assert candidate.newest_at == "2026-07-20"
    assert candidate.oldest_at == "2026-07-05"


def test_a_person_who_restated_counts_once_at_their_latest_date():
    """Ten restatements are one voice, not ten — and the date shown is the current one."""
    ctx = _ctx()
    setups = [
        cross_reference(_row(id="a", person="Mayne", published_at="2026-07-05"), ctx,
                        published_close=100.0),
        cross_reference(_row(id="b", person="Mayne", published_at="2026-07-20"), ctx,
                        published_close=100.0),
    ]
    candidate = collapse(setups)[0]
    assert candidate.agreement == 1
    assert candidate.views == (View(person="Mayne", published_at="2026-07-20"),)
    assert len(candidate.thesis_ids) == 2


def test_published_at_is_normalized_to_a_date_even_from_a_full_timestamp():
    setup = cross_reference(
        _row(published_at="2026-07-20T14:33:02Z"), _ctx(), published_close=100.0
    )
    assert setup.published_at == "2026-07-20"


def test_collapsing_recomputes_agreement_from_the_group():
    """Six people on one zone is one strong candidate, not six weak ones."""
    alone = collapse(_setups_for(["Mayne"]))[0]
    crowded = collapse(_setups_for(["Mayne", "Cred", "DonAlt", "Pierre"]))[0]
    assert crowded.score > alone.score


def test_a_candidate_takes_the_freshness_of_its_freshest_supporter():
    """The question a candidate answers is "is anyone still saying this", so one current voice
    carries the zone even when the others have gone quiet. Taking the freshest member also
    avoids threading ``as_of`` into ``collapse``, which has no business knowing the date."""
    ctx = _ctx()
    stale_voice = cross_reference(_row(id="a", person="Cowen", published_at="2024-01-01"),
                                  ctx, published_close=100.0)
    live_voice = cross_reference(_row(id="b", person="Mayne", published_at="2025-01-09"),
                                 ctx, published_close=100.0)
    candidate = collapse([stale_voice, live_voice])[0]
    assert candidate.freshness == live_voice.freshness


def test_a_candidate_everyone_has_gone_quiet_on_outscores_nothing_but_ranks_low():
    ctx = _ctx()
    quiet = collapse([cross_reference(_row(published_at="2024-01-01"), ctx,
                                      published_close=100.0)])[0]
    current = collapse([cross_reference(_row(published_at="2025-01-09"), ctx,
                                        published_close=100.0)])[0]
    assert 0.0 < quiet.score < current.score


# ── two corpus labels, one instrument ───────────────────────────────────────
#
# The corpus names the same instrument more than one way, and the zone, stop and target are
# drawn on the instrument — so those rows are digit-for-digit the same trade. Live on
# 2026-07-30: RUT/IWM, EUR/EURUSD and GBP/GBPUSD, offered twice each.


def test_two_labels_for_one_instrument_collapse_into_one_candidate():
    """The same defect ``collapse`` already fixes for people, one level up: the supporters of
    one zone were being split across two spellings of its ticker."""
    ctx = _ctx()
    index = cross_reference(_row(id="a", asset="RUT", person="DataDash"), ctx,
                            published_close=100.0)
    fund = cross_reference(_row(id="b", asset="IWM", person="Raoul"), ctx,
                           published_close=100.0)
    (candidate,) = collapse([index, fund], aliases={"RUT": "IWM"})
    assert candidate.agreement == 2
    assert set(candidate.people) == {"DataDash", "Raoul"}


def test_the_merged_row_is_labelled_by_the_instrument_not_by_the_representative():
    """The label decides venue lookup and the decision key, so it cannot depend on which
    member happened to win target selection — ``RUT`` reaches Alpaca alone, ``IWM`` reaches
    three venues, and a row that flips between them routes differently run to run."""
    ctx = _ctx()
    nearer = cross_reference(_row(id="a", asset="RUT", key_levels=[130.0]), ctx,
                             published_close=100.0)
    further = cross_reference(_row(id="b", asset="IWM", key_levels=[150.0]), ctx,
                              published_close=100.0)
    (candidate,) = collapse([nearer, further], aliases={"RUT": "IWM"})
    # RUT's row is still the representative — its 130 is the only authored level to survive
    # the nearest-wins rule, and it rides along as a partial under the shared boundary target.
    assert 130.0 in [level.price for level in candidate.ladder]
    assert candidate.asset == "IWM"


def test_a_folded_label_is_recorded_rather_than_discarded():
    """``cfg/exclusions.yaml`` and the queue both key on the label, so a spelling that is
    silently absorbed takes a standing "I don't trade this" with it."""
    ctx = _ctx()
    setups = [cross_reference(_row(id="a", asset=a), ctx, published_close=100.0)
              for a in ("RUT", "IWM")]
    (candidate,) = collapse(setups, aliases={"RUT": "IWM"})
    assert candidate.aliases == ("RUT",)


def test_an_unaliased_label_keeps_its_own_candidate_and_carries_no_aliases():
    """The map is the only thing that merges anything. Two tickers that merely trade alike
    stay two trades."""
    ctx = _ctx()
    setups = [cross_reference(_row(id="a", asset=a), ctx, published_close=100.0)
              for a in ("SPY", "QQQ")]
    candidates = collapse(setups)
    assert len(candidates) == 2
    assert all(c.aliases == () for c in candidates)


def test_the_nearest_target_wins_among_disagreeing_views():
    """If they can't agree how far price goes, the smallest claim is the one to hold them to."""
    ctx = _ctx()
    optimist = cross_reference(_row(id="t1", key_levels=[135.0]), ctx, published_close=100.0)
    modest = cross_reference(_row(id="t2", key_levels=[130.0]), ctx, published_close=100.0)
    candidate = collapse([optimist, modest])[0]
    # Both name a level and both survive; the group carries the modest one's ladder. The
    # *target* can no longer express this — members of a group share a zone, so they share a
    # boundary — which is why the representative choice reads the ladder now.
    assert 130.0 in [level.price for level in candidate.ladder]
    assert 135.0 not in [level.price for level in candidate.ladder]


def test_a_named_target_represents_the_group_over_an_unnamed_one():
    """'If they call something, we listen.' The preference survives, but it can no longer pull
    the group's target *further out*: by the time a row reaches ``collapse``, ``core.exits`` has
    already dropped any authored number that structure undercut. Here 130 beats the extreme at
    140 on its own row, so the named target is also the nearer one."""
    ctx = _ctx()
    authored = cross_reference(_row(id="t1", key_levels=[130.0]), ctx, published_close=100.0)
    structural = cross_reference(_row(id="t2", key_levels=[]), ctx, published_close=100.0)
    assert _nearest(structural).kind == EXTREME
    candidate = collapse([authored, structural])[0]
    assert _nearest(candidate).price == 130.0
    assert _nearest(candidate).kind == STATED


def test_structure_is_still_used_when_nobody_stated_a_target():
    candidate = collapse(_setups_for(["Mayne", "Cred"]))[0]
    assert _nearest(candidate).kind == EXTREME


def test_separate_zones_stay_separate():
    ctx_a = _ctx()
    ctx_b = _ctx(zones=(Zone(block=_block(top=115.0, bottom=105.0, confirmed_day=9),
                             structural_target=150.0),))
    both = [
        cross_reference(_row(id="a"), ctx_a, published_close=100.0),
        cross_reference(_row(id="b"), ctx_b, published_close=100.0),
    ]
    assert len(collapse(both)) == 2


def test_collapse_ignores_rejections_so_the_raw_stream_can_be_passed_through():
    mixed = [*_setups_for(["Mayne"]), cross_reference(_row(published_at=None), _ctx(), published_close=100.0)]
    candidates = collapse(mixed)
    assert len(candidates) == 1


def test_collapse_returns_best_score_first():
    ctx_near = _ctx(price=101.0)
    ctx_far = _ctx(price=118.0, zones=(Zone(block=_block(top=115.0, bottom=105.0,
                                                        confirmed_day=9),
                                            structural_target=150.0),))
    candidates = collapse([
        cross_reference(_row(id="far"), ctx_far, published_close=100.0),
        cross_reference(_row(id="near"), ctx_near, published_close=100.0),
    ])
    assert [c.score for c in candidates] == sorted((c.score for c in candidates), reverse=True)


def test_collapse_of_nothing_is_empty():
    assert collapse([]) == ()


def test_candidate_key_is_content_addressed_not_positional():
    """Keyed on the zone's prices and date, never on block.index. Backfilling earlier price
    data shifts every index — the same failure that forced content-addressed thesis ids, where
    positional ids silently re-pointed triage decisions at unrelated theses."""
    same_zone_moved = _ctx(zones=(Zone(block=_block(), structural_target=140.0),))
    a = collapse(_setups_for(["Mayne"]))[0]
    b = collapse([cross_reference(_row(id="other"), same_zone_moved, published_close=100.0)])[0]
    assert a.key == b.key

    different = collapse([
        cross_reference(_row(), _ctx(zones=(Zone(block=_block(top=115.0, bottom=105.0),
                                                 structural_target=150.0),)),
                        published_close=100.0)
    ])[0]
    assert different.key != a.key


# ── zone timeframes ─────────────────────────────────────────────────────────

def _tf_ctx(**overrides):
    """A context carrying one weekly zone and one daily zone, both bullish and both live.

    The weekly zone is deliberately the *wider and further* of the two, mirroring the live
    GOOGL shape that motivated all of this: a tight daily block sitting on top of price while
    the weekly block price is actually drawn to sits well below it.
    """
    base = {"zones": (
        Zone(block=_block(top=95.0, bottom=75.0, invalidation=70.0, confirmed_day=7),
             structural_target=140.0, timeframe=WEEKLY),
        Zone(block=_block(top=110.0, bottom=100.0, confirmed_day=6),
             structural_target=140.0, timeframe=DAILY),
    )}
    base.update(overrides)
    return _ctx(**base)


def test_cross_reference_still_reads_the_daily_zone_by_default():
    """Back-compat is load-bearing: every other test in this file, and the whole notion of an
    unchanged decision key, depends on the daily pass being what you get for free."""
    setup = cross_reference(_row(), _tf_ctx(), published_close=100.0)
    assert setup.zone_timeframe == DAILY
    assert (setup.entry_bottom, setup.entry_top) == (100.0, 110.0)


def test_the_weekly_pass_reads_the_weekly_zone():
    setup = cross_reference(_row(), _tf_ctx(), published_close=100.0, zone_timeframe=WEEKLY)
    assert setup.zone_timeframe == WEEKLY
    assert (setup.entry_bottom, setup.entry_top) == (75.0, 95.0)


def test_the_two_timeframes_do_not_compete_for_the_same_slot():
    """The weekly zone here is both older and further from price, so a newest-wins or a
    score-wins contest across timeframes would hand both passes the daily block — which is
    exactly the bias weekly structure was added to correct."""
    weekly = cross_reference(_row(), _tf_ctx(), published_close=100.0, zone_timeframe=WEEKLY)
    daily = cross_reference(_row(), _tf_ctx(), published_close=100.0, zone_timeframe=DAILY)
    assert weekly.entry != daily.entry


def test_pricing_risk_on_the_weekly_zone_gives_a_sane_reward_risk():
    """The point of the whole exercise. The daily block is 10 wide and the weekly one 20, so
    the weekly setup risks more per unit and reports a reward:risk a human recognizes rather
    than the inflated number a one-candle stop produces."""
    weekly = cross_reference(_row(key_levels=[140.0]), _tf_ctx(), published_close=100.0,
                             zone_timeframe=WEEKLY)
    daily = cross_reference(_row(key_levels=[140.0]), _tf_ctx(), published_close=100.0,
                            zone_timeframe=DAILY)
    assert weekly.reward_risk < daily.reward_risk
    # Each stop is its zone's far edge padded by one ATR: 75 - 5 and 100 - 5.
    assert weekly.stop == 70.0 and daily.stop == 95.0


def test_a_timeframe_with_no_live_zone_is_refused_without_borrowing_the_other():
    daily_only = _ctx(zones=(Zone(block=_block(), structural_target=140.0, timeframe=DAILY),))
    assert _reason(cross_reference(_row(), daily_only, published_close=100.0,
                                   zone_timeframe=WEEKLY)) == "no_live_zone"
    assert isinstance(cross_reference(_row(), daily_only, published_close=100.0), Setup)


def test_every_zone_level_refusal_the_engine_emits_is_classified_as_one():
    """``ZONE_LEVEL_REASONS`` repeats refusal strings that are written out in
    ``cross_reference``, and nothing in the type system ties the two together. Renaming a reason
    there would silently drop it out of the set, at which point the tally starts deduping it as
    a thesis-level reason and under-counts it. Driving each refusal for real is what keeps them
    in step — asserting the strings by hand would just restate the bug.
    """
    emitted = {
        _reason(cross_reference(_row(), _ctx(), published_close=100.0,
                                zone_timeframe=WEEKLY)),
        _reason(cross_reference(
            _row(), _ctx(zones=(Zone(block=_block(invalidation=None),
                                     structural_target=140.0),)), published_close=100.0)),
        _reason(cross_reference(
            _row(), _ctx(zones=(Zone(block=_block(top=110.0, bottom=110.0),
                                     structural_target=140.0),)), published_close=100.0)),
        # no_target now needs the range boundary out of reach too — it is a target source in
        # its own right, so a zone with no post-break extreme is no longer automatically dead.
        # See test_no_target_from_any_source_is_refused for why the range is 102-109.
        _reason(cross_reference(
            _row(), _ctx(zones=(Zone(block=_block(), structural_target=None),),
                         dealing_range=_range(low=102.0, high=109.0)),
            published_close=100.0)),
        # reward_risk_too_low needs a tight range for the same reason — see
        # test_a_candidate_below_the_reward_risk_floor_is_refused.
        _reason(cross_reference(
            _row(), _ctx(zones=(Zone(block=_block(), structural_target=115.0),),
                         dealing_range=_range(low=102.0, high=115.0)),
            published_close=100.0)),
        # entry_outside_range: entry 45 sits 0.29 widths below an 80-200 range's low.
        _reason(cross_reference(
            _row(), _ctx(zones=(Zone(block=_block(top=45.0, bottom=35.0, invalidation=25.0),
                                     structural_target=140.0),)),
            published_close=40.0)),
        # no_external_target: a level above the entry, but no boundary rung beyond it.
        _reason(cross_reference(
            _row(), _ctx(zones=(
                Zone(block=_block(top=210.0, bottom=100.0, invalidation=90.0),
                     structural_target=None),
                Zone(block=_block(BEARISH, top=350.0, bottom=340.0, invalidation=360.0),
                     structural_target=None))),
            published_close=100.0)),
        _reason(cross_reference(_row(), _ctx(price=95.0), published_close=100.0)),
        # Funding so extreme that carry eats the whole target — the trade loses money at its
        # own target. A zone-level fact, so it belongs in this set rather than the thesis one.
        _reason(cross_reference(
            _row(), _ctx(funding=FundingOutlook(venue="hyperliquid", median=20.0, p90=25.0, n=1)),
            published_close=100.0)),
    }
    assert emitted == ZONE_LEVEL_REASONS


def test_a_thesis_level_refusal_is_never_classified_as_a_zone_one():
    """The other half of the split. These are decided from the thesis and the asset alone, so
    they fire identically on every timeframe and counting them per pass would double them."""
    emitted = {
        _reason(cross_reference(_row(published_at=None), _ctx(), published_close=100.0)),
        _reason(cross_reference(_row(direction="sideways"), _ctx(), published_close=100.0)),
        _reason(cross_reference(_row(timeframe="epochal"), _ctx(), published_close=100.0)),
        _reason(cross_reference(_row(), _ctx(dealing_range=None), published_close=100.0)),
        _reason(cross_reference(_row(), _ctx(price=180.0), published_close=100.0)),
        _reason(cross_reference(_row(), _ctx(daily_trend=DOWNTREND), published_close=100.0)),
        _reason(cross_reference(_row(), _ctx(weekly_trend=DOWNTREND), published_close=100.0)),
    }
    assert emitted.isdisjoint(ZONE_LEVEL_REASONS)


def test_collapse_keeps_the_two_timeframes_as_separate_candidates():
    ctx = _tf_ctx()
    candidates = collapse([
        cross_reference(_row(id="w"), ctx, published_close=100.0, zone_timeframe=WEEKLY),
        cross_reference(_row(id="d"), ctx, published_close=100.0, zone_timeframe=DAILY),
    ])
    assert len(candidates) == 2
    assert {c.zone_timeframe for c in candidates} == {WEEKLY, DAILY}


def test_collapse_ranks_a_weekly_candidate_above_a_daily_one_it_outscores_on_nothing():
    """'The macro is much stronger' — Rule 8 already lets the weekly veto a direction, and the
    same precedence decides the queue. The weekly zone here scores *worse* (price has not
    reached it, so proximity and depth are both lower) and must still come first."""
    ctx = _tf_ctx()
    candidates = collapse([
        cross_reference(_row(id="d"), ctx, published_close=100.0, zone_timeframe=DAILY),
        cross_reference(_row(id="w"), ctx, published_close=100.0, zone_timeframe=WEEKLY),
    ])
    assert candidates[0].zone_timeframe == WEEKLY
    assert candidates[0].score < candidates[1].score


def test_a_daily_candidates_decision_key_is_unchanged_by_the_weekly_addition():
    """Decisions on disk are keyed by this hash. Changing the daily form would orphan every
    approve/reject already recorded, so the timeframe is only mixed in for weekly zones."""
    candidate = collapse([cross_reference(_row(), _tf_ctx(), published_close=100.0)])[0]
    legacy = sha256(
        f"{candidate.asset}\x1f{candidate.direction}\x1f{candidate.block.date.isoformat()}"
        f"\x1f{candidate.block.top}\x1f{candidate.block.bottom}".encode()
    ).hexdigest()[:12]
    assert candidate.key == legacy


def test_a_weekly_zone_cannot_collide_with_a_daily_one_on_the_same_bar():
    """``to_weekly`` dates a weekly bar at the last daily bar it aggregates, so a week with a
    single trading day produces a bar identical to that day's — same date, same high, same low.
    Without the timeframe in the hash both would key to one decision."""
    twin = _block(top=110.0, bottom=100.0)
    ctx = _ctx(zones=(Zone(block=twin, structural_target=140.0, timeframe=WEEKLY),))
    weekly = collapse([cross_reference(_row(), ctx, published_close=100.0,
                                       zone_timeframe=WEEKLY)])[0]
    daily = collapse([cross_reference(_row(), _ctx(), published_close=100.0)])[0]
    assert weekly.block.date == daily.block.date
    assert (weekly.block.top, weekly.block.bottom) == (daily.block.top, daily.block.bottom)
    assert weekly.key != daily.key


# ── shape ───────────────────────────────────────────────────────────────────

def test_outcomes_are_immutable():
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    rejected = cross_reference(_row(published_at=None), _ctx(), published_close=100.0)
    for outcome in (setup, rejected):
        with pytest.raises(FrozenInstanceError):
            outcome.asset = "ETH"


def test_a_rejection_still_identifies_the_thesis():
    """Rejections are categorized, never silently dropped — a corpus-wide tally of reasons is
    how you learn whether the gates are too tight."""
    rejected = cross_reference(_row(published_at=None), _ctx(), published_close=100.0)
    assert rejected.thesis_id == "t1"
    assert rejected.asset == "BTC"
    assert rejected.person == "TraderMayne"


def test_collapse_keeps_a_thesis_two_zones_adjacent():
    """Both expressions of one thesis must land side by side, whatever else is in the queue.

    §27's sitting hit the failure this fixes: SPX6900's weekly zone was row 1, an unrelated WLD
    row was 2, and SPX6900's daily zone was row 3 — so the second was judged against a memory of
    the first rather than against the first. The unconditional weekly-then-score sort put every
    weekly ahead of every daily, which separates precisely the rows that belong together.
    """
    ctx = _tf_ctx()
    other = _ctx(price=104.0)     # a different asset, scoring between the pair
    candidates = collapse([
        cross_reference(_row(id="a-d", asset="AAA"), ctx, published_close=100.0,
                        zone_timeframe=DAILY),
        cross_reference(_row(id="b-d", asset="BBB"), other, published_close=100.0,
                        zone_timeframe=DAILY),
        cross_reference(_row(id="a-w", asset="AAA"), ctx, published_close=100.0,
                        zone_timeframe=WEEKLY),
    ])
    order = [(c.asset, c.zone_timeframe) for c in candidates]
    a_positions = [i for i, (asset, _) in enumerate(order) if asset == "AAA"]
    assert a_positions == [min(a_positions), min(a_positions) + 1], (
        f"AAA's two zones are not adjacent: {order}"
    )


def test_within_a_thesis_the_weekly_zone_is_still_offered_first():
    """§19(e)'s precedence, narrowed to where it is unambiguous. "The macro is much stronger"
    decides which expression of *one* thesis leads; it no longer reorders unrelated theses."""
    ctx = _tf_ctx()
    candidates = collapse([
        cross_reference(_row(id="d"), ctx, published_close=100.0, zone_timeframe=DAILY),
        cross_reference(_row(id="w"), ctx, published_close=100.0, zone_timeframe=WEEKLY),
    ])
    assert [c.zone_timeframe for c in candidates] == [WEEKLY, DAILY]


def test_a_thesis_is_ranked_by_its_best_zone_not_by_its_weekly_one():
    """Measured over 27 paired assets on 2026-07-28, the daily zone scored higher 15 times to
    the weekly's 12 — near parity, no stable winner. Ranking a whole thesis by its weekly alone
    would therefore bury a strong daily behind a weak weekly, which is the §19(e) harm that put
    TSLA at 0.906 in position 29 and off the screen entirely."""
    strong_daily = _tf_ctx()                      # its daily zone holds price: high approach
    weak_other = _ctx(price=130.0)                # far from its zone: low approach
    candidates = collapse([
        cross_reference(_row(id="w2", asset="WEAK"), weak_other, published_close=100.0,
                        zone_timeframe=DAILY),
        cross_reference(_row(id="s-w", asset="STRONG"), strong_daily, published_close=100.0,
                        zone_timeframe=WEEKLY),
        cross_reference(_row(id="s-d", asset="STRONG"), strong_daily, published_close=100.0,
                        zone_timeframe=DAILY),
    ])
    best = max(candidates, key=lambda c: c.score)
    assert best.asset == "STRONG"
    assert candidates[0].asset == "STRONG", (
        "the thesis containing the best-scoring zone must lead, even though that zone is the "
        f"daily one: {[(c.asset, c.zone_timeframe, round(c.score, 3)) for c in candidates]}"
    )


# ── the setup rung is a parameter, not always the daily ─────────────────────────────────────
# Crypto takes weekly + H12; equities and session-bound indices take weekly + daily. An
# asset gets one or the other, never both — "the H12 *is* the daily, two H12 candles are one
# daily candle", so running both would be a 2x step carrying no new information.

def _bos_bars():
    """Bars that produce a live bullish order block: a down-leg, then a break above its high."""
    pairs = [(102, 98), (101, 97), (100, 96), (99, 95), (98, 94), (97, 93), (96, 92),
             (95, 91), (99, 93), (104, 97), (112, 103), (114, 108), (113, 106)]
    out = []
    for i, (high, low) in enumerate(pairs):
        mid = (high + low) / 2
        out.append(SimpleNamespace(date=date(2025, 1, 1) + timedelta(days=i),
                                   open=mid, high=float(high), low=float(low), close=mid))
    return out


def test_zones_are_tagged_with_the_setup_timeframe_they_came_from():
    bars = _bos_bars()
    ctx = build_context(bars, bars, as_of=bars[-1].date, setup_timeframe=H12)
    assert ctx is not None
    tags = {z.timeframe for z in ctx.zones}
    assert DAILY not in tags, "the setup rung was H12; nothing may still call itself daily"
    assert tags <= {WEEKLY, H12}


def test_the_setup_timeframe_defaults_to_daily():
    """Back-compat is load-bearing here exactly as it is for ``cross_reference``: every caller
    that predates the H12 rung, and every fixture built by hand, keeps its meaning untouched."""
    bars = _bos_bars()
    ctx = build_context(bars, bars, as_of=bars[-1].date)
    assert H12 not in {z.timeframe for z in ctx.zones}


def test_an_h12_zone_gets_a_different_candidate_key_than_a_daily_one():
    """A real consequence worth pinning rather than discovering in production.

    ``Candidate.key`` omits the timeframe only when it is ``DAILY`` — see its docstring, which
    keeps that asymmetry precisely so decisions already on disk are not orphaned. Moving crypto
    onto H12 therefore changes every crypto key. That is *correct*, since an H12 zone is a
    different setup from a daily one with its own block and its own stop, but it is not free:
    existing crypto approve/reject decisions stop matching, and reconciliation compares keys.
    """
    setup = cross_reference(_row(), _tf_ctx(), published_close=100.0)
    assert setup.zone_timeframe == DAILY
    as_daily = collapse([setup])[0]
    as_h12 = collapse([replace(setup, zone_timeframe=H12)])[0]
    assert as_daily.key != as_h12.key


def test_h12_is_a_known_zone_timeframe_so_the_queue_can_order_it():
    """``collapse`` sorts on ``ZONE_TIMEFRAMES.index`` and sends unknown values to the end, which
    would quietly bury every crypto candidate beneath every equity one."""
    assert H12 in ZONE_TIMEFRAMES
    assert ZONE_TIMEFRAMES.index(WEEKLY) == 0
