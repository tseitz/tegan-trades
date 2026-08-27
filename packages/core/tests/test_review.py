from datetime import date
from types import SimpleNamespace

import pytest
from core.dealing_range import DealingRange
from core.review import (
    ABOVE_RANGE,
    ADD,
    AT_RESISTANCE,
    AT_SUPPORT,
    BEARISH_ROSTER,
    BELOW_RANGE,
    BULLISH_ROSTER,
    HOLD,
    MID,
    MIN_VOICES,
    MIXED,
    NO_READ,
    NO_VIEW,
    PREMIUM_EDGE,
    SILENT,
    TRIM,
    UNREADABLE,
    WATCH,
    Holding,
    locate,
    review,
    roster_lean,
    verdict_for,
)
from core.setups import WEEKLY, Context, Zone
from core.structure import (
    BEARISH,
    BULLISH,
    RANGING,
    SWING_HIGH,
    SWING_LOW,
    UPTREND,
    Break,
    OrderBlock,
    Swing,
)

AS_OF = date(2025, 1, 10)


def _swing(price, kind, *, index=0, day=1):
    when = date(2025, 1, day)
    return Swing(date=when, price=price, kind=kind, confirmed_at=when, index=index)


def _block(kind=BULLISH, *, top=110.0, bottom=100.0, invalidation=90.0):
    if kind == BULLISH:
        broken, origin_kind = _swing(120.0, SWING_HIGH), SWING_LOW
    else:
        broken, origin_kind = _swing(80.0, SWING_LOW), SWING_HIGH
    origin = None if invalidation is None else _swing(invalidation, origin_kind, index=3, day=4)
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
    """Price at 140 — dead centre of an 80-200 range, and nowhere near any zone."""
    base = {
        "as_of": AS_OF,
        "price": 140.0,
        "weekly_trend": UPTREND,
        "daily_trend": UPTREND,
        "dealing_range": _range(),
        "zones": (),
        "atr": 5.0,
    }
    base.update(overrides)
    return Context(**base)


def _folded(person, lean, *, published_at="2025-01-01"):
    return SimpleNamespace(
        person_canonical=person,
        current=SimpleNamespace(
            lean=lean, source=SimpleNamespace(published_at=published_at),
        ),
    )


# ── the roster side ────────────────────────────────────────────────────────


def test_no_stances_is_silent_not_neutral():
    """Silence and agreement-on-neutral are opposite facts. Collapsing them would let an
    asset the roster has never mentioned read as an asset it has considered and shrugged at."""
    view = roster_lean([], as_of=AS_OF)
    assert view.lean == SILENT
    assert view.people == 0
    assert view.newest is None


def test_majority_decides_the_lean():
    view = roster_lean(
        [_folded("A", "bullish"), _folded("B", "bullish"), _folded("C", "bearish")],
        as_of=AS_OF,
    )
    assert view.lean == BULLISH_ROSTER
    assert (view.bulls, view.bears) == (2, 1)
    assert view.voices == ("A", "B")


def test_a_tie_is_mixed():
    view = roster_lean([_folded("A", "bullish"), _folded("B", "bearish")], as_of=AS_OF)
    assert view.lean == MIXED


def test_only_neutral_and_uncertain_is_silent():
    """Neither lean says which way to act, so a wall of them is no view — but the people
    are still counted, or the renderer cannot tell 'nobody spoke' from 'nobody committed'."""
    view = roster_lean([_folded("A", "neutral"), _folded("B", "uncertain")], as_of=AS_OF)
    assert view.lean == SILENT
    assert view.people == 2


def test_one_person_counted_once_across_horizons():
    """Folding is per (person, asset, horizon), so one voice can arrive twice. Counting
    statements instead of people would let a single prolific feed outvote the roster."""
    view = roster_lean(
        [_folded("A", "bullish"), _folded("A", "bullish"), _folded("B", "bearish")],
        as_of=AS_OF,
    )
    assert (view.bulls, view.bears) == (1, 1)
    assert view.lean == MIXED


def test_age_is_measured_from_the_newest_statement():
    view = roster_lean(
        [_folded("A", "bullish", published_at="2024-12-01"),
         _folded("B", "bullish", published_at="2025-01-05")],
        as_of=AS_OF,
    )
    assert view.newest == date(2025, 1, 5)
    assert view.age_days == 5


def test_undated_statements_still_count_toward_the_split():
    view = roster_lean([_folded("A", "bullish", published_at=None)], as_of=AS_OF)
    assert view.lean == BULLISH_ROSTER
    assert view.newest is None
    assert view.age_days is None


