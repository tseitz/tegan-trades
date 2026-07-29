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

    uv run book               # what is holding the budget, and how old it is
    uv run book --cancel      # ...and pick entries to retire
    uv run book --reconcile   # ...and settle what the log still calls placed
    uv run book --network live
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import UTC, datetime

from execution import config as config_module
from execution import store
from execution.book import RestingOrder, stale
from execution.session import open_broker
from execution.venues import ALL_NETWORKS


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
                        help="ask the venue what became of every order the log calls placed")
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
    if args.reconcile:
        reconcile(broker, orders_path, network=config.network, out=out)
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
