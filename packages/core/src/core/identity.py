"""Is this instrument the asset we think it is? Settled by price, never by name. Pure.

A name match is silently catastrophic — the venues list a memecoin under ``SPX``, Yahoo's bare
``WTI`` is W&T Offshore rather than crude, and both resolve to real, liquid, entirely wrong
markets. The only evidence that separates them without a human is the price: two sources
quoting the same instrument agree, and two sources quoting different ones do not.

Here rather than in the probe that discovered it, because three callers need the same answer
and a private copy in each is three definitions that drift: the probe supplies evidence to
whoever curates the map, the fetch gate refuses to price an unconfirmed guess, and the
placement guard refuses to send an order against one. ``core`` holds no I/O, so the marks and
the closes are the caller's problem and only the arithmetic is here.

**The tolerance is loose on purpose, and 12% is not a claim about price accuracy.** What this
separates is instruments, and wrong instruments are wrong by orders of magnitude rather than by
percentages — 4.3x for the yen, 23x for crude against the E&P company, exactly 100x for PURR,
4e-5 for the memecoin. Meanwhile two *correct* sources routinely disagree by several percent,
because venues close at different times and a volatile name moves. Tightening this toward a
price check would buy nothing against the failures it exists to catch and would start refusing
honest movement, which is the expensive direction to fail.

**Four verdicts, and the reason there are four rather than two.** ``MATCH`` is agreement.
``DIFFERS`` is a real market at a genuinely different number. ``IN_RANGE`` is the mark sitting
inside our own recent trading band — a timing gap, since a venue quoting a session we have not
cached yet is right and we are behind. ``NO_PRICE`` is *no comparison was possible*, and
keeping it separate from ``MATCH`` is the entire point of the module: a gate that reads "could
not check" as "checked and agreed" is a gate that turns itself off exactly where the data is
worst. Only ``MATCH`` is ``confirmed``.
"""
from __future__ import annotations

from dataclasses import dataclass

MATCH = "MATCH"
IN_RANGE = "IN RANGE"
DIFFERS = "DIFFERS"
NO_PRICE = "NO PRICE"

# Fractional distance from 1.0 that still counts as the same instrument. See the module note:
# this is an identity threshold, not a price one.
MATCH_TOLERANCE = 0.12


@dataclass(frozen=True, slots=True)
class Comparison:
    """What one price said about another. ``ratio`` is carried whatever the verdict, because a
    refusal without it cannot be acted on — 10.03 is a proxy someone forgot to declare and 4e-5
    is a collision, and the verdict alone does not say which."""

    verdict: str
    ratio: float | None

    @property
    def confirmed(self) -> bool:
        """Agreement, and nothing else. ``IN_RANGE`` and ``NO_PRICE`` are both *unconfirmed*:
        one is probably fine and the other is unknown, and neither is evidence."""
        return self.verdict == MATCH


def compare(
    *,
    mark: float,
    close: float | None,
    low: float = 0.0,
    high: float = 0.0,
    scale: float | None = None,
    tolerance: float = MATCH_TOLERANCE,
) -> Comparison:
    """Compare an independent ``mark`` against our own ``close`` for the same asset.

    ``scale`` is ``cfg/venue_map.yaml``'s declared ratio, ours over theirs — 10.03 means SPY
    against the S&P. Applying it is what lets a *declared* proxy confirm while an undeclared
    one still fails: RUT carried IWM with no scale, and an order quoted on the index would have
    gone out at a tenth of the price. Absent the declaration there is nothing in the arithmetic
    that distinguishes that from a collision, and it must not pass.

    ``low``/``high`` are our recent trading band, in *our* units — they are scaled alongside the
    close for the same reason, or a proxy's timing gap would read as a collision.

    A ``mark`` of zero is absence, not a price near zero: it is what a venue returns for a
    symbol it does not answer for, and a mapped symbol no venue answers for is a curated fact
    that would refuse at order time. It reports ``NO_PRICE`` rather than an enormous ratio.
    """
    if not close or not mark:
        return Comparison(verdict=NO_PRICE, ratio=None)

    divisor = scale if scale else 1.0
    expected = close / divisor
    ratio = mark / expected

    if abs(ratio - 1.0) <= tolerance:
        return Comparison(verdict=MATCH, ratio=ratio)
    if low and high and (low / divisor) <= mark <= (high / divisor):
        return Comparison(verdict=IN_RANGE, ratio=ratio)
    return Comparison(verdict=DIFFERS, ratio=ratio)
