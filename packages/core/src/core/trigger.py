"""The entry trigger — confirmation on the timeframe below the zone.

Price reaching a setup zone is not an entry. It opens a window, and the entry is a confirmation
on the timeframe below: a break of structure carrying displacement, entered on the pullback into
the gap that displacement left. `~/vault/Trading/_Structure.md` § the entry trigger holds the
method and its citations; this module is that mechanic and nothing else.

**The defect it fixes.** The engine entered like a reversal — a resting limit in the zone, with
nothing waited for — and stopped like a continuation, padding tight off that same zone. §48
measured the result: 69% of stops landed on the very bar that filled the entry. The two halves
were drawn from different trades.

**This module needs a richer bar than the rest of ``core``.** ``core.structure`` and
``core.imbalance`` are duck-typed on ``date``/``open``/``high``/``low``/``close``; here a bar
must also expose ``volume``. That is the one place ``core``'s bar contract widens, and it is
required rather than incidental — the participation floor is the whole reason the trigger can
be trusted on a thin instrument, and it cannot be computed without volume. ``volume`` may be
None (unmeasured, per ``oracle.intraday.IntradayBar``), and a series that never reports it is
``UNREADABLE`` rather than silently exempt from the test.

**Purity is unchanged.** No I/O, no local imports beyond ``core`` itself, nothing mutated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import median

from core.imbalance import ATR_LOOKBACK, Gap, fair_value_gaps
from core.structure import BEARISH, BULLISH, SWING_WIDTH, Break, breaks, swings

# Bars of history the participation median is taken over. Measured, not picked: rejections are
# violently phase-sensitive below two days (81 at 14 bars, 61 at 24) and flat from ~48 up
# (70-73 all the way to a whole-series median). 80 sits mid-plateau — a trading week of equity
# bars, ~3.3 days of crypto — and the point of the plateau is that the exact value does not
# matter. Full sweep in ``scripts/probe_intraday_gaps.py``.
PARTICIPATION_WINDOW = 80

# Fraction of that median a displacement candle must carry. Returns the session clock's own
# verdict on 95% of equity gaps while staying inert on crypto (0-2%), and sweeps up 48 of 49
# closure artifacts for free. A volume test rather than a clock window because "was this real
# participation" is the actual concern, and an hour range means nothing to a market that never
# closes.
PARTICIPATION_FLOOR = 0.50

# Verdicts. ``UNREADABLE`` is the one the gate refuses on and it is deliberately not None: a
# thin instrument with no computable structure must never be mistaken for a healthy one that
# simply has not triggered yet. That distinction is the gate.
FIRED = "fired"              # broke, displaced, and price has returned into the gap
ARMED = "armed"              # broke and displaced; waiting for the pullback
NO_TRIGGER = "no_trigger"    # readable, nothing qualifying yet
NO_ZONE_TAG = "no_zone_tag"  # price never reached the zone — there is no setup at all
UNREADABLE = "unreadable"    # not enough structure or volume to judge


@dataclass(frozen=True, slots=True)
class Trigger:
    """What the trigger timeframe says about one candidate, as of the last bar supplied.

    ``entry`` and ``stop`` are populated for ``FIRED`` and ``ARMED`` alike — on ``ARMED`` they
    are the levels to wait for rather than to act on, which is what lets a caller show a live
    setup without implying it is fillable.
    """
    state: str
    entry: float | None = None
    stop: float | None = None
    gap: Gap | None = None
    structure_break: Break | None = None
    #: The bar the verdict was computed as of — the last one supplied.
    as_of: date | datetime | None = None


def detect(bars, *, direction: str, zone_tagged: bool,
           width: int = SWING_WIDTH,
           window: int = PARTICIPATION_WINDOW,
           floor: float = PARTICIPATION_FLOOR) -> Trigger:
    """Run the five-step trigger over ``bars``.

    ``bars`` is an indexable sequence in ascending order with unique stamps, exposing
    ``date``/``open``/``high``/``low``/``close``/``volume`` — ``oracle.intraday.IntradaySeries``
    satisfies it. ``direction`` is the higher-timeframe bias, "long" or "short".

    The steps, in the order the spec states them and the order they are cheapest to refuse in:

    1. **Price must tag the zone.** Refused first because it costs nothing and is absolute:
       *"no zone tag, there is no setup... The model does not work."* Supplied by the caller,
       which owns the zone; this module never sees zone geometry.
    2. **A structure break in the direction of the bias.** A long needs the most recent lower
       high taken.
    3. **The break must carry displacement**, which is what leaves a gap — and that displacement
       must be real participation, not a thin candle clearing a threshold set by thin candles.
    4. **Entry is the pullback into the gap**, never the break itself.
    5. **Stop is the swing that preceded the break.**
    """
    if not zone_tagged:
        return Trigger(state=NO_ZONE_TAG, as_of=_last(bars))

    if len(bars) < ATR_LOOKBACK + 3:
        # Too short for a displacement threshold at all; ``atr`` would return None for every
        # candle and every gap would be silently unconfirmable.
        return Trigger(state=UNREADABLE, as_of=_last(bars))

    if all(getattr(bar, "volume", None) is None for bar in bars):
        # The participation floor is not optional — see the module docstring.
        return Trigger(state=UNREADABLE, as_of=_last(bars))

    if not swings(bars, width=width):
        # A series of single prints (o == h == l == c) yields no swing, so there is no structure
        # to break. ``INTL`` does exactly this. Distinct from "no qualifying break yet".
        return Trigger(state=UNREADABLE, as_of=_last(bars))

    wanted = BULLISH if direction == "long" else BEARISH
    candidates = [b for b in breaks(bars, width=width) if b.kind == wanted]
    if not candidates:
        return Trigger(state=NO_TRIGGER, as_of=_last(bars))

    latest = candidates[-1]
    gap = _gap_for(bars, latest, wanted, window=window, floor=floor)
    if gap is None:
        return Trigger(state=NO_TRIGGER, structure_break=latest, as_of=_last(bars))

    # Step 4: the shallowest fill inside the void, so reward-to-risk is stated conservatively —
    # the same convention ``core.setups.Setup.entry`` uses for a zone's near edge.
    entry = gap.top if wanted == BULLISH else gap.bottom
    stop = _stop_for(bars, latest, wanted, width=width)
    if stop is None:
        return Trigger(state=NO_TRIGGER, structure_break=latest, gap=gap, as_of=_last(bars))

    if _stop_taken(bars, latest, stop, wanted):
        # The break has already failed, so there is nothing here to enter. Reported as
        # NO_TRIGGER rather than as a state of its own: a dead setup is not a thing to act on,
        # and ``detect`` only ever considers the most recent break in the bias direction.
        return Trigger(state=NO_TRIGGER, structure_break=latest, gap=gap, as_of=_last(bars))

    state = FIRED if _retraced_into(bars, gap, wanted) else ARMED
    return Trigger(state=state, entry=entry, stop=stop, gap=gap,
                   structure_break=latest, as_of=_last(bars))


def _last(bars):
    return bars[-1].date if bars else None


def _gap_for(bars, structure_break: Break, kind: str, *, window: int, floor: float) -> Gap | None:
    """The displacement gap belonging to ``structure_break``, if it carries real participation.

    A gap qualifies when its displacement candle is the breaking candle or one adjacent to it —
    the void the break itself cut, not an unrelated one earlier in the series.
    """
    for gap in reversed(fair_value_gaps(bars)):
        if gap.kind != kind:
            continue
        if abs(gap.middle_index - structure_break.index) > 1:
            continue
        if _single_print(bars, gap):
            continue
        if _participated(bars, gap.middle_index, window=window, floor=floor):
            return gap
    return None


def _single_print(bars, gap: Gap) -> bool:
    """Is any of the gap's three candles a bar where nothing but one price ever traded?

    ``high == low`` means the hour produced a single price. Such a bar has no interior and no
    direction; a void measured against its edge is measured against a point that one trade put
    there. Observed on ``INTL`` 2026-07-29 20:00 — o/h/l/c all 28.80 on **one trade of 150
    shares**, which the trigger was happily quoting as an entry.

    Deliberately local, and deliberately not a judgement about the instrument. A blanket
    thinness threshold cannot be drawn: measured across 76 routable instruments, ``ILMN`` has
    19% single-print bars on 2,730 trades a bar (they are its dead overnight hours) while
    ``AVAX`` sits 1.6x above ``INTL`` on median dollar volume and forms price perfectly well.
    There is no line between them to find — but a gap whose own edge is one print is fabricated
    whoever printed it, and that is decidable without a constant.
    """
    return any(
        bars[i].high == bars[i].low
        for i in (gap.index - 2, gap.middle_index, gap.index)
        if 0 <= i < len(bars)
    )


def _participated(bars, index: int, *, window: int, floor: float) -> bool:
    """Did the candle at ``index`` trade enough to count as institutional participation?

    The window ends **strictly before** ``index`` — a thing is not judged against itself — which
    mirrors ``imbalance.atr``'s rule without inheriting its reasoning. There the statistic is a
    *mean*, so a large candle inside its own window really does inflate the threshold it is
    tested against and the filter partly cancels itself out. Here it is a *median*, which one
    sample of eighty cannot move: the rule is kept for principle and consistency, and its
    numerical effect is nil. Do not cite the ATR argument for it.
    """
    volume = getattr(bars[index], "volume", None)
    if volume is None:
        return False
    history = [
        bar.volume for bar in bars[max(0, index - window):index]
        if getattr(bar, "volume", None) is not None
    ]
    if not history:
        return False
    return volume >= floor * median(history)


def _stop_for(bars, structure_break: Break, kind: str, *, width: int) -> float | None:
    """The trigger-timeframe swing preceding the break — where the break is proven wrong.

    *"if price comes down here and takes that out, well, your market structure break has now
    failed. The trade is wrong."* For a bullish break that is the last swing low before it.
    """
    wanted_kind = "low" if kind == BULLISH else "high"
    prior = [
        s for s in swings(bars, width=width)
        if s.kind == wanted_kind and s.index < structure_break.index
    ]
    if not prior:
        return None
    return prior[-1].price


def _retraced_into(bars, gap: Gap, kind: str) -> bool:
    """Has price come back into the void since the gap was confirmed?

    Step 4 is a *pullback*, so a break price has run away from is armed, not entered. Only bars
    after the gap's confirming candle count — the candle that formed the void cannot fill it.
    """
    for bar in bars[gap.index + 1:]:
        if kind == BULLISH and bar.low <= gap.top:
            return True
        if kind != BULLISH and bar.high >= gap.bottom:
            return True
    return False


def _stop_taken(bars, structure_break: Break, stop: float, kind: str) -> bool:
    """Has price traded through the stop since the break?

    *"if price comes down here and takes that out, well, your market structure break has now
    failed. The trade is wrong."* Without this, ``_retraced_into`` answers only whether price
    ever returned to the gap and never whether the trade had already been invalidated — so a
    setup stopped out days ago still reads as a live entry. ``COMP`` did exactly that on
    2026-08-05: FIRED at 16.76 with a 16.41 stop, having traded to 16.27 the day before.

    Strict inequality, so a wick that merely reaches the level is not a failure. The whole
    point of the trigger is to stop treating ordinary noise as invalidation.
    """
    for bar in bars[structure_break.index + 1:]:
        if kind == BULLISH and bar.low < stop:
            return True
        if kind != BULLISH and bar.high > stop:
            return True
    return False
