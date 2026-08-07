from datetime import date

import pytest
from core.dealing_range import DealingRange
from core.exits import (
    EXTERNAL,
    EXTREME,
    INTERNAL,
    MATERIAL_R,
    OPPOSING,
    RANGE_BOUND,
    ExitLevel,
    entry_liquidity,
    exit_levels,
    is_sweep,
    select_target,
)
from core.levels import NEAREST, STATED
from core.setups import Zone
from core.structure import (
    BEARISH,
    BULLISH,
    SWING_HIGH,
    SWING_LOW,
    Break,
    OrderBlock,
    Swing,
)


def _swing(price, kind, *, index=0, day=1):
    when = date(2025, 1, day)
    return Swing(date=when, price=price, kind=kind, confirmed_at=when, index=index)


def _block(kind=BEARISH, *, top=130.0, bottom=120.0, invalidation=140.0):
    if kind == BULLISH:
        broken, origin_kind = _swing(200.0, SWING_HIGH), SWING_LOW
    else:
        broken, origin_kind = _swing(80.0, SWING_LOW), SWING_HIGH
    origin = _swing(invalidation, origin_kind, index=3, day=4)
    bos = Break(kind=kind, level=broken.price, swing=broken, origin=origin,
                date=date(2025, 1, 6), index=5)
    return OrderBlock(kind=kind, top=top, bottom=bottom, date=date(2025, 1, 5), index=4,
                      confirmed_at=date(2025, 1, 6), bos=bos, invalidation=invalidation)


def _zone(kind=BEARISH, **kwargs):
    return Zone(block=_block(kind, **kwargs), structural_target=None)


def _range(low=80.0, high=200.0):
    return DealingRange(
        low=low, high=high,
        low_swing=_swing(low, SWING_LOW), high_swing=_swing(high, SWING_HIGH),
        confirmed_at=date(2025, 1, 6),
    )


def _levels(**overrides):
    """A long entered at 100 risking 10, with the extreme at 140 and the range top at 200."""
    base = {
        "entry": 100.0, "stop": 90.0, "sign": 1,
        "zones": (), "dealing_range": _range(), "structural_target": 140.0,
        "authored": None, "authored_source": "",
    }
    base.update(overrides)
    return exit_levels(**base)


def _prices(levels):
    return [level.price for level in levels]


def _kinds(levels):
    return [level.kind for level in levels]


# ── ordering: nearest first, because the nearest is the one price meets ──────────────────

def test_levels_come_back_nearest_first():
    found = _levels(zones=(_zone(bottom=120.0),))
    assert _prices(found) == [120.0, 140.0, 200.0]
    assert _kinds(found) == [OPPOSING, EXTREME, RANGE_BOUND]


def test_a_short_orders_by_distance_too_rather_than_by_price():
    found = _levels(
        entry=100.0, stop=110.0, sign=-1, structural_target=60.0,
        zones=(_zone(BULLISH, top=80.0, bottom=70.0, invalidation=60.0),),
        dealing_range=_range(low=20.0, high=140.0),
    )
    assert _prices(found) == [80.0, 60.0, 20.0]
    assert _kinds(found) == [OPPOSING, EXTREME, RANGE_BOUND]


def test_reward_risk_is_quoted_per_level_from_entry():
    found = _levels(zones=(_zone(bottom=120.0),))
    assert [level.reward_risk for level in found] == [2.0, 4.0, 10.0]


# ── the obstruction rule ────────────────────────────────────────────────────────────────

def test_the_near_edge_of_an_opposing_block_is_where_a_long_meets_supply():
    """Not the far edge: price runs *into* the block, so the bottom is what it reaches first."""
    found = _levels(zones=(_zone(bottom=120.0, top=130.0),))
    assert found[0] == ExitLevel(price=120.0, kind=OPPOSING, reward_risk=2.0)


def test_a_same_side_block_is_not_an_obstruction():
    """A bullish block above a long is support it already broke, not supply in the way."""
    found = _levels(zones=(_zone(BULLISH, bottom=120.0, top=130.0, invalidation=110.0),))
    assert OPPOSING not in _kinds(found)


def test_an_opposing_block_inside_the_entrys_own_congestion_is_ignored():
    """A bearish block a fraction of an R above a bullish zone is the other side of the same
    congestion. Counting those reported 48 of 49 live candidates obstructed — see
    scripts/probe_target_reachability.py."""
    just_above = 100.0 + (MATERIAL_R * 10.0) - 0.01
    found = _levels(zones=(_zone(bottom=just_above, top=just_above + 5),))
    assert OPPOSING not in _kinds(found)


