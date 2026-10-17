"""Liquidity pools — swing points at roughly one price that nothing has traded through since.

Pure and duck-typed like ``core.structure``: any sequence of bars exposing
``date``/``open``/``high``/``low``/``close``, no I/O.

**"Untaken" is independent of ``structure.breaks``.** ``breaks`` tracks only the newest swing per
side, so a superseded higher high is forgotten there. Here it survives until a bar's wick
actually trades through it, which is where resting stops sit.

**Join before take.** A swing that lands within ``tolerance`` of a live pool joins it even when
its own bar is a tick beyond the pool's price; otherwise a later equal high would kill the very
pool it should have extended. Any other bar beyond a pool takes it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.structure import (
    SWING_HIGH,
    SWING_LOW,
    SWING_WIDTH,
    Swing,
    confirmed_by,
    on_or_before,
    swings,
)

# Pool tolerance as a multiple of the series' own ATR (average true range, 14 bars).
#
# Measured 2026-10-16 over 86 live candidates (``scripts/probe_liquidity.py``): new ladder rungs
# per candidate fall from median 2 / p90 5 at 0.1 to median 1 / p90 4 at 0.25 (H12 p90 7 -> 5),
# while 2+-touch pools rise from 12% to 17% of rungs. 0.5 merges further but near-duplicates of
# existing rungs climb 133 -> 149. Chosen in that band, not discovered. TUNE.
POOL_TOLERANCE_ATR = 0.25


@dataclass(frozen=True, slots=True)
class Pool:
    kind: str                     # SWING_HIGH | SWING_LOW
    price: float                  # the extreme member: highest high or lowest low
    members: tuple[Swing, ...]    # ascending by bar

    @property
    def touches(self) -> int:
        return len(self.members)


def pools(bars, *, as_of: date | None = None, width: int = SWING_WIDTH,
          tolerance: float) -> tuple[Pool, ...]:
    """The pools alive at ``as_of``, highs first, each side in order of creation.

    ``tolerance`` is absolute, in price units; the caller scales it by its own ATR. A pool's
    span (highest member minus lowest) never exceeds it, so a staircase cannot chain into one
    pool.
    """
    if tolerance < 0:
        raise ValueError(f"tolerance must be non-negative, got {tolerance}")
    known = confirmed_by(swings(bars, width=width), as_of) if as_of is not None else swings(bars, width=width)
    live = [b for b in bars if on_or_before(b.date, as_of)]
    return (_sweep(live, known, SWING_HIGH, tolerance)
            + _sweep(live, known, SWING_LOW, tolerance))


def _sweep(bars, known, kind: str, tolerance: float) -> tuple[Pool, ...]:
    """One forward pass for one side. Works in a sign-flipped space so lows mirror highs."""
    sign = 1 if kind == SWING_HIGH else -1
    swing_at = {s.index: s for s in known if s.kind == kind}
    alive: list[list[Swing]] = []

    for i, bar in enumerate(bars):
        reach = sign * (bar.high if kind == SWING_HIGH else bar.low)
        swing = swing_at.get(i)
        joined = None
        if swing is not None:
            joined = _pool_to_join(alive, swing, sign, tolerance)
            if joined is not None:
                joined.append(swing)

        alive = [p for p in alive if p is joined or reach <= max(sign * m.price for m in p)]
        if swing is not None and joined is None:
            alive.append([swing])

    return tuple(
        Pool(kind=kind,
             price=max(m.price for m in p) if sign == 1 else min(m.price for m in p),
             members=tuple(p))
        for p in alive
    )


def _pool_to_join(alive, swing: Swing, sign: int, tolerance: float) -> list[Swing] | None:
    """The live pool ``swing`` joins: smallest resulting span, ties to the most recently formed.

    A pool joins only if it is still alive at the swing's own bar, which every pool in
    ``alive`` is: the take test for earlier bars has already run.
    """
    best, best_span = None, None
    for pool in alive:
        values = [sign * m.price for m in pool] + [sign * swing.price]
        span = max(values) - min(values)
        if span <= tolerance and (best_span is None or span <= best_span):
            best, best_span = pool, span
    return best
