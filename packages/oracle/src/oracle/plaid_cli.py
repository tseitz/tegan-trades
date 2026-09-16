"""``plaid-link`` and ``plaid-sync`` — connect a broker once, then refresh it for free.

``plaid-link`` is the one step that needs a human: a broker login happens in a browser, and no
amount of code gets around that. It runs once per account and leaves an access token in
``.env``. ``plaid-sync`` is what runs every night, and it never asks for anything.

Both are read-only. The link requests the ``investments`` product and nothing else, so the
token it mints cannot place a trade or move cash — unlike ``execution``, which is the only
package in this repo that signs a write.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from core.env import REPO_ROOT
from core.transactions import InvestmentTransaction

from oracle import plaid, portfolios, transaction_store

# ADR-0003's Plaid ceiling: /investments/transactions/get reaches at most 2 years back from
# when an Item was linked.
_HISTORY_FLOOR_DAYS = 730
# How far back a re-run re-reads once the floor is already reached, to catch a late-settling
# correction Plaid makes to a transaction after the fact.
_RESETTLE_WINDOW_DAYS = 30
_PAGE_SIZE = 500


def link(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plaid-link",
        description="Connect a brokerage account to Plaid, once. Read-only; places nothing.")
    parser.add_argument("portfolio",
                        help="which data/portfolios/<name>.yaml this account fills")
    parser.add_argument("--no-browser", action="store_true",
                        help="print the URL instead of opening it (use it on another device)")
    args = parser.parse_args(argv)

    try:
        token, url = plaid.hosted_link(args.portfolio)
    except plaid.PlaidError as exc:
        print(f"could not start the login: {exc}", file=sys.stderr)
        return 1

    print(f"log in to the broker for '{args.portfolio}' here:\n\n  {url}\n")
    if not args.no_browser:
        webbrowser.open(url)
    print("waiting for the login to finish (Ctrl-C to give up)", end="", flush=True)

    try:
        public = plaid.wait_for_login(token, tick=lambda: print(".", end="", flush=True))
        print()
        access = plaid.exchange(public)
    except KeyboardInterrupt:
        print("\ngave up. Nothing was saved; re-run to try again.")
        return 1
    except plaid.PlaidError as exc:
        print(f"\nlogin did not complete: {exc}", file=sys.stderr)
        return 1

    print(plaid.remember_token(args.portfolio, access, env=REPO_ROOT / ".env"))

    # Fetched immediately rather than left to the first sync: a link that succeeded but reaches
    # an account with no holdings is worth knowing about now, while the browser is still open.
    try:
        payload = plaid.holdings(access)
    except plaid.PlaidError as exc:
        print(f"linked, but the first holdings call failed: {exc}", file=sys.stderr)
        return 1

    rows, skipped, accounts = plaid.rows_from(payload)
    print(f"\nreached {len(accounts)} account(s): {', '.join(accounts) or 'none'}")
    print(f"{len(rows)} position(s) available")
    for miss in skipped:
        print(f"  cannot use {miss.what}: {miss.why}")
    print(f"\nnow run: uv run plaid-sync {args.portfolio}")
    return 0


def sync(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plaid-sync",
        description="Refresh portfolio files from the brokers they are linked to.")
    parser.add_argument("portfolio", nargs="*",
                        help="which accounts to refresh (default: every linked one)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change and write nothing")
    args = parser.parse_args(argv)

    # Defaults to every portfolio that has a token rather than every portfolio on disk: a
    # hand-kept file with no broker behind it is not a failure, and reporting it as one every
    # night is how a nightly's warnings stop being read.
    names = args.portfolio or [n for n in portfolios.available() if _linked(n)]
    if not names:
        # Nothing linked and nothing asked for is not a failure — it is a repo where every
        # portfolio is hand-kept, which is a supported way to run. Failing here would make the
        # nightly report a red step every night on a fresh clone, and a step that is always red
        # is a step nobody reads.
        if args.portfolio:
            print("no such account", file=sys.stderr)
            return 1
        print("no linked accounts — run `uv run plaid-link <name>` to connect one")
        return 0

    failures = 0
    for name in names:
        try:
            token = plaid.access_token(name)
            payload = plaid.holdings(token)
        except plaid.PlaidError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            failures += 1
            continue

        narrowed = _narrow(name)
        rows, skipped, accounts = plaid.rows_from(payload, accounts=narrowed)
        cash, cash_by = plaid.cash_from(payload, accounts=narrowed)
        path = portfolios.DATA_ROOT / f"{name}.yaml"
        before = _tickers(name)
        now = {r.ticker for r in rows}

        if not rows:
            # Refuses to write rather than emptying the file. `portfolios.load` rejects a
            # portfolio with no positions, so an empty write would turn a broker hiccup into an
            # account that vanishes from the review with no verdict anywhere saying so.
            print(f"{name}: Plaid returned no usable positions — file left alone", file=sys.stderr)
            failures += 1
            continue

        verb = "would write" if args.dry_run else "wrote"
        if not args.dry_run:
            portfolios.write_positions(path, rows, source=plaid.SOURCE,
                                       cash=cash, cash_by=cash_by)
        money = "" if cash is None else f", {cash:,.2f} cash"
        print(f"{name}: {verb} {len(rows)} position(s) from "
              f"{len(accounts)} account(s){money} -> {path}")
        for label in accounts:
            print(f"    {label}")
        # Every investment account the login reaches, not only the ones holding something. An
        # empty second IRA is silent today and merges into this file the day it is funded —
        # naming it now is what makes that a choice rather than a surprise.
        for idle in _idle(payload, narrowed, accounts):
            print(f"    {idle}  (reachable, nothing in it — it would merge in if funded)")
        _report(before, now)
        for miss in skipped:
            print(f"    dropped {miss.what}: {miss.why}")

        if _wants_history(name):
            _sync_transactions(name, token, narrowed=narrowed, dry_run=args.dry_run)

    if failures:
        return 1
    print(f"\nsynced {datetime.now(UTC).date().isoformat()}. "
          f"Prices: uv run fetch-prices --all-portfolios")
    return 0


def _wants_history(name: str) -> bool:
    """Whether ``name``'s mandate declares a ``held_flat`` benchmark — the gate on the
    transaction-history pull, because that pull starts metered billing on the Item (see
    ``portfolios.Benchmark`` and ``oracle.plaid``'s module docstring).

    Does not reuse ``_existing`` below: that function swallows a ``PortfolioError`` into
    ``None``, which is right for its own job of deciding what to diff against, and wrong here.
    A freshly-linked account's first sync writes a file with no `mandate:` block at all
    (``write_positions``' ``HEADER`` never writes one), and ``portfolios.load`` refuses that
    file — a silent ``False`` here would then skip the transaction pull forever with nothing
    ever saying why.
    """
    try:
        # `root=portfolios.DATA_ROOT` read here rather than left to `load`'s own default:
        # that default is bound once at import time, so a test pointing `portfolios.DATA_ROOT`
        # at `tmp_path` (the convention `_narrow` below already follows) would otherwise still
        # reach the real data directory.
        book = portfolios.load(name, root=portfolios.DATA_ROOT)
    except portfolios.PortfolioError as exc:
        print(f"{name}: cannot tell whether it wants transaction history — {exc}",
              file=sys.stderr)
        return False
    return any(b.type == "held_flat" for b in book.mandate.benchmarks)


def _window(name: str, *, root: Path) -> tuple[date, date]:
    """``(start, today)`` for the next fetch. Re-extends backwards, never forward-only: an
    interrupted first backfill must stay repairable by a later run, so the floor is always
    ``today - _HISTORY_FLOOR_DAYS`` unless the document already reached it — in which case this
    asks for a short recent window instead, to pick up a late-settling correction."""
    today = datetime.now(UTC).date()
    floor = today - timedelta(days=_HISTORY_FLOOR_DAYS)
    cached = transaction_store.load(name, root=root)
    reaches = cached[1] if cached else None
    if reaches is not None and reaches <= floor:
        newest = max((t.date for t in cached[0]), default=today) if cached and cached[0] else today
        return newest - timedelta(days=_RESETTLE_WINDOW_DAYS), today
    return floor, today


def _sync_transactions(name: str, token: str, *, narrowed: tuple[str, ...], dry_run: bool = False,
                       root: Path | None = None) -> None:
    """Fetch, page and commit one account's transaction-history window.

    **A transaction failure warns and never fails the step.** This is a secondary feed
    alongside the positions sync that must stay loud — see ``sync()``'s own comment on
    ``failures`` for why a secondary feed reddening the nightly is how a step stops being read.

    **One commit at the end, never per page.** Pages accumulate in a list and
    ``transaction_store.replace_window`` is called exactly once, after the last page lands — so
    a page-3 failure leaves the cache exactly as it was rather than half-written.
    """
    root = transaction_store.DATA_ROOT if root is None else root
    if transaction_store.load(name, root=root) is None:
        # Named out loud: this call is the moment `name`'s Item starts a metered, non-
        # cancellable Plaid subscription. See `portfolios.Benchmark` for what declaring
        # `held_flat` costs.
        print(f"{name}: first transaction-history pull — starts metered Plaid billing on "
              f"this connection (see portfolios.Benchmark)", file=sys.stderr)
    start, end = _window(name, root=root)

    pages: list[InvestmentTransaction] = []
    offset = 0
    while True:
        try:
            payload = plaid.investment_transactions(
                token, start=start, end=end, offset=offset, count=_PAGE_SIZE)
        except plaid.PlaidError as exc:
            print(f"{name}: transaction history fetch failed (page at offset {offset}) — "
                  f"{exc}. Cache left alone.", file=sys.stderr)
            return
        raw = payload.get("investment_transactions") or ()
        rows, skipped = plaid.transactions_from(payload, accounts=narrowed)
        pages.extend(rows)
        # `.get(key, default)` only substitutes when the key is absent; Plaid sending an
        # explicit `null` — seen in the wild — passes straight through and breaks the `>=`
        # comparison below with a `TypeError`, which would escape `sync()` as an unhandled
        # exception rather than the warning this feed promises to fail as.
        total = payload.get("total_investment_transactions")
        if total is None:
            total = len(raw)
        for miss in skipped:
            print(f"    dropped transaction {miss.what}: {miss.why}", file=sys.stderr)
        # Paged on the raw count Plaid returned, not on `rows` — a page where every
        # transaction belongs to an account `narrowed` excludes would otherwise come back
        # empty after filtering and stop the walk long before `total` is actually reached.
        offset += len(raw)
        if offset >= total or not raw:
            break

    if not pages and _window_had_cached_rows(name, start=start, end=end, root=root):
        # Mirrors the positions sync's own guard above: a Plaid hiccup that answers 200 with
        # zero rows must not read as "everything in this window was deleted". Without this, a
        # blip would wipe up to two years of cache and the resettle window would then only
        # re-ask the last 30 days — the loss would be permanent until someone hand-deletes the
        # file, and `review` would compute #71's held-flat math against an empty basket with
        # nothing saying why.
        print(f"{name}: Plaid returned zero transactions for a window that already had cached "
              f"rows — treating as a hiccup, cache left alone", file=sys.stderr)
        return

    verb = "would cache" if dry_run else "cached"
    print(f"{name}: {verb} {len(pages)} transaction(s) back to {start.isoformat()}")
    if not dry_run:
        transaction_store.replace_window(name, tuple(pages), start=start, end=end, root=root)


def _window_had_cached_rows(name: str, *, start: date, end: date, root: Path) -> bool:
    existing = transaction_store.load(name, root=root)
    if existing is None:
        return False
    return any(start <= t.date <= end for t in existing[0])


def _linked(name: str) -> bool:
    try:
        plaid.access_token(name)
    except plaid.PlaidError:
        return False
    return True


def _existing(name: str):
    """The portfolio as it stands, or None if it does not parse. A file we are about to
    replace is not worth failing over — the point of the sync is to fix it."""
    try:
        return portfolios.load(name)
    except portfolios.PortfolioError:
        return None


def _tickers(name: str) -> set[str]:
    book = _existing(name)
    return {p.holding.ticker for p in book.positions} if book else set()


def _narrow(name: str) -> tuple[str, ...]:
    """``plaid_accounts:`` from the portfolio file, when one Item holds more than one account."""
    import yaml
    path = portfolios.DATA_ROOT / f"{name}.yaml"
    if not path.is_file():
        return ()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = doc.get("plaid_accounts") if isinstance(doc, dict) else None
    return tuple(str(a) for a in raw) if isinstance(raw, list) else ()


def _idle(payload: dict, narrowed: tuple[str, ...], used: tuple[str, ...]) -> list[str]:
    """Investment accounts this login reaches that contributed no position."""
    return [f"{a.get('name') or a['account_id']} ({a.get('mask') or '—'})"
            for a in payload.get("accounts") or ()
            if (a.get("type") or "") == plaid.INVESTMENT
            and (not narrowed or a.get("account_id") in narrowed)
            and f"{a.get('name') or a['account_id']} ({a.get('mask') or '—'})" not in used]


def _report(before: set[str], now: set[str]) -> None:
    """Names what moved. A sync that only printed a count would hide a position closing, which
    is exactly the event the review exists to catch."""
    for ticker in sorted(now - before):
        print(f"    new  {ticker}")
    for ticker in sorted(before - now):
        print(f"    gone {ticker}")


if __name__ == "__main__":
    raise SystemExit(sync())
