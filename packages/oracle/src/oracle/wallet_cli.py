"""``wallet-sync`` — refresh a portfolio file from the public addresses written in it.

Free, read-only, and safe to run nightly. There is no ``wallet-link`` twin to ``plaid-link``:
a public address needs no browser and no token, so the one human step is writing the address
into ``data/portfolios/<name>.yaml`` once.

The file is the configuration. A ``wallets:`` block names the addresses; everything else in
the document means what it always meant, and ``portfolios.load`` ignores the block entirely —
which is why no reader had to change to make chain data reviewable.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

import yaml

from oracle import portfolios, wallet

EXAMPLE = """\
account: {name}
domain: crypto
horizon: position
stale_after: 7

# Quoted, because an unquoted `0x` address is a valid YAML hex number and parses as one.
wallets:
  - address: "0xYourEthereumAddressHere"
  - address: "YourSolanaAddressHere"

positions:
  - ticker: ETH
    shares: 0.0001
"""


def sync(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wallet-sync",
        description="Refresh portfolio files from the public wallet addresses written in them.")
    parser.add_argument("portfolio", nargs="*",
                        help="which accounts to refresh (default: every one with wallets)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change and write nothing")
    parser.add_argument("--dropped", action="store_true",
                        help="name every dropped token, not just the ones that cost you a "
                             "position (several hundred per chain)")
    parser.add_argument("--min-value", type=float, default=None,
                        help=f"drop positions worth less than this many dollars "
                             f"(default {wallet.MIN_VALUE_USD:g}, or `min_value:` in the file)")
    args = parser.parse_args(argv)

    # Defaults to files that name a wallet rather than every file on disk, for the same reason
    # `plaid-sync` defaults to linked accounts: a hand-kept or Plaid-filled portfolio has no
    # address to read and reporting it as a failure every night is how a nightly stops being read.
    names = args.portfolio or [n for n in portfolios.available() if _wallets(n)]
    if not names:
        if args.portfolio:
            print("no such account", file=sys.stderr)
            return 1
        print("no wallet accounts — add a `wallets:` block to a file in "
              f"{portfolios.DATA_ROOT}, like:\n\n{EXAMPLE.format(name='crypto')}")
        return 0

    failures = 0
    for name in names:
        addresses = _wallets(name)
        if not addresses:
            print(f"{name}: no `wallets:` block — nothing to read", file=sys.stderr)
            failures += 1
            continue

        floor = args.min_value if args.min_value is not None else _min_value(name)
        rows, skipped, cash, failed, counted = _one(name, addresses, floor)
        if rows is None:
            failures += 1
            continue

        # A chain that errored looks exactly like a chain you sold out of, so writing on a
        # partial read would delete live positions and report it as movement. Refuse instead.
        if failed:
            for network, why in failed:
                print(f"{name}: {network} did not answer: {why}", file=sys.stderr)
            print(f"{name}: incomplete read — file left alone", file=sys.stderr)
            failures += 1
            continue

        if not rows:
            # Same refusal `plaid-sync` makes: `portfolios.load` rejects a portfolio with no
            # positions, so an empty write turns a bad read into an account that vanishes from
            # the review with nothing saying so.
            print(f"{name}: no usable positions on chain — file left alone", file=sys.stderr)
            failures += 1
            continue

        path = portfolios.DATA_ROOT / f"{name}.yaml"
        before = _tickers(name)
        verb = "would write" if args.dry_run else "wrote"
        if not args.dry_run:
            portfolios.write_positions(path, rows, source=wallet.SOURCE,
                                       horizon=_horizon(name), cash=cash)
        money = "" if cash is None else f", {cash:,.2f} in stablecoins"
        print(f"{name}: {verb} {len(rows)} position(s) from {len(addresses)} wallet(s) "
              f"across {len(counted)} network(s){money} -> {path}")
        _report(before, {r.ticker for r in rows})
        _dropped(skipped, everything=args.dropped)

    if failures:
        return 1
    print(f"\nsynced {datetime.now(UTC).date().isoformat()}. "
          f"Prices: uv run fetch-prices --all-portfolios")
    return 0


def _one(name: str, addresses, floor: float):
    """Read every address on one account and fold them into a single book.

    Several addresses become one file on purpose: two wallets holding ETH are one exposure to
    one chart, exactly as two brokerage accounts holding the same fund are in ``plaid``.
    """
    tokens: list[dict] = []
    failed: list[tuple[str, str]] = []
    counted: dict[str, None] = {}
    for entry in addresses:
        try:
            found = wallet.read(entry["address"], entry["networks"])
            # One chain answering "Internal server error" beside four that worked is the common
            # case, and it is usually transient — the probe saw Polygon do it once and not
            # again. Without this retry a single flaky chain would block the write for all of
            # them every night. Asked alone the chain normally answers in full.
            #
            # The partial rows for that chain are DISCARDED before the retry runs. A partial
            # read already returned the pages that did work, so keeping them and appending a
            # complete re-read would count those positions twice.
            retry = [n for n, _ in found.failed if n != "*"]
            kept = [t for t in found.tokens if str(t.get("network")) not in retry]
            for network in retry:
                again = wallet.read(entry["address"], (network,))
                if again.failed:
                    failed.extend(again.failed)
                else:
                    kept.extend(again.tokens)
            failed.extend((n, w) for n, w in found.failed if n == "*")
        except wallet.WalletError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return None, (), None, (), ()
        tokens.extend(kept)
        for network in found.networks:
            counted.setdefault(network, None)

    rows, skipped, cash = wallet.rows_from(
        wallet.Read(tokens=tuple(tokens), networks=tuple(counted)),
        min_value=floor, prefer=_prefer(name))
    return rows, skipped, cash, tuple(failed), tuple(counted)


def _prefer(name: str) -> dict[str, str]:
    """``prefer:`` from the portfolio file — the ticker-to-contract pins that settle a
    collision ``wallet._fold`` will otherwise refuse to guess at."""
    raw = _doc(name).get("prefer")
    if not isinstance(raw, dict):
        return {}
    return {str(k).strip().upper(): str(v).strip() for k, v in raw.items() if v}


def _doc(name: str) -> dict:
    path = portfolios.DATA_ROOT / f"{name}.yaml"
    if not path.is_file():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return doc if isinstance(doc, dict) else {}


def _wallets(name: str) -> tuple[dict, ...]:
    """The ``wallets:`` block, normalised. A bare string is accepted as an address, because
    the networks are only ever a narrowing and most files will not want to say."""
    raw = _doc(name).get("wallets")
    if not isinstance(raw, list):
        return ()
    out: list[dict] = []
    for entry in raw:
        if isinstance(entry, str | int):
            entry = {"address": entry}
        if not isinstance(entry, dict) or not entry.get("address"):
            continue
        address = _address(entry["address"])
        networks = entry.get("networks")
        out.append({
            "address": address,
            "networks": (tuple(str(n) for n in networks) if isinstance(networks, list)
                         else wallet.networks_for(address)),
        })
    return tuple(out)


def _address(raw) -> str:
    """An address as written, including when YAML decided it was a number.

    **An unquoted Ethereum address parses as an integer.** ``0x`` followed by 40 hex digits is
    valid YAML for a hex literal, so ``- 0xd8dA6BF2...`` arrives here as 1235...L, not as a
    string, and any leading zeros are gone from the text. Reformatting to 40 places restores it
    exactly — an EVM address is 20 bytes and case carries no meaning — so this recovers rather
    than refuses. A Solana address is base58 and cannot parse as a number, so it is unaffected.
    """
    if isinstance(raw, int) and not isinstance(raw, bool):
        return f"0x{raw:040x}"
    return str(raw).strip()


def _min_value(name: str) -> float:
    raw = _doc(name).get("min_value")
    if raw is None:
        return wallet.MIN_VALUE_USD
    try:
        return float(raw)
    except (TypeError, ValueError):
        return wallet.MIN_VALUE_USD


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


# Which drops are printed one by one, and which are only counted. A collision or a `prefer:`
# pin costs you a position you actually hold, so it is always named. The rest are airdropped
# junk arriving several hundred per chain, and a report nobody reads to the end has failed the
# same way as one that said nothing. `--dropped` prints all of it.
LOUD = ("collision", "pinned")

_SAID = {
    "unquoted": "unquoted by anyone — airdropped tokens",
    "dust": "under the value floor",
    "unreadable": "no symbol or decimals to read them by",
}


def _dropped(skipped, *, everything: bool) -> None:
    for miss in skipped:
        if everything or miss.kind in LOUD:
            print(f"    dropped {miss.what}: {miss.why}")
    if everything:
        return
    counted: dict[str, int] = {}
    for miss in skipped:
        if miss.kind not in LOUD:
            counted[miss.kind] = counted.get(miss.kind, 0) + 1
    for kind, total in sorted(counted.items()):
        print(f"    dropped {total} {_SAID.get(kind, kind)}  (--dropped to list them)")


def _report(before: set[str], now: set[str]) -> None:
    """Names what moved. A sync that only printed a count would hide a position closing, which
    is exactly the event the review exists to catch."""
    for ticker in sorted(now - before):
        print(f"    new   {ticker}")
    for ticker in sorted(before - now):
        print(f"    gone  {ticker}")
