"""What the account is already holding — resting entries and open positions. Pure parsing.

The counterpart to ``account``: that module reads the *total* the venue has committed, this
one reads what makes it up, so a full account can be acted on rather than merely reported.

**Only opening orders are ever returned as resting, and that is a safety property, not a
filter for tidiness.** A bracket's take-profit and stop-loss are ordinary orders in their own
right and appear in the venue's open-orders list exactly like an entry does. Cancelling one of
those does not free any budget — it strips the protection off a live position and leaves it
naked. Alpaca labels the difference itself (``position_intent``: ``buy_to_open`` against
``sell_to_close``), so the distinction is read from the venue rather than guessed at from the
side, which would be exactly backwards on a short.

**A resting entry consumes buying power at full notional and expires in 90 days.** That is the
third of ``docs/IMPROVEMENTS.md`` §40's caps: an approval nobody cancelled sits against the
budget for a quarter, and a zone price has not reached in a fortnight is a staler thesis than
the one that was approved.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

# Order states the venue can still act on. Everything else is done with, and is excluded for
# the same fail-closed reason ``alpaca_broker.TERMINAL_STATUSES`` exists: an unrecognised
# status is treated as still-working, because offering a dead order for cancellation is
# harmless while hiding a live one is not.
DEAD_STATUSES = frozenset({
    "canceled", "expired", "rejected", "done_for_day", "replaced", "filled",
})

# The venue's own label for "this order opens exposure". The only orders safe to cancel.
OPENING_INTENTS = frozenset({"buy_to_open", "sell_to_open"})


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value) -> datetime | None:
    """Alpaca sends RFC 3339 with a ``Z``. Returns None rather than raising on anything else —
    an unparseable timestamp costs an age column, not the whole listing."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class RestingOrder:
    """One entry waiting to fill, and what it is holding while it waits."""
    order_id: str
    candidate_key: str          # the client_order_id this repo set — joins back to the queue
    symbol: str
    side: str                   # buy | sell
    qty: float
    limit_price: float
    submitted_at: datetime | None
    status: str

    @property
    def notional(self) -> float:
        """What it is reserving. Priced at the limit, which is where it will fill if it does."""
        return self.qty * self.limit_price

    def age_days(self, now: datetime) -> float | None:
        if self.submitted_at is None:
            return None
        return (now - self.submitted_at).total_seconds() / 86_400


@dataclass(frozen=True)
class Position:
    """One open position. Listed for context and never cancelled from here — flattening a
    position is a trading decision with a live stop attached to it, not budget housekeeping."""
    symbol: str
    side: str
    qty: float
    market_value: float
    unrealised_pl: float
    opened_at: datetime | None

    def age_days(self, now: datetime) -> float | None:
        if self.opened_at is None:
            return None
        return (now - self.opened_at).total_seconds() / 86_400


def parse_resting(payload) -> tuple[RestingOrder, ...]:
    """Opening entries still working at the venue, oldest first.

    Exit legs are dropped — see the module docstring. An order carrying no ``position_intent``
    falls back to "does it have legs", which is what a bracket parent has and a leg does not;
    an order that answers neither is dropped, because a cancellable order this code cannot
    positively identify as an entry is one it must not offer to cancel.
    """
    orders: list[RestingOrder] = []
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "")
        if status in DEAD_STATUSES:
            continue
        intent = raw.get("position_intent")
        opening = str(intent) in OPENING_INTENTS if intent else bool(raw.get("legs"))
        if not opening:
            continue
        qty = _number(raw.get("qty"))
        limit_price = _number(raw.get("limit_price"))
        order_id = raw.get("id")
        if qty is None or limit_price is None or order_id is None:
            continue
        orders.append(RestingOrder(
            order_id=str(order_id),
            candidate_key=str(raw.get("client_order_id") or ""),
            symbol=str(raw.get("symbol") or ""),
            side=str(raw.get("side") or ""),
            qty=qty,
            limit_price=limit_price,
            submitted_at=_timestamp(raw.get("submitted_at") or raw.get("created_at")),
            status=status,
        ))
    # Oldest first: the list is read to decide what to cancel, and age is the reason.
    return tuple(sorted(orders, key=lambda o: (o.submitted_at is None,
                                               o.submitted_at or datetime.max.replace(tzinfo=UTC))))


def filled_at_by_symbol(payload) -> dict[str, datetime]:
    """When each symbol's entry most recently filled, from the closed-order history.

    Positions carry no timestamp of their own at Alpaca, so the age of a position has to come
    from the order that opened it. Ambiguous if a symbol was entered more than once; the most
    recent fill is used, which is the conservative direction — it reports the position as
    *younger* than it may be, so nothing is retired on the strength of a guess.
    """
    newest: dict[str, datetime] = {}
    for raw in payload or []:
        if not isinstance(raw, dict) or str(raw.get("status") or "") != "filled":
            continue
        intent = raw.get("position_intent")
        if intent and str(intent) not in OPENING_INTENTS:
            continue
        symbol = str(raw.get("symbol") or "")
        at = _timestamp(raw.get("filled_at"))
        if not symbol or at is None:
            continue
        if symbol not in newest or at > newest[symbol]:
            newest[symbol] = at
    return newest


def parse_positions(payload, opened: dict[str, datetime] | None = None
                    ) -> tuple[Position, ...]:
    """Open positions, largest first. ``opened`` supplies the ages ``/v2/positions`` omits."""
    opened = opened or {}
    positions: list[Position] = []
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "")
        qty = _number(raw.get("qty"))
        value = _number(raw.get("market_value"))
        if not symbol or qty is None or value is None:
            continue
        positions.append(Position(
            symbol=symbol,
            side=str(raw.get("side") or ""),
            qty=qty,
            market_value=value,
            unrealised_pl=_number(raw.get("unrealized_pl")) or 0.0,
            opened_at=opened.get(symbol),
        ))
    return tuple(sorted(positions, key=lambda p: -abs(p.market_value)))


def stale(orders, max_age_days: float, now: datetime) -> tuple[RestingOrder, ...]:
    """The entries old enough to be worth retiring. An unknown age is never stale — it is
    unmeasured, and retiring an order on a missing timestamp is the wrong direction of error.
    """
    return tuple(o for o in orders
                 if (age := o.age_days(now)) is not None and age >= max_age_days)
