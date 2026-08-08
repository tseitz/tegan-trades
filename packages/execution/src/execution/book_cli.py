"""``book`` — what the account is already holding, and what to retire.

The third of ``docs/IMPROVEMENTS.md`` §40's three caps, and the only one that is a command
rather than a check. A resting entry reserves its full notional and Alpaca expires it after 90
days, so an approval nobody cancelled holds a slice of the budget for a quarter — and a zone
price has not reached in a fortnight is a staler thesis than the one that was approved.

**Nothing expires automatically, by design.** An unfilled entry is a live intention: the zone
may simply not have been reached yet, which is the ordinary case for a weekly order block. So
this lists ages and flags what is past ``max_order_age_days``, and a person decides. An
automatic sweep would quietly cancel the patient trades and keep the impatient ones.

**Positions are listed and never cancelled here.** Flattening a position is a trading decision
with a live stop attached to it, not budget housekeeping — and the two are one keystroke apart
in a numbered menu, which is exactly why only one of them is numbered.

``--reconcile`` answers the other half: ``store.record_placement`` writes ``placed`` from the
*submission* reply, and Alpaca runs its buying-power and account-type checks at the open. So an
order can be logged as placed and be dead hours later with nothing looking. This asks.

It then asks the question after that — **how the trade ended**. Settling the entry took the
candidate off every work list at *entry fill*, so realised P/L, which leg took it, and how long
it was held were recorded nowhere; the log could say "I got in" and never "it worked". The two
phases run in order because the first writes the row that puts a candidate on the second's list,
which means a trade that opened and closed since the last pass is fully recorded tonight rather
than half tonight and half tomorrow. See ``close_out`` and ``execution.outcome``.

    uv run book                  # what is holding the budget, and how old it is
    uv run book --cancel         # ...and pick entries to retire
    uv run book --reconcile      # ...settle placed orders, then record how filled trades ended
    uv run book --closed         # the realised history: what each finished trade made
    uv run book --network live
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import UTC, datetime

from execution import config as config_module
from execution import journal, outcome, store
from execution.book import RestingOrder, stale
from execution.session import open_broker
from execution.venues import ALL_NETWORKS, is_real_money


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="book",
        description="What the account is holding: resting entries and open positions.",
    )
    parser.add_argument("--network", choices=sorted(ALL_NETWORKS), default=None,
                        help="override cfg/execution.yaml's network")
    parser.add_argument("--cancel", action="store_true",
                        help="offer to cancel resting entries (never positions)")
    parser.add_argument("--reconcile", action="store_true",
                        help="settle what the log calls placed, and record how filled trades "
                             "ended")
    parser.add_argument("--closed", action="store_true",
                        help="list the realised history — what each finished trade made")
    parser.add_argument("--no-vault-note", action="store_true",
                        help="do not mirror closes to the vault note (the order log is "
                             "unaffected either way)")
    parser.add_argument("--max-age", type=float, default=None,
                        help="days before an entry is flagged stale "
                             "(default: cfg/execution.yaml's max_order_age_days)")
    return parser.parse_args(argv)


def _age(days: float | None) -> str:
    return "     ?" if days is None else f"{days:6.1f}d"


def render(orders, positions, *, account, max_age: float, now: datetime) -> list[str]:
    """The listing, as lines. Pure given its inputs, which is what makes it testable.

    Both sections print their total, because the total is the thing §40 says nothing computed.
    """
    lines: list[str] = []

    if account is not None and account.buying_power is not None:
        lines.append(
            f"  ${account.buying_power:,.2f} free of ${account.equity:,.2f}"
            + (f" (${account.committed:,.2f} committed)"
               if account.committed is not None else "")
        )
        lines.append("")

    if orders is None:
        lines.append("  resting entries: this venue cannot be asked (see IMPROVEMENTS §33)")
    elif not orders:
        lines.append("  resting entries: none")
    else:
        held = sum(o.notional for o in orders)
        old = set(stale(orders, max_age, now))
        lines.append(f"  RESTING ENTRIES  {len(orders)}, holding ${held:,.2f}")
        lines.append(f"   {'#':>2}  {'age':>7}  {'symbol':<8} {'side':<5} {'qty':>8} "
                     f"{'limit':>10} {'notional':>13}  candidate")
        for i, o in enumerate(orders, 1):
            lines.append(
                f"   {i:>2}  {_age(o.age_days(now))}  {o.symbol:<8} {o.side:<5} {o.qty:>8g} "
                f"{o.limit_price:>10,.2f} {o.notional:>13,.2f}  {o.candidate_key}"
                + ("   STALE" if o in old else "")
            )
        if old:
            lines.append(f"    {len(old)} past {max_age:g} days — "
                         f"${sum(o.notional for o in old):,.2f} held by zones price has not "
                         f"reached")

    lines.append("")
    if positions is None:
        lines.append("  positions: this venue cannot be asked")
    elif not positions:
        lines.append("  positions: none")
    else:
        value = sum(p.market_value for p in positions)
        lines.append(f"  POSITIONS  {len(positions)}, ${value:,.2f}")
        lines.append(f"       {'age':>7}  {'symbol':<8} {'side':<5} {'qty':>8} "
                     f"{'value':>13} {'P/L':>12}")
        for p in positions:
            lines.append(
                f"       {_age(p.age_days(now))}  {p.symbol:<8} {p.side:<5} {p.qty:>8g} "
                f"{p.market_value:>13,.2f} {p.unrealised_pl:>12,.2f}"
            )
        # Said out loud rather than merely implemented. A reader who has just been offered a
        # numbered menu will reasonably wonder why this section has no numbers.
        lines.append("    listed for context only — closing a position is a trading decision "
                     "with a live stop on it, and this command will not do it")
    return lines


def selected(answer: str, orders, *, max_age: float, now: datetime
             ) -> tuple[RestingOrder, ...] | str:
    """Turn what was typed into orders to cancel, or a message saying why not. Pure.

    An out-of-range or unparseable entry aborts the whole selection rather than cancelling the
    subset that happened to parse. Partial obedience is the worst possible answer here: the
    reader believes they cancelled three orders and two of them are still holding the budget.
    """
    answer = answer.strip().lower()
    if not answer or answer in ("n", "no", "none"):
        return ()
    if answer == "stale":
        return stale(orders, max_age, now)
    if answer == "all":
        return tuple(orders)

    chosen: list[RestingOrder] = []
    for token in answer.replace(",", " ").split():
        if not token.isdigit():
            return f"{token!r} is not a row number"
        index = int(token)
        if not 1 <= index <= len(orders):
            return f"there is no row {index}"
        order = orders[index - 1]
        if order not in chosen:
            chosen.append(order)
    return tuple(chosen)


def offer(broker, orders, *, max_age: float, now: datetime, input_fn, out) -> int:
    """Ask which entries to retire, confirm, and cancel them. Returns the number cancelled."""
    answer = input_fn('  cancel which? [row numbers, "stale", "all", or enter for none]: ')
    chosen = selected(answer, orders, max_age=max_age, now=now)
    if isinstance(chosen, str):
        out(f"  {chosen} — nothing cancelled")
        return 0
    if not chosen:
        out("  nothing cancelled")
        return 0

    freed = sum(o.notional for o in chosen)
    out(f"  about to cancel {len(chosen)} entr{'y' if len(chosen) == 1 else 'ies'}, "
        f"freeing ${freed:,.2f}:")
    for o in chosen:
        out(f"    {o.symbol} {o.side} {o.qty:g} @ {o.limit_price:,.2f}")
    if input_fn("  confirm? [y/N]: ").strip().lower() not in ("y", "yes"):
        out("  nothing cancelled")
        return 0

    cancelled = 0
    for o in chosen:
        error = broker.cancel(o.order_id)
        if error is None:
            cancelled += 1
            out(f"  + cancelled {o.symbol} {o.order_id}")
        else:
            # Named individually rather than summarised: a bracket that filled between the
            # listing and the confirmation is no longer cancellable, and that is a position
            # the reader now has and did not a moment ago.
            out(f"  ! {o.symbol} {o.order_id} not cancelled — {error}")
    return cancelled


def reconcile(broker, orders_path, *, network: str, out) -> int:
    """Ask the venue what became of every order the log still calls ``placed``.

    ``placed`` is written from the *submission* reply, and a GTC bracket sent while the market
    is shut comes back ``accepted`` with the buying-power and account-type checks still to run.
    On 2026-07-29 three of eight orders were rejected at the open and the log went on saying
    ``placed`` for all three — so "what did I send" was answerable and "what actually happened"
    was not.

    Returns the number of orders whose fate the venue killed, because that is the number worth
    reacting to: a rejected order means a candidate never traded and its budget was never
    really spent.
    """
    keys = store.unsettled_keys(orders_path, network=network)
    if not keys:
        out("  nothing awaiting reconciliation")
        return 0

    # Asset names come from the log rather than the venue: the point of the log is that it is
    # the only thing holding the candidate-to-order join, and a reconcile line without the
    # asset in it is not a line anyone can act on.
    assets = {str(r.get("candidate_key")): str(r.get("asset") or "?")
              for r in store.load(orders_path)}

    states = broker.states(sorted(keys))

    out(f"  RECONCILING  {len(keys)} placed order(s) against {network}")
    killed = 0
    working = 0
    unreadable = 0
    for key, state in states.items():
        asset = assets.get(key, "?")
        if state is None:
            # Not recorded. We do not know what happened, and writing "unknown" as a verdict
            # would take the order off the work list and lose the question permanently.
            unreadable += 1
            out(f"    {asset:<8} {key}  ? unreadable — left on the list")
            continue
        if not state.settled:
            working += 1
            out(f"    {asset:<8} {key}  still working ({state.status})")
            continue

        store.record_reconciliation(orders_path, state, network=network)
        if state.failed:
            killed += 1
            out(f"    {asset:<8} {key}  ! {state.status.upper()} — the log said placed; this "
                f"candidate never traded")
        else:
            fill = (f" at {state.filled_avg_price:,.2f}"
                    if state.filled_avg_price is not None else "")
            out(f"    {asset:<8} {key}  + {state.status}{fill}")

    out(f"  {killed} killed by the venue, {working} still working, "
        f"{unreadable} unreadable")
    # Phrased as the likely cause rather than a verdict. Nothing readable came back, and the
    # two reasons for that look identical from here: a venue that cannot be asked about a
    # candidate at all (Hyperliquid sends no ``cloid`` — §33), or one that simply did not
    # answer this time. Claiming the first would be a confident guess.
    if unreadable == len(states) and unreadable:
        out("  nothing came back — either this venue cannot be asked about a candidate "
            "(see IMPROVEMENTS §33) or it did not answer; the log is unchanged")
    if killed:
        # Said plainly because the consequence is not obvious from the word "rejected": the
        # duplicate guard reads live state, so these candidates are already free to be offered
        # again, and the budget they appeared to be holding was never actually committed.
        out("  those candidates are free to be offered again — the guard reads live state, "
            "not this log")
    return killed


def render_closed(rows, *, network: str) -> list[str]:
    """The realised history, as lines. Pure given its inputs.

    **Two totals, and the second one is the honest one.** Pooling a paper fill on 8.5% of a
    median session with real ones summarises fiction — see ``outcome.fill_quality``. Both are
    printed because hiding the first would make the listing disagree with the log, and a reader
    who cannot reconcile the two stops trusting either.
    """
    closes = [r for r in rows
              if r.get("outcome") == store.CLOSED and r.get("network") == network]
    if not closes:
        return ["  closed trades: none"]

    credible = [r for r in closes if r.get("credible")]
    total = sum(float(r.get("pnl") or 0.0) for r in closes)
    real = sum(float(r.get("pnl") or 0.0) for r in credible)

    lines = [f"  CLOSED  {len(closes)} closed, ${total:,.2f}  ·  "
             f"{len(credible)} credible, ${real:,.2f}"]
    lines.append(f"   {'asset':<8} {'reason':<7} {'held':>7} {'qty':>8} {'exit':>10} "
                 f"{'P/L':>12} {'R':>7}  candidate")
    for r in closes:
        held = r.get("held_days")
        r_at_fill = r.get("r_at_fill")
        asset = r.get("asset") or "?"
        reason = r.get("exit_reason") or "?"
        key = r.get("candidate_key") or "?"
        lines.append(
            f"   {asset!s:<8} {reason!s:<7} "
            f"{(f'{held:6.1f}d' if isinstance(held, (int, float)) else '     ?')} "
            f"{float(r.get('exit_qty') or 0):>8g} {float(r.get('exit_price') or 0):>10,.2f} "
            f"{float(r.get('pnl') or 0):>+12,.2f} "
            f"{(f'{r_at_fill:+7.2f}' if isinstance(r_at_fill, (int, float)) else '      ?')}"
            f"  {key!s}"
            + ("" if r.get("credible") else "   NOT EVIDENCE")
        )
    if len(credible) < len(closes):
        # Said out loud rather than left to the flag: a reader scanning the P/L column will
        # otherwise take the first total as performance, which is the whole failure mode.
        lines.append(f"    {len(closes) - len(credible)} fill(s) marked NOT EVIDENCE — paper, "
                     f"or above the participation ceiling. Not performance.")
    return lines


def close_out(broker, orders_path, *, network: str, max_participation: float | None,
              out, note_path=None) -> int:
    """Record how every filled entry ENDED. Returns the number of closes written.

    The second half of the same argument ``reconcile`` makes. That pass settles the *entry*, and
    ``store.unsettled_keys`` then drops the candidate — at entry fill, before the trade has an
    outcome. So "did I get in" was answerable and "did it work" was not, which is the one
    question the candidate-to-order join exists to make askable (``store`` module docstring).

    **One request for the whole account**, not one per candidate: a fill carries its own
    ``order_id``, so ``outcome.reason_for`` attributes it back to a bracket leg for free. The
    feed is asked from the earliest open position's placement date, which is the oldest thing
    any of these keys could need.

    **A partial exit stays pending.** Writing a close for a half-exited position would take the
    candidate off the work list with shares still on the book, and the remainder would never be
    looked for again. See ``outcome.is_flat``.

    Everything written here is ``reconstructed``: it is derived from the venue's history after
    the fact, not captured as it happened. ``oracle.decisions`` explains why that distinction
    has to survive onto the row.
    """
    keys = store.awaiting_exit_keys(orders_path, network=network)
    if not keys:
        out("  nothing awaiting a close")
        return 0

    placed: dict[str, dict] = {}
    reconciled: dict[str, dict] = {}
    for row in store.load(orders_path):
        if row.get("network") != network:
            continue
        key = str(row.get("candidate_key") or "")
        if row.get("outcome") == store.PLACED:
            placed[key] = row
        elif row.get("outcome") == store.RECONCILED:
            reconciled[key] = row

    # The oldest placement among the open positions. Asking from further back would be correct
    # but slower every night forever; asking from later would miss the entry prints that date
    # the position, and an undated position cannot be closed safely.
    #
    # ``default`` because a key can reach the exit list with no placement on this network — a
    # hand-edited log, or a settlement recorded under a different network than its order. An
    # unguarded ``min()`` raises on the empty sequence and takes the whole pass with it.
    dates = [str(placed[k].get("at") or "")[:10] for k in keys if k in placed]
    if not dates:
        out("  nothing awaiting a close has a placement on this network")
        return 0
    fills = broker.fills(since=min(dates))
    if fills is None:
        out("  exits cannot be read from this venue — nothing settled "
            "(see IMPROVEMENTS §33)")
        return 0

    # How many open positions each symbol carries. Two candidates long the same instrument is
    # ordinary — two zones on one asset — and the account-wide feed cannot tell whose sell is
    # whose. See the ambiguity guard below.
    holders: dict[str, int] = {}
    for key in keys:
        row = placed.get(key) or {}
        holders[str(row.get("coin") or row.get("asset") or "")] = (
            holders.get(str(row.get("coin") or row.get("asset") or ""), 0) + 1
        )

    out(f"  CLOSING OUT  {len(keys)} filled entr{'y' if len(keys) == 1 else 'ies'}")
    written = 0
    for key in sorted(keys):
        entry, settle = placed.get(key), reconciled.get(key)
        if entry is None or settle is None:
            continue
        asset = str(entry.get("asset") or "?")
        order_ids = [str(i) for i in (entry.get("order_ids") or [])]
        symbol = str(entry.get("coin") or entry.get("asset") or "")
        direction = str(entry.get("direction") or "long")
        # Every numeric comes off a log that predates several of its own fields, so each is
        # parsed rather than cast. One malformed row must cost ONE candidate, not every
        # candidate sorted after it — this runs unattended.
        entry_price = outcome.number(settle.get("filled_avg_price"))
        entry_qty = outcome.number(settle.get("filled_qty")) or 0.0
        stop = outcome.number(entry.get("stop"))

        if not order_ids or entry_price is None or stop is None or entry_qty <= 0:
            out(f"    {asset:<8} {key}  ? cannot be attributed — the log row is missing an "
                f"entry order id, fill price, quantity or stop")
            continue

        opened = outcome.entry_end(fills, order_ids[0])
        if opened is None:
            # Not an error: the feed window simply does not reach this entry. Left pending
            # rather than dated from the reconciled row, whose stamp is hours late — see
            # ``outcome.entry_end``.
            out(f"    {asset:<8} {key}  ? entry not in the fill window — left pending")
            continue

        exits = outcome.exit_fills(
            fills, symbol=symbol, entry_order_id=order_ids[0],
            exit_side=outcome.exit_side_for(direction), after=opened,
        )

        # THE AMBIGUITY GUARD. ``exit_fills`` filters on symbol, side and time, none of which
        # distinguish two concurrent positions in the same instrument. A fill on this
        # candidate's OWN bracket leg names its owner exactly; an unknown id — a hand-placed
        # exit — does not. So while another open position shares the symbol, an unattributable
        # print stops the close rather than being guessed at.
        #
        # Guessing costs two rows, not one: this candidate gets a fabricated close built from
        # someone else's fill, and the position that really closed is dropped off the work list
        # and never looked at again.
        if holders.get(symbol, 0) > 1:
            own = set(order_ids[1:])
            stray = [f for f in exits if f.order_id not in own]
            if stray:
                ids = ", ".join(sorted({f.order_id[:8] for f in stray}))
                out(f"    {asset:<8} {key}  ? {len(stray)} exit print(s) cannot be attributed "
                    f"— {holders[symbol]} open positions in {symbol} and {ids} is not this "
                    f"candidate's leg; left pending")
                continue

        exited = sum(f.qty for f in exits)
        if not exits:
            out(f"    {asset:<8} {key}  still open ({entry_qty:g} @ {entry_price:,.2f})")
            continue
        if not outcome.is_flat(exit_qty=exited, entry_qty=entry_qty):
            out(f"    {asset:<8} {key}  partly closed, {exited:g} of {entry_qty:g} "
                f"— left pending")
            continue

        depth = broker.depth(symbol)
        for group in outcome.group_by_order(exits).values():
            close = outcome.close_from_fills(group, candidate_key=key, order_ids=order_ids)
            if close is None:
                continue
            # Pro-rated by share of the position, so a two-leg exit yields two comparable R's
            # rather than two rows each claiming the whole trade's risk.
            share = close.qty / entry_qty
            planned = outcome.number(entry.get("risk"))
            result = outcome.realized(
                direction=direction, entry=entry_price, exit_price=close.price,
                qty=close.qty, stop=stop,
                risk_planned=planned * share if planned is not None else None,
            )
            quality = outcome.fill_quality(
                qty=close.qty,
                median_volume=depth.median_volume if depth is not None else None,
                ceiling=max_participation if max_participation is not None else 1.0,
                paper=not is_real_money(network),
                # Measured against the entry the plan asked for, not the one it got — that
                # difference IS the check. See ``outcome.stop_survival``.
                stop_survival=outcome.stop_survival(
                    planned_entry=entry.get("entry"), fill=entry_price, stop=stop),
            )
            row = store.record_close(orders_path, close, result, quality, network=network,
                                     asset=asset, entry_price=entry_price,
                                     entry_at=opened, reconstructed=True)
            written += 1
            # After the log, never instead of it, and it cannot fail the pass. See ``journal``.
            if note_path is not None:
                journal.append(note_path, row, warn=out)
            r = f"{result.r_at_fill:+.2f}R" if result.r_at_fill is not None else "?R"
            flag = "" if quality.credible else "   NOT EVIDENCE"
            out(f"    {asset:<8} {key}  + {close.reason} @ {close.price:,.2f}  "
                f"{result.pnl:+,.2f} ({r}){flag}")

    out(f"  {written} close(s) recorded")
    return written


def main(argv: list[str] | None = None, *, now: datetime | None = None,
         input_fn=input, out=print, broker=None,
         orders_path=store.DEFAULT_PATH) -> int:
    args = _parse_args(argv)
    now = now or datetime.now(UTC)

    config = config_module.load()
    if args.network is not None:
        # ``replace``, not a field-by-field rebuild — the same trap ``oracle.execute`` names:
        # a rebuild silently resets every setting it forgot to list, and a flag about *where*
        # to look would quietly change how much to risk.
        config = replace(config, network=args.network)
        config.validate()
    max_age = args.max_age if args.max_age is not None else config.max_order_age_days

    if broker is None:
        try:
            broker = open_broker(config, config_module.credentials_for(config.venue))
        except Exception as exc:  # noqa: BLE001 - a message beats a traceback here
            print(f"error: could not reach {config.venue} {config.network} — "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    out(f"  {config.venue} {config.network}")
    out("")

    # Before the listing, because it changes what the listing means: an order the venue killed
    # is not holding any budget, however confidently the log says it was placed.
    #
    # **The two phases are ordered, not merely adjacent.** ``reconcile`` writes the ``reconciled``
    # row that puts a candidate on the exit work list, so running it first means a trade that
    # both entered AND exited since the last pass gets both rows tonight instead of waiting a
    # day for the second one.
    if args.reconcile:
        reconcile(broker, orders_path, network=config.network, out=out)
        out("")
        close_out(broker, orders_path, network=config.network,
                  max_participation=config.max_participation, out=out,
                  note_path=None if args.no_vault_note else journal.DEFAULT_NOTE)
        out("")

    if args.closed:
        for line in render_closed(store.load(orders_path), network=config.network):
            out(line)
        out("")

    orders = broker.resting()
    positions = broker.positions()
    for line in render(orders, positions, account=broker.account(),
                       max_age=max_age, now=now):
        out(line)

    if not args.cancel:
        if orders:
            out("")
            out("  run with --cancel to retire any of these")
        return 0
    if not orders:
        return 0

    out("")
    offer(broker, orders, max_age=max_age, now=now, input_fn=input_fn, out=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
