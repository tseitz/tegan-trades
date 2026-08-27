from datetime import date

import pytest
from core.dealing_range import DealingRange
from core.imbalance import Gap
from core.nearby import (
    ALL_KINDS,
    DAILY_ZONE,
    GAP,
    RANGE_EDGE,
    REACH,
    RESISTANCE,
    SUPPORT,
    WEEKLY_ZONE,
    levels_near,
)
from core.setups import DAILY, WEEKLY, Context, GapZone, Zone
from core.structure import (
    BEARISH,
    BULLISH,
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
    bos = Break(kind=kind, level=broken.price, swing=broken, origin=origin,
                date=date(2025, 1, 6), index=5)
    return OrderBlock(kind=kind, top=top, bottom=bottom, date=date(2025, 1, 5), index=4,
                      confirmed_at=date(2025, 1, 6), bos=bos, invalidation=invalidation)


def _gap(*, top, bottom, kind=BULLISH):
    return Gap(kind=kind, top=top, bottom=bottom, date=date(2025, 1, 7), index=6, middle_index=5)


def _ctx(price=140.0, **overrides):
    base = {
        "as_of": AS_OF, "price": price, "weekly_trend": UPTREND, "daily_trend": UPTREND,
        "dealing_range": DealingRange(low=80.0, high=200.0,
                                      low_swing=_swing(80.0, SWING_LOW),
                                      high_swing=_swing(200.0, SWING_HIGH),
                                      confirmed_at=date(2025, 1, 6)),
        "zones": (), "gaps": (), "atr": 5.0,
    }
    base.update(overrides)
    return Context(**base)


def _kinds(levels):
    return sorted({level.kind for level in levels})


# ── what counts as near ────────────────────────────────────────────────────


def test_a_level_beyond_reach_is_not_a_level_you_are_near():
    far = Zone(block=_block(top=1000.0, bottom=990.0), structural_target=None, timeframe=WEEKLY)
    assert levels_near(_ctx(price=140.0, zones=(far,))) == ()


def test_a_level_inside_reach_is_returned():
    close = Zone(block=_block(top=146.0, bottom=144.0), structural_target=None,
                 timeframe=WEEKLY)
    found = levels_near(_ctx(price=140.0, zones=(close,)))
    assert len(found) == 1
    assert found[0].kind == WEEKLY_ZONE


def test_price_inside_a_level_is_always_returned_however_wide_it_is():
    """A zone spanning half the chart is still the zone price is standing in. Filtering on
    distance-to-edge would drop exactly the levels that matter most."""
    huge = Zone(block=_block(top=400.0, bottom=20.0), structural_target=None, timeframe=WEEKLY)
    found = levels_near(_ctx(price=140.0, zones=(huge,)))
    assert len(found) == 1
    assert found[0].inside is True
    assert found[0].distance == 0.0


def test_levels_come_back_nearest_first():
    near = Zone(block=_block(top=142.0, bottom=141.0), structural_target=None, timeframe=WEEKLY)
    far = Zone(block=_block(top=146.0, bottom=145.0), structural_target=None, timeframe=WEEKLY)
    found = levels_near(_ctx(price=140.0, zones=(far, near)))
    assert [level.bottom for level in found] == [141.0, 145.0]


def test_reach_is_measured_as_a_fraction_of_price_not_an_absolute():
    """A 5-dollar gap is nothing on NVDA and everything on SOFI. An absolute band would make
    the section all mega-caps and no small ones, or the reverse."""
    zone = Zone(block=_block(top=104.0, bottom=103.0), structural_target=None,
                timeframe=WEEKLY)
    assert levels_near(_ctx(price=100.0, zones=(zone,)), reach=0.05)
    assert levels_near(_ctx(price=100.0, zones=(zone,)), reach=0.01) == ()


# ── which side of price ────────────────────────────────────────────────────


def test_a_level_below_price_is_support_and_above_is_resistance():
    """Position decides, not the block's own direction. A holder asks "what is under me and
    what is over me"; a bearish block sitting below price is still the thing price would land
    on, and calling it resistance would point them the wrong way."""
    below = Zone(block=_block(BEARISH, top=136.0, bottom=135.0), structural_target=None,
                 timeframe=WEEKLY)
    above = Zone(block=_block(BULLISH, top=145.0, bottom=144.0), structural_target=None,
                 timeframe=WEEKLY)
    found = {level.bottom: level.side for level in levels_near(_ctx(price=140.0,
                                                                   zones=(below, above)))}
    assert found[135.0] == SUPPORT
    assert found[144.0] == RESISTANCE


def test_when_price_is_inside_a_level_its_own_direction_decides():
    """There is no "under" or "over" to read. What the zone was built as is the only thing
    left saying which way it is expected to resolve — and it is what `core.review.locate`
    already reads, so the two cannot disagree about a zone price is standing in."""
    inside = Zone(block=_block(BULLISH, top=145.0, bottom=135.0), structural_target=None,
                  timeframe=WEEKLY)
    assert levels_near(_ctx(price=140.0, zones=(inside,)))[0].side == SUPPORT

    inside_bear = Zone(block=_block(BEARISH, top=145.0, bottom=135.0), structural_target=None,
                       timeframe=WEEKLY)
    assert levels_near(_ctx(price=140.0, zones=(inside_bear,)))[0].side == RESISTANCE


# ── the four kinds ─────────────────────────────────────────────────────────


def test_every_kind_is_found():
    ctx = _ctx(
        price=140.0,
        zones=(Zone(block=_block(top=142.0, bottom=141.0), structural_target=None,
                    timeframe=WEEKLY),
               Zone(block=_block(top=139.0, bottom=138.0), structural_target=None,
                    timeframe=DAILY)),
        gaps=(GapZone(gap=_gap(top=143.0, bottom=142.5), timeframe=WEEKLY),),
        dealing_range=DealingRange(low=137.0, high=143.0,
                                   low_swing=_swing(137.0, SWING_LOW),
                                   high_swing=_swing(143.0, SWING_HIGH),
                                   confirmed_at=date(2025, 1, 6)),
    )
    assert _kinds(levels_near(ctx)) == sorted([WEEKLY_ZONE, DAILY_ZONE, GAP, RANGE_EDGE])


def test_kinds_can_be_narrowed():
    """The knob that lets a long-horizon account drop daily noise without a code change."""
    ctx = _ctx(
        price=140.0,
        zones=(Zone(block=_block(top=142.0, bottom=141.0), structural_target=None,
                    timeframe=WEEKLY),
               Zone(block=_block(top=139.0, bottom=138.0), structural_target=None,
                    timeframe=DAILY)),
    )
    assert _kinds(levels_near(ctx, kinds=(WEEKLY_ZONE,))) == [WEEKLY_ZONE]
    assert levels_near(ctx, kinds=()) == ()


def test_a_range_edge_is_one_price_not_a_band():
    ctx = _ctx(price=140.0,
               dealing_range=DealingRange(low=137.0, high=200.0,
                                          low_swing=_swing(137.0, SWING_LOW),
                                          high_swing=_swing(200.0, SWING_HIGH),
                                          confirmed_at=date(2025, 1, 6)))
    edge = next(level for level in levels_near(ctx) if level.kind == RANGE_EDGE)
    assert edge.top == edge.bottom == 137.0
    assert edge.side == SUPPORT


def test_a_zone_carries_where_it_dies_and_a_gap_does_not():
    """Invalidation is a property of a break's origin swing. A void has no such level, and
    inventing one would put a number on the row that nothing measured."""
    ctx = _ctx(price=140.0,
               zones=(Zone(block=_block(top=142.0, bottom=141.0, invalidation=120.0),
                           structural_target=None, timeframe=WEEKLY),),
               gaps=(GapZone(gap=_gap(top=143.0, bottom=142.5), timeframe=WEEKLY),))
    by_kind = {level.kind: level for level in levels_near(ctx)}
    assert by_kind[WEEKLY_ZONE].invalidation == 120.0
    assert by_kind[GAP].invalidation is None


def test_no_dealing_range_yields_no_range_edges_rather_than_raising():
    assert levels_near(_ctx(price=140.0, dealing_range=None)) == ()


def test_the_timeframe_survives_onto_the_level():
    ctx = _ctx(price=140.0,
               zones=(Zone(block=_block(top=142.0, bottom=141.0), structural_target=None,
                           timeframe=WEEKLY),))
    assert levels_near(ctx)[0].timeframe == WEEKLY


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_declared_kind_is_one_the_scanner_actually_produces(kind):
    """A typo in ALL_KINDS would silently narrow the default to fewer sources than advertised,
    and nothing else here would fail."""
    ctx = _ctx(
        price=140.0,
        zones=(Zone(block=_block(top=142.0, bottom=141.0), structural_target=None,
                    timeframe=WEEKLY),
               Zone(block=_block(top=139.0, bottom=138.0), structural_target=None,
                    timeframe=DAILY)),
        gaps=(GapZone(gap=_gap(top=143.0, bottom=142.5), timeframe=WEEKLY),),
        dealing_range=DealingRange(low=137.0, high=143.0,
                                   low_swing=_swing(137.0, SWING_LOW),
                                   high_swing=_swing(143.0, SWING_HIGH),
                                   confirmed_at=date(2025, 1, 6)),
    )
    assert any(level.kind == kind for level in levels_near(ctx, kinds=(kind,)))


def test_reach_default_is_stated_once():
    assert REACH == 0.05
