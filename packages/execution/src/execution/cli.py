"""``execute`` — pre-flight. Reports what a trading session *would* be able to do.

Deliberately incapable of placing an order. Its whole job is to answer the questions worth
answering before a triage session rather than during one: is the key loaded, does it reach
the right network, what does the account hold, and does the market you care about exist here.

That last question is the one worth a command. ``cfg/venue_map.yaml`` saying ``xyz:GOLD`` is
not evidence the symbol exists where the order is going — testnet's ``xyz`` builder carries
68 markets against mainnet's 103 — and finding out mid-session costs an approval.

    uv run execute                          # network, equity, market count
    uv run execute --coins BTC ETH xyz:GOLD # can I trade these, and at what precision
    uv run execute --network mainnet        # read-only; no confirmation needed to *look*
"""
from __future__ import annotations

import argparse
import sys

from execution import config as config_module
from execution.broker import (
    NETWORKS,
    SPOT_COLLATERAL_MODES,
    HyperliquidBroker,
    dex_of,
)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="execute",
        description="Pre-flight for order execution. Reports only; never places an order.",
    )
    parser.add_argument("--network", choices=sorted(NETWORKS), default=None,
                        help="override cfg/execution.yaml's network")
    parser.add_argument("--coins", nargs="+", default=None,
                        help="venue-native symbols to check (e.g. BTC ETH xyz:GOLD)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = config_module.load()
    network = args.network or config.network

    try:
        credentials = config_module.credentials()
    except config_module.MissingCredentials as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Every HIP-3 builder mentioned by the coins being checked, so their metas load. Without
    # this a namespaced coin reports as missing when it is merely not requested.
    dexs = tuple(sorted({dex_of(c) for c in (args.coins or [])} - {""}))

    try:
        broker = HyperliquidBroker(credentials, network=network, dexs=dexs)
        markets = broker.markets()
    except Exception as exc:  # noqa: BLE001 - a message beats a traceback here
        print(f"error: could not reach {network} — {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    print(f"network        {network}")
    print(f"account        {credentials.account_address}")
    print(f"risk per trade {config.risk_pct:.2%}"
          + (f", capped at {config.max_notional_frac:g}x notional"
             if config.max_notional_frac else ""))
    print(f"markets        {len(markets)} perpetuals"
          + (f" across dexs {', '.join(('core', *dexs))}" if dexs else ""))
    # Printed because it decides which balance counts as collateral, and reading the wrong
    # one makes a fully funded account report $0.00 — which looks exactly like an unfunded
    # one. Naming the mode here turns that into a one-line diagnosis.
    mode = broker.mode or "unknown (reading the perps balance)"
    print(f"account mode   {mode}"
          + ("  — spot balance is the collateral" if broker.mode in SPOT_COLLATERAL_MODES
             else "  — perps and spot are separate balances"))

    # How equity is displayed follows the mode, because the two are genuinely different
    # shapes. Under a unified account there is ONE pool shared by every dex — printing it
    # once per dex would read as that many multiples of the same money. Under manual mode
    # the pools really are separate, and a HIP-3 balance of zero beside a funded core book is
    # normal and must not be summed away.
    if broker.mode in SPOT_COLLATERAL_MODES:
        print(f"equity (shared) ${broker.equity():,.2f}"
              + (f"  — one pool backing core{''.join(' + ' + d for d in dexs)}"
                 if dexs else ""))
    else:
        for dex in ("", *dexs):
            print(f"equity ({'core' if dex == '' else dex:<6}) ${broker.equity(dex):,.2f}")

    if args.coins:
        print()
        for coin in args.coins:
            market = markets.get(coin)
            if market is None:
                print(f"  {coin:<14} NOT AVAILABLE on {network}")
            else:
                print(f"  {coin:<14} ok — lot {10 ** -market.sz_decimals:g}"
                      f" (szDecimals {market.sz_decimals})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
