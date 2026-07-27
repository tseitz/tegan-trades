from datetime import date, timedelta
from types import SimpleNamespace

from core.structure import (
    BEARISH,
    BULLISH,
    DOWNTREND,
    RANGING,
    SWING_WIDTH,
    UPTREND,
    UPTREND_FAILED_BREAKOUT,
    Break,
    Swing,
    _invalidation_for,
    breaks,
    confirmed_by,
    invalidated_on,
    order_blocks,
    position_in_range,
    swings,
    trend_state,
)

START = date(2025, 1, 1)


def _bars(highs_lows, *, start: date = START):
    """Consecutive daily bars from (high, low) pairs.

    open/close default to the midpoint — swing and trend detection read only high and low,
    and a midpoint close keeps these fixtures usable where candle direction is irrelevant.
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


def _candles(specs, *, start: date = START):
    """Consecutive daily bars from explicit (open, high, low, close) tuples.

    Order-block detection turns on candle *direction*, so those fixtures must state open and
    close rather than inherit a midpoint.
    """
    return tuple(
        SimpleNamespace(
            date=start + timedelta(days=i),
            open=o, high=h, low=lo, close=c,
        )
        for i, (o, h, lo, c) in enumerate(specs)
    )


def _highs(found):
    return tuple(s for s in found if s.kind == "high")


def _lows(found):
    return tuple(s for s in found if s.kind == "low")


# ── swings: basic detection ─────────────────────────────────────────────────

def test_finds_a_swing_high():
    bars = _bars([(10, 1), (11, 1), (12, 1), (20, 1), (12, 1), (11, 1), (10, 1)])
    found = _highs(swings(bars))
    assert len(found) == 1
    assert found[0].price == 20
    assert found[0].date == date(2025, 1, 4)


def test_finds_a_swing_low():
    bars = _bars([(20, 18), (15, 13), (10, 8), (5, 3), (10, 8), (15, 13), (20, 18)])
    found = _lows(swings(bars))
    assert len(found) == 1
    assert found[0].price == 3
    assert found[0].date == date(2025, 1, 4)


def test_swings_are_ordered_by_date():
    bars = _bars([
        (10, 9), (20, 8), (10, 7), (11, 2), (12, 8), (25, 9), (12, 8), (11, 7), (10, 6),
    ])
    found = swings(bars)
    assert [s.date for s in found] == sorted(s.date for s in found)


# ── swings: the no-look-ahead invariant ─────────────────────────────────────
#
# A swing needs `width` bars on BOTH sides to exist, so it is not merely unconfirmed on the
# bar that forms it — it is unknowable. These tests are the reason this module reports
# `confirmed_at` at all: without it a caller can read a swing into a date on which nobody
# could have known it, which silently inflates every backtest downstream.

def test_the_last_bars_can_never_be_swings():
    """The final bar has the highest high in the series, and must still not be a swing."""
    bars = _bars([(10, 1), (11, 1), (12, 1), (13, 1), (20, 1)])
    assert _highs(swings(bars)) == ()


def test_confirmed_at_is_when_the_swing_became_knowable_not_when_it_formed():
    bars = _bars([(10, 1), (11, 1), (12, 1), (20, 1), (12, 1), (11, 1), (10, 1)])
    swing = _highs(swings(bars))[0]
    assert swing.date == date(2025, 1, 4)          # the bar that formed it
    assert swing.confirmed_at == date(2025, 1, 6)  # width=2 bars later


def test_confirmed_by_filters_out_swings_not_yet_knowable():
    bars = _bars([(10, 1), (11, 1), (12, 1), (20, 1), (12, 1), (11, 1), (10, 1)])
    found = swings(bars)
    assert confirmed_by(found, date(2025, 1, 5)) == ()
    assert len(confirmed_by(found, date(2025, 1, 6))) == 1


# ── swings: ties, width, degenerate input ───────────────────────────────────

def test_a_plateau_produces_no_swing():
    """Equal highs mean neither bar dominates its window. Comparison is strict so a flat
    top yields no swing rather than two adjacent ones."""
    bars = _bars([(5, 1), (10, 1), (20, 1), (20, 1), (10, 1), (5, 1)])
    assert _highs(swings(bars)) == ()


def test_width_is_configurable():
    bars = _bars([(10, 1), (20, 1), (10, 1)])
    assert _highs(swings(bars, width=1))[0].price == 20
    assert _highs(swings(bars, width=2)) == ()


def test_series_shorter_than_the_window_yields_nothing():
    bars = _bars([(10, 1), (20, 1), (10, 1), (11, 1)])
    assert swings(bars, width=SWING_WIDTH) == ()


def test_empty_input():
    assert swings(()) == ()


def test_swing_is_frozen():
    swing = Swing(date=START, price=1.0, kind="high", confirmed_at=START, index=0)
    try:
        swing.price = 2.0
    except Exception:
        return
    raise AssertionError("Swing must be immutable")


# ── position_in_range ───────────────────────────────────────────────────────

def test_position_in_range_spans_zero_to_one():
    assert position_in_range(10, 20, 10) == 0.0
    assert position_in_range(10, 20, 20) == 1.0
    assert position_in_range(10, 20, 15) == 0.5


def test_position_in_range_clamps_outside_the_range():
    assert position_in_range(10, 20, 5) == 0.0
    assert position_in_range(10, 20, 25) == 1.0


def test_position_in_range_refuses_a_degenerate_range():
    """A zero-width range has no interior, so there is no honest answer — and returning 0.5
    would put a fabricated 'midpoint' into a score."""
    assert position_in_range(10, 10, 10) is None
    assert position_in_range(20, 10, 15) is None


# ── trend state ─────────────────────────────────────────────────────────────

# Each fixture carries THREE swings of each kind, because the comparison reaches
# `TREND_DEPTH` swings back. Swing sequences, for reading the assertions below:
#   UPTREND_BARS    highs 16 → 18 → 20   lows  4 →  6 →  8
#   DOWNTREND_BARS  highs 34 → 31 → 28   lows 17 → 14 → 11
UPTREND_BARS = _bars([
    (10, 5), (11, 6), (9, 4), (11, 6), (13, 8), (16, 9), (13, 8), (12, 7), (11, 6), (13, 8),
    (15, 10), (18, 11), (15, 10), (14, 9), (13, 8), (15, 10), (17, 12), (20, 13), (17, 12),
    (16, 11),
])

DOWNTREND_BARS = _bars([
    (30, 25), (29, 24), (34, 29), (29, 24), (27, 22), (24, 17), (26, 20), (27, 22), (31, 26),
    (26, 21), (24, 19), (21, 14), (23, 17), (24, 19), (28, 23), (23, 18), (21, 16), (18, 11),
    (20, 14), (21, 16),
])

# Two swings of each kind — one short of the anchor.
SHALLOW_BARS = _bars([
    (10, 5), (11, 6), (15, 7), (12, 6), (11, 4), (13, 6), (14, 8),
    (18, 10), (15, 9), (14, 8), (16, 10), (17, 11), (15, 10),
])


def test_uptrend_is_higher_highs_and_higher_lows():
    assert trend_state(UPTREND_BARS) == UPTREND


def test_downtrend_is_lower_highs_and_lower_lows():
    assert trend_state(DOWNTREND_BARS) == DOWNTREND


def test_disagreeing_highs_and_lows_are_ranging_not_a_trend():
    """Higher highs with lower lows is an expanding range. Calling it a trend would hand the
    cross-ref engine a directional bias the chart does not support."""
    expanding = _bars([
        (10, 9), (11, 10), (9, 8), (11, 10), (13, 11), (16, 12), (13, 10), (12, 9), (11, 6),
        (13, 9), (15, 11), (18, 13), (15, 11), (14, 9), (13, 4), (15, 9), (17, 12), (20, 14),
        (17, 12), (16, 11),
    ])
    assert trend_state(expanding) == RANGING


def test_a_bounce_inside_a_larger_decline_is_still_a_downtrend():
    """The defect that motivated the anchored comparison.

    Swing highs run 104 → 90 → 93 and lows 60 → 40 → 41: the newest leg of each is *up*, so
    comparing only the last two swings reports ``uptrend`` on a chart that has lost a third of
    its range. Reaching two swings back spans the bounce and reads the structure containing it.

    Taken from ETH weekly on 2026-07-25, where the real numbers were +3.4% and +0.3% against a
    50% decline — and the resulting ``uptrend`` let a long through the direction gate.
    """
    bounce = _bars([
        (100, 70), (99, 69), (104, 74), (98, 68), (96, 66), (92, 60), (86, 64), (88, 66),
        (90, 70), (87, 63), (85, 61), (82, 40), (84, 44), (86, 48), (93, 52), (88, 46),
        (86, 44), (83, 41), (85, 45), (87, 47),
    ])
    assert trend_state(bounce) == DOWNTREND


def test_moves_under_the_noise_floor_are_not_a_trend():
    """Swing highs 100.8 → 100.9 → 101.0 and lows 90.0 → 90.1 → 90.2 — every leg rises, and
    every rise is a rounding error. Direction has to clear the floor to count, or noise reads
    as structure: 12% of decisive verdicts across the cached corpus rested on a sub-1% move.
    """
    flat = _bars([
        (100.6, 90.6), (100.5, 90.5), (100.8, 90.8), (100.4, 90.4), (100.3, 90.3),
        (100.2, 90.0), (100.3, 90.15), (100.5, 90.25), (100.9, 90.5), (100.45, 90.35),
        (100.35, 90.3), (100.25, 90.1), (100.3, 90.2), (100.5, 90.3), (101.0, 90.6),
        (100.5, 90.4), (100.4, 90.35), (100.3, 90.2), (100.4, 90.3), (100.5, 90.4),
    ])
    assert trend_state(flat) == RANGING


def test_too_few_swings_to_reach_the_anchor_is_ranging():
    """Two swings of each kind cannot support a depth-2 comparison. Abstaining is the point —
    thin series are common (TLT had three weekly swing highs across two years), and answering
    from whatever exists would put a verdict on the whole of a short history."""
    assert trend_state(SHALLOW_BARS) == RANGING


def test_trend_depth_and_noise_floor_are_tunable():
    """Both knobs are parameters, not constants baked into the comparison — the floor in
    particular is marked TUNE and will move once decisions accumulate."""
    assert trend_state(SHALLOW_BARS, depth=1) == UPTREND
    assert trend_state(UPTREND_BARS, noise_floor=10.0) == RANGING


def test_a_wick_above_the_last_swing_high_without_a_close_is_a_failed_breakout():
    bars = UPTREND_BARS + _bars([(21, 12)], start=START + timedelta(days=20))
    assert trend_state(bars) == UPTREND_FAILED_BREAKOUT


def test_trend_state_respects_as_of():
    """Asking what the trend was earlier must not use swings confirmed later."""
    assert trend_state(UPTREND_BARS, as_of=date(2025, 1, 3)) == RANGING


# ── break of structure ──────────────────────────────────────────────────────
#
# Fixture: swing high 16 at index 2, swing low 9 at index 6, then a rally whose close at
# index 9 (17) takes out the swing high. The last down candle before that rally is index 7,
# which is the order block.

BULL_BOS = _candles([
    (10, 12, 9, 11),
    (11, 13, 10, 12),
    (12, 16, 11, 15),
    (15, 15, 12, 13),
    (13, 14, 11, 12),
    (12, 13, 10, 11),
    (11, 12, 9, 10),
    (10, 11, 9.2, 9.5),
    (9.5, 13, 9.3, 12),
    (12, 18, 11, 17),
])


def test_detects_a_bullish_break_of_structure():
    found = [b for b in breaks(BULL_BOS) if b.kind == BULLISH]
    assert len(found) == 1
    bos = found[0]
    assert bos.level == 16
    assert bos.date == date(2025, 1, 10)


def test_a_break_is_close_based_not_wick_based():
    """The manifesto specifies a *close* above the previous range high. A wick through the
    level is a failed breakout, and treating it as a break would invert the signal."""
    wick_only = BULL_BOS[:9] + _candles(
        [(12, 18, 11, 15.5)], start=START + timedelta(days=9)
    )
    assert [b for b in breaks(wick_only) if b.kind == BULLISH] == []


def test_the_break_carries_the_swing_low_it_originated_from():
    """That origin is the invalidation level and therefore the stop, so a break without it
    cannot produce a tradeable candidate."""
    bos = [b for b in breaks(BULL_BOS) if b.kind == BULLISH][0]
    assert bos.origin is not None
    assert bos.origin.price == 9
    assert bos.origin.date == date(2025, 1, 7)


def test_breaks_respect_as_of():
    assert breaks(BULL_BOS, as_of=date(2025, 1, 9)) == ()


# ── order blocks ────────────────────────────────────────────────────────────

def _bull_ob():
    return [o for o in order_blocks(BULL_BOS) if o.kind == BULLISH][0]


def test_order_block_is_the_last_down_candle_before_the_breaking_move():
    ob = _bull_ob()
    assert ob.date == date(2025, 1, 8)   # index 7, the last red candle before the rally
    assert ob.top == 11                  # full candle, high to low
    assert ob.bottom == 9.2


def test_order_block_walk_back_skips_consecutive_up_candles():
    """Index 8 is green and index 7 is red, so the search must pass over 8 rather than stop
    at the bar immediately before the break."""
    assert _bull_ob().index == 7


def test_order_block_is_only_knowable_once_the_break_happens():
    """The candle at index 7 is unremarkable until index 9 closes above the swing high. Dating
    the order block at its own candle would let a caller act three days early."""
    ob = _bull_ob()
    assert ob.date == date(2025, 1, 8)
    assert ob.confirmed_at == date(2025, 1, 10)


def test_order_block_invalidation_is_the_origin_swing_low():
    assert _bull_ob().invalidation == 9


# A break whose origin swing lands *inside* the order block it produces.
#
# Not a contrived shape — it is what look-ahead avoidance does on wick-heavy candles, and live
# GOOGL weekly data hits it exactly. The block at index 8 wicks to 85, and the bar after it goes
# lower still (80), which is the low the move truly began at. But that swing is only confirmed
# two bars later, *after* the break, so ``_origin_before`` may not use it and reaches back to
# the last swing low that was knowable in time — index 2 at 90, which sits inside the block and
# above its own stop.
WICK_BELOW_ORIGIN = _candles([
    (100, 102, 98, 101),
    (101, 103, 99, 102),
    (102, 104, 90, 103),    # swing low 90, confirmed at index 4 — the eligible origin
    (103, 108, 100, 107),
    (107, 112, 104, 111),   # swing high 112 — the level the break clears
    (111, 111, 106, 108),
    (108, 110, 105, 109),
    (109, 110, 107, 108),
    (108, 109, 85, 87),     # the order block: a down candle wicking below the origin
    (87, 100, 80, 98),      # the true low, confirmed too late to be the origin
    (98, 120, 97, 118),     # break of structure
    (118, 122, 115, 120),
])


def _wicked_ob():
    return [o for o in order_blocks(WICK_BELOW_ORIGIN) if o.kind == BULLISH][0]


def test_the_fixture_really_does_put_the_origin_swing_inside_the_block():
    """Guards the premise of the next test: if the walk-back or the origin rule changes, this
    fails loudly rather than leaving the clamp test silently asserting nothing."""
    ob = _wicked_ob()
    assert (ob.top, ob.bottom) == (109, 85)
    assert ob.bos.origin.price == 90          # the stale swing, not the 80 that came later
    assert ob.bottom < ob.bos.origin.price < ob.top


def test_an_origin_swing_inside_the_zone_is_clamped_to_the_far_edge():
    """Left alone the zone would die at 90 while a trade on it survived to its stop at 85 —
    inverting the relationship the rest of the module assumes, where invalidation outlives the
    stop. Clamping lands them together so zone and trade die on the same close."""
    assert _wicked_ob().invalidation == 85


def test_clamping_does_not_touch_an_origin_that_is_already_outside_the_zone():
    """The normal case, and by far the common one: BULL_BOS's origin is 9 against a block
    bottom of 9.2, so the clamp is a no-op and the real swing level survives."""
    assert _bull_ob().invalidation == 9
    assert _bull_ob().invalidation < _bull_ob().bottom


def _bos(kind, *, origin_price):
    """A hand-built break, for exercising the clamp directly in both directions.

    Bearish fixtures big enough to produce this shape from real bars cost far more than the rule
    is worth, and the bullish path is already covered end-to-end by ``WICK_BELOW_ORIGIN``.
    """
    opposing = "low" if kind == BULLISH else "high"
    origin = None if origin_price is None else Swing(
        date=START, price=origin_price, kind=opposing, confirmed_at=START, index=0)
    broken = Swing(date=START, price=120.0, kind=("high" if kind == BULLISH else "low"),
                   confirmed_at=START, index=1)
    return Break(kind=kind, level=broken.price, swing=broken, origin=origin,
                 date=START, index=2)


def test_a_bearish_origin_inside_the_zone_clamps_up_to_the_top():
    bos = _bos(BEARISH, origin_price=100.0)
    assert _invalidation_for(bos, top=105.0, bottom=95.0) == 105.0
    assert _invalidation_for(bos, top=98.0, bottom=90.0) == 100.0   # already outside, untouched


def test_a_break_with_no_origin_still_has_no_invalidation():
    assert _invalidation_for(_bos(BULLISH, origin_price=None),
                             top=110.0, bottom=100.0) is None


def test_order_block_depth_runs_from_the_near_edge():
    """A bullish zone is approached from above, so depth is 0 at the top and 1 at the bottom —
    'more confidence as you dive deeper into that candle'."""
    ob = _bull_ob()
    assert ob.depth_at(11) == 0.0
    assert ob.depth_at(9.2) == 1.0
    assert 0.4 < ob.depth_at(10.1) < 0.6


def test_depth_refuses_a_price_outside_the_zone_rather_than_clamping():
    """``position_in_range`` clamps deliberately, which is right for premium/discount — a price
    above the range really is "at the extreme". Read as *depth* it inverts: a bullish zone price
    has fallen clean through clamps to position 0.0 and so reported depth **1.0**, paying a
    dead zone the maximum for having been abandoned.

    Measured on the live queue 2026-07-27: ``depth == 1.0`` in exactly 13 of 82 candidates, and
    those 13 were precisely the ones price had traded past — nothing legitimately rests at the
    far edge. They outscored the rest (mean 0.621 vs 0.562) and took 4 of the top 13 places.
    """
    ob = _bull_ob()
    assert ob.depth_at(9.1) is None    # through the far edge — the trade is already stopped
    assert ob.depth_at(11.5) is None   # short of the near edge — price has not arrived


def test_a_wick_into_the_zone_counts_as_a_touch():
    ob = _bull_ob()
    wick_in = SimpleNamespace(date=START, open=13, high=13, low=10.5, close=12.5)
    clears_above = SimpleNamespace(date=START, open=13, high=14, low=11.5, close=13.5)
    assert ob.touches(wick_in)
    assert not ob.touches(clears_above)


def test_order_block_dies_when_price_closes_below_the_origin_low():
    extended = BULL_BOS + _candles(
        [(17, 18, 12, 13), (13, 14, 8, 8.5)], start=START + timedelta(days=10)
    )
    ob = [o for o in order_blocks(extended) if o.kind == BULLISH][0]
    assert invalidated_on(ob, extended) == date(2025, 1, 12)


def test_an_untouched_order_block_is_not_invalidated():
    assert invalidated_on(_bull_ob(), BULL_BOS) is None


# ── bearish mirror ──────────────────────────────────────────────────────────

def test_detects_a_bearish_break_and_its_order_block():
    """Mirror of BULL_BOS: swing low 9 at index 2, swing high 16 at index 6, then a selloff
    closing below the swing low. The order block is the last up candle before it."""
    bear = _candles([
        (15, 16, 13, 14),
        (14, 15, 12, 13),
        (13, 14, 9, 10),
        (10, 13, 10, 12),
        (12, 14, 11, 13),
        (13, 15, 12, 14),
        (14, 16, 13, 15),
        (15, 15.8, 14, 15.5),
        (15.5, 15.7, 11, 12),
        (12, 13, 7, 8),
    ])
    bos = [b for b in breaks(bear) if b.kind == BEARISH]
    assert len(bos) == 1
    assert bos[0].level == 9
    ob = [o for o in order_blocks(bear) if o.kind == BEARISH][0]
    assert ob.date == date(2025, 1, 8)   # index 7, the last green candle before the drop
    assert ob.top == 15.8
    assert ob.bottom == 14
    assert ob.invalidation == 16         # the origin swing high
    assert ob.depth_at(14) == 0.0        # approached from below
    assert ob.depth_at(15.8) == 1.0
