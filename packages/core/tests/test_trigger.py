"""The entry trigger — confirmation on the timeframe below the zone.

The defect this exists to fix, measured in §48: we rest a limit in the zone with nothing waited
for (a reversal entry) and pad the stop tight off that zone (a continuation stop). 69% of stops
landed on the very bar that filled the entry. The trigger is the missing step 4.

Every test here builds bars by hand rather than from fixtures, because the mechanic is a
sequence of conditions and each test needs to fail exactly one of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from core import trigger
from core.structure import BEARISH, BULLISH


@dataclass(frozen=True, slots=True)
class B:
    """A bar carrying volume — the protocol ``core.trigger`` needs and the rest of core does not."""
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = 1_000.0


START = datetime(2026, 8, 1, tzinfo=UTC)


def bar(i: int, o: float, h: float, low: float, c: float, volume: float | None = 1_000.0) -> B:
    return B(date=START + timedelta(hours=i), open=o, high=h, low=low, close=c, volume=volume)


def quiet(n: int, price: float = 100.0, start: int = 0, volume: float | None = 1_000.0):
    """Flat filler — enough history for the ATR window, and no structure of its own."""
    return [bar(start + i, price, price + 0.5, price - 0.5, price, volume) for i in range(n)]


def wiggle(n: int, base: float = 100.0, start: int = 0, volume: float | None = 1_000.0):
    """Readable but dull: enough oscillation for swings to form and highs to be taken, with no
    candle anywhere near large enough to be displacement. The ``NO_TRIGGER`` baseline.

    ``quiet`` cannot serve here — its bars are identical, and ``swings`` compares strictly, so a
    flat plateau yields no swing at all and the series is genuinely ``UNREADABLE``.
    """
    out = []
    for i in range(n):
        mid = base + (i // 4) * 0.4 + (0.0, 0.6, 0.9, 0.4)[i % 4]
        out.append(bar(start + i, mid, mid + 0.5, mid - 0.5, mid + 0.1, volume))
    return out


def long_sequence(*, displacement_volume: float | None = 1_000.0, pull_back: bool = True):
    """A bullish trigger: a down-leg making a lower high, then a break of it with displacement.

    Laid out so exactly one condition can be knocked out per test. The lower high is at index
    18 (103.0); the displacement candle at 22 clears it and leaves a void between 21's high and
    23's low; 24-25 pull back into that void.
    """
    bars = quiet(16, 100.0)
    bars += [
        bar(16, 100.0, 100.5, 97.0, 97.5),      # down-leg
        bar(17, 97.5, 98.0, 96.5, 97.0),
        bar(18, 97.0, 103.0, 96.8, 102.5),      # the lower high to be taken
        bar(19, 102.5, 102.8, 99.0, 99.5),
        bar(20, 99.5, 100.0, 96.0, 96.5),       # the low the stop sits under
        bar(21, 96.5, 97.5, 96.2, 97.2),        # candle 1 of the FVG
        bar(22, 97.2, 112.0, 97.0, 111.0,       # displacement: takes 103.0, leaves a void
            displacement_volume),
        bar(23, 111.0, 113.0, 104.0, 112.0),    # candle 3 — low 104.0 > candle 1 high 97.5
    ]
    if pull_back:
        bars += [
            bar(24, 112.0, 112.5, 106.0, 107.0),   # retraces into the gap (97.5 - 104.0)
            bar(25, 107.0, 108.0, 103.0, 105.0),
        ]
    return bars


# ── the happy path ──────────────────────────────────────────────────────────────────────────

def test_a_clean_long_fires():
    found = trigger.detect(long_sequence(), direction="long", zone_tagged=True)
    assert found.state == trigger.FIRED
    assert found.gap is not None
    assert found.gap.kind == BULLISH


def test_the_entry_is_the_gap_not_the_break():
    """Step 4, and the one people get wrong: *"they're going to get antsy and jump in. Then
    price is going to pull back because it always does, into the fair value gap where they
    could have had a better entry."* Entering at the break's close would be 111.0."""
    found = trigger.detect(long_sequence(), direction="long", zone_tagged=True)
    assert found.entry == pytest.approx(104.0)     # the gap's top edge — the shallowest fill
    assert found.entry < 111.0


