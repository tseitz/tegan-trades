"""The one total nothing computed: what the whole account has already spoken for. Pure.

``sizing.size_for_risk`` is per-trade and correct — every order risks the same fraction of
equity. The gap is that **nothing added them up**. On 2026-07-29 eight 1%-risk orders sized
against the same $100,000 wanted 123.6% of it and 8% of risk in aggregate, and the budget was
enforced by the venue, at the open, by rejecting three of them — hours after this repo had
already logged them as placed.

**This gate is the account's answer, not the market's.** ``participation`` caps because a thin
market cannot carry the size; this caps because the account cannot carry another position at
all. They compose: the first is a property of the instrument and travels with it, the second
depends entirely on what else happens to be open tonight.

WHY IT SHRINKS *AND* REFUSES. Shrinking to whatever is left would make an order's risk an
artefact of where its candidate fell in the queue — the eighth approval of the night gets the
remainder, whatever that happens to be. Refusing outright would throw away a trade that fits
in all but the last few percent. So it shrinks while the fitted order still carries a
meaningful share of the risk it was meant to, and refuses below that.

``MIN_BUDGET_FILL`` is **chosen, not measured**, and it is the one number here without
evidence behind it: half the intended risk is the point where the trade is still recognisably
the trade that was approved. What would settle it is the sidecar (`docs/IMPROVEMENTS.md` §4) —
whether budget-shrunk orders are approved and held at the same rate as full-size ones — and
that needs shrunk orders to exist first. Until then it is a round number, deliberately, so
that nobody reads precision into it.
"""
from __future__ import annotations

from execution.guards import Refusal

# The account is full. Its own code rather than ``dust`` or ``min_notional``, which it would
# otherwise be mistaken for: those two mean the risk budget is too small for this market and
# call for a different market, while this means the account is full and calls for cancelling
# something. Same rule as ``REFUSAL_PROXY`` against ``REFUSAL_UNLISTED``.
REFUSAL_NO_HEADROOM = "no_headroom"

# Smallest share of the intended size a budget-shrunk order may be. See the module docstring —
# this one is a choice, not a measurement.
MIN_BUDGET_FILL = 0.5


def affordable(headroom: float, entry: float) -> float:
    """Units the remaining room pays for, at the price the order will rest at.

    Priced at the entry rather than the mark for the same reason ``size_for_risk``'s notional
    ceiling is: this is a resting limit, so the entry is where the notional is established.
    """
    if headroom <= 0 or entry <= 0:
        return 0.0
    return headroom / entry


def check_fill(*, fitted: float, wanted: float, headroom: float, needed: float,
               min_fill: float = MIN_BUDGET_FILL) -> Refusal | None:
    """Is what fits still worth sending? ``None`` means yes — shrink and carry on.

    ``wanted`` is the size after every other ceiling has applied, so the fraction compared
    against the floor measures what the *budget* cost and not what the market's thinness
    already did. A participation-capped order that then fits its remaining budget exactly is
    not penalised twice.

    A ``wanted`` of zero is not answered here. That is ``guards.check_size``'s dust refusal,
    and reporting it as a full account would name the wrong cause on an untouched account.
    """
    if wanted <= 0:
        return None
    fill = fitted / wanted
    if fill >= min_fill:
        return None
    return Refusal(
        REFUSAL_NO_HEADROOM,
        f"${headroom:,.2f} of buying power is left and this order needs ${needed:,.2f} — "
        f"what fits carries {fill:.0%} of the risk budget, under the {min_fill:.0%} floor. "
        f"Cancel a resting order (`uv run book`) or fund the account.",
    )