def test_an_opposing_block_exactly_at_the_materiality_floor_counts():
    found = _levels(zones=(_zone(bottom=100.0 + MATERIAL_R * 10.0, top=125.0),))
    assert _kinds(found)[0] == OPPOSING


def test_an_opposing_block_behind_the_entry_is_not_a_target():
    found = _levels(zones=(_zone(bottom=70.0, top=80.0),))
    assert OPPOSING not in _kinds(found)


# ── authored targets: believed only when they are the nearest thing ─────────────────────

def test_an_authored_target_nearer_than_every_structural_level_wins():
    found = _levels(authored=115.0, authored_source=STATED,
                    zones=(_zone(bottom=120.0),))
    assert _prices(found)[0] == 115.0
    assert _kinds(found)[0] == STATED


def test_an_authored_target_beyond_the_nearest_structural_level_is_dropped():
    """The LINK case: a stated 11.70 against a structural 8.907 and weekly supply at 9.09."""
    found = _levels(authored=180.0, authored_source=NEAREST,
                    zones=(_zone(bottom=120.0),))
    assert 180.0 not in _prices(found)
    assert NEAREST not in _kinds(found)


def test_an_authored_target_is_kept_when_structure_offers_nothing():
    found = _levels(authored=180.0, authored_source=STATED,
                    structural_target=None, dealing_range=None)
    assert _prices(found) == [180.0]


def test_an_authored_target_behind_the_entry_is_dropped():
    found = _levels(authored=95.0, authored_source=STATED)
    assert 95.0 not in _prices(found)


# ── the range boundary ends the trade ───────────────────────────────────────────────────

def test_levels_past_the_range_boundary_are_dropped():
    """Live LINK listed five runners between +61% and +178% — old weekly supply stacked above a
    range topping out at +36%. Past the boundary the range has broken and the setup is a
    different one."""
    found = _levels(
        dealing_range=_range(high=200.0),
        zones=(_zone(bottom=150.0), _zone(bottom=260.0), _zone(bottom=400.0)),
    )
    assert _prices(found) == [140.0, 150.0, 200.0]


def test_the_range_boundary_itself_survives_truncation():
    found = _levels(dealing_range=_range(high=200.0), structural_target=None)
    assert _prices(found) == [200.0]


def test_a_short_truncates_at_the_range_low():
    found = _levels(
        entry=100.0, stop=110.0, sign=-1, structural_target=60.0,
        zones=(_zone(BULLISH, top=40.0, bottom=30.0, invalidation=20.0),),
        dealing_range=_range(low=50.0, high=140.0),
    )
    assert _prices(found) == [60.0, 50.0]


def test_an_extreme_beyond_the_boundary_defers_to_the_range():
    """Only reachable while the range lags the swing that would redraw it. Deferring to the
    range is the conservative side of that disagreement."""
    found = _levels(structural_target=260.0, dealing_range=_range(high=200.0))
    assert _prices(found) == [200.0]


def test_without_a_range_nothing_is_truncated():
    found = _levels(dealing_range=None, zones=(_zone(bottom=400.0),))
    assert _prices(found) == [140.0, 400.0]


# ── degenerate and empty inputs ─────────────────────────────────────────────────────────

def test_nothing_beyond_entry_yields_no_levels():
    found = _levels(structural_target=None, dealing_range=_range(high=95.0))
    assert found == ()


def test_a_structural_target_behind_entry_is_not_offered():
    found = _levels(structural_target=80.0, dealing_range=None)
    assert found == ()


def test_a_range_boundary_at_the_entry_is_not_a_target():
    found = _levels(structural_target=None, dealing_range=_range(high=100.0))
    assert found == ()


def test_levels_landing_on_one_price_collapse_to_the_most_specific_reason():
    """An opposing block whose near edge is the range top states *why* price stops there."""
    found = _levels(structural_target=200.0, zones=(_zone(bottom=200.0, top=210.0),),
                    dealing_range=_range(high=200.0))
    assert _prices(found) == [200.0]
    assert _kinds(found) == [OPPOSING]


def test_a_zero_risk_trade_is_refused_rather_than_dividing_by_zero():
    with pytest.raises(ValueError):
        _levels(entry=100.0, stop=100.0)


# ── entry liquidity: which side of the boundary the order rests on ──────────────────────

def test_an_entry_inside_the_range_is_internal():
    found = entry_liquidity(entry=100.0, dealing_range=_range(low=80.0, high=200.0), sign=1)
    assert found.kind == INTERNAL
    assert found.widths < 0


