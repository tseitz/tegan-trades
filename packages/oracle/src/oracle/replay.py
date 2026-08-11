"""What a bracket did, walked forward against daily bars.

Pure and duck-typed: bars need ``date``/``high``/``low``, nothing else. No I/O, no network,
no notion of where the levels came from — a hand-entered decision and a generated candidate
resolve through the same code, which is the reason this is a module rather than a function
inside one probe.

**Strictly after ``from_date``.** The session a trade was chosen in already contains ticks that
had not happened when it was chosen, so its high and low would let a target hit *before* the
decision count as a win. That is the look-ahead ``oracle.series`` opens by forbidding, and it is
the single easiest way for a replay to report an edge it never had.

**Fill and exit are evaluated in one pass, and the orderings are not symmetric.** A bar touching
both stop and target is ``AMBIGUOUS`` — daily OHLC cannot say which came first. A bar that
*fills and then stops* is not ambiguous at all: a long's stop sits below its entry, so price had
to trade through the entry to reach the stop, and fill-then-stop is the only ordering available.
It is flagged ``same_bar`` rather than waved through, because a stop reached inside the fill
session is a statement about ``STOP_PAD_ATR`` — that the stop sits inside one day's noise.
Measured on the recorded decisions, that is **69% of all stops**.

**Ambiguity is a valuation choice, never a state change.** ``AMBIGUOUS`` stays ``AMBIGUOUS``;
what moves is ``r``. Resolving it pessimistically is the default because it is the conservative
direction for a claim of edge, and a caller that wants the other bound asks for it explicitly
and reports both. Collapsing the state instead would destroy the only record of how much of a
result rests on the convention.

**``OPEN`` is marked to market, not discarded and not force-closed.** A filled trade that has
not reached either level by the end of the window has a real unrealized value, and dropping it
biases toward whatever resolves fastest — which is tight stops, which is a construction
difference rather than an edge. Inventing a "trade expires after N days" cutoff instead would
resurrect exactly the fixed-horizon constant ``docs/IMPROVEMENTS.md`` §2 is trying to delete.
The tail is a measurement boundary, not a claim that the trade ended.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# ── outcome states ──────────────────────────────────────────────────────────

TARGET = "target"
STOP = "stop"
AMBIGUOUS = "ambiguous"   # one bar touched both; daily data cannot order them
OPEN = "open"             # filled, neither level reached yet
NOFILL = "nofill"         # the limit was never traded through
UNREPLAYABLE = "unreplayable"

# States representing a completed trade. ``AMBIGUOUS`` is settled, just not cleanly.
RESOLVED = (TARGET, STOP, AMBIGUOUS)

PESSIMISTIC = "pessimistic"
OPTIMISTIC = "optimistic"


@dataclass(frozen=True)
class Outcome:
    """One bracket's fate. ``r`` is None only when there was nothing to value — a trade that
    never filled took no risk, so it has no R, and 0R is the caller's convention to apply."""
    state: str
    r: float | None = None
    filled_on: date | None = None
    resolved_on: date | None = None
    bars: int = 0
    same_bar: bool = False
    detail: str | None = None


def touches(bar, level: float, *, above: bool) -> bool:
    return bar.high >= level if above else bar.low <= level


def planned_r(*, entry: float, stop: float, target: float) -> float | None:
    """The R the bracket was drawn to return. None when risk is zero — an entry equal to its
    own stop is a degenerate bracket, and dividing by it would manufacture an infinite edge."""
    risk = abs(entry - stop)
    return abs(target - entry) / risk if risk else None


def resolve(
    *,
    entry: float,
    stop: float,
    target: float,
    direction: str,
    bars,
    from_date: date,
    fill_within: int | None = None,
    tail_days: int | None = None,
    ambiguity: str = PESSIMISTIC,
) -> Outcome:
    """Walk *bars* forward from the day after ``from_date`` and say what happened.

    ``fill_within`` bounds how long the resting limit waits before the row is called
    ``NOFILL``; ``tail_days`` bounds the whole measurement. They are different questions and
    a caller may want either, both, or neither — the default of neither is what makes ``OPEN``
    and ``NOFILL`` honest terminal states rather than artifacts of a cutoff.
    """
    long = direction == "long"
    forward = [b for b in bars if b.date > from_date]
    if tail_days is not None:
        horizon = from_date + timedelta(days=tail_days)
        forward = [b for b in forward if b.date <= horizon]
    if not forward:
        return Outcome(state=OPEN, detail="no bars after the decision")

    reward = planned_r(entry=entry, stop=stop, target=target)
    filled_on: date | None = None

    for i, bar in enumerate(forward):
        if fill_within is not None and filled_on is None and i >= fill_within:
            return Outcome(state=NOFILL, bars=i, detail=f"not reached in {fill_within}d")

        if filled_on is None:
            # A long rests below the market and fills when price trades down to it.
            if not touches(bar, entry, above=not long):
                continue
            filled_on = bar.date

        hit_target = touches(bar, target, above=long)
        hit_stop = touches(bar, stop, above=not long)
        same_bar = bar.date == filled_on

        if hit_target and hit_stop:
            r = reward if ambiguity == OPTIMISTIC else -1.0
            return Outcome(state=AMBIGUOUS, r=r, filled_on=filled_on,
                           resolved_on=bar.date, bars=i + 1, same_bar=same_bar)
        if hit_target:
            return Outcome(state=TARGET, r=reward, filled_on=filled_on,
                           resolved_on=bar.date, bars=i + 1, same_bar=same_bar)
        if hit_stop:
            return Outcome(state=STOP, r=-1.0, filled_on=filled_on,
                           resolved_on=bar.date, bars=i + 1, same_bar=same_bar)

    last = forward[-1]
    if filled_on is None:
        return Outcome(state=NOFILL, bars=len(forward),
                       detail=f"never traded through {entry:g}")
    return Outcome(state=OPEN, r=mark_to_market(entry=entry, stop=stop,
                                                close=last.close, direction=direction),
                   filled_on=filled_on, resolved_on=last.date, bars=len(forward))


def mark_to_market(*, entry: float, stop: float, close: float, direction: str) -> float | None:
    """Unrealized R at *close*, on the risk the trade actually took.

    The denominator is entry-to-stop, the same one a realized R uses, so an open row and a
    closed one are denominated identically and can sit in the same mean.
    """
    risk = abs(entry - stop)
    if not risk:
        return None
    move = close - entry if direction == "long" else entry - close
    return move / risk
