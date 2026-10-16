"""``kraken-sync`` — refresh one portfolio file from what Kraken says the account holds.

Free and read-only. Unlike ``wallet-sync``, which discovers its accounts from a ``wallets:``
block, this reads one account with one key, so opt-in has to be something the key cannot
express: the git-crypt ``.env`` puts the key on every unlocked checkout, which makes "no key"
mean nothing. **The book file is the opt-in.** No ``kraken.yaml`` prints an example and exits
0; a file without a ``mandate:`` exits 1 and writes nothing, because ``write_positions`` on it
would produce a file ``portfolios.load`` rejects and preflight halts the whole nightly on that.

A failed or empty read leaves the file alone for the reason ``wallet-sync`` does: an account
that did not answer looks exactly like one that was sold out of.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from oracle import kraken_account, portfolios, wallet

DEFAULT_BOOK = "kraken"

EXAMPLE = """\
account: {name}
domain: crypto
stale_after: 7

mandate:
  name: {name}
  benchmarks:
    - type: symbol
      key: btc
    - type: symbol
      key: eth
  horizon: swing
  risk_posture: aggressive
"""


def sync(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kraken-sync",
        description="Refresh a portfolio file from the Kraken account its key can read.",
    )
    parser.add_argument(
        "portfolio",
        nargs="?",
        default=DEFAULT_BOOK,
        help=f"which book to write (default: {DEFAULT_BOOK})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would change and write nothing",
    )
    parser.add_argument(
        "--min-value",
        type=float,
        default=None,
        help=f"drop positions worth less than this many dollars "
        f"(default {wallet.MIN_VALUE_USD:g}, or `min_value:` in the file)",
    )
    args = parser.parse_args(argv)
    name = args.portfolio
    path = portfolios.DATA_ROOT / f"{name}.yaml"

    if not path.is_file():
        print(
            f"no {name} book — to sync Kraken, write {path} like:\n\n"
            f"{EXAMPLE.format(name=name)}"
        )
        return 0

    try:
        doc = portfolios.strict_load(path)
    except portfolios.PortfolioError as exc:
        print(f"{name}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(doc.get("mandate"), dict):
        print(
            f"{name}: {path} has no `mandate:` block — file left alone "
            f"(a synced file without one fails to load). Add one like:\n\n"
            f"{EXAMPLE.format(name=name)}",
            file=sys.stderr,
        )
        return 1

    try:
        floor = args.min_value if args.min_value is not None else _min_value(doc)
    except ValueError as exc:
        print(f"{name}: {exc} — file left alone", file=sys.stderr)
        return 1
    try:
        rows, skipped, cash = kraken_account.rows_from(
            kraken_account.read(), min_value=floor
        )
    except kraken_account.MissingCredentials as exc:
        print(f"{name}: {exc}", file=sys.stderr)
        return 1
    except kraken_account.KrakenError as exc:
        print(f"{name}: {exc} — file left alone", file=sys.stderr)
        return 1

    if not rows:
        # `portfolios.load` rejects a portfolio with no positions, so an empty write would
        # turn a bad read into a book that vanishes from the review.
        print(
            f"{name}: no usable positions on Kraken — file left alone", file=sys.stderr
        )
        return 1

    before = _tickers(name)
    verb = "would write" if args.dry_run else "wrote"
    if not args.dry_run:
        portfolios.write_positions(path, rows, source=kraken_account.SOURCE, cash=cash)
    staked_note = "".join(f", {r.staked:g} {r.ticker} staked" for r in rows if r.staked)
    print(
        f"{name}: {verb} {len(rows)} position(s), {cash:,.2f} in cash"
        f"{staked_note} -> {path}"
    )
    _report(before, {r.ticker for r in rows})
    for row in rows:
        if row.mark is None:
            print(f"    UNPRICED {row.ticker}: no USD pair on Kraken, so no mark")
    _dropped(skipped)

    print(
        f"\nsynced {datetime.now(UTC).date().isoformat()}. "
        f"Prices: uv run fetch-prices --all-portfolios"
    )
    return 0


def _min_value(doc: dict) -> float:
    raw = doc.get("min_value")
    if raw is None:
        return wallet.MIN_VALUE_USD
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"`min_value: {raw!r}` is not a number") from exc


def _tickers(name: str) -> set[str]:
    """Tickers in the book as it stands. A file we are about to replace is not worth failing
    over, so one that does not parse counts as empty."""
    try:
        book = portfolios.load(name, root=portfolios.DATA_ROOT)
    except portfolios.PortfolioError:
        return set()
    return {p.holding.ticker for p in book.positions}


_SAID = {"dust": "under the value floor"}


def _dropped(skipped) -> None:
    """Fee credits are named, because they are a balance Kraken shows that never becomes a
    row; dust is only counted, since an exchange has no airdrop junk to list."""
    counted: dict[str, int] = {}
    for miss in skipped:
        if miss.kind == "dust":
            counted[miss.kind] = counted.get(miss.kind, 0) + 1
        else:
            print(f"    dropped {miss.what}: {miss.why}")
    for kind, total in sorted(counted.items()):
        print(f"    dropped {total} {_SAID.get(kind, kind)}")


def _report(before: set[str], now: set[str]) -> None:
    for ticker in sorted(now - before):
        print(f"    new   {ticker}")
    for ticker in sorted(before - now):
        print(f"    gone  {ticker}")
