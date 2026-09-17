"""#71's held-flat math, run against the real retirement account — this ticket's only
end-to-end check, since no surface in the repo calls ``benchmarks.report()`` for a
``held_flat`` mandate yet (``plan.py``/``score_cli.py`` both read the domain-keyed face
instead).

RUN IT:

    uv run python scripts/probe_held_flat.py [portfolio]

Defaults to "retirement", the only mandate declaring a ``held_flat`` benchmark today. Needs
``data/portfolios/<portfolio>.yaml`` and ``data/transactions/<portfolio>.json`` — both built by
``plaid-sync``.

**Bypasses ``benchmarks.resolve()`` on purpose.** That function refuses honestly the moment any
anchor holding lacks a cached price, and on this account most of them do (see FINDINGS below) —
correct production behavior, but it would make this probe print nothing. This script calls
``core.held_flat`` directly so the diagnostics stay visible while the guarded API still says
``Unresolved``.

FINDINGS (retirement, 2026-09-17):

    anchor: 2024-09-16 (the oldest cached transaction row)
    basket size: 67 tickers
    flows since the anchor: 787 buy, 730 sell, 321 dividend, 105 other (all `transfer`/
        `transfer`, deposits by sign), 15 fee (ignored), 14 withdrawal
    0 unclassified rows
    priced at the anchor: 21 of 67 under this probe's portfolio-only routing (see "Bypasses"
        below) — the module docstring's own count (35 of 67, corpus-assisted routing) is the
        more accurate production figure; either way a large minority has no cached series
    value_on and every window return: `None`, because `raw_value` is all-or-nothing and any
        unpriced member blocks the whole basket — the honest gap this ticket's design expects,
        not a bug. ^GSPC's own five windows compute fine alongside it (7d -0.80%, 30d -1.18%,
        90d +3.03%, 1y +16.24%), which is what makes the basket's blank column visible rather
        than silent.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core import held_flat
from core.canon import load_registry
from core.transactions import TransactionSpan
from oracle import cache, transaction_store
from oracle import listings as listings_mod
from oracle import route as route_mod
from oracle.benchmarks import SERIES
from oracle.portfolios import canonical_domain_rows
from oracle.portfolios import load as load_portfolio
from oracle.route import load_routing_table

# scripts/probe_held_flat.py -> scripts -> <repo root>
CONFIG_DIR = Path(__file__).resolve().parents[1] / "cfg"

WINDOWS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}


def _price_on(table: route_mod.RoutingTable):
    def inner(ticker: str, day) -> float | None:
        ref = route_mod.route(ticker, table)
        if not isinstance(ref, route_mod.OracleRef):
            return None
        series = cache.load(ref.source, ref.symbol)
        if series is None:
            return None
        return series.close_on(day)
    return inner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("portfolio", nargs="?", default="retirement")
    args = parser.parse_args(argv)

    loaded = transaction_store.load(args.portfolio)
    if loaded is None:
        print(f"no cached transactions for {args.portfolio!r} — "
              f"run `uv run plaid-sync {args.portfolio}` first")
        return 1
    transactions, _reaches_back_to = loaded

    span = TransactionSpan.of(transactions)
    if span.oldest is None:
        print("transaction cache is empty — nothing to anchor to")
        return 1
    anchor = span.oldest

    book = load_portfolio(args.portfolio)
    shares_today = {h.ticker: h.shares for h in book.holdings}
    basket = held_flat.build(shares_today, transactions, anchor=anchor)
    if basket is None:
        print("basket reconstruction went negative — the transaction feed is incomplete")
        return 1

    print(f"anchor: {anchor}")
    print(f"basket size: {len(basket.shares)} tickers\n")

    kinds = Counter(t.kind for t in transactions if t.date >= anchor)
    print("flows since the anchor, by classification:")
    for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  {kind}")
    unclassified_rows = held_flat.unclassified(transactions, anchor=anchor)
    print(f"  {len(unclassified_rows):>4}  unclassified (other, with a security)\n")

    # Routed off the portfolio's own declared domain, not the full corpus (`the_routing_table`)
    # — this probe only needs an answer for the 67 basket tickers, and `fetch_cli.py`'s own
    # rule is that a held row is fed *alongside* the corpus, never instead of it, to route the
    # wider universe. Here there is no wider universe to route, so the held row is enough — see
    # the module docstring's "Bypasses" note for why a shortcut that would be wrong for routing
    # the whole corpus is fine for routing exactly the tickers this account already names.
    registry = load_registry(CONFIG_DIR)
    domain_rows = canonical_domain_rows(book, registry)
    listings = listings_mod.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    table = load_routing_table(CONFIG_DIR, domain_rows, listings=listings)
    price_on = _price_on(table)

    sp500_source, sp500_symbol = SERIES["sp500"]
    sp500 = cache.load(sp500_source, sp500_symbol)
    # The freshest day both sides can actually answer for, rather than the live clock — a
    # `fetch-prices` cache a few days behind "now" would otherwise make every window read
    # `None` from staleness alone, which is a cache-freshness fact, not a held_flat one.
    today = datetime.now(UTC).date()
    if sp500 is not None and sp500.span is not None:
        today = min(today, sp500.span[1])

    unpriced = held_flat.unpriced_at(basket, price_on, anchor)
    priced_count = len(basket.shares) - len(unpriced)
    print(f"priced at the anchor: {priced_count} of {len(basket.shares)}")
    if unpriced:
        print(f"  unpriced: {', '.join(sorted(unpriced))}\n")

    value_today = held_flat.value_on(basket, price_on, today)
    print(f"value_on({today}): {value_today}\n")

    print(f"{'window':<8}{'basket':>12}{'^GSPC':>12}")
    for label, days in WINDOWS.items():
        start = today - timedelta(days=days)
        basket_return = held_flat.return_over(basket, price_on, start, today)
        sp500_return = None
        if sp500 is not None:
            start_close = sp500.close_on(start)
            end_close = sp500.close_on(today)
            if start_close and end_close is not None:
                sp500_return = (end_close - start_close) / start_close
        basket_cell = "—" if basket_return is None else f"{basket_return:+.2%}"
        sp500_cell = "—" if sp500_return is None else f"{sp500_return:+.2%}"
        print(f"{label:<8}{basket_cell:>12}{sp500_cell:>12}")

    since_days = (today - anchor).days
    since_return = held_flat.return_over(basket, price_on, anchor, today)
    since_cell = "—" if since_return is None else f"{since_return:+.2%}"
    print(f"{'since':<8}{since_cell:>12}   ({since_days}d)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
