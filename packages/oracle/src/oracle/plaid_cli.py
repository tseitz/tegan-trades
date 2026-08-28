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
from datetime import UTC, datetime

from core.env import REPO_ROOT

from oracle import plaid, portfolios


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
            payload = plaid.holdings(plaid.access_token(name))
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
            plaid.write_positions(path, rows, horizon=_horizon(name), cash=cash,
                                  cash_by=cash_by)
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

    if failures:
        return 1
    print(f"\nsynced {datetime.now(UTC).date().isoformat()}. "
          f"Prices: uv run fetch-prices --all-portfolios")
    return 0


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


def _horizon(name: str) -> str:
    book = _existing(name)
    return book.horizon if book else portfolios.DEFAULT_HORIZON


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
