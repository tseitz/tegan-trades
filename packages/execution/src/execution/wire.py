"""The exact payload sent to the venue, and the reply parsed back. Pure — no network.

Split out of ``broker`` so the part that is easy to get wrong is the part that is fully
unit-tested. The broker becomes a shell that signs and posts whatever this produces.

**The bracket.** Three orders sent as one action under the ``normalTpsl`` grouping: a resting
entry, plus a take-profit and a stop-loss that only become live once the entry fills. Sending
them separately would leave a window where a filled entry has no stop attached to it.

**Why the two triggers are not symmetric.** The take-profit is a *limit* trigger at the
target — there is no urgency, and a limit gets that price or better. The stop-loss is a
*market* trigger with a slippage allowance, because a stop that fails to fill is not a stop.
That asymmetry is deliberate: one order exists to capture a price, the other exists to
guarantee an exit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from execution.plan import OrderPlan
from execution.rounding import round_price

# Matches the SDK's own ``Exchange.DEFAULT_SLIPPAGE``. How far past the stop trigger the
# protective order is still willing to fill.
STOP_SLIPPAGE = 0.05

GROUPING = "normalTpsl"

# Whether the take-profit fires as a market order rather than a limit at the target.
#
# **This is the one choice here not copied from a verified reference.** The bracket otherwise
# matches the SDK's own ``examples/basic_tpsl.py`` leg for leg — same order, same sides, same
# reduce_only, same slippage direction on the stop — but that example uses a *market* take
# -profit and this uses a limit one, to get the target price or better rather than whatever is
# available. A limit TP is offered by the venue's own UI so it is expected to be accepted;
# it has not been confirmed against a live grouped order. If a placement is ever rejected
# with a complaint about the take-profit leg, flip this to True first.
TAKE_PROFIT_IS_MARKET = False

# Per-leg outcomes the venue reports as **bare strings** rather than objects.
#
# Confirmed against a live testnet bracket on 2026-07-27, whose reply was
# ``[{"resting": {"oid": …}}, "waitingForFill", "waitingForFill"]`` — the entry rests as an
# object, and the two triggers come back as plain strings meaning "armed, waiting for the
# parent to fill". That is the **success** case for a ``normalTpsl`` group, and reading it as
# a failure reports a perfectly good bracket as rejected.
WAITING_STATUSES = frozenset({"waitingForFill", "waitingForTrigger"})


@dataclass(frozen=True)
class Placement:
    """What the venue said. ``ok`` is false if *any* leg of the bracket was rejected.

    Shared by every venue, so ``order_ids`` is deliberately loose: Hyperliquid mints integer
    oids and Alpaca mints UUID strings. Both are opaque handles whose only use is finding an
    order again to cancel it, and ``store`` writes them to JSON either way — narrowing this to
    one venue's type would force the other to lie about its own identifiers.
    """
    ok: bool
    order_ids: tuple[int | str, ...] = ()
    statuses: tuple[str, ...] = ()
    error: str | None = None
    raw: dict = field(default_factory=dict)


def stop_limit_price(plan: OrderPlan, sz_decimals: int,
                     *, slippage: float = STOP_SLIPPAGE) -> float:
    """The worst price the stop-loss will still accept, on the correct side of the trigger.

    A long exits by selling, so it must tolerate filling *below* the trigger; a short exits by
    buying and must tolerate filling *above* it. Getting this sign backwards produces a stop
    that can never fill — which looks like a working bracket right up until it is needed.
    """
    factor = (1 - slippage) if plan.is_buy else (1 + slippage)
    return round_price(plan.stop * factor, sz_decimals)


def order_requests(plan: OrderPlan, sz_decimals: int,
                   *, slippage: float = STOP_SLIPPAGE) -> list[dict]:
    """The three legs, in the order the venue expects them for ``normalTpsl``.

    Both exit legs are ``reduce_only`` and take the opposite side of the entry — without
    ``reduce_only`` a trigger firing on an unfilled entry would open a *new* position in the
    opposite direction rather than closing anything.
    """
    exit_is_buy = not plan.is_buy
    return [
        {
            "coin": plan.coin,
            "is_buy": plan.is_buy,
            "sz": plan.size,
            "limit_px": plan.entry,
            "order_type": {"limit": {"tif": "Gtc"}},
            "reduce_only": False,
        },
        {
            "coin": plan.coin,
            "is_buy": exit_is_buy,
            "sz": plan.size,
            "limit_px": plan.target,
            "order_type": {
                "trigger": {
                    "triggerPx": plan.target,
                    "isMarket": TAKE_PROFIT_IS_MARKET,
                    "tpsl": "tp",
                }
            },
            "reduce_only": True,
        },
        {
            "coin": plan.coin,
            "is_buy": exit_is_buy,
            "sz": plan.size,
            "limit_px": stop_limit_price(plan, sz_decimals, slippage=slippage),
            "order_type": {
                "trigger": {"triggerPx": plan.stop, "isMarket": True, "tpsl": "sl"}
            },
            "reduce_only": True,
        },
    ]


def parse_placement(raw) -> Placement:
    """Turn the venue's reply into a verdict, treating anything unrecognised as failure.

    The reply nests four levels deep and reports per-leg outcomes in a ``statuses`` array
    where a rejected leg is ``{"error": ...}`` rather than an HTTP failure. A partially
    rejected bracket therefore arrives as ``status: ok``, which is exactly the case that must
    not be read as success — an entry that rested while its stop was rejected is the worst
    outcome available, so any leg erroring makes the whole placement not-ok.
    """
    if not isinstance(raw, dict):
        return Placement(ok=False, error=f"unrecognised reply: {raw!r}", raw={})

    if raw.get("status") != "ok":
        return Placement(ok=False, error=str(raw.get("response") or raw.get("status")), raw=raw)

    data = ((raw.get("response") or {}).get("data") or {})
    statuses = data.get("statuses")
    if not statuses:
        return Placement(ok=False, error="reply carried no order statuses", raw=raw)

    order_ids: list[int] = []
    labels: list[str] = []
    errors: list[str] = []
    for status in statuses:
        # Trigger legs report as bare strings. Known waiting states are successes; any other
        # string is still treated as a failure, so this stays fail-closed against a status
        # the venue adds later that might not be benign.
        if isinstance(status, str):
            if status in WAITING_STATUSES:
                labels.append(status)
            else:
                errors.append(f"unrecognised status {status!r}")
                labels.append("error")
            continue
        if not isinstance(status, dict):
            errors.append(f"unrecognised status {status!r}")
            labels.append("error")
            continue
        if "error" in status:
            errors.append(str(status["error"]))
            labels.append("error")
            continue
        for kind in ("resting", "filled"):
            if kind in status:
                labels.append(kind)
                oid = (status[kind] or {}).get("oid")
                if oid is not None:
                    order_ids.append(int(oid))
                break
        else:
            labels.append(str(next(iter(status), "unknown")))

    return Placement(
        ok=not errors,
        order_ids=tuple(order_ids),
        statuses=tuple(labels),
        error="; ".join(errors) or None,
        raw=raw,
    )