def test_an_entry_below_the_range_low_is_external_for_a_long():
    """120 wide, entry 12 below the low — a tenth of a width, which is a sweep."""
    found = entry_liquidity(entry=68.0, dealing_range=_range(low=80.0, high=200.0), sign=1)
    assert found.kind == EXTERNAL
    assert found.widths == pytest.approx(0.1)


def test_an_entry_above_the_range_high_is_external_for_a_short():
    found = entry_liquidity(entry=212.0, dealing_range=_range(low=80.0, high=200.0), sign=-1)
    assert found.kind == EXTERNAL
    assert found.widths == pytest.approx(0.1)


def test_an_entry_exactly_on_the_boundary_is_external_at_zero_widths():
    """ENA measured 0.00 widths outside — sitting exactly on the boundary is the purest sweep,
    not an interior entry, so it must not fall through to the internal branch."""
    found = entry_liquidity(entry=80.0, dealing_range=_range(low=80.0, high=200.0), sign=1)
    assert found.kind == EXTERNAL
    assert found.widths == 0.0


def test_a_sweep_is_a_poke_past_the_boundary_and_a_regime_change_is_not():
    """The live split at ``MAX_SWEEP_WIDTHS``: DASH at 0.09 is a sweep, SOL at 3.92 — a short
    whose entry sat 3.92 widths above a range price was trading inside — is not."""
    span = _range(low=80.0, high=200.0)
    sweep = entry_liquidity(entry=80.0 - (0.09 * 120), dealing_range=span, sign=1)
    regime = entry_liquidity(entry=80.0 - (3.92 * 120), dealing_range=span, sign=1)
    assert is_sweep(sweep) is True
    assert is_sweep(regime) is False


def test_an_internal_entry_is_never_a_sweep():
    inside = entry_liquidity(entry=100.0, dealing_range=_range(), sign=1)
    assert is_sweep(inside) is False


# ── target selection: the destination depends on where you entered ──────────────────────

def test_an_internal_entry_targets_the_range_boundary_and_the_rest_are_partials():
    """TraderMayne, ``NSTmMdnQg7Y``: "An order block itself is internal range liquidity, so
    generally we're going to be targeting external range liquidity." The LNGX shape — the
    nearest opposing block was being quoted as the target at 1.05R while the boundary sat at
    3.55R with four blocks in between."""
    levels = _levels(zones=(_zone(bottom=120.0), _zone(bottom=160.0)))
    assert _prices(levels) == [120.0, 140.0, 160.0, 200.0]
    inside = entry_liquidity(entry=100.0, dealing_range=_range(), sign=1)

    target, ladder = select_target(levels, inside)
    assert target.price == 200.0
    assert target.kind == RANGE_BOUND
    assert _prices(ladder) == [120.0, 140.0, 160.0]


def test_an_external_sweep_entry_targets_the_nearest_internal_level():
    """The inverse leg of the same rule: "if you're entering based on external, ultimately
    you're targeting internal"."""
    levels = _levels(entry=76.0, zones=(_zone(bottom=120.0),))
    outside = entry_liquidity(entry=76.0, dealing_range=_range(), sign=1)

    target, ladder = select_target(levels, outside)
    assert target.price == levels[0].price
    assert _prices(ladder) == _prices(levels[1:])


def test_an_internal_entry_with_no_boundary_rung_has_no_target():
    """Mayne's disqualifier #3 — "if there's no clear liquidity, there is no trade". The caller
    refuses on this rather than falling back to an internal level."""
    levels = _levels(dealing_range=None, zones=(_zone(bottom=120.0),))
    assert RANGE_BOUND not in _kinds(levels)
    inside = entry_liquidity(entry=100.0, dealing_range=_range(), sign=1)

    assert select_target(levels, inside) is None


def test_selecting_from_no_levels_at_all_yields_nothing():
    inside = entry_liquidity(entry=100.0, dealing_range=_range(), sign=1)
    assert select_target((), inside) is None


def test_a_boundary_that_is_already_the_nearest_level_leaves_an_empty_ladder():
    """14 of 36 live internal entries already targeted the boundary — that is a no-op, not a
    special case, and it must not manufacture a partial rung out of the target itself."""
    levels = _levels(structural_target=None)
    assert _prices(levels) == [200.0]
    inside = entry_liquidity(entry=100.0, dealing_range=_range(), sign=1)

    target, ladder = select_target(levels, inside)
    assert target.price == 200.0
    assert ladder == ()
