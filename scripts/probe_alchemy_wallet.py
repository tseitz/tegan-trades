"""What does a chain actually hand back for a public address, and which chains will answer?

Two questions, and the second one is why this exists. Measured 2026-08-28 on this repo's key:

    eth-mainnet      ok
    solana-mainnet   ok
    sol-mainnet      Unsupported network: sol-mainnet

**The Solana network is spelled `solana-mainnet`, and Alchemy's own documentation says
`sol-mainnet`.** So does its published agent reference. That name is rejected outright, and the
rejection reads as an entitlement problem — "Unsupported network" sends you to the dashboard to
enable something that was already enabled, and the plain RPC endpoint answering Solana fine on
the same key makes the wrong diagnosis look confirmed. It cost an hour here. A name is not
support: probe the name before concluding anything about the key.

Run this before assuming a chain is reachable, and whenever a network starts refusing.

WHAT THE FIRST QUESTION FOUND, all of it load-bearing in ``oracle/wallet.py``:

1. **The native coin arrives with no metadata.** ETH itself comes back as ``tokenAddress:
   null`` with ``symbol`` and ``decimals`` both null, while every ERC-20 beside it is fully
   described. Read the symbol off the response and you drop the position you cared about most.
2. **Zero balances are returned.** Of the first 100 tokens on a well-used address, most were
   ``0x0`` — every token it has ever touched, not every token it holds.
3. **Junk carries no price.** Airdropped scam tokens come back with ``tokenPrices: []``, which
   is a better dust filter than any name blocklist and needs no maintenance.
4. **Pagination is real.** 100 tokens per page with a ``pageKey``; a busy address runs deep.

RUN IT:

    uv run python scripts/probe_alchemy_wallet.py
    uv run python scripts/probe_alchemy_wallet.py --address 0xYours --network eth-mainnet

NEEDS: ``ALCHEMY_API_KEY`` in ``.env``. Free tier; this spends nothing. The default address is
a famous public one (vitalik.eth), so the probe answers the coverage question without anyone
having to paste a wallet they own into a terminal.
"""
from __future__ import annotations

import argparse
import json
import sys

from oracle import wallet

# Public, well-known, and busy enough to exhibit all four traps above.
DEFAULT_ADDRESS = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
DEFAULT_NETWORKS = ("eth-mainnet", "base-mainnet", "arb-mainnet", "opt-mainnet",
                    "matic-mainnet", "solana-mainnet")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--network", action="append", dest="networks",
                        help="repeatable; default probes each of the six one at a time")
    parser.add_argument("--show", type=int, default=5, help="sample rows to print per network")
    args = parser.parse_args(argv)

    try:
        wallet.api_key()
    except wallet.WalletError as exc:
        print(exc, file=sys.stderr)
        return 1

    # One network per request on purpose. Asked together, a single unsupported chain can fail
    # the whole call, and the point here is to learn which ones individually answer.
    for network in args.networks or DEFAULT_NETWORKS:
        try:
            found = wallet.read(args.address, (network,))
        except wallet.WalletError as exc:
            print(f"{network:<16} REFUSED  {str(exc).split(': ', 1)[-1]}")
            continue

        rows, skipped, cash = wallet.rows_from(found)
        held = sum(1 for t in found.tokens if (t.get("tokenBalance") or "0x0") not in ("0x0", "0"))
        money = "-" if cash is None else f"${cash:,.2f}"
        print(f"{network:<16} ok  {len(found.tokens):>4} returned  {held:>4} non-zero  "
              f"{len(rows):>3} kept  {len(skipped):>4} dropped  {money} stables")
        for why in found.failed:
            print(f"    partial: {why}")
        for row in rows[:args.show]:
            print(f"    {row.ticker:<10} {row.shares:>18.8f} @ {row.mark}  {row.figi or 'native'}")
        for miss in skipped[:args.show]:
            print(f"    dropped {miss.what}: {miss.why}")

    # The native row, printed raw, because trap 1 is the one nobody believes without seeing it.
    native = [t for t in wallet.read(args.address, ("eth-mainnet",)).tokens
              if t.get("tokenAddress") is None]
    if native:
        print(f"\nthe native row, verbatim:\n{json.dumps(native[0], indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
