import dataclasses
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from core.imbalance import (
    ATR_LOOKBACK,
    BEARISH,
    BULLISH,
    Gap,
    atr,
    fair_value_gaps,
    is_displacement,
    true_range,
)

START = date(2025, 1, 1)


def _bar(offset: int, o: float, h: float, l: float, c: float):
    return SimpleNamespace(date=START + timedelta(days=offset), open=o, high=h, low=l, close=c)


# Three flat bars: true range 2 on each (high-low=2, and the prev-close legs never exceed
# it), giving atr(bars, 2, lookback=3) == 2. Every FVG fixture below is built on top of this
# so the ATR math stays legible across tests.
def _flat_history():
    return [_bar(0, 100, 101, 99, 100), _bar(1, 100, 101, 99, 100), _bar(2, 100, 101, 99, 100)]


# ── bullish / bearish gaps ──────────────────────────────────────────────────

def test_bullish_gap_has_correct_top_bottom_and_is_dated_at_candle_three():
    bars = _flat_history() + [
        _bar(3, 105, 116, 104, 115),   # c2: displacement candle, body=10
        _bar(4, 120, 125, 105, 122),   # c3: low(105) > c1.high(101) -> bullish gap
    ]
    found = fair_value_gaps(bars, lookback=3)
    assert len(found) == 1
    gap = found[0]
    assert gap.kind == BULLISH
    assert gap.bottom == 101 and gap.top == 105
    # Dating this at candle 2 (bars[3].date) would be look-ahead: the void isn't knowable
    # until candle 3 closes without retracing into it.
    assert gap.date == bars[4].date
    assert gap.index == 4 and gap.middle_index == 3


def test_bearish_gap_has_correct_top_bottom_and_is_dated_at_candle_three():
    bars = _flat_history() + [
        _bar(3, 115, 116, 104, 105),   # c2: displacement candle, body=10
        _bar(4, 90, 95, 88, 92),       # c3: high(95) < c1.low(99) -> bearish gap
    ]
    found = fair_value_gaps(bars, lookback=3)
    assert len(found) == 1
    gap = found[0]
    assert gap.kind == BEARISH
    assert gap.top == 99 and gap.bottom == 95
    # Same no-look-ahead rule as the bullish case: dated at candle 3, not candle 2.
    assert gap.date == bars[4].date


# ── no gap when candles overlap ─────────────────────────────────────────────

def test_overlapping_candles_produce_no_gap():
    """c1.high >= c3.low (and the mirror for lows) means there's no untraded void at all —
    displacement or not, this must never register as a gap."""
    bars = _flat_history() + [
        _bar(3, 105, 116, 104, 115),
        _bar(4, 105, 108, 100, 106),    # low(100) overlaps c1.high(101)
    ]
    assert fair_value_gaps(bars, lookback=3) == ()


# ── the displacement filter bites ───────────────────────────────────────────

def test_displacement_filter_gates_an_otherwise_identical_price_gap():
    """The most important behaviour here: an identical bullish price gap is rejected when
    candle 2 has a small body and accepted when candle 2 has a large body. Without this
    filter, fair_value_gaps degenerates into flagging ordinary gaps."""
    small_body_c2 = _bar(3, 103, 105, 102, 104)     # body = 1
    large_body_c2 = _bar(3, 105, 116, 104, 115)     # body = 10
    c3 = _bar(4, 120, 125, 105, 122)                # low(105) > c1.high(101), same in both

    rejected = fair_value_gaps(_flat_history() + [small_body_c2, c3], lookback=3)
    accepted = fair_value_gaps(_flat_history() + [large_body_c2, c3], lookback=3)

    assert rejected == ()
    assert len(accepted) == 1
    assert accepted[0].kind == BULLISH


# ── ATR is measured at index - 1, not index ─────────────────────────────────

def test_displacement_measures_atr_one_bar_before_the_candle_it_gates():
    """Pins atr(bars, index - 1) against the easy-to-get-wrong atr(bars, index). Candle 2
    here has a body of 10 but a huge true range (35, via a big prev-close gap on its high) —
    large enough that folding it into its own ATR window would inflate the threshold past
    its own body, flipping the verdict from displacement to not-displacement."""
    bars = _flat_history() + [_bar(3, 105, 135, 104, 115)]  # body=10, true range=35

    threshold_correct = 1.5 * atr(bars, 2, lookback=3)   # excludes c2's own range
    threshold_buggy = 1.5 * atr(bars, 3, lookback=3)      # would include c2's own range
    body = abs(bars[3].close - bars[3].open)

    assert body >= threshold_correct
    assert body < threshold_buggy   # the discriminating fact: this is what would flip
    assert is_displacement(bars, 3, lookback=3) is True


# ── insufficient history ────────────────────────────────────────────────────

def test_atr_and_displacement_and_gaps_all_refuse_insufficient_history():
    """Same price gap and same displacement candle as the very first test, but with the
    default 14-bar lookback and only 5 bars total: nothing here has enough history to judge
    'unusually large', so nothing should be reported — not even a false negative dressed up
    as a real answer."""
    bars = _flat_history() + [
        _bar(3, 105, 116, 104, 115),
        _bar(4, 120, 125, 105, 122),
    ]
    assert ATR_LOOKBACK == 14
    assert atr(bars, 2) is None
    assert is_displacement(bars, 3) is False
    assert fair_value_gaps(bars) == ()


# ── true_range ───────────────────────────────────────────────────────────────

def test_true_range_uses_the_prev_close_legs():
    """A gap-up bar whose own high-low range (2) is dwarfed by the leg back to the prior
    close (15) — true_range must pick up that leg, not just the candle's own range."""
    bar = _bar(0, 104, 105, 103, 104)
    assert true_range(bar, 90) == 15   # max(105-103, |105-90|, |90-103|)


def test_true_range_falls_back_to_high_low_without_a_predecessor():
    bar = _bar(0, 100, 105, 99, 102)
    assert true_range(bar, None) == 6


# ── empty / too-short input ─────────────────────────────────────────────────

def test_empty_and_too_short_inputs_return_without_raising():
    assert fair_value_gaps(()) == ()
    assert fair_value_gaps(_flat_history()[:2]) == ()   # only 2 bars, no triple possible
    assert atr((), 0) is None
    assert is_displacement((), 0) is False


# ── immutability ─────────────────────────────────────────────────────────────

def test_gap_is_immutable():
    gap = Gap(kind=BULLISH, top=105, bottom=101, date=START, index=4, middle_index=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        gap.top = 999
