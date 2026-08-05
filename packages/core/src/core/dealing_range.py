"""Premium/discount — where the manifesto says a trade is even allowed to happen.

Pure and duck-typed exactly like ``core.structure``: the caller supplies any *sequence* of
bars exposing ``date``/``open``/``high``/``low``/``close``, and nothing here does I/O or
mutates anything.

**Why this module exists at all.** The manifesto's rule — "Only ever TRULY buy the discount
of the range" — is a gate, not a score: a setup that is otherwise perfect but sits in premium
is not a lesser long, it is not a long. ``structure`` gives us swings and breaks; this module
answers the one question none of those constructs answer on their own, which is *where in the
current range is price right now*.

Direction vocabulary is owned by ``core.structure`` and re-exported here rather than
redefined, so the two modules can never drift into disagreeing about what "bullish" is.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from core.structure import (
    SWING_HIGH,
    SWING_LOW,
    SWING_WIDTH,
    Swing,
    confirmed_by,
    position_in_range,
    swings,
)

PREMIUM = "premium"
DISCOUNT = "discount"
EQUILIBRIUM = "equilibrium"

__all__ = [
    "DISCOUNT",
    "EQUILIBRIUM",
    "PREMIUM",
    "SWING_WIDTH",
    "DealingRange",
    "dealing_range",
]


@dataclass(frozen=True, slots=True)
class DealingRange:
    """The most recent swing-bounded range, used to gate long/short eligibility.

    ``low``/``high`` are carried alongside ``low_swing``/``high_swing`` rather than derived
    on read, so a caller can compare prices without reaching back into the swings on every
    call — the same shape ``OrderBlock`` uses for ``top``/``bottom`` next to its ``bos``.
    """
    low: float
    high: float
    low_swing: Swing
    high_swing: Swing
    confirmed_at: date | datetime

    @property
    def equilibrium(self) -> float:
        return (self.low + self.high) / 2

    def position_at(self, price: float) -> float | None:
        return position_in_range(self.low, self.high, price)

    def zone_at(self, price: float) -> str | None:
        """Three-valued on purpose: exactly at equilibrium is neither cheap nor expensive,
        and folding it into one side would let a coin-flip location satisfy a rule meant to
        require an edge."""
        position = self.position_at(price)
        if position is None:
            return None
        if position > 0.5:
            return PREMIUM
        if position < 0.5:
            return DISCOUNT
        return EQUILIBRIUM

    def permits(self, direction: str, price: float) -> bool:
        """Whether ``direction`` is allowed to act on ``price`` under this range.

        Fails closed: an unrecognised ``direction`` (or a ``None`` zone from a degenerate
        query) returns False rather than True, because this method encodes the manifesto's
        gate directly and a permissive default would silently let a coin-flip location, or a
        typo'd direction string, pass as a legitimate setup.
        """
        zone = self.zone_at(price)
        if direction == "long":
            return zone == DISCOUNT
        if direction == "short":
            return zone == PREMIUM
        return False


def dealing_range(bars, *, as_of: date | None = None,
                   width: int = SWING_WIDTH) -> DealingRange | None:
    """The range bounded by the most recent confirmed swing low and swing high.

    Either leg missing (not enough structure yet) yields None rather than a partial range —
    same discipline as ``order_blocks`` skipping breaks with no qualifying candle.
    """
    found = swings(bars, width=width)
    if as_of is not None:
        found = confirmed_by(found, as_of)

    highs = [s for s in found if s.kind == SWING_HIGH]
    lows = [s for s in found if s.kind == SWING_LOW]
    if not highs or not lows:
        return None

    high_swing = highs[-1]
    low_swing = lows[-1]

    if high_swing.price <= low_swing.price:
        # An inverted or zero-width range has no interior — position_in_range already
        # refuses this input, and inventing a range here would just push the same
        # degenerate case one layer further from where it's caught.
        return None

    # The range is not knowable until BOTH legs are, so confirmed_at is the LATER of the two
    # swings' confirmed_at, not the date the range's bounding prices happen to lock in. Taking
    # the earlier one would let a caller use a range before both legs existed — the same
    # look-ahead bug Swing.confirmed_at exists to prevent, reintroduced one layer up.
    confirmed_at = max(high_swing.confirmed_at, low_swing.confirmed_at)

    return DealingRange(
        low=low_swing.price,
        high=high_swing.price,
        low_swing=low_swing,
        high_swing=high_swing,
        confirmed_at=confirmed_at,
    )
