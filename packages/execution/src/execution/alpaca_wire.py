"""The exact payload sent to Alpaca, and the reply parsed back. Pure — no network.

The equity counterpart to ``wire``, and deliberately its mirror image. Both modules build the
same *thing* — a resting entry with a take-profit and a stop-loss that only arm once the entry
fills — and both refuse to send an entry that could rest without a stop attached to it. What
differs is only how each venue spells it.

**One order, not three.** Alpaca calls this an OTOCO and expresses it as a single order with
two nested exit legs, so the atomicity Hyperliquid gets from the ``normalTpsl`` grouping comes
for free here. There is no window in which a filled entry is unprotected.

**The same asymmetry between the two exits, reached differently.** The take-profit is a limit
at the target — it exists to capture a price, so it should get that price or better. The
stop-loss carries **no** ``limit_price``, which is how Alpaca is told to queue a plain stop:
a market order once triggered. That is the same decision ``wire`` makes by setting
``isMarket`` on the perp stop leg, for the same reason — a stop that fails to fill is not a
stop, and a stop-limit can gap straight through its own limit.

**Whole shares only.** A fractional or notional order cannot carry bracket legs (Alpaca
rejects it as ``42210000``). Since a bracket is the only thing this package sends, an equity
size is always a whole number of shares — see ``shares.round_shares``.
"""
from __future__ import annotations

from execution.plan import OrderPlan
from execution.wire import Placement

# Bracket orders accept only ``day`` or ``gtc``. GTC matches the perp entry, which also rests
# until filled or cancelled rather than expiring silently at the close.
TIME_IN_FORCE = "gtc"

ORDER_CLASS = "bracket"

# Parent-order statuses that mean the venue took the order. ``held`` appears on the exit legs
# of a bracket whose entry has not filled yet — the equity analogue of ``waitingForFill``.
#
# Confirmed against a live paper bracket on 2026-07-28, whose reply was
# ``status: "accepted"`` with both legs ``"held"``. That was placed at 22:45 ET, with the
# market SHUT — so a GTC bracket sent outside hours is queued for the open rather than
# rejected, which is the fact the nightly's 06:15 run depends on. Reading ``accepted`` as a
# failure would report a perfectly good queued bracket as rejected.
#
# Anything not listed here is treated as a failure, including a status Alpaca adds later. The
# same fail-closed rule ``parse_placement`` applies to perp statuses: a status that might not
# be benign must not be read as a resting order.
ACCEPTED_STATUSES = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding", "held",
    "partially_filled", "filled",
})


def _decimal(value: float) -> str:
    """Prices and sizes go on the wire as strings.

    Alpaca accepts numbers too, but a JSON float round-trips through the venue's own decimal
    parser and the failure mode is a rejection naming neither the field nor the cause. Every
    value reaching here has already been put on a legal grid by ``shares``, so the shortest
    round-trip repr is exact.
    """
    return str(float(value))


def bracket_order(plan: OrderPlan) -> dict:
    """The single OTOCO order carrying both exits.

    Raises rather than truncating on a fractional size. ``round_shares`` runs upstream, so a
    fraction arriving here means the perp grid was applied to an equity — a routing bug, and
    one whose venue-side error names a code rather than a cause.
    """
    if plan.size != int(plan.size):
        raise ValueError(
            f"{plan.coin} size {plan.size} is not whole shares; a bracket cannot be attached "
            f"to a fractional order (Alpaca 42210000) — round with shares.round_shares first"
        )
    return {
        "symbol": plan.coin,
        "qty": str(int(plan.size)),
        "side": "buy" if plan.is_buy else "sell",
        "type": "limit",
        "limit_price": _decimal(plan.entry),
        "time_in_force": TIME_IN_FORCE,
        "order_class": ORDER_CLASS,
        "take_profit": {"limit_price": _decimal(plan.target)},
        # No ``limit_price`` — see the module docstring. This is the market-stop choice.
        "stop_loss": {"stop_price": _decimal(plan.stop)},
        # Venue-side duplicate protection, independent of this repo's own order log. Alpaca
        # rejects a re-used client_order_id, so a lost log or a session run twice cannot
        # produce two brackets on one candidate.
        "client_order_id": plan.candidate_key,
    }


def parse_order(raw) -> Placement:
    """Turn Alpaca's reply into a verdict, treating anything unrecognised as failure.

    A rejected *leg* fails the whole placement even when the parent was accepted. That is the
    same judgement ``parse_placement`` makes about a partially rejected perp bracket, and for
    the same reason: an entry resting while its stop was refused is the worst outcome
    available, and it arrives looking like success.
    """
    if not isinstance(raw, dict):
        return Placement(ok=False, error=f"unrecognised reply: {raw!r}", raw={})

    # An error body carries no ``id``. Reported with its code because Alpaca's codes are the
    # documented handle on the cause — 42210000 is the fractional/bracket collision.
    if "id" not in raw:
        message = raw.get("message") or raw.get("error") or repr(raw)
        code = raw.get("code")
        detail = f"{code}: {message}" if code is not None else str(message)
        return Placement(ok=False, error=detail, raw=raw)

    order_ids: list[str] = [str(raw["id"])]
    statuses: list[str] = []
    errors: list[str] = []

    status = str(raw.get("status") or "")
    statuses.append(status)
    if status not in ACCEPTED_STATUSES:
        errors.append(f"entry {status!r}")

    for leg in raw.get("legs") or []:
        if not isinstance(leg, dict):
            errors.append(f"unrecognised leg {leg!r}")
            continue
        # Recorded before the status check, so a half-placed bracket can still be found and
        # cancelled by whoever reads the log.
        if leg.get("id") is not None:
            order_ids.append(str(leg["id"]))
        leg_status = str(leg.get("status") or "")
        statuses.append(leg_status)
        if leg_status not in ACCEPTED_STATUSES:
            errors.append(f"leg {leg_status!r}")

    return Placement(
        ok=not errors,
        order_ids=tuple(order_ids),
        statuses=tuple(statuses),
        error="; ".join(errors) or None,
        raw=raw,
    )