def test_the_stop_is_the_swing_that_preceded_the_break():
    """*"if price comes down here and takes that out, well, your market structure break has now
    failed. The trade is wrong."* Index 20's low is 96.0."""
    found = trigger.detect(long_sequence(), direction="long", zone_tagged=True)
    assert found.stop == pytest.approx(96.0)
    assert found.stop < found.entry


def test_a_short_mirrors_the_long():
    """Sign errors read perfectly and invert the result — the same hazard ``probe_replay``
    documents. Built by reflecting the long sequence about 100.0."""
    reflected = [
        B(date=b.date, open=200 - b.open, high=200 - b.low, low=200 - b.high,
          close=200 - b.close, volume=b.volume)
        for b in long_sequence()
    ]
    found = trigger.detect(reflected, direction="short", zone_tagged=True)
    assert found.state == trigger.FIRED
    assert found.gap.kind == BEARISH
    assert found.entry == pytest.approx(96.0)      # 200 - 104.0
    assert found.stop == pytest.approx(104.0)      # 200 - 96.0
    assert found.stop > found.entry


# ── the five steps, knocked out one at a time ───────────────────────────────────────────────

def test_no_zone_tag_is_no_setup():
    """*"no zone tag, there is no setup. I don't care if we get a five minute market structure
    break above here. The model does not work."* The most emphatic line in the spec."""
    found = trigger.detect(long_sequence(), direction="long", zone_tagged=False)
    assert found.state == trigger.NO_ZONE_TAG
    assert found.entry is None


def test_a_break_against_the_bias_does_not_fire():
    """The break must run *with* the higher-timeframe direction. A bullish break is not a short
    trigger — asking for one on this sequence must not quietly return the bullish one."""
    found = trigger.detect(long_sequence(), direction="short", zone_tagged=True)
    assert found.state != trigger.FIRED


def test_a_break_without_displacement_does_not_fire():
    """*"How do you know if a market structure break is legit?... It all comes back to
    displacement."* Here the level is taken by a limp candle that leaves no void."""
    bars = quiet(16, 100.0)
    bars += [
        bar(16, 100.0, 100.5, 97.0, 97.5),
        bar(17, 97.5, 98.0, 96.5, 97.0),
        bar(18, 97.0, 103.0, 96.8, 102.5),      # the lower high, as in the happy path
        bar(19, 102.5, 102.8, 99.0, 99.5),
        bar(20, 99.5, 100.0, 96.0, 96.5),
        # Now grind over 103.0 in one-point steps with three-point ranges, so consecutive bars
        # always overlap and no void can form behind any of them.
        bar(21, 96.5, 99.5, 96.2, 99.0),
        bar(22, 99.0, 100.5, 97.5, 100.0),
        bar(23, 100.0, 101.5, 98.5, 101.0),
        bar(24, 101.0, 102.5, 99.5, 102.0),
        bar(25, 102.0, 103.5, 100.5, 103.0),    # takes 103.0 — limply
        bar(26, 103.0, 104.0, 101.5, 103.5),
        bar(27, 103.5, 104.5, 102.5, 104.0),
    ]
    found = trigger.detect(bars, direction="long", zone_tagged=True)
    assert found.state == trigger.NO_TRIGGER


def test_a_starved_displacement_candle_does_not_fire():
    """*"displacement on pre-market volume is not the institutional participation the
    concept is about."* Same bars, same geometry, 2% of the volume."""
    found = trigger.detect(long_sequence(displacement_volume=20.0),
                           direction="long", zone_tagged=True)
    assert found.state == trigger.NO_TRIGGER


def test_a_gap_that_has_not_been_retraced_into_is_armed_not_fired():
    """Step 4 says enter the pullback, so a break whose gap price has not returned to is a live
    setup — not a miss, and not an entry. Conflating the two would either drop the trade or
    chase it."""
    found = trigger.detect(long_sequence(pull_back=False),
                           direction="long", zone_tagged=True)
    assert found.state == trigger.ARMED
    assert found.entry == pytest.approx(104.0)     # the price to wait for
    assert found.gap is not None


# ── cannot-compute is not the same as no-trigger ────────────────────────────────────────────

def test_too_few_bars_cannot_be_computed():
    """The gate refuses on this; it must never look like an ordinary "not yet"."""
    found = trigger.detect(quiet(5), direction="long", zone_tagged=True)
    assert found.state == trigger.UNREADABLE


