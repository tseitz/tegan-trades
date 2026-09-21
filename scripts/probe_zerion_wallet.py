"""Does Zerion see positions ``wallet-sync`` cannot, and are they worth a second adapter?

``oracle/wallet.py`` asks Alchemy what ERC-20s an address *holds*. That is a different question
from what an address *owns*: an LP share, a lending deposit, or anything locked inside a
protocol that never minted a receipt token back to the wallet is invisible to a balance read.

Zerion indexes protocols rather than balances, the way DeBank does (and Rabby is DeBank — same
company, ``api.rabby.io`` is its backend, which is why "use the wallet's API" is not a separate
option). This prints the difference so the decision to build ``oracle/zerion.py`` is made on
what it would actually recover, not on the brochure.

WHAT IT FOUND, 2026-09-20, on this repo's own two addresses. $12,244 of EVM value against the
$8,146 ``wallet-sync`` writes, and the gap is three different bugs wearing one symptom:

1. **$7,328 — 60% of the wallet — sits on Robinhood Chain, which Alchemy does not index.**
   ETH 1.318, PONS 4,463, LIT (Lighter) 240. Chain ``0x1237``, robinscan.io. No number of
   entries in ``DEFAULT_EVM_NETWORKS`` reaches it; the provider has to support the chain.
2. **``NATIVE`` covers 6 networks, so the native coin is dropped on every other one.** ETH
   $26 on ink came back ``tokenAddress: null`` on a chain with no ``NATIVE`` row, so
   ``_describe`` returned ``(None, None)`` and it skipped as "no symbol or decimals". Loud in
   ``--dropped``, invisible in the position list. Any chain added without a ``NATIVE`` entry
   silently keeps only its ERC-20s.
3. **$255 of DeFi that a balance read cannot see by construction**: ETHFI $153 deposited in
   ether.fi, ACX $43 staked in Across V2, ETH $29 in the Blast Bridge, plus PUMP $379 on
   Solana. Small here, but it is the category that grows the moment anything is farmed.

Watch the double-count in 3: Zerion reports ``AAVE staked in Aave V2`` for the same units
``wallet-sync`` already writes as ``STKAAVE``. One position, two names — merging the sources
naively books it twice.

FOUR THINGS THE API DOES THAT WILL COST YOU A POSITION IF YOU MISS THEM:

1. **``filter[positions]`` defaults to ``only_simple``, which excludes every DeFi position.**
   The default answer to "what does this wallet own" is the same answer Alchemy already gives.
   ``no_filter`` below is the whole reason this probe exists; forget it and Zerion looks
   identical to what we have and the evaluation concludes the opposite of the truth.
2. **One pool returns one position PER TOKEN**, tied together by ``group_id``. A USDC/WETH LP
   is two rows. Summing by ticker without folding on ``group_id`` double-counts nothing, but
   reading a row as a standalone position misreads half a pool as a naked leg.
3. **``filter[trash]`` defaults to ``only_non_trash``** — Zerion drops airdrop spam server-side.
   That is the 1,064 unquoted tokens ``wallet-sync`` filters by hand, already done.
4. **``no_filter`` is a 400 on a Solana address**, and the message says so plainly. Send the
   one filter to both address shapes and every Solana wallet returns nothing.

Auth is HTTP Basic with the key as the username and an EMPTY password. The trailing colon is
load-bearing: base64 of ``key`` and base64 of ``key:`` are different strings, and the second is
the correct one.

RUN IT:

    uv run python scripts/probe_zerion_wallet.py
    uv run python scripts/probe_zerion_wallet.py --address 0xYours
    uv run python scripts/probe_zerion_wallet.py --all   # include sub-floor rows

NEEDS: ``ZERION_API_KEY`` in ``.env`` (dashboard.zerion.io, Developer plan, $0 forever,
2,000 requests/day). Free and read-only — a public address, like every other wallet read here.
DeFi Positions endpoints bill against 25% of the plan quota, so this costs ~1 call per address
out of ~500/day. Nothing here spends real money.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from oracle import wallet, wallet_cli, zerion


def describe(item: dict) -> dict:
    attrs = item.get("attributes") or {}
    info = attrs.get("fungible_info") or {}
    # The chain you HOLD it on lives in the relationship. `fungible_info.implementations`
    # is every chain the token was ever deployed to — 33 of them for ETH — so reading the
    # chain off there labels every row with the same meaningless list.
    chain = (
        ((item.get("relationships") or {}).get("chain") or {}).get("data") or {}
    ).get("id")
    return {
        "ticker": (info.get("symbol") or "?").upper(),
        "value": attrs.get("value"),
        "quantity": ((attrs.get("quantity") or {}).get("float")),
        "type": attrs.get("position_type") or "?",
        "protocol": attrs.get("protocol") or "",
        "module": attrs.get("protocol_module") or "",
        "group": attrs.get("group_id") or "",
        "chains": chain or "?",
    }


def alchemy_tickers(address: str) -> set[str]:
    """What ``wallet-sync --source alchemy`` would keep for this address today — the baseline
    to diff against. ``wallet.post`` retries a truncated chunked body internally now (it used
    to raise ``IncompleteRead`` as a bare traceback several times an hour on a busy address);
    a ``WalletError`` here means that retry budget is already exhausted."""
    read = wallet.read(address, wallet.networks_for(address))
    rows, _skipped, _cash, _unpriced = wallet.rows_from(read)
    return {row.ticker for row in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--address",
        action="append",
        help="override the addresses (default: the `wallets:` block of data/portfolios/crypto.yaml)",
    )
    parser.add_argument("--portfolio", default="crypto")
    parser.add_argument(
        "--min-value",
        type=float,
        default=wallet.MIN_VALUE_USD,
        help=f"hide rows under this many dollars (default {wallet.MIN_VALUE_USD:g})",
    )
    parser.add_argument("--all", action="store_true", help="show every row, no floor")
    parser.add_argument(
        "--json", action="store_true", help="dump the raw response and exit"
    )
    args = parser.parse_args(argv)

    try:
        zerion.api_key()
    except wallet.WalletError as exc:
        print(exc, file=sys.stderr)
        return 1
    addresses = args.address or [
        entry["address"] for entry in wallet_cli._wallets(args.portfolio)
    ]
    if not addresses:
        print(
            f"no addresses — is data/portfolios/{args.portfolio}.yaml there?",
            file=sys.stderr,
        )
        return 1

    floor = 0.0 if args.all else args.min_value
    for address in addresses:
        print(f"\n=== {address} ===")
        raw = list(zerion.read(address).positions)
        if args.json:
            print(json.dumps(raw[:5], indent=2))
            continue

        rows = [describe(item) for item in raw]
        kept = [r for r in rows if (r["value"] or 0) >= floor]
        total = sum(r["value"] or 0 for r in rows)
        print(
            f"{len(raw)} positions, ${total:,.2f} total, "
            f"{len(kept)} at or above ${floor:,.2f}"
        )

        by_type: dict[str, list[dict]] = defaultdict(list)
        for row in kept:
            by_type[row["type"]].append(row)
        for kind in sorted(by_type):
            print(f"\n  [{kind}]")
            for row in sorted(by_type[kind], key=lambda r: -(r["value"] or 0)):
                money = "-" if row["value"] is None else f"${row['value']:>12,.2f}"
                where = f"{row['protocol']} on " if row["protocol"] else ""
                print(
                    f"    {row['ticker']:<12} {money}  {row['quantity']:>18.8f}  "
                    f"{where}{row['chains']}"
                )

        try:
            baseline = alchemy_tickers(address)
        except wallet.WalletError as exc:
            print(f"\n  (no Alchemy baseline to diff against: {exc})")
            continue

        # The headline number. A ticker Zerion reports above the floor that `wallet-sync`
        # never writes is the position this whole evaluation is about.
        missed = [r for r in kept if r["ticker"] not in baseline]
        print(f"\n  ONLY ZERION SEES ({len(missed)} above ${floor:,.2f}):")
        if not missed:
            print("    nothing — the balance read already had everything")
        for row in sorted(missed, key=lambda r: -(r["value"] or 0)):
            money = "-" if row["value"] is None else f"${row['value']:,.2f}"
            where = f"{row['protocol']} on " if row["protocol"] else ""
            print(
                f"    {row['ticker']:<12} {money:>14}  {row['type']:<10} "
                f"{where}{row['chains']}"
            )
        only_alchemy = baseline - {r["ticker"] for r in kept}
        if only_alchemy:
            print(f"\n  ONLY ALCHEMY SEES: {', '.join(sorted(only_alchemy))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
