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
    BULLISH,
    SWING_HIGH,
    SWING_LOW,
    SWING_WIDTH,
    Swing,
    breaks,
    confirmed_by,
    on_or_before,
    position_in_range,
    swings,
)

PREMIUM = "premium"
DISCOUNT = "discount"
EQUILIBRIUM = "equilibrium"

CONFIRMED = "confirmed"
RESET = "reset"

__all__ = [
    "CONFIRMED",
    "DISCOUNT",
    "EQUILIBRIUM",
    "PREMIUM",
    "RESET",
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

    ``low_swing``/``high_swing`` are ``None`` on whichever side came from a **reset** rather
    than a confirmed swing (see ``source``). Manufacturing a ``Swing`` for that side would
    invent a ``confirmed_at`` for something never confirmed — the exact failure
    ``core.structure`` exists to prevent.
    """
    low: float
    high: float
    low_swing: Swing | None
    high_swing: Swing | None
    # For a confirmed range this is the later of the two swings' `confirmed_at` (see
    # `dealing_range`). For a reset it is the date of the break that created it — a break
    # date, not a confirmation date, and the two must never be pooled in the same statistic.
    confirmed_at: date | datetime
    source: str = CONFIRMED

    @property
    def equilibrium(self) -> float:
        return (self.low + self.high) / 2

    def position_at(self, price: float) -> float | None:
        """Where ``price`` sits in the range, or None when it sits outside it.

        ``position_in_range`` **clamps**, which is right for its other caller — order-block
        zone depth, where a price past the far edge is meaningfully "at the extreme". It is
        wrong here. A price outside the range has no position *in* the range, and clamping
        invents the most extreme reading available: a long below the range low returns 0.0 and
        reads as a maximally deep discount, which is the strongest possible signal produced
        from the weakest possible evidence.

        Measured: 5 of 52 live candidates had price outside their own range, headed by a TSLA
        long at 321.55 against a range of 368.60-432.86 — 0.73 widths below the low, reported
        as a perfect discount. Those rows are not deep, they are stale: price has left the
        range and the range is meant to be redrawn, since "as price breaks out of this range,
        it forms a new range". See ``scripts/probe_external_target.py``.
        """
        if not self.low <= price <= self.high:
            return None
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


def dealing_range(bars, *, as_of: date | None = None, width: int = SWING_WIDTH,
                   price: float | None = None) -> DealingRange | None:
    """The range bounded by the most recent confirmed swing low and swing high.

    Either leg missing (not enough structure yet) yields None rather than a partial range —
    same discipline as ``order_blocks`` skipping breaks with no qualifying candle.

    ``price``, when given, lets the range **reset** after a break of structure: if the
    confirmed range doesn't contain it (or there is none), the range is redrawn from the
    break's own origin swing and the extreme since — see ``_reset_range``. Every caller
    written before this keeps ``price=None`` and therefore the confirmed-only behaviour.

    Measured 2026-09-13: 133 of 410 priced assets sat outside their confirmed range, with
    ``permits`` refusing long *and* short there. See ``scripts/probe_range_staleness.py``.
    """
    confirmed = _confirmed_range(bars, as_of=as_of, width=width)
    if price is None:
        return confirmed
    if confirmed is not None and confirmed.low <= price <= confirmed.high:
        return confirmed
    reset = _reset_range(bars, as_of=as_of, width=width, confirmed=confirmed)
    return reset if reset is not None else confirmed


def _confirmed_range(bars, *, as_of: date | None, width: int) -> DealingRange | None:
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


def _reset_range(bars, *, as_of: date | None, width: int,
                  confirmed: DealingRange | None) -> DealingRange | None:
    """The range TraderMayne calls a **range reset**, built the moment a confirmed range no
    longer contains price rather than waiting on two fresh swings to confirm.

    > "We just broke this high. This is an MSB. We've made a higher high. Where's my higher
    > low? ... That higher low to higher high, that's my new dealing range. Mark out the 50%.
    > Mark out the discount, the premium." — TraderMayne, 2026-05-04

    Bounded by the break's own origin swing and the extreme since — neither of which waits on
    confirmation, so it exists exactly when a confirmed range cannot be drawn.

    ``confirmed`` bounds which break may reset it, **by level, not by date**: the break must
    close through *this* range's own boundary, or an old break from years back could reset a
    range it has long since been replaced by. Dates were tried first and recovered fewer
    cases — ``DealingRange.confirmed_at`` lags its swing by ``width`` bars, so a break date
    compares two different clocks; a level comparison has no clock in it at all. With no
    confirmed range there is nothing to be a reset *of*, so any break is accepted.

    The extreme is bounded at ``as_of`` on both ends. An unfiltered read would let a past
    ``as_of`` see tomorrow's high — the same look-ahead ``Swing.confirmed_at`` exists to
    prevent. See ``scripts/probe_range_staleness.py`` for the measurement that justified this
    construct and the alternatives it beat.
    """
    found = [b for b in breaks(bars, as_of=as_of, width=width) if b.origin is not None]
    if confirmed is not None:
        found = [b for b in found
                 if (b.level >= confirmed.high if b.kind == BULLISH
                     else b.level <= confirmed.low)]
    if not found:
        return None
    last = found[-1]
    origin = last.origin
    assert origin is not None  # guaranteed by the `found` filter above
    after = [b for b in bars
             if on_or_before(last.date, b.date) and on_or_before(b.date, as_of)]
    if not after:
        return None
    extreme = (max(b.high for b in after) if last.kind == BULLISH
               else min(b.low for b in after))
    low, high = sorted((origin.price, extreme))
    if high <= low:
        return None

    # A bullish break's origin is the swing LOW it rose from, so it bounds the range's low
    # side; a bearish break's origin is the swing HIGH it fell from, bounding the high side.
    # The other side is the bare extreme, with no confirming Swing behind it.
    origin_is_low = last.kind == BULLISH
    return DealingRange(
        low=low, high=high,
        low_swing=origin if origin_is_low else None,
        high_swing=None if origin_is_low else origin,
        confirmed_at=last.date,
        source=RESET,
    )