def test_a_series_of_single_prints_cannot_be_computed():
    """``INTL`` — 77% of the book — returns hourly bars of 1-21 trades, several a single print
    where o == h == l == c. There is no structure to read and no vendor fixes it."""
    flat = [bar(i, 100.0, 100.0, 100.0, 100.0, 3.0) for i in range(40)]
    found = trigger.detect(flat, direction="long", zone_tagged=True)
    assert found.state == trigger.UNREADABLE


def test_a_series_with_no_volume_at_all_cannot_be_computed():
    """Unmeasured is not zero, but it is also not a licence to skip the participation test —
    the whole gate rests on it, so a source that reports nothing cannot be judged."""
    blank = [B(date=b.date, open=b.open, high=b.high, low=b.low, close=b.close, volume=None)
             for b in long_sequence()]
    found = trigger.detect(blank, direction="long", zone_tagged=True)
    assert found.state == trigger.UNREADABLE


def test_one_unmeasured_displacement_candle_is_not_a_trigger_but_is_readable():
    """Narrower than the case above and a different verdict: the series can be judged, this
    particular candle cannot, so there is no qualifying gap — not a refusal of the instrument."""
    found = trigger.detect(long_sequence(displacement_volume=None),
                           direction="long", zone_tagged=True)
    assert found.state == trigger.NO_TRIGGER


def test_unreadable_and_no_trigger_are_distinguishable():
    """The distinction the gate is built on: one is refused, the other is a live setup waiting."""
    assert trigger.UNREADABLE != trigger.NO_TRIGGER
    assert trigger.detect(wiggle(40), direction="long", zone_tagged=True).state == trigger.NO_TRIGGER
    # And a flat series of identical bars really is unreadable, not merely quiet.
    assert trigger.detect(quiet(40), direction="long", zone_tagged=True).state == trigger.UNREADABLE


# ── the participation floor ─────────────────────────────────────────────────────────────────

def test_an_enormous_displacement_candle_does_not_disqualify_itself():
    """The floor is a floor, not a band — conviction is the thing being looked for.

    Note what this does *not* pin. ``_participated`` ends its window strictly before the candle,
    but that rule is unfalsifiable here and this test does not claim otherwise: the statistic is
    a median, and one sample of eighty cannot move it. Mutating the slice to include the candle
    leaves the whole suite green, verified. The rule stands on principle, not on arithmetic.
    """
    huge = long_sequence(displacement_volume=10_000_000.0)
    assert trigger.detect(huge, direction="long", zone_tagged=True).state == trigger.FIRED


def test_the_floor_reads_recent_volume_not_the_whole_series():
    """Trailing, per the decision. A name whose volume regime stepped up months ago must be
    judged against what it does now — here the early history is 100x the recent baseline, and
    a whole-series median would starve an otherwise healthy candle."""
    bars = quiet(200, 100.0, volume=1_000_000.0) + quiet(100, 100.0, start=200, volume=1_000.0)
    tail = long_sequence()
    shifted = [B(date=START + timedelta(hours=300 + i), open=b.open, high=b.high,
                 low=b.low, close=b.close, volume=b.volume) for i, b in enumerate(tail)]
    found = trigger.detect(bars + shifted, direction="long", zone_tagged=True)
    assert found.state == trigger.FIRED


def test_the_window_is_the_documented_constant():
    """80 bars, chosen off the plateau in ``scripts/probe_intraday_gaps.py`` — not tuned here."""
    assert trigger.PARTICIPATION_WINDOW == 80
    assert trigger.PARTICIPATION_FLOOR == 0.50


# ── fabricated structure, from real bars ────────────────────────────────────────────────────

