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
    attributable — it is the only field that ties a print back to a bracket leg.

    ``closing`` is the venue SAYING whether this print opened or closed exposure. Hyperliquid
    does (``dir``: "Close Short"); Alpaca does not, and ``None`` means the reader has to fall
    back to side and timing. Worth carrying because the fallback has a real blind spot: a
    re-entry in the same direction produces a print with the same side as this position's exit,
    and only the venue's own word separates them.

    ``fee`` is what the venue charged for this print. ``None`` on a venue that reports none.
    """
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    at: datetime | None
    closing: bool | None = None
    fee: float | None = None


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
    """What the trade made, in dollars and in R. See the module docstring for the two denominators.

    ``pnl`` is the PRICE MOVE and nothing else, which is what Hyperliquid's own ``closedPnl``
    reports and what a plan priced on levels predicted. ``pnl_net`` is what the account actually
    kept, after what the venue charged to open, close and hold.

    On equities those are the same number. On a perp they are not, and the gap is not small: the
    SOL short closed 2026-08-08 moved -5.66528 and cost 0.17263 in fees plus 3.43939 in funding
    over 11 days, so it was -0.57R on price and -0.93R in fact. Funding scales with the hold, so
    reporting gross alone would flatter long holds least and bias any calibration toward short
    ones for reasons unrelated to the setup.
    """
    pnl: float
    risk_planned: float | None
    risk_at_fill: float
    r_planned: float | None
    r_at_fill: float | None
    # ``None`` means not measured, never "there was none" — see ``realized``.
    fees: float | None = None
    funding: float | None = None
    pnl_net: float | None = None
    r_net_at_fill: float | None = None


@dataclass(frozen=True)
class FillQuality:
    """Whether this fill is evidence. ``None`` means unmeasured, never measured-and-fine.

    ``reasons`` carries the disqualifiers in words. The flag alone invites a reader to dismiss
    the flag rather than the number, and it is written to the row so every surface — the
    listing, the vault note, a future calibration pass — says the same thing.
    """
    participation: float | None
    paper: bool
    credible: bool | None
    stop_survival: float | None = None
    reasons: tuple[str, ...] = ()


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


# Hyperliquid's ``dir`` on a fill. "Close Long" / "Close Short" / "Long > Short" all reduce or
# reverse exposure; "Open Long" / "Open Short" add it. Matched on the leading word so a phrasing
# this list has not seen falls through to ``None`` — unknown, not asserted either way.
def closing_from_dir(direction: str | None) -> bool | None:
    """Did this print reduce exposure? ``None`` where the venue did not say."""
    if not direction:
        return None
    text = str(direction).strip().lower()
    if text.startswith("close"):
        return True
    if text.startswith("open"):
        return False
    return None


# Hyperliquid names its own order kinds, so the exit legs are identified by type rather than by
# the position they appear in — the same read-don't-infer rule ``reason_for`` follows.
def exit_leg_ids(children) -> tuple[str, ...]:
    """A bracket's take-profit and stop oids, in that order, from the entry's ``children``.

    Ordered take-profit-then-stop to match ``store.record_placement``'s ``[entry, tp, sl]``
    convention, so one ``reason_for`` serves both venues. A child whose type names neither is
    dropped rather than guessed at.
    """
    take_profit, stop = [], []
    for child in children or []:
        if not isinstance(child, dict):
            continue
        oid, kind = child.get("oid"), str(child.get("orderType") or "").lower()
        if oid is None:
            continue
        if "take profit" in kind:
            take_profit.append(str(oid))
        elif "stop" in kind:
            stop.append(str(oid))
    return (*take_profit, *stop)


def parse_hl_fills(rows) -> tuple[ExitFill, ...]:
    """Hyperliquid's fill feed as ``ExitFill``s, oldest first.

    Field names differ from Alpaca's throughout (``px``/``sz``/``coin``/``time``), and two fields
    have no Alpaca counterpart: ``dir`` states whether the print opened or closed, and ``fee`` is
    charged per print. Both are carried rather than derived — see ``ExitFill``.

    ``side`` is normalised from the venue's book notation: ``B`` is the bid (a buy), ``A`` the ask
    (a sell). Left as the repo's own words so ``exit_side_for`` compares against one vocabulary.
    """
    fills: list[ExitFill] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        qty, price = number(raw.get("sz")), number(raw.get("px"))
        oid = raw.get("oid")
        if qty is None or price is None or oid is None or qty <= 0:
            continue
        stamp = number(raw.get("time"))
        fills.append(ExitFill(
            order_id=str(oid),
            symbol=str(raw.get("coin") or ""),
            side={"B": "buy", "A": "sell"}.get(str(raw.get("side") or ""), ""),
            qty=qty,
            price=price,
            at=(datetime.fromtimestamp(stamp / 1000, tz=UTC) if stamp is not None else None),
            closing=closing_from_dir(raw.get("dir")),
            fee=number(raw.get("fee")),
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


def entry_price(fills, entry_order_id: str) -> float | None:
    """The qty-weighted price the entry actually filled at, from its own prints.

    Needed because the two venues report it in different places. Alpaca puts it on the order
    (``filled_avg_price``, which lands on the reconciled row); Hyperliquid puts it only in the
    fills — ``query_order_by_oid`` returns size remaining and no price at all. Reading it from
    the feed works for both, and the feed is authoritative either way.
    """
    prints = [f for f in fills if f.order_id == entry_order_id and f.qty > 0]
    if not prints:
        return None
    qty = sum(f.qty for f in prints)
    return sum(f.qty * f.price for f in prints) / qty if qty else None


def fees_for(fills, order_ids) -> float | None:
    """What the venue charged across these orders' prints. ``None`` if nothing can be said.

    Summed over the ENTRY and the exit together, because the question a close row answers is what
    the round trip cost — the SOL short paid 0.04252 to open and 0.13011 to close.

    **A venue that reported prints and charged nothing on any of them charged nothing.** The
    distinction that matters is prints-with-no-fee-field (Alpaca, which is commission-free on
    equities) versus no prints at all (nothing to say). Returning ``None`` for the first would
    make ``costs_known`` false on every equity close and quietly strip ``credible`` from the whole
    Alpaca history over a fee that does not exist.

    NOT COMPLETE ON ALPACA. ``fills`` reads the ``FILL`` activity type only, and regulatory pass-
    throughs (SEC/TAF) and short borrow arrive as their own activity types. Those are cents on
    the trades recorded so far and would matter on a short or at size — the same caveat
    ``AlpacaBroker.funding_paid`` carries.
    """
    wanted = {str(i) for i in (order_ids or [])}
    prints = [f for f in fills if f.order_id in wanted]
    if not prints:
        return None
    return sum(f.fee for f in prints if f.fee is not None)


def exit_fills(fills, *, symbol: str, entry_order_id: str, exit_side: str,
               after: datetime) -> tuple[ExitFill, ...]:
    """The prints that took this position off, out of an account-wide feed.

    Four filters, and each one is a way of crediting the wrong trade if it is missing:

    * ``symbol`` — the feed carries every instrument the account traded.
    * ``exit_side`` — the entry's own prints are in the feed too, on the opposite side.
    * ``after`` — an earlier, separate position in the same symbol also closed on this side.
    * ``entry_order_id`` — excluded **by id, not only by time**, so a straggling entry print
      landing after the boundary cannot be misread as a partial close.

    **Where the venue states whether a print closed exposure, that wins over the side.** Side
    plus timing cannot tell this position's exit from a re-entry in the same direction, and
    Hyperliquid answers the question directly. See ``ExitFill.closing``.
    """
    return tuple(
        f for f in fills
        if f.symbol == symbol
        and f.order_id != entry_order_id
        and (f.closing if f.closing is not None else f.side == exit_side)
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
             stop: float, risk_planned: float | None,
             fees: float | None = None, funding: float | None = None) -> Realized:
    """P/L in dollars and in R. See the module docstring for why there are two denominators.

    A missing ``risk_planned`` yields ``None``, never 0.0 — "no risk recorded" and "risked
    nothing" must not read the same, and a zero would present as a scratch. Same asymmetry
    ``store.risk_by_key`` is built on.

    **``fees`` is a cost and ``funding`` is signed.** Fees are always paid; funding is received
    as often as it is paid, because a short in a backwardated market is on the receiving side.
    Treating funding as a cost regardless would understate a winner as reliably as ignoring it
    overstates a loser.

    **Unmeasured costs leave ``pnl_net`` absent, never equal to gross.** Defaulting them to zero
    makes a perp row claim its holding was free — the SOL short's 209 funding events would have
    read as 0.00. An equity passes ``0.0`` for both, which is a measurement (there is no funding
    on a share) and keeps its net knowable.
    """
    move = exit_price - entry if direction == "long" else entry - exit_price
    pnl = move * qty
    costs_known = fees is not None and funding is not None
    pnl_net = pnl - fees + funding if costs_known else None
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
        fees=fees,
        funding=funding,
        pnl_net=pnl_net,
        r_net_at_fill=(pnl_net / risk_at_fill
                       if pnl_net is not None and risk_at_fill > 0 else None),
    )


# How much of the planned distance to the stop must survive the entry fill for the trade to be
# the trade that was approved.
#
# MEASURED, on the only two closes that exist: INTL kept 0.707 and VRT kept 0.085. That is one
# clear pass and one clear failure with nothing between them, so anything from about 0.15 to 0.6
# separates the pair and the number is not tuned to either edge. 0.5 is chosen because it states
# something — *half the intended risk distance is gone* — rather than because it fits two points.
# Re-derive it once there are enough closes for the distribution to have a shape.
MIN_STOP_SURVIVAL = 0.5


def stop_survival(*, planned_entry, fill, stop) -> float | None:
    """What fraction of the planned distance-to-stop the fill left intact. ``None`` if unmeasurable.

    **A limit entry fills at the open, not at the limit.** On a gapped session the entry walks
    toward a stop that does not move with it, and the position that results is not the position
    that was sized: VRT was planned at 266.52 against a 241.18 stop — 9.5% away — filled at
    243.33, leaving 0.9%, and round-tripped flat in 49 seconds. ``plan.build``'s note explains
    why nothing at placement time can prevent this; this measures it afterwards.

    **A better entry is always a tighter stop, and there is no benign direction.** A limit fills
    only on its favourable side — a buy at or below its price, a sell at or above — and the stop
    sits on the far side of the entry. So every improvement on the planned entry closes distance
    to a stop the size was never adjusted for. Survival is bounded above by 1.0 for any fill a
    limit order can produce, and 1.0 means the entry filled exactly at its limit.

    That is why this is worth recording rather than assumed away: "we got a better price" and
    "the trade now risks a fraction of what it was sized for" are the same event.
    """
    planned_entry, fill, stop = number(planned_entry), number(fill), number(stop)
    if planned_entry is None or fill is None or stop is None:
        return None
    planned = abs(planned_entry - stop)
    if planned <= 0:
        return None
    return abs(fill - stop) / planned


def fill_quality(*, qty: float, median_volume: float | None, ceiling: float,
                 paper: bool, stop_survival: float | None = None,
                 costs_known: bool = True) -> FillQuality:
    """Is this row evidence about the strategy, or an artefact? Three independent ways to fail.

    * **Venue.** Paper matches against the quote without ever consuming the book, so every paper
      fill is one that was never tested. A small one is realistic by luck, and luck is not a
      property worth recording as credibility.
    * **Size.** An exit above the participation ceiling did not have to compete for liquidity in
      any market that would have made it compete. See ``participation``.
    * **Plan integrity.** A gapped fill can leave the stop a fraction of its planned distance
      away, and then the trade that happened is not the trade that was approved — however
      honestly it filled. See ``stop_survival``; this is the only one of the three that still
      bites on a real-money account, which is why it exists.

    **A definite disqualifier beats an unmeasured one.** Paper is knowable without measuring
    anything, so an unreadable market must not soften it to ``None`` — "unknown" reads as "might
    be fine". Only a row with nothing against it *and* nothing unmeasured is ``True``.
    """
    participation = (qty / median_volume
                     if median_volume is not None and median_volume > 0 else None)

    reasons: list[str] = []
    if paper:
        reasons.append("paper — the simulator never consumed the book")
    if participation is not None and participation > ceiling:
        reasons.append(f"{participation:.2%} of a median session, ceiling {ceiling:.2%}")
    if stop_survival is not None and stop_survival < MIN_STOP_SURVIVAL:
        reasons.append(f"the fill left {stop_survival:.1%} of the planned stop distance")

    if reasons:
        credible = False
    elif participation is None or stop_survival is None or not costs_known:
        # ``None``, not ``False``. A perp row missing its funding is not a disproven row, it is
        # an unfinished one — and 0.36R of unmeasured holding cost is exactly the size of error
        # that would survive a reviewer glancing at the P/L column.
        credible = None
    else:
        credible = True

    return FillQuality(
        participation=participation,
        paper=paper,
        credible=credible,
        stop_survival=stop_survival,
        reasons=tuple(reasons),
    )
