"""One risk number for the whole book, across every venue. Pure.

``budget`` answers "can this account commit another position" and does it per venue, because
buying power is per venue and there is nothing to pool: a perp margin pool cannot fund equity
buying power and no transfer path exists between them. **Risk is the opposite kind of quantity.**
Losing 1% on Hyperliquid and 1% on Alpaca is losing 2%, and the account it is 2% of is the
person's, not the venue's. So risk pools and buying power does not, and conflating the two is
the bug this module exists to avoid rather than a subtlety to be careful about later.

MEASURED, 2026-07-29, and this is the sitting the whole thing is for. Eight brackets went out
between 03:24 and 04:01 ET, each sized to risk 1% of the same $100,000 account:

    INTL 1.00%  HOOD 0.99%  RKLB 0.99%  CRM 0.98%  CRM 0.99%  VRT 0.99%  BE 0.99%  SBSW 1.00%

Together, **7.94%**. Nothing computed that, nothing displayed it, and the way it surfaced was
the venue rejecting three orders at the open — hours after this repo had logged them as placed.
``6e404e4`` fixed the *notional* half of that night (buying power, per venue); this is the risk
half, which pools. Re-measure with ``scripts/probe_portfolio_budget.py``.

WHY 5% AND NOT A ROUND GUESS. ``Config.max_position_frac`` is 0.20, which already says five
positions fit a 1x account — and five positions at the 1% risk budget risk 5%. The two settings
are one choice wearing two names (see ``sizing.notional_ceiling``: ``1/ceiling`` positions fit at
1x, so concurrency and per-trade risk cannot be chosen independently). A pooled ceiling that
disagreed with the concentration cap would make one of the two dead, and it would not be obvious
which. Against the sitting above it admits the first five in full and offers the sixth 3.2% of
what it asked for — refused for a stated reason, rather than sent and killed at the open.

**A silent venue is named, never assumed.** Its equity is missing from the denominator, which
tightens the ceiling, *and* its open risk is missing from the total, which loosens it — so which
way the answer is wrong is not knowable from here. What is knowable is that it is partial, and
that is what gets said. A pool no venue answered is ``known == False`` and switches the ceiling
off rather than setting it to zero, on the asymmetry ``account.parse_account`` documents: an
unreadable balance defaulting to 0.0 refuses every order in the account, which is much worse
than the failure this gate prevents.

WHAT IS NOT COUNTED, and it must not read as zero. ``spent`` is whatever the caller can account
for — for a ``Session`` that is the risk of everything **this repo** placed and the venue still
reports as live. A position opened by hand is invisible to it, and so is one placed before the
order log existed. On Hyperliquid in *manual* margin mode each HIP-3 builder holds its own
collateral, so a pool built from the core balance alone understates a book trading ``xyz:``
markets (under unified/portfolio margin there is one pool and the question does not arise — see
``broker.HyperliquidBroker.equity``). Both gaps make the ceiling tighter, which is the safe
direction, and both are stated where the number is shown rather than only here.
"""
from __future__ import annotations

from dataclasses import dataclass

from execution.guards import Refusal

# The book is carrying all the risk it is allowed to. Its own code and not ``no_headroom``:
# that one means *this venue* has no buying power and calls for cancelling a resting order
# there, while this one means the portfolio is full and may call for closing something on a
# different venue entirely. Same rule as ``REFUSAL_NO_HEADROOM`` against ``dust``.
REFUSAL_PORTFOLIO_FULL = "portfolio_full"

# Fraction of combined equity that may be at risk across every venue at once. See the module
# docstring — this is ``max_position_frac`` restated in risk terms, not an independent choice.
MAX_PORTFOLIO_RISK = 0.05


