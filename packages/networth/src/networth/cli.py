"""`networth`: tonight's total and how it has moved. `--record` appends it to the ledger.

**`--record` writes nothing on a night it cannot vouch for** — a portfolio file that failed to
load, no portfolio files at all, or a different set of books than the last row without
`--books-changed`. Every one of those would otherwise go into a history nobody can rebuild as a
drop that never happened. Reads only; places nothing.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from core import networth
from review.cli import load_books, price_freshness, review_for
from treasury.cli import load_result
from treasury.render import money

from networth import ledger


class Refused(Exception):
    """Tonight's total is not one to record."""


def tonight(*, as_of: date) -> networth.NetWorth:
    """Raises `Refused` on any partial load, rather than summing what happened to arrive."""
    problems: list[str] = []
    books = load_books(warn=problems.append)
    if not books:
        problems.append("no portfolio files loaded")
    parked = 0.0
    if not problems:
        treasury = load_result(books=tuple(books), as_of=as_of, warn=problems.append)
        parked = 0.0 if treasury is None else treasury.total
    if problems:
        raise Refused("; ".join(problems))
    net = networth.of(review_for(books, as_of=as_of), parked=parked)
    if net is None:
        raise Refused("nothing to sum")
    return net


def _changes(changes: dict[int, networth.Change]) -> str:
    def one(c: networth.Change) -> str:
        figure = f"{c.pct:+.1%}" if c.pct is not None else money(c.amount)
        return f"{figure} {c.days}d{'*' if c.uneven else ''}"
    return " · ".join(one(changes[d]) for d in sorted(changes)) or "no history yet"


# More newly unpriced holdings than this since the last row reads as a failed price fetch, not
# a market move. A few rows sit unpriced every night.
UNPRICED_SLACK = 2


def _drift(previous: networth.Row, net: networth.NetWorth, *, books_changed: bool,
           unpriced_ok: bool) -> list[str]:
    """Why tonight does not line up with the last row. Each is a typed override, not a guess."""
    reasons = []
    if not books_changed:
        if set(previous.by_book) != set(net.by_book):
            reasons.append(f"books changed since {previous.date}: {sorted(previous.by_book)} → "
                           f"{sorted(net.by_book)} (--books-changed if deliberate)")
        if previous.parked > 0 and net.parked == 0:
            reasons.append(f"treasury held {money(previous.parked)} on {previous.date} and "
                           f"nothing tonight (--books-changed if deliberate)")
    if not unpriced_ok and net.unpriced > previous.unpriced + UNPRICED_SLACK:
        reasons.append(f"{net.unpriced} unpriced tonight against {previous.unpriced} on "
                       f"{previous.date} — a price fetch likely failed (--unpriced-ok to record "
                       f"anyway)")
    return reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true",
                        help="append tonight's row to the ledger (the nightly does this)")
    parser.add_argument("--books-changed", action="store_true",
                        help="record even though the portfolio files or the treasury differ "
                             "from the last row — after adding, removing or renaming one")
    parser.add_argument("--unpriced-ok", action="store_true",
                        help="record even though more holdings are unpriced than last time")
    parser.add_argument("--ledger", type=Path, default=ledger.LEDGER,
                        help=f"ledger file (default: {ledger.LEDGER})")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    today = now.date()
    try:
        net = tonight(as_of=today)
    except Refused as exc:
        print(f"networth: not recorded — {exc}", file=sys.stderr)
        return 1

    freshness = price_freshness()
    fetched = None if freshness.age is None else now - freshness.age
    row = networth.row(net, on=today, recorded_at=now, prices_fetched_at=fetched,
                       stale=freshness.is_stale)

    history = ledger.read(args.ledger, warn=lambda m: print(m, file=sys.stderr))
    changes = networth.horizons(net.total, net.unpriced, history, today=today,
                                trusted=row.trusted)
    print(f"net worth {money(net.total)} across {len(net.by_book)} book(s) + treasury "
          f"{money(net.parked)} · {_changes(changes)}")
    if net.unpriced:
        print(f"  excludes {net.unpriced} unpriced"
              + (f", left out {', '.join(net.mismatched)} (price disagrees with its mark)"
                 if net.mismatched else ""))

    if not args.record:
        return 0

    previous = [r for r in networth.latest_per_date(history) if r.date < today]
    if previous:
        reasons = _drift(previous[-1], net, books_changed=args.books_changed,
                         unpriced_ok=args.unpriced_ok)
        if reasons:
            print(f"networth: not recorded — {'; '.join(reasons)}", file=sys.stderr)
            return 1

    ledger.append(row, args.ledger)
    print(f"recorded {today} to {args.ledger}" + (" — prices STALE" if row.stale else ""))
    return 0
