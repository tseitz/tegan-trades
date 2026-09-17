"""What "just holding the pot flat since day one" would have returned — #71.

The anchor basket is the mandate's holdings on the oldest day the cached transaction feed
reaches, reconstructed backwards from today's shares. Two kinds of money act on it
differently: **external** money (a deposit or withdrawal) issues or redeems units at the
current value per unit, so it can never itself move the return — it is new money, not skill.
**Income** (a dividend, and `interest` riding in under the same `kind` — see
`core.transactions.InvestmentTransaction.kind`) is reinvested into the basket, raising value
per unit while leaving unit count alone, per ADR-0003's "a dividend-heavy holding must not
read as losing to itself".

**Two named approximations this module does not retire.** The dividend rows are the ones the
account actually received against its *traded* holdings, not the anchor basket, so reinvesting
them onto the anchor basket is a stand-in ADR-0003 asks for but does not size. And
`cash/interest` is folded into `"dividend"` by `InvestmentTransaction.kind` — sweep interest,
not basket income — because re-splitting that taxonomy would cross the boundary #65 drew for
23 small rows.

**The trap:** the `other` classification is a catch-all keyed on `amount`'s *sign*, not a rule
about `transfer`/`transfer` specifically. Any future Plaid pair that falls through to `"other"`
with a positive amount silently becomes a withdrawal.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from core.transactions import InvestmentTransaction

# No sub-1e-4 residue in the real 1972-row reconstruction; anything this small is float noise,
# not a real fractional share.
_EPSILON = 1e-4

PriceOn = Callable[[str, date], float | None]


@dataclass(frozen=True, slots=True)
class Flow:
    """One flow against the basket. ``amount`` is money-in-positive — the opposite of Plaid's
    own "positive amount leaves the account" convention; `flow_from` is where the sign flips."""
    date: date
    amount: float
    kind: Literal["external", "income"]


@dataclass(frozen=True, slots=True)
class Basket:
    anchor: date
    shares: dict[str, float]
    flows: tuple[Flow, ...]


def flow_from(t: InvestmentTransaction) -> Flow | None:
    """Classify one transaction, or ``None`` when the basket ignores it (a trade, a fee) or
    cannot classify it (an ``other`` row carrying a security — see `unclassified`)."""
    kind = t.kind
    if kind in ("deposit", "withdrawal"):
        return Flow(date=t.date, amount=-t.amount, kind="external")
    if kind == "dividend":
        return Flow(date=t.date, amount=-t.amount, kind="income")
    if kind in ("buy", "sell", "fee"):
        return None
    if kind == "other" and t.security_id is None:
        return Flow(date=t.date, amount=-t.amount, kind="external")
    return None


def unclassified(
    transactions: tuple[InvestmentTransaction, ...], *, anchor: date,
) -> tuple[InvestmentTransaction, ...]:
    """``other`` rows carrying a security since the anchor — neither cash nor a trade, and
    guessing which would be exactly the failure mode `flow_from` is built to avoid."""
    return tuple(
        t for t in transactions
        if t.date >= anchor and t.kind == "other" and t.security_id is not None
    )


def anchor_shares(
    shares_today: dict[str, float], transactions: tuple[InvestmentTransaction, ...],
    *, anchor: date,
) -> dict[str, float] | None:
    """Today's shares, walked backward over every buy/sell since the anchor. Plaid signs
    ``quantity`` consistently (buy positive, sell negative), so summing it directly gives the
    net shares acquired since the anchor with no need to consult `kind` for direction.

    ``None`` when any ticker nets negative — unresolvable, and never clamped, because clamping
    would silently shrink the basket the whole benchmark rests on."""
    net_since_anchor: dict[str, float] = {}
    for t in transactions:
        if t.date < anchor or t.kind not in ("buy", "sell") or t.ticker is None or t.quantity is None:
            continue
        net_since_anchor[t.ticker] = net_since_anchor.get(t.ticker, 0.0) + t.quantity

    result: dict[str, float] = {}
    for ticker in set(shares_today) | set(net_since_anchor):
        reconstructed = shares_today.get(ticker, 0.0) - net_since_anchor.get(ticker, 0.0)
        if reconstructed < -_EPSILON:
            return None
        if reconstructed > _EPSILON:
            result[ticker] = reconstructed
    return result


def build(
    shares_today: dict[str, float], transactions: tuple[InvestmentTransaction, ...],
    *, anchor: date,
) -> Basket | None:
    shares = anchor_shares(shares_today, transactions, anchor=anchor)
    if shares is None:
        return None
    flows = tuple(sorted(
        (f for f in (flow_from(t) for t in transactions if t.date >= anchor) if f is not None),
        key=lambda f: f.date,
    ))
    return Basket(anchor=anchor, shares=shares, flows=flows)


def unpriced_at(basket: Basket, price_on: PriceOn, day: date) -> tuple[str, ...]:
    return tuple(ticker for ticker in basket.shares if price_on(ticker, day) is None)


def raw_value(basket: Basket, price_on: PriceOn, day: date) -> float | None:
    """The anchor basket's ex-dividend value on ``day``, or ``None`` when any member has no
    price that day — never price a partial basket."""
    total = 0.0
    for ticker, shares in basket.shares.items():
        price = price_on(ticker, day)
        if price is None:
            return None
        total += shares * price
    return total


def state_on(basket: Basket, price_on: PriceOn, day: date) -> tuple[float, float] | None:
    """``(units, value_per_unit)`` on ``day``, walking every flow up to and including it from a
    starting state of one unit valued at the anchor's raw basket value. One walk producing both
    numbers, so the reinvestment multiplier and the unit count can never diverge across two
    separate traversals.

    ``None`` before the anchor. Without this, a fixed window longer than the account's actual
    history (a 1y window on a 15-day-old basket) would price *today's* holdings on a day before
    the basket existed instead of reporting "too new", the same way `PriceSeries.close_on`
    returns `None` before its own first bar rather than the nearest one it has."""
    if day < basket.anchor:
        return None
    units = 1.0
    multiplier = 1.0
    for flow in basket.flows:
        if flow.date > day:
            break
        raw_t = raw_value(basket, price_on, flow.date)
        if raw_t is None:
            return None
        value_per_unit = multiplier * raw_t
        if value_per_unit == 0:
            return None
        if flow.kind == "income":
            multiplier *= 1 + flow.amount / (units * value_per_unit)
        else:
            units += flow.amount / value_per_unit

    raw_day = raw_value(basket, price_on, day)
    if raw_day is None:
        return None
    return units, multiplier * raw_day


def value_on(basket: Basket, price_on: PriceOn, day: date) -> float | None:
    state = state_on(basket, price_on, day)
    if state is None:
        return None
    units, value_per_unit = state
    return units * value_per_unit


def return_over(basket: Basket, price_on: PriceOn, start: date, end: date) -> float | None:
    """``V(end) / V(start) - 1``. External flows cancel out of this by construction — value per
    unit never moves for them — which is how a deposit or withdrawal leaves the return
    unchanged without a guard someone could later delete."""
    start_state = state_on(basket, price_on, start)
    end_state = state_on(basket, price_on, end)
    if start_state is None or end_state is None:
        return None
    _, v_start = start_state
    _, v_end = end_state
    if v_start == 0:
        return None
    return v_end / v_start - 1
