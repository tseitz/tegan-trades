"""How a trade ended: which leg took it, at what price, what it made, and whether to believe it.

Pure. No I/O, no network — ``store.record_close`` persists what this computes.

**This is the dependent variable.** ``store``'s docstring says the candidate-to-order join is
what makes it possible "to ever ask whether the scorer predicts outcomes", and until this module
existed the answer stopped at *the entry filled*. Everything here exists to make one row per
finished trade that a calibration pass can actually regress against.

WHY BOTH R DENOMINATORS ARE RECORDED. An entry is a limit order, so it can fill *better* than
planned, and then the risk actually taken is not the risk that was budgeted. INTL: sized to risk
$999.79 to a 29.19 stop from a planned 29.80 entry, filled at 29.621233, so the real distance to
the stop was 0.431233 and the real exposure $706.79. The same realised +$2,603.99 is **+2.60R**
against the budget and **+3.68R** against the fill. Neither is wrong; picking one silently would
bake a choice nobody made into every future calibration, so both are written and the reader
chooses. (Against the *budget* answers "did the sizing model work"; against the *fill* answers
"did the trade work".)

WHY FILL CREDIBILITY IS A FIELD AND NOT A FOOTNOTE. The trade that motivated this module exited
1,639 shares of a stock whose median session is 19,262 shares — 8.51%, against a participation
ceiling of 1% — and it filled instantly, in two prints, both at the quote. On paper that is what
always happens: the simulator matches against the SIP quote without ever consuming the book. As
evidence about whether the strategy works, that fill is worth nothing. Recorded, the row labels
itself unusable; unrecorded, it enters the first calibration pass as a clean +2.6R and quietly
raises the estimate of every setup that looked like it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

# How the position came off. ``target`` and ``stop`` are read from the order id, never inferred
# from the price — a stop that gapped can fill *through* the target's side of the entry, and a
# manual exit can land anywhere.
TARGET = "target"
STOP = "stop"
MANUAL = "manual"
# The leg ids were not recorded, so which one filled cannot be recovered. Deliberately distinct
# from ``manual``: "it was neither leg" is knowledge, "there is nothing to compare against" is
# the absence of it, and pooling them would invent hand-exits that never happened.
UNKNOWN = "unknown"

# Alpaca's activity feed labels every print of a working order ``partial_fill`` until the last,
# which is ``fill``. Both moved shares; the distinction belongs to the order, not the trade.
FILL_ACTIVITY = "FILL"


def number(value) -> float | None:
    """A float, or ``None`` for anything that will not parse. Public because the log rows the
    close pass reads are as untrustworthy as the venue payloads this module was written for —
    ``store`` has carried fields that older rows simply do not have."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value) -> datetime | None:
    """Same parser as ``book._timestamp``, and deliberately as forgiving: an unreadable stamp
    costs the holding period, not the whole close."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ExitFill:
    """One print from the venue's activity feed. ``order_id`` is what makes the exit
    attributable — it is the only field that ties a print back to a bracket leg."""
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    at: datetime | None


@dataclass(frozen=True)
class TradeClose:
    """One closing order, however many prints it took."""
    candidate_key: str
    symbol: str
    order_id: str
    qty: float
    price: float                # qty-weighted across prints
    at: datetime | None         # the LAST print — when the position actually went flat
    reason: str
    prints: int


@dataclass(frozen=True)
class Realized:
    """What the trade made, in dollars and in both R's. See the module docstring."""
    pnl: float
    risk_planned: float | None
    risk_at_fill: float
    r_planned: float | None
    r_at_fill: float | None


@dataclass(frozen=True)
class FillQuality:
    """Whether this fill is evidence. ``None`` means unmeasured, never measured-and-fine."""
    participation: float | None
    paper: bool
    credible: bool | None