# ── the chart side ─────────────────────────────────────────────────────────


def test_arrived_weekly_bullish_zone_is_support():
    ctx = _ctx(price=108.0, zones=(Zone(block=_block(BULLISH), structural_target=None,
                                        timeframe=WEEKLY),))
    where = locate(ctx)
    assert where.where == AT_SUPPORT
    assert where.zone is not None


def test_arrived_weekly_bearish_zone_is_resistance():
    ctx = _ctx(price=108.0, zones=(Zone(block=_block(BEARISH), structural_target=None,
                                        timeframe=WEEKLY),))
    assert locate(ctx).where == AT_RESISTANCE


def test_a_daily_zone_does_not_move_a_weekly_reading():
    """The grid is a higher-timeframe call. A daily block price happens to sit in says
    nothing about whether the weekly is cheap, and letting it vote would make a long-term
    holding review react to noise it is explicitly meant to ignore."""
    ctx = _ctx(price=108.0, zones=(Zone(block=_block(BULLISH), structural_target=None),))
    assert locate(ctx).zone is None


def test_range_position_decides_when_no_zone_is_reached():
    assert locate(_ctx(price=140.0)).where == MID
    assert locate(_ctx(price=90.0)).where == AT_SUPPORT
    assert locate(_ctx(price=190.0)).where == AT_RESISTANCE


def test_the_edge_band_is_the_outer_quarter_not_merely_the_wrong_half():
    """`DealingRange.zone_at` splits at 0.5 because that is the manifesto's *permission*
    gate. 'At resistance' is a stronger claim than 'not cheap', so it needs the outer band —
    otherwise every holding one tick above midpoint reads as a place to sell."""
    just_above_mid = 80.0 + (200.0 - 80.0) * 0.55
    assert locate(_ctx(price=just_above_mid)).where == MID
    at_edge = 80.0 + (200.0 - 80.0) * PREMIUM_EDGE
    assert locate(_ctx(price=at_edge)).where == AT_RESISTANCE


def test_price_outside_the_range_says_which_side():
    """`position_at` refuses outside the range, and clamping it would report a price below
    the low as a perfect discount. But which side price left on is a fact, not a gap — and
    the commonest one: measured over the cached corpus, 81 of 105 unreadable assets were
    above their range, which is simply what a holding in an uptrend looks like."""
    assert locate(_ctx(price=250.0)).where == ABOVE_RANGE
    assert locate(_ctx(price=50.0)).where == BELOW_RANGE


def test_a_breakout_is_not_reported_as_a_range_position():
    """There is no honest percentage to print once price has left the range. The renderer
    must not be handed one, or a breakout reads as a location inside the leg."""
    assert locate(_ctx(price=250.0)).position is None
    assert locate(_ctx(price=250.0)).basis == "outside"


def test_no_dealing_range_at_all_is_still_unreadable():
    """Distinct from a breakout. There the range is stale; here there was never enough
    structure to draw one, and no side to be on."""
    assert locate(_ctx(dealing_range=None)).where == UNREADABLE


# ── the grid ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("lean", "where", "expected"), [
    (BULLISH_ROSTER, AT_SUPPORT, ADD),
    (BULLISH_ROSTER, MID, HOLD),
    (BULLISH_ROSTER, AT_RESISTANCE, HOLD),
    (BEARISH_ROSTER, AT_SUPPORT, HOLD),
    (BEARISH_ROSTER, MID, WATCH),
    (BEARISH_ROSTER, AT_RESISTANCE, TRIM),
    (MIXED, AT_SUPPORT, WATCH),
    (MIXED, AT_RESISTANCE, WATCH),
    # Above the range: nothing overhead, and the old range is stale. A roster turning bearish
    # while you sit on an extended gain is the strongest trim there is; a roster still bullish
    # is being proved right, so it holds.
    (BULLISH_ROSTER, ABOVE_RANGE, HOLD),
    (BEARISH_ROSTER, ABOVE_RANGE, TRIM),
    (MIXED, ABOVE_RANGE, WATCH),
    # Below the range: the structure that held it is gone. Never ADD here even on a bullish
    # roster — buying more of a position whose range just broke is averaging into a thesis
    # the chart is actively arguing with.
    (BULLISH_ROSTER, BELOW_RANGE, WATCH),
    (BEARISH_ROSTER, BELOW_RANGE, WATCH),
    (MIXED, BELOW_RANGE, WATCH),
])
def test_the_grid(lean, where, expected):
    assert verdict_for(lean, where) == expected


