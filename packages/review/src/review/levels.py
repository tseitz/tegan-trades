"""Which of the levels near your positions are worth putting in front of you. Pure.

``core.nearby`` finds them all, and "all" is the problem: measured on the live 77-position
account it returns **234 levels across 69 holdings** at the default reach, more than half of
them daily order blocks. Printing that is a wall, and a wall answers "what should I pay
attention to" with "everything", which is the same as not answering.

So this ranks and caps. Two decisions, and they are display decisions rather than facts about
the market, which is why they live here and not in ``core``:

**One row per holding per group.** A position standing in a weekly zone, a gap and two daily
blocks is one thing to look at, not four. The most significant level represents it and the
rest are counted on the row — never dropped silently, because a row that quietly stands for
four levels reads as though it stands for one.

**Significance is timeframe first, then kind.** A weekly block outranks anything daily however
much closer the daily one is, because the horizon this exists for makes a daily touch noise.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.nearby import DAILY_ZONE, GAP, RANGE_EDGE, WEEKLY_ZONE, Level
from core.setups import WEEKLY

# How many rows each group prints before it starts counting instead. Twelve is a screenful —
# the point at which a shortlist stops being read as a shortlist. Whatever it drops is always
# reported; see ``shortlist``.
SHOWN = 12

# Weekly beats intraday, and a range edge carries no timeframe of its own because the dealing
# range is always read off the weekly (``build_context``). Anything unrecognised sorts last
# rather than first, so a new timeframe added upstream degrades to "least important" instead
# of silently taking the top of the section.
_TIMEFRAME_RANK = {WEEKLY: 0, "": 0}

# Within one timeframe: a structural block outranks the range boundary, which outranks a void,
# which outranks a daily block. Order-of-evidence, not preference — a block is where price
# turned, an edge is where the leg ends, a gap is only where price moved fast.
_KIND_RANK = {WEEKLY_ZONE: 0, RANGE_EDGE: 1, GAP: 2, DAILY_ZONE: 3}


@dataclass(frozen=True, slots=True)
class Spotlight:
    """One holding, the level standing for it, and how many more it is standing for."""
    reading: object            # core.review.Reading
    level: Level
    others: int                # further levels in the same group, not shown


def _rank(level: Level) -> tuple[int, int]:
    return (_TIMEFRAME_RANK.get(level.timeframe, 1), _KIND_RANK.get(level.kind, len(_KIND_RANK)))


def shortlist(pairs, *, limit: int | None = SHOWN):
    """``(standing_on, closing_in, suppressed)`` from ``[(reading, levels), ...]``.

    The two groups are separate because they are different questions. *Standing on* is where
    price is right now; *closing in* is what it is about to reach. One holding can legitimately
    appear in both — sitting on weekly support while 1% under weekly resistance is two facts,
    and collapsing them would drop whichever came second.

    ``limit=None`` returns everything, which is what the full listing uses. Anything the cap
    drops is returned as ``suppressed`` rather than swallowed: a truncated section that says
    nothing reads as the complete picture, which is the one thing a levels list must never
    imply about a portfolio.
    """
    standing, closing = [], []
    for reading, levels in pairs:
        # No price means no position relative to anything. A level measured against a missing
        # price would be measured against zero.
        if reading.price is None:
            continue
        _pick(standing, reading, [level for level in levels if level.inside])
        _pick(closing, reading, [level for level in levels if not level.inside])

    # Distance is identically zero across the standing group, so it cannot break a tie there.
    # What the position is worth can, and the biggest one is where being at a level matters
    # most. In the closing group distance is the whole point and leads.
    standing.sort(key=lambda s: (_rank(s.level), -(s.reading.market_value or 0.0)))
    closing.sort(key=lambda s: (_rank(s.level), s.level.distance))

    if limit is None:
        return tuple(standing), tuple(closing), 0
    suppressed = max(0, len(standing) - limit) + max(0, len(closing) - limit)
    return tuple(standing[:limit]), tuple(closing[:limit]), suppressed


def _pick(into: list, reading, levels) -> None:
    """Append the most significant of ``levels``, carrying a count of the ones it stands for."""
    if not levels:
        return
    best = min(levels, key=lambda level: (_rank(level), level.distance))
    into.append(Spotlight(reading=reading, level=best, others=len(levels) - 1))
