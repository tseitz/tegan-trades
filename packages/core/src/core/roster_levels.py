"""The price levels the roster has called on one asset, over a short horizon. Pure.

``Thesis.key_levels`` is an unlabeled bag of floats (see ``core.levels``), so this module
labels nothing it cannot know. A level is read against the asset's close on the publish day:
on the profit side of that close (above for a long, below for a short) it is a ``TARGET``, or
a ``HIT`` once current price has crossed it; on the other side it is ``AGAINST`` — a stop, an
entry or a fail level, the bag does not say which. A neutral call, an unparseable date or a
missing publish-day close gives ``LEVEL``. ``HIT`` is stateless: if price comes back it reads
``TARGET`` again.

Selection, in order: near-term horizons only (``core.term``), then not stale for the horizon,
then newest-per-person — every row of that person's newest date, since one video often states
several theses. Staleness runs before newest-per-person so a newer macro call cannot hide a
live swing call.

Levels outside ``[BAND_LOW, BAND_HIGH]`` times price are dropped and counted, never rescaled:
a ratio (an ETH/BTC 0.031) or $k shorthand (BTC 75.0) would otherwise print as a price, and
the drop count is what keeps that honest. Two people calling the same level stay two rows —
who said it is the point; one person repeating a level across theses is one row.

Rows are duck-typed on ``person``, ``asset``, ``direction``, ``timeframe``, ``published_at``
and ``key_levels``. The publish-day close arrives as a callable so ``core`` never sees a
series.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from core.levels import PROFIT_SIDE
from core.rank import parse_date
from core.term import is_stale, term_of

TARGET = "target"
HIT = "hit"
AGAINST = "against"
LEVEL = "level"

BAND_LOW = 0.5
BAND_HIGH = 2.0


@dataclass(frozen=True, slots=True)
class CalledLevel:
    person: str
    direction: str
    horizon: str
    published_at: str | None
    level: float
    role: str
    distance: float
    vs_cost: float | None


@dataclass(frozen=True, slots=True)
class RosterLevels:
    levels: tuple[CalledLevel, ...] = ()
    people: int = 0
    dropped: int = 0


def _role(direction: str, level: float, ref: float | None, price: float) -> str:
    sign = PROFIT_SIDE.get(direction)
    if sign is None or ref is None:
        return LEVEL
    if (level - ref) * sign <= 0:
        return AGAINST
    return HIT if (price - level) * sign >= 0 else TARGET


def roster_levels(
    rows: Iterable,
    *,
    price: float | None,
    cost: float | None,
    close_on: Callable[[date], float | None],
    today: date | None,
) -> RosterLevels:
    if price is None or price <= 0:
        return RosterLevels()

    live = [
        r for r in rows
        if term_of(getattr(r, "timeframe", None)) == "near"
        and not is_stale(getattr(r, "published_at", None), r.timeframe, today)
    ]

    newest: dict[str, date | None] = {}
    for r in live:
        d = parse_date(r.published_at)
        cur = newest.get(r.person)
        if r.person not in newest or (d is not None and (cur is None or d > cur)):
            newest[r.person] = d

    seen: set[tuple[str, float]] = set()
    called: list[CalledLevel] = []
    dropped = 0
    for r in live:
        published = parse_date(r.published_at)
        if published != newest[r.person]:
            continue
        ref = close_on(published) if published is not None else None
        for raw in getattr(r, "key_levels", None) or ():
            level = float(raw)
            key = (r.person, level)
            if key in seen:
                continue
            seen.add(key)
            if not BAND_LOW <= level / price <= BAND_HIGH:
                dropped += 1
                continue
            called.append(CalledLevel(
                person=r.person,
                direction=r.direction,
                horizon=r.timeframe,
                published_at=r.published_at,
                level=level,
                role=_role(r.direction, level, ref, price),
                distance=(level - price) / price,
                vs_cost=(level - cost) / cost if cost else None,
            ))

    called.sort(key=lambda c: c.level, reverse=True)
    return RosterLevels(
        levels=tuple(called),
        people=len({c.person for c in called}),
        dropped=dropped,
    )