def test_one_voice_is_marked_thin():
    """Not silenced — the view is real and still counted. But one person calling a sell on a
    position you hold is a different quality of evidence from three, and the grid alone
    cannot tell them apart."""
    solo = roster_lean([_folded("A", "bearish")], as_of=AS_OF)
    assert solo.lean == BEARISH_ROSTER
    assert solo.thin is True

    pair = roster_lean([_folded("A", "bearish"), _folded("B", "bearish")], as_of=AS_OF)
    assert pair.thin is False


def test_a_thin_roster_downgrades_the_actions_only():
    """ADD and TRIM ask you to move money, so they need more than one voice. HOLD and WATCH
    already ask for nothing, and downgrading them would only add noise."""
    assert verdict_for(BEARISH_ROSTER, AT_RESISTANCE, thin=True) == WATCH
    assert verdict_for(BULLISH_ROSTER, AT_SUPPORT, thin=True) == WATCH
    assert verdict_for(BULLISH_ROSTER, MID, thin=True) == HOLD
    assert verdict_for(BEARISH_ROSTER, MID, thin=True) == WATCH


def test_the_threshold_is_people_not_statements():
    """One prolific feed restating a call is still one voice. Counting statements would let
    it clear a bar that exists precisely to require a second opinion."""
    view = roster_lean([_folded("A", "bullish"), _folded("A", "bullish")], as_of=AS_OF)
    assert view.thin is True
    assert MIN_VOICES == 2


def test_review_applies_the_thin_rule_end_to_end():
    reading = review(
        Holding(ticker="CRM", shares=1.0, cost=None), _ctx(price=190.0),
        folded=[_folded("A", "bearish")], as_of=AS_OF,
    )
    assert reading.location.where == AT_RESISTANCE
    assert reading.verdict == WATCH


def test_silence_beats_an_unreadable_chart():
    """Both are refusals, and the roster one is the more useful thing to print: with no
    view there is nothing to act on however clean the chart is."""
    assert verdict_for(SILENT, UNREADABLE) == NO_VIEW
    assert verdict_for(SILENT, AT_RESISTANCE) == NO_VIEW


def test_an_unreadable_chart_refuses_rather_than_defaulting_to_hold():
    """HOLD is advice. A missing weekly range is a gap in the evidence, and dressing one as
    the other is how a broken price feed comes out looking like a considered decision."""
    assert verdict_for(BEARISH_ROSTER, UNREADABLE) == NO_READ


# ── the whole reading ──────────────────────────────────────────────────────


def test_review_pairs_the_two_sides():
    holding = Holding(ticker="BTC", shares=2.0, cost=100.0)
    reading = review(
        holding, _ctx(price=190.0),
        folded=[_folded("A", "bearish"), _folded("B", "bearish")], as_of=AS_OF,
    )
    assert reading.verdict == TRIM
    assert reading.location.where == AT_RESISTANCE
    assert reading.roster.lean == BEARISH_ROSTER
    assert reading.price == 190.0


def test_review_without_a_context_says_so_rather_than_guessing():
    holding = Holding(ticker="NOPE", shares=1.0, cost=None)
    reading = review(holding, None, folded=[_folded("A", "bullish")], as_of=AS_OF)
    assert reading.verdict == NO_READ
    assert reading.price is None


def test_market_value_and_pnl_need_both_halves():
    priced = review(Holding(ticker="BTC", shares=2.0, cost=100.0), _ctx(price=140.0),
                    folded=[], as_of=AS_OF)
    assert priced.market_value == 280.0
    assert priced.pnl == 80.0

    uncosted = review(Holding(ticker="BTC", shares=2.0, cost=None), _ctx(price=140.0),
                      folded=[], as_of=AS_OF)
    assert uncosted.market_value == 280.0
    assert uncosted.pnl is None


def test_a_ranging_weekly_still_produces_a_reading():
    """Trend is reported, not gated on. `setups` refuses an unaligned weekly because it is
    opening a new trade; this reviews one already open, and 'the weekly went sideways' is
    not a reason to stop having an opinion about a position you hold."""
    reading = review(
        Holding(ticker="BTC", shares=1.0, cost=None),
        _ctx(price=90.0, weekly_trend=RANGING),
        folded=[_folded("A", "bullish"), _folded("B", "bullish")], as_of=AS_OF,
    )
    assert reading.verdict == ADD
    assert reading.weekly_trend == RANGING
