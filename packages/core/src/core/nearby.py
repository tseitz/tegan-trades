"""Every price level near where an asset is trading. Pure.

``core.review`` answers *what should I do*, and it needs the roster to answer at all — with
nobody speaking on an asset there is no view to act on. That is correct for advice and wrong
for the chart: measured on a real 77-position account, **30 holdings sat on a weekly level and
14 of those had a silent roster**, so the most concrete fact the pipeline knew about them was
computed and then discarded.

This module is the chart's own voice. It takes no opinion from anyone and answers one
question: what is price standing on, and what is it about to reach.

Four sources, all of them structure the engine already derives:

- **weekly and daily order blocks** — ``core.structure``, via ``Context.zones``
- **fair value gaps** — ``core.imbalance``, via ``Context.gaps``, live ones only
- **dealing range edges** — ``core.dealing_range``, the swing high and low bounding the leg

Which of the four count is the caller's choice, because it depends on the horizon rather than
on the asset: a daily block matters to a swing trade and is noise against a position you plan
to hold for a decade.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.setups import WEEKLY, Context
from core.structure import BULLISH

# The four sources, and the vocabulary a caller narrows with.
WEEKLY_ZONE = "weekly_zone"
DAILY_ZONE = "daily_zone"
GAP = "gap"
RANGE_EDGE = "range_edge"
ALL_KINDS = (WEEKLY_ZONE, DAILY_ZONE, GAP, RANGE_EDGE)

SUPPORT = "support"
RESISTANCE = "resistance"

# How far from price a level is still worth naming, as a fraction of price.
#
# **A fraction, never an absolute.** Five dollars is nothing on NVDA and a fifth of SOFI; a
# fixed band would fill the section with mega-caps or with nothing but them.
#
# 5% is a readability choice, not a measured threshold. Measured on the live 77-position
# account: 2% finds 140 levels, 5% finds 234, 10% finds 389 — so **no value of this makes the
# raw output a shortlist**, and a caller that prints everything ``levels_near`` returns will
# produce a wall whatever it is set to. Ranking and capping are the display's job, not this
# constant's; it only decides where "near" stops meaning anything.
#
# Where the 234 sit is the more useful number: 137 daily zones, 53 weekly, 23 range edges,
# 21 gaps. Daily blocks are more than half the noise on their own, which is why ``kinds`` is a
# parameter — a decade-horizon account has no business reading them.
REACH = 0.05


@dataclass(frozen=True, slots=True)
class Level:
    """One price level near the market, and what kind of thing it is.

    ``top`` and ``bottom`` are equal for a level that is a single price — a range edge is a
    swing, not a band, and widening it into one would claim a zone nothing measured.
    """
    kind: str
    timeframe: str                    # weekly | daily | h12; "" for a range edge
    side: str                         # SUPPORT | RESISTANCE
    top: float
    bottom: float
    # 0.0 when price is inside the level; otherwise the gap to its near edge as a fraction of
    # price. Unsigned — ``side`` already carries the direction, and a signed distance would
    # say the same thing twice and let the two disagree.
    distance: float
    # Where the level itself dies, for the kinds that have such a thing. A void has no origin
    # swing, so it has no invalidation, and inventing one would put a number on the row that
    # nothing measured.
    invalidation: float | None = None

    @property
    def inside(self) -> bool:
        return self.distance == 0.0

    @property
    def near_edge(self) -> float:
        """The edge price meets first. Meaningless while inside, where both are behind it."""
        return self.bottom if self.side == RESISTANCE else self.top


def levels_near(context: Context, *, kinds=ALL_KINDS, reach: float = REACH) -> tuple[Level, ...]:
    """Every level within ``reach`` of price, nearest first, plus every level price is inside.

    **A level price is standing in is always returned, however wide it is.** Filtering those on
    distance-to-edge would drop exactly the ones that matter: a zone spanning half the chart is
    still the zone price is in, and it is the whole reading this module exists to surface.
    """
    price = context.price
    if price <= 0:
        return ()

    found: list[Level] = []
    for zone in context.zones:
        kind = WEEKLY_ZONE if zone.timeframe == WEEKLY else DAILY_ZONE
        if kind not in kinds:
            continue
        found.append(_level(
            kind=kind, timeframe=zone.timeframe, price=price,
            top=zone.block.top, bottom=zone.block.bottom,
            own_direction=zone.block.kind, invalidation=zone.block.invalidation,
        ))

    if GAP in kinds:
        found += [
            _level(kind=GAP, timeframe=held.timeframe, price=price,
                   top=held.gap.top, bottom=held.gap.bottom, own_direction=held.gap.kind)
            for held in context.gaps
        ]

    if RANGE_EDGE in kinds and context.dealing_range is not None:
        for edge in (context.dealing_range.low, context.dealing_range.high):
            found.append(_level(kind=RANGE_EDGE, timeframe="", price=price,
                                top=edge, bottom=edge, own_direction=None))

    within = [level for level in found if level.inside or level.distance <= reach]
    return tuple(sorted(within, key=lambda level: (level.distance, level.bottom)))


def _level(*, kind: str, timeframe: str, price: float, top: float, bottom: float,
           own_direction: str | None, invalidation: float | None = None) -> Level:
    """One level, with its side and distance measured against ``price``.

    **Side is decided by position, not by the level's own direction** — except while price is
    inside it. A holder is asking what is under them and what is over them, and a bearish block
    sitting below price is still the thing price would land on; calling that resistance points
    them the wrong way. Inside a level there is no under or over to read, so what the level was
    built as is the only thing left saying which way it is expected to resolve. That is also
    what ``core.review.locate`` reads, which is what keeps the two from disagreeing about a
    zone price is standing in.
    """
    if bottom <= price <= top:
        side = SUPPORT if own_direction in (BULLISH, None) else RESISTANCE
        return Level(kind=kind, timeframe=timeframe, side=side, top=top, bottom=bottom,
                     distance=0.0, invalidation=invalidation)

    if price > top:
        side, gap = SUPPORT, price - top
    else:
        side, gap = RESISTANCE, bottom - price
    return Level(kind=kind, timeframe=timeframe, side=side, top=top, bottom=bottom,
                 distance=gap / price, invalidation=invalidation)