def test_a_gap_edged_by_a_single_print_does_not_fire():
    """Real ``INTL`` bars, Alpaca SIP, 2026-07-29 — the case that made this test exist.

    The trigger fired short with an entry of 28.80, and that price was **one trade of 150
    shares** in an hour: o/h/l/c all 28.80. Its neighbours carried 6 and 10 trades. The
    participation floor passed it because that floor is *relative* — INTL's own median is tiny,
    so a thin candle clears a bar set by thin candles. Relative thinness and absolute
    unreadability are different questions and this is the second one.
    """
    tail = [
        bar(16, 29.90, 29.95, 29.60, 29.70, 5_000.0),
        bar(17, 29.70, 29.75, 29.55, 29.60, 4_200.0),
        bar(18, 29.60, 29.98, 29.55, 29.90, 6_100.0),   # the higher low taken by the break
        bar(19, 29.90, 29.92, 29.72, 29.75, 3_800.0),
        bar(20, 29.75, 29.83, 29.70, 29.80, 3_100.0),
        bar(21, 29.70, 29.70, 29.48, 29.48, 3_390.0),   # gap candle 1 — 6 trades
        bar(22, 29.78, 29.83, 29.43, 29.43, 1_438.0),   # displacement — 10 trades
        bar(23, 28.80, 28.80, 28.80, 28.80, 150.0),     # gap candle 3 — ONE trade, flat
        bar(24, 28.80, 29.20, 28.75, 29.10, 900.0),
    ]
    bars = quiet(16, 29.85, volume=4_000.0) + tail
    found = trigger.detect(bars, direction="short", zone_tagged=True)
    assert found.state != trigger.FIRED
    assert found.entry != pytest.approx(28.80)


def test_a_flat_bar_only_disqualifies_the_gap_it_edges():
    """Not a blacklist on the instrument. ``ILMN`` has 19% single-print bars on 2,730 trades a
    bar — they are its dead overnight hours, and a clean gap elsewhere in that series is real."""
    bars = long_sequence()
    bars.insert(2, bar(2, 100.0, 100.0, 100.0, 100.0, 5.0))   # a flat bar far from the gap
    found = trigger.detect(bars, direction="long", zone_tagged=True)
    assert found.state == trigger.FIRED


# ── a break that has already failed ─────────────────────────────────────────────────────────

def test_a_trigger_whose_stop_was_taken_after_the_break_is_dead():
    """*"if price comes down here and takes that out, well, your market structure break has now
    failed. The trade is wrong."*

    Found by running the queue, not by any unit test here. ``COMP`` on 2026-08-05 reported
    ``FIRED · enter 16.76 · stop 16.41`` while price sat at 16.38 — it had traded down to 16.27
    in the 33 hours after the break, straight through the stop. The retrace test asked only
    whether price had *ever* returned into the gap and never whether the trade had already been
    invalidated, so a dead setup was being offered as a live entry.
    """
    bars = [
        *long_sequence(),
        bar(26, 105.0, 105.5, 97.0, 98.0),
        bar(27, 98.0, 98.5, 95.5, 96.2),      # takes out the 96.0 stop
        bar(28, 96.2, 97.0, 95.8, 96.5),
    ]
    found = trigger.detect(bars, direction="long", zone_tagged=True)
    assert found.state == trigger.NO_TRIGGER


def test_an_armed_setup_is_killed_by_the_same_test():
    """The gap never filled and price took the stop instead. The setup is not still waiting —
    the level it was waiting from is gone."""
    bars = [
        *long_sequence(pull_back=False),
        bar(24, 112.0, 112.5, 108.0, 109.0),
        bar(25, 109.0, 109.5, 95.0, 95.5),    # straight through the 96.0 stop, no gap fill
    ]
    found = trigger.detect(bars, direction="long", zone_tagged=True)
    assert found.state == trigger.NO_TRIGGER


def test_a_short_trigger_dies_when_price_runs_above_its_stop():
    reflected = [
        B(date=b.date, open=200 - b.open, high=200 - b.low, low=200 - b.high,
          close=200 - b.close, volume=b.volume)
        for b in [
            *long_sequence(),
            bar(26, 105.0, 105.5, 97.0, 98.0),
            bar(27, 98.0, 98.5, 95.5, 96.2),
            bar(28, 96.2, 97.0, 95.8, 96.5),
        ]
    ]
    assert trigger.detect(reflected, direction="short",
                          zone_tagged=True).state == trigger.NO_TRIGGER


def test_a_stop_merely_approached_does_not_kill_the_trigger():
    """The boundary matters: a wick that comes close is ordinary noise, and killing on it would
    reintroduce §48's problem — a stop so eager that it fires before the trade has begun."""
    bars = [*long_sequence(), bar(26, 105.0, 105.5, 96.1, 98.0)]   # low 96.1 vs a 96.0 stop
    assert trigger.detect(bars, direction="long", zone_tagged=True).state == trigger.FIRED
