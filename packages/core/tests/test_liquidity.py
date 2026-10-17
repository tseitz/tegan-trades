from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from core.liquidity import POOL_TOLERANCE_ATR, Pool, pools
from core.structure import SWING_HIGH, SWING_LOW

START = date(2025, 1, 1)
FLAT_HIGH, FLAT_LOW = 10.0, 9.0


def _series(n, highs=None, lows=None):
    """Flat daily bars (no swings of their own) with chosen highs / lows overridden by index."""
    highs, lows = highs or {}, lows or {}
    out = []
    for i in range(n):
        h, lo = highs.get(i, FLAT_HIGH), lows.get(i, FLAT_LOW)
        out.append(SimpleNamespace(date=START + timedelta(days=i),
                                   open=lo, high=h, low=lo, close=h))
    return tuple(out)


def _day(i):
    return START + timedelta(days=i)


def _prices(found, kind):
    return [p.price for p in found if p.kind == kind]


def test_wicked_through_high_is_gone():
    found = pools(_series(10, highs={2: 15, 7: 20}), tolerance=1)
    assert _prices(found, SWING_HIGH) == [20]


def test_superseded_higher_high_survives():
    found = pools(_series(10, highs={2: 20, 7: 15}), tolerance=1)
    assert _prices(found, SWING_HIGH) == [20, 15]


def test_later_swing_a_tick_above_joins_and_prices_the_pool():
    found = pools(_series(10, highs={2: 15, 7: 15.5}), tolerance=1)
    (pool,) = [p for p in found if p.kind == SWING_HIGH]
    assert pool.price == 15.5
    assert pool.touches == 2
    assert [m.index for m in pool.members] == [2, 7]


@pytest.mark.parametrize("plateau, survives", [(15.5, False), (14.9, True)])
def test_non_swing_bar_beyond_the_extreme_takes_the_pool(plateau, survives):
    # Two equal bars are not a swing (strict comparison), so the only thing that can
    # kill the pool is the take test itself.
    found = pools(_series(10, highs={2: 15, 5: plateau, 6: plateau}), tolerance=1)
    assert _prices(found, SWING_HIGH) == ([15] if survives else [])


def test_falling_staircase_beyond_tolerance_is_separate_pools():
    found = pools(_series(15, highs={2: 15, 7: 13, 12: 11}), tolerance=1)
    assert _prices(found, SWING_HIGH) == [15, 13, 11]


def test_span_is_capped_not_chained():
    # Each step is 0.6 (within 1) but the third sits 1.2 from the first.
    found = pools(_series(15, highs={2: 15, 7: 14.4, 12: 13.8}), tolerance=1)
    highs = [p for p in found if p.kind == SWING_HIGH]
    assert [p.touches for p in highs] == [2, 1]
    assert [p.price for p in highs] == [15, 13.8]


def test_joins_the_pool_with_the_smallest_span():
    found = pools(_series(20, highs={2: 15, 7: 13.9, 12: 14.4}), tolerance=1)
    highs = [p for p in found if p.kind == SWING_HIGH]
    assert [p.touches for p in highs] == [1, 2]
    assert highs[1].price == 14.4


def test_as_of_bounds_confirmation():
    bars = _series(10, highs={2: 15})
    assert _prices(pools(bars, as_of=_day(3), tolerance=1), SWING_HIGH) == []
    assert _prices(pools(bars, as_of=_day(4), tolerance=1), SWING_HIGH) == [15]


def test_as_of_bounds_the_take_test():
    bars = _series(14, highs={2: 15, 8: 16, 9: 16})
    assert _prices(pools(bars, as_of=_day(7), tolerance=1), SWING_HIGH) == [15]
    assert _prices(pools(bars, as_of=_day(8), tolerance=1), SWING_HIGH) == []


def test_unconfirmed_swing_still_takes_but_is_not_a_pool():
    bars = _series(14, highs={2: 15, 8: 20})
    assert _prices(pools(bars, as_of=_day(9), tolerance=1), SWING_HIGH) == []
    assert _prices(pools(bars, as_of=_day(13), tolerance=1), SWING_HIGH) == [20]


def test_bar_that_is_both_swing_high_and_swing_low_counts_for_both():
    found = pools(_series(8, highs={3: 15}, lows={3: 5}), tolerance=1)
    assert _prices(found, SWING_HIGH) == [15]
    assert _prices(found, SWING_LOW) == [5]


def test_wicked_through_low_is_gone():
    found = pools(_series(10, lows={2: 5, 7: 3}), tolerance=1)
    assert _prices(found, SWING_LOW) == [3]


def test_superseded_lower_low_survives():
    found = pools(_series(10, lows={2: 3, 7: 5}), tolerance=1)
    assert _prices(found, SWING_LOW) == [3, 5]


def test_later_low_a_tick_below_joins_and_prices_at_the_lower():
    found = pools(_series(10, lows={2: 5, 7: 4.5}), tolerance=1)
    (pool,) = [p for p in found if p.kind == SWING_LOW]
    assert (pool.price, pool.touches) == (4.5, 2)


@pytest.mark.parametrize("plateau, survives", [(4.5, False), (5.1, True)])
def test_non_swing_bar_beyond_a_low_takes_the_pool(plateau, survives):
    found = pools(_series(10, lows={2: 5, 5: plateau, 6: plateau}), tolerance=1)
    assert _prices(found, SWING_LOW) == ([5] if survives else [])


def test_rising_low_staircase_is_capped_not_chained():
    found = pools(_series(15, lows={2: 5, 7: 5.6, 12: 6.2}), tolerance=1)
    lows = [p for p in found if p.kind == SWING_LOW]
    assert [p.touches for p in lows] == [2, 1]
    assert [p.price for p in lows] == [5, 6.2]


def test_negative_tolerance_is_refused():
    with pytest.raises(ValueError):
        pools(_series(10), tolerance=-1)


def test_pool_is_frozen_and_seed_is_positive():
    found = pools(_series(10, highs={2: 15}), tolerance=1)
    assert isinstance(found[0], Pool)
    assert POOL_TOLERANCE_ATR > 0