def parse_fills(activities) -> tuple[ExitFill, ...]:
    """Readable ``FILL`` rows from the venue's activity feed, oldest first.

    A row missing or malforming any load-bearing field is **skipped, not defaulted**. A price
    coerced to zero would drag the weighted average toward nothing and report a loss that never
    happened — the same reasoning as ``participation.depth_from_bars``.
    """
    fills: list[ExitFill] = []
    for raw in activities or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("activity_type") or FILL_ACTIVITY) != FILL_ACTIVITY:
            continue
        qty = number(raw.get("qty"))
        price = number(raw.get("price"))
        order_id = raw.get("order_id")
        if qty is None or price is None or order_id is None or qty <= 0:
            continue
        fills.append(ExitFill(
            order_id=str(order_id),
            symbol=str(raw.get("symbol") or ""),
            side=str(raw.get("side") or ""),
            qty=qty,
            price=price,
            at=_timestamp(raw.get("transaction_time")),
        ))
    return tuple(sorted(fills, key=lambda f: (f.at is None,
                                              f.at or datetime.max.replace(tzinfo=UTC))))


def reason_for(order_id: str, order_ids) -> str:
    """Which leg filled, from the id alone.

    ``store.record_placement`` writes ``order_ids`` as ``[entry, take_profit, stop_loss]`` —
    the order ``alpaca_wire`` builds the bracket in — so the exit's own id names the outcome
    exactly. **Read rather than inferred**, because inference from price is wrong in both
    directions: a stop that gaps can fill far past its trigger, and a manual exit can land
    anywhere including exactly on a level.
    """
    ids = [str(i) for i in (order_ids or [])]
    if len(ids) < 3:
        return UNKNOWN
    if order_id == ids[1]:
        return TARGET
    if order_id == ids[2]:
        return STOP
    return MANUAL


def close_from_fills(fills, *, candidate_key: str, order_ids) -> TradeClose | None:
    """Aggregate the prints of one closing order into a single close. ``None`` if there are none.

    ``None`` rather than a zero-quantity close: no exit prints means the position is still
    open, which is not an outcome and must not be written as one.

    Priced qty-weighted, never as a plain mean over prints. Today's INTL exit went 699 then
    940 at the same price, where the two agree by coincidence — the moment a large order walks
    the book they diverge, and it is the large orders whose price matters most.
    """
    fills = tuple(fills)
    if not fills:
        return None
    qty = sum(f.qty for f in fills)
    return TradeClose(
        candidate_key=candidate_key,
        symbol=fills[0].symbol,
        order_id=fills[0].order_id,
        qty=qty,
        price=sum(f.qty * f.price for f in fills) / qty,
        # The last print, not the first: the position is flat when the final share is sold.
        at=fills[-1].at,
        reason=reason_for(fills[0].order_id, order_ids),
        prints=len(fills),
    )


def entry_end(fills, entry_order_id: str) -> datetime | None:
    """When the entry finished filling, from its own prints. ``None`` if it is not in the feed.

    **The reconciled row cannot answer this.** Its ``at`` is when the reconcile pass *ran*,
    which on INTL was 20:57 for a fill at 13:34 — seven hours late. Using it as the boundary
    would discard any exit that happened between the fill and the nightly, i.e. exactly the
    same-day round trips (a bracket stopping out on a gapped open) that most need recording.

    The LAST print, because a large entry arrives in pieces and the position is only fully on
    when the final share is bought.
    """
    stamps = [f.at for f in fills if f.order_id == entry_order_id and f.at is not None]
    return max(stamps) if stamps else None


def exit_fills(fills, *, symbol: str, entry_order_id: str, exit_side: str,
               after: datetime) -> tuple[ExitFill, ...]:
    """The prints that took this position off, out of an account-wide feed.

    Four filters, and each one is a way of crediting the wrong trade if it is missing:

    * ``symbol`` — the feed carries every instrument the account traded.
    * ``exit_side`` — the entry's own prints are in the feed too, on the opposite side.
    * ``after`` — an earlier, separate position in the same symbol also closed on this side.
    * ``entry_order_id`` — excluded **by id, not only by time**, so a straggling entry print
      landing after the boundary cannot be misread as a partial close.
    """
    return tuple(
        f for f in fills
        if f.symbol == symbol
        and f.order_id != entry_order_id
        and f.side == exit_side
        and f.at is not None and f.at >= after
    )


