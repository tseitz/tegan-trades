from datetime import date
from types import SimpleNamespace

import pytest

from core.dealing_range import DealingRange
from core.levels import NEAREST, STATED
from core.setups import (
    STRUCTURAL,
    TIER_LARGE,
    TIER_MAJOR,
    TIER_NONCRYPTO,
    TIER_SMALL,
    TIER_UNRANKED,
    Candidate,
    Context,
    NotASetup,
    Setup,
    StaleAfter,
    View,
    Zone,
    collapse,
    cross_reference,
    proximity_to,
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
    base = dict(
        as_of=AS_OF,
        price=105.0,
        weekly_trend=UPTREND,
        daily_trend=UPTREND,
        dealing_range=_range(),
        zones=(Zone(block=_block(), structural_target=140.0),),
        atr=5.0,   # so the plausibility ceiling sits at 20 x 5 = 100 beyond entry
    )
    base.update(overrides)
    return Context(**base)


def _row(**overrides):
    base = dict(
        id="t1", asset="BTC", person="TraderMayne", direction="long",
        timeframe="swing", published_at=PUBLISHED, key_levels=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _reason(outcome):
    assert isinstance(outcome, NotASetup), f"expected a rejection, got {outcome!r}"
    return outcome.reason


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_permitted_long_becomes_a_setup():
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    assert isinstance(setup, Setup)
    assert setup.direction == "long"
    assert setup.entry_top == 110.0 and setup.entry_bottom == 100.0


def test_the_stop_is_the_far_edge_and_invalidation_is_the_origin_swing():
    """Two different levels answering two different questions: the stop is where this trade is
    wrong, invalidation is where the zone itself dies. Conflating them was measured and
    rejected — pricing risk down to the origin swing put 17 of 18 live candidates under 1.0 RR,
    and it also implied a stopped-out trade had destroyed the zone, which it hasn't."""
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    assert setup.stop == 100.0          # just past the zone's far edge
    assert setup.invalidation == 90.0   # the origin swing low


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
    # entry 110, stop 100, structural target 140 -> risk 10, reward 30
    assert setup.reward_risk == 3.0


def test_depth_reflects_how_far_price_has_travelled_into_the_zone():
    shallow = cross_reference(_row(), _ctx(price=109.0), published_close=100.0)
    deep = cross_reference(_row(), _ctx(price=101.0), published_close=100.0)
    assert deep.depth > shallow.depth
    assert deep.score > shallow.score


# ── the weekly gate ─────────────────────────────────────────────────────────

def test_weekly_downtrend_refuses_a_long():
    outcome = cross_reference(
        _row(), _ctx(weekly_trend=DOWNTREND, daily_trend=DOWNTREND), published_close=100.0
    )
    assert _reason(outcome) == "weekly_disagrees"


def test_ranging_weekly_permits_nothing():
    """Rule 8 needs a macro direction to defer to. Ranging supplies none, so there is no
    setup rather than a setup with a weak bias."""
    outcome = cross_reference(_row(), _ctx(weekly_trend=RANGING), published_close=100.0)
    assert _reason(outcome) == "weekly_disagrees"


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


# ── staleness ───────────────────────────────────────────────────────────────

def test_a_stale_view_is_refused():
    outcome = cross_reference(
        _row(published_at="2024-01-01"), _ctx(), published_close=100.0
    )
    assert _reason(outcome) == "stale"


def test_staleness_scales_with_the_stated_timeframe():
    """A four-month-old swing call is dead; a position call of the same age is not. One rule,
    different durations, because the candle length scales with the horizon."""
    old = _row(published_at="2024-10-01")
    assert _reason(cross_reference(old, _ctx(), published_close=100.0)) == "stale"
    position = _row(published_at="2024-10-01", timeframe="position")
    assert isinstance(cross_reference(position, _ctx(), published_close=100.0), Setup)


def test_staleness_is_configurable():
    outcome = cross_reference(
        _row(), _ctx(), published_close=100.0, stale_after=StaleAfter(swing=1)
    )
    assert _reason(outcome) == "stale"


def test_an_undated_view_is_refused_rather_than_assumed_fresh():
    outcome = cross_reference(_row(published_at=None), _ctx(), published_close=100.0)
    assert _reason(outcome) == "undated"


# ── target resolution ───────────────────────────────────────────────────────

def test_a_reasonable_stated_target_is_preferred_over_the_structural_one():
    """'If the person has a target, listen to that.'"""
    setup = cross_reference(_row(key_levels=[150.0]), _ctx(), published_close=100.0)
    assert setup.target == 150.0
    assert setup.target_source == STATED


def test_an_abstained_reading_falls_back_to_the_structural_target():
    """No levels at all means structure supplies the number. 'That's generally where it's going
    whether they call it that way or not.'"""
    setup = cross_reference(_row(key_levels=[]), _ctx(), published_close=100.0)
    assert setup.target == 140.0
    assert setup.target_source == STRUCTURAL


def test_an_inferred_nearest_target_is_used_and_labelled_as_such():
    """Several levels beyond entry resolve to the nearest rather than abstaining, and the
    source records that it was inferred so it stays separable from a clean read."""
    setup = cross_reference(_row(key_levels=[150.0, 160.0]), _ctx(), published_close=100.0)
    assert setup.target == 150.0
    assert setup.target_source == NEAREST


def test_a_stated_target_below_entry_is_unreasonable():
    """Stated at 105 it was above the publish price of 100, but the zone's near edge is 110 —
    so by the time there's an entry, the 'target' is behind it."""
    setup = cross_reference(_row(key_levels=[105.0]), _ctx(), published_close=100.0)
    assert setup.target_source == STRUCTURAL


def test_a_stated_target_with_reward_risk_below_one_is_unreasonable():
    # entry 110, stop 100 -> risk 10. A target at 115 is only 5 of reward.
    setup = cross_reference(_row(key_levels=[115.0]), _ctx(), published_close=100.0)
    assert setup.target_source == STRUCTURAL


def test_an_implausibly_distant_stated_target_is_unreasonable():
    """ATR is 5, so anything beyond 100 past the entry is further than this instrument travels.
    A call for 500 is not a target, it's a vibe."""
    setup = cross_reference(_row(key_levels=[500.0]), _ctx(), published_close=100.0)
    assert setup.target == 140.0
    assert setup.target_source == STRUCTURAL


def test_the_distance_ceiling_scales_with_volatility_not_with_the_structural_target():
    """Measured: judging against the post-break extreme rejected 78 of 135 live ETH readings,
    because a recent break leaves a tiny structural distance. A target 190 beyond entry is
    implausible on ATR 5 and entirely ordinary on ATR 50 — the structural target is identical
    in both cases, so it cannot be the yardstick."""
    quiet = cross_reference(_row(key_levels=[300.0]), _ctx(atr=5.0), published_close=100.0)
    volatile = cross_reference(_row(key_levels=[300.0]), _ctx(atr=50.0), published_close=100.0)
    assert quiet.target_source == STRUCTURAL
    assert volatile.target == 300.0 and volatile.target_source == STATED


def test_an_unknown_atr_skips_the_distance_check_rather_than_failing_it():
    """Inability to judge must not read as a verdict — the rule imbalance.is_displacement
    follows too."""
    setup = cross_reference(_row(key_levels=[300.0]), _ctx(atr=None), published_close=100.0)
    assert setup.target == 300.0


def test_a_candidate_below_the_reward_risk_floor_is_refused():
    """A trade risking more than it stands to make is not a setup. Left to scoring alone, a
    0.32-RR candidate still surfaced mid-list on live data."""
    thin = (Zone(block=_block(), structural_target=115.0),)   # entry 110, stop 100 -> RR 0.5
    outcome = cross_reference(_row(), _ctx(zones=thin), published_close=100.0)
    assert _reason(outcome) == "reward_risk_too_low"


def test_the_reward_risk_floor_is_configurable():
    thin = (Zone(block=_block(), structural_target=115.0),)
    setup = cross_reference(
        _row(), _ctx(zones=thin), published_close=100.0, min_reward_risk=0.1
    )
    assert isinstance(setup, Setup)
    assert setup.reward_risk == 0.5


def test_no_target_from_either_source_is_refused():
    no_structure = (Zone(block=_block(), structural_target=None),)
    outcome = cross_reference(_row(), _ctx(zones=no_structure), published_close=100.0)
    assert _reason(outcome) == "no_target"


def test_a_structural_target_behind_entry_is_not_used():
    behind = (Zone(block=_block(), structural_target=105.0),)
    outcome = cross_reference(_row(), _ctx(zones=behind), published_close=100.0)
    assert _reason(outcome) == "no_target"


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


# ── proximity ───────────────────────────────────────────────────────────────

def test_proximity_is_one_inside_the_zone():
    assert proximity_to(_block(), 105.0) == 1.0
    assert proximity_to(_block(), 110.0) == 1.0
    assert proximity_to(_block(), 100.0) == 1.0


def test_proximity_decays_with_distance_from_the_near_edge():
    near = proximity_to(_block(), 111.0)
    far = proximity_to(_block(), 118.0)
    assert 0.0 < far < near < 1.0


def test_proximity_is_zero_beyond_the_span():
    assert proximity_to(_block(), 400.0) == 0.0


def test_proximity_is_scale_free():
    """Expressed as a fraction of price so a 1%-away BTC zone and a 1%-away SOL zone score the
    same — an absolute distance would make every cheap asset look imminent."""
    big = _block(top=110000.0, bottom=100000.0, invalidation=90000.0)
    small = _block(top=1.10, bottom=1.00, invalidation=0.90)
    assert proximity_to(big, 111000.0) == pytest.approx(proximity_to(small, 1.11))


def test_an_approaching_zone_outscores_a_distant_one():
    """The finding that motivated proximity: on real data every candidate sat outside its zone
    with depth pinned at 0, so a zone 1% away and one 30% away scored identically."""
    approaching = cross_reference(_row(), _ctx(price=111.0), published_close=100.0)
    distant = cross_reference(_row(), _ctx(price=118.0), published_close=100.0)
    assert approaching.proximity > distant.proximity
    assert approaching.score > distant.score


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


def test_the_nearest_target_wins_among_disagreeing_views():
    """If they can't agree how far price goes, the smallest claim is the one to hold them to."""
    ctx = _ctx()
    optimist = cross_reference(_row(id="t1", key_levels=[160.0]), ctx, published_close=100.0)
    modest = cross_reference(_row(id="t2", key_levels=[130.0]), ctx, published_close=100.0)
    candidate = collapse([optimist, modest])[0]
    assert candidate.target == 130.0


def test_a_stated_target_beats_a_structural_one_even_when_further_away():
    """'If they call something, we listen.' Nearest-outright was tried and measured: structural
    targets are usually closer, so on live ETH data 7 accepted readings all lost to structure
    and the listen-to-them half of the design never fired."""
    ctx = _ctx()   # structural target 140, which is nearer to entry 110 than 150 is
    authored = cross_reference(_row(id="t1", key_levels=[150.0]), ctx, published_close=100.0)
    structural = cross_reference(_row(id="t2", key_levels=[]), ctx, published_close=100.0)
    assert structural.target_source == STRUCTURAL
    candidate = collapse([authored, structural])[0]
    assert candidate.target == 150.0
    assert candidate.target_source == STATED


def test_structure_is_still_used_when_nobody_stated_a_target():
    candidate = collapse(_setups_for(["Mayne", "Cred"]))[0]
    assert candidate.target_source == STRUCTURAL


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
    mixed = _setups_for(["Mayne"]) + [
        cross_reference(_row(published_at=None), _ctx(), published_close=100.0)
    ]
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


# ── shape ───────────────────────────────────────────────────────────────────

def test_outcomes_are_immutable():
    setup = cross_reference(_row(), _ctx(), published_close=100.0)
    rejected = cross_reference(_row(published_at=None), _ctx(), published_close=100.0)
    for outcome in (setup, rejected):
        try:
            outcome.asset = "ETH"
        except Exception:
            continue
        raise AssertionError(f"{type(outcome).__name__} must be immutable")


def test_a_rejection_still_identifies_the_thesis():
    """Rejections are categorized, never silently dropped — a corpus-wide tally of reasons is
    how you learn whether the gates are too tight."""
    rejected = cross_reference(_row(published_at=None), _ctx(), published_close=100.0)
    assert rejected.thesis_id == "t1"
    assert rejected.asset == "BTC"
    assert rejected.person == "TraderMayne"