@dataclass(frozen=True, slots=True)
class Pool:
    """Combined equity across venues, and which venues could not be counted.

    ``silent`` is carried beside ``equity`` rather than derived from it because the two answer
    different questions — how much backs the book, and how much of the book was measurable —
    and a total shown without the second is a partial number wearing a complete one's clothes.
    """
    equity: float
    answered: tuple[str, ...] = ()
    silent: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Every venue answered. A ceiling from an incomplete pool is still usable, and still
        has to say so."""
        return not self.silent

    @property
    def known(self) -> bool:
        """At least one venue answered, so there is a denominator at all."""
        return bool(self.answered)


def combine(equity_by_venue: dict[str, float | None]) -> Pool:
    """Sum what the venues reported. ``None`` from a venue means it did not answer.

    Floors each contribution at zero, matching ``account.parse_account``: a debit balance is not
    a budget, and letting one venue's deficit buy room on another would be arithmetic nobody
    asked for.
    """
    answered = tuple(v for v, e in sorted(equity_by_venue.items()) if e is not None)
    silent = tuple(v for v, e in sorted(equity_by_venue.items()) if e is None)
    total = sum(max(0.0, e) for e in equity_by_venue.values() if e is not None)
    return Pool(equity=total, answered=answered, silent=silent)


def remaining(pool: Pool, *, spent: float,
              max_risk: float | None = MAX_PORTFOLIO_RISK) -> float | None:
    """Dollars of risk the book may still take on. ``None`` means the ceiling does not apply.

    ``None`` in two cases, both of which leave the gate **off** rather than refusing everything:
    no venue answered (there is no denominator) and no ceiling configured. Zero is different and
    means the book is full — the same None/0.0 distinction ``Account.headroom`` draws.

    Floors at zero: an over-risked book has no room, not negative room.
    """
    if max_risk is None or not pool.known:
        return None
    return max(0.0, pool.equity * max_risk - max(0.0, spent))


def size_ceiling(*, remaining: float | None, entry: float, stop: float) -> float | None:
    """Largest size whose stop-out costs at most ``remaining``. ``None`` passes through.

    The inverse of ``sizing.size_for_risk`` against the room that is left rather than against
    the per-trade budget, so it composes with the other three ceilings in ``apply_caps`` instead
    of competing with them.

    Raises on a zone with no stop distance, matching ``size_for_risk``: a caller passing that
    has a bug, and substituting a reasonable number would place a real order on it.
    """
    if remaining is None:
        return None
    distance = abs(entry - stop)
    if distance == 0:
        raise ValueError(
            f"entry and stop are both {entry}; a zone with no stop distance cannot be sized"
        )
    return remaining / distance


@dataclass(frozen=True, slots=True)
class Book:
    """The portfolio's risk state: what backs it, what is at stake, and the ceiling.

    Exists so ``plan.build`` takes one argument rather than four that must agree with each
    other. Every ceiling in that function already travels as a single value; this one needs its
    provenance as well, because the refusal has to be able to say *which venues* the total
    covers and which it could not reach.
    """
    pool: Pool
    spent: float = 0.0
    max_risk: float | None = MAX_PORTFOLIO_RISK
    # Live orders whose risk the log cannot state, so ``spent`` is a LOWER bound. Counted rather
    # than shrugged off because an under-counted total *loosens* the ceiling, which is the
    # dangerous direction — the opposite of a silent venue's effect on the denominator.
    unpriced: int = 0

    @property
    def exact(self) -> bool:
        """Every venue answered and every live order's risk is known."""
        return self.pool.complete and self.unpriced == 0

    @property
    def remaining(self) -> float | None:
        return remaining(self.pool, spent=self.spent, max_risk=self.max_risk)

    @property
    def at_stake(self) -> float:
        """Risk already taken, as a fraction of combined equity."""
        return self.spent / self.pool.equity if self.pool.equity else 0.0

    def size_ceiling(self, *, entry: float, stop: float) -> float | None:
        return size_ceiling(remaining=self.remaining, entry=entry, stop=stop)

    def check_fill(self, *, fitted: float, wanted: float, needed: float,
                   min_fill: float) -> Refusal | None:
        left = self.remaining
        if left is None:
            return None
        return check_fill(fitted=fitted, wanted=wanted, remaining=left, needed=needed,
                          pool=self.pool, spent=self.spent, min_fill=min_fill,
                          unpriced=self.unpriced)


def check_fill(*, fitted: float, wanted: float, remaining: float, needed: float,
               pool: Pool, spent: float, min_fill: float, unpriced: int = 0) -> Refusal | None:
    """Is what the book still allows worth sending? ``None`` means yes — shrink and carry on.

    The same shape as ``budget.check_fill`` and for the same reason: shrinking to whatever is
    left would make an order's risk an artefact of where its candidate fell in tonight's queue,
    while refusing outright would throw away a trade that fits in all but the last few percent.

    ``min_fill`` is the caller's ``min_budget_fill``, deliberately reused rather than given a
    second setting. The question — "is what fits still recognisably the trade that was
    approved" — is identical, and a second knob nobody tunes is a second knob that drifts.

    A ``wanted`` of zero is not answered here; that is ``guards.check_size``'s dust refusal, and
    reporting it as a full book would name the wrong cause on an untouched account.
    """
    if wanted <= 0:
        return None
    if fitted / wanted >= min_fill:
        return None

    at_stake = spent / pool.equity if pool.equity else 0.0
    caveats = []
    if pool.silent:
        caveats.append(f"{', '.join(pool.silent)} not counted")
    if unpriced:
        caveats.append(f"{unpriced} live order(s) with no recorded risk not counted")
    partial = f" ({'; '.join(caveats)} — the real total is higher)" if caveats else ""
    return Refusal(
        REFUSAL_PORTFOLIO_FULL,
        f"the book already risks {at_stake:.2%} of ${pool.equity:,.2f} across "
        f"{', '.join(pool.answered) or 'no venue'}{partial}, leaving ${remaining:,.2f} of risk "
        f"against the ${needed:,.2f} this order wants — what fits carries "
        f"{fitted / wanted:.0%} of the risk budget, under the {min_fill:.0%} floor. Close or "
        f"resolve a position, or raise max_portfolio_risk.",
    )