def group_by_order(fills) -> dict[str, tuple[ExitFill, ...]]:
    """Prints bucketed by the order that produced them, in first-seen order.

    A position can come off in pieces with different *reasons* — the target takes half and the
    remainder is sold by hand. Those are two outcomes, and averaging them into one row would
    describe a trade that did not happen. One close per order id keeps them separable.
    """
    groups: dict[str, list[ExitFill]] = {}
    for fill in fills:
        groups.setdefault(fill.order_id, []).append(fill)
    return {order_id: tuple(group) for order_id, group in groups.items()}


def exit_side_for(direction: str) -> str:
    """The side that closes a position. A long is sold, a short is bought back."""
    return "sell" if direction == "long" else "buy"


# How much of the entry must have come back off before the trade counts as finished. Not 1.0
# exactly: perp sizes are fractional and a venue's own rounding can leave a remainder no order
# will clear, which an exact test would strand as permanently open. Equities fill in whole
# shares and are unaffected either way.
FLAT_TOLERANCE = 0.999


def is_flat(*, exit_qty: float, entry_qty: float) -> bool:
    """Has the whole position come off?

    Guards the one way this pass can lose data: writing a ``closed`` row for a half-exited
    position takes the candidate off the work list while shares are still on the book, and the
    remaining exit is then never looked for again. A partial exit stays pending instead.
    """
    if entry_qty <= 0:
        return False
    return exit_qty >= entry_qty * FLAT_TOLERANCE


def realized(*, direction: str, entry: float, exit_price: float, qty: float,
             stop: float, risk_planned: float | None) -> Realized:
    """P/L in dollars and in both R's. See the module docstring for why there are two.

    A missing ``risk_planned`` yields ``None``, never 0.0 — "no risk recorded" and "risked
    nothing" must not read the same, and a zero would present as a scratch. Same asymmetry
    ``store.risk_by_key`` is built on.
    """
    move = exit_price - entry if direction == "long" else entry - exit_price
    pnl = move * qty
    # Distance to the stop from where the entry ACTUALLY filled. Absolute, so it is a magnitude
    # on either side and a long and a short divide the same way.
    risk_at_fill = abs(entry - stop) * qty
    return Realized(
        pnl=pnl,
        risk_planned=risk_planned,
        risk_at_fill=risk_at_fill,
        r_planned=(pnl / risk_planned
                   if risk_planned is not None and risk_planned > 0 else None),
        # Undefined rather than infinite when the stop sits on the entry. Raising here would
        # take out a nightly step over a degenerate row.
        r_at_fill=pnl / risk_at_fill if risk_at_fill > 0 else None,
    )


def fill_quality(*, qty: float, median_volume: float | None, ceiling: float,
                 paper: bool) -> FillQuality:
    """Is this fill evidence about the strategy, or an artefact of the simulator?

    Two independent ways to fail, and either is disqualifying:

    * **Size.** An exit above the participation ceiling did not have to compete for liquidity
      in any market that would actually have made it compete. See ``participation``.
    * **Venue.** Paper matches against the quote without consuming the book, so *every* paper
      fill is a fill that was never tested. A small one is realistic by luck, and luck is not
      a property worth recording as credibility.

    ``median_volume=None`` yields ``credible=None`` — ``participation.check_depth``'s asymmetry
    carried through to the record. Not measured must never read as measured-and-fine.
    """
    if median_volume is None or median_volume <= 0:
        return FillQuality(participation=None, paper=paper, credible=None)
    participation = qty / median_volume
    return FillQuality(
        participation=participation,
        paper=paper,
        credible=(not paper) and participation <= ceiling,
    )
