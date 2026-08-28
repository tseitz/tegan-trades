"""Is a wrapper alias still telling the truth about its underlying?

**Aliasing a wrapped token to the coin it wraps is a claim that they are the same price, and
that claim can rot silently.** ``cfg/assets.yaml`` says WETH *is* ETH, so `review` fetches
ETH's chart and prints it under WETH. That is right while the wrapper redeems 1:1 and wrong
the moment it does not — and nothing in the pipeline would say so, because the alias makes the
two indistinguishable downstream. This probe is where that claim gets re-checked.

**The distinction is redeemable vs yield-bearing, and it is not visible in the ticker.**

    WETH     0.9999   a contract wrapper. Redeem 1:1, atomically, any time. Cannot drift.
    stkAAVE  0.9956   a 1:1 claim on AAVE with a cooldown. Drifts only by quote noise.
    SolvBTC  0.9974   a 1:1 BTC-backed wrapper. Yield lives in a separate token.
    mETH     1.0942   staked ETH that ACCRUES. Drifts on purpose, and is NOT aliased.

Measured 2026-08-28. mETH is in the table as the counter-example: it looks exactly like the
other three, it is 9.4% away, and aliasing it to ETH would have quietly misreported a position
by that much forever. The 25% tolerance in ``core.review.mark_disagrees`` is far too wide to
catch it — that check hunts ticker collisions, which miss by multiples, not pegs, which
creep. So this is the only thing watching.

WHY A PROBE AND NOT A COMMENT IN ``cfg/assets.yaml``: ``distill-canon --review`` rewrites that
file with ``yaml.safe_dump``, which deletes every comment in it. A note explaining the aliases
would survive exactly until the next canon pass.

RUN IT when adding a wrapper alias, and whenever a wrapped position looks mispriced:

    uv run python scripts/probe_peg_drift.py

NEEDS ``ALCHEMY_API_KEY`` in ``.env``. Free, and reads only. Cross-source on purpose: the
wrapper is priced by contract address (Alchemy), the underlying by ticker on an exchange
(Coinbase). One number coming from each side is what makes the comparison worth anything.

READ THE OUTPUT LIKE THIS. Inside the tolerance, the alias is still honest. Outside it, either
drop the alias and let the token price on its own, or accept that every reading for it is off
by the drift — and say which, in the commit that changes it.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from oracle import wallet

# How far a 1:1 wrapper may sit from its underlying before the alias is a lie worth acting on.
# Deliberately tight where `mark_disagrees` is deliberately wide: that check separates a real
# asset from a scam token wearing its ticker, which misses by multiples. This one watches a peg
# creep, and a peg that has crept 2% is already reporting a position wrong by 2%.
TOLERANCE = 0.02

# Every wrapper this repo has an opinion about, aliased or not. `alias` is what
# cfg/assets.yaml claims; None means deliberately NOT aliased, and those rows are the point of
# the table — they record a decision that would otherwise look like an oversight.
#
# THE NETWORK IS PART OF THE IDENTITY, not decoration. A contract address is only unique within
# one chain, and asking the wrong chain returns no quote rather than an error — which reads as
# "the peg broke" when it means "you looked in the wrong place". SolvBTC is on Base, not
# mainnet, and cost one wrong reading here before that was obvious.
WRAPPERS = (
    ("WETH", "eth-mainnet", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "ETH", "ETH"),
    ("stkAAVE", "eth-mainnet", "0x4da27a545c0c5b758a6ba100e3a049001de870f5", "AAVE", "AAVE"),
    ("SolvBTC", "base-mainnet", "0x3b86ad95859b6ab773f55f8d94b4b9d443ee931f", "BTC", "BTC"),
    ("mETH", "eth-mainnet", "0xd5f7838f5c461feff7fe49ea5ebaf7728bb0adfa", "ETH", None),
)

COINBASE = "https://api.exchange.coinbase.com/products/{}-USD/ticker"


def _coinbase(ticker: str) -> float | None:
    request = urllib.request.Request(COINBASE.format(ticker),
                                     headers={"User-Agent": "tegan-trades/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return float(json.loads(response.read())["price"])
    except (OSError, ValueError, KeyError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    args = parser.parse_args(argv)

    try:
        wallet.api_key()
    except wallet.WalletError as exc:
        print(exc, file=sys.stderr)
        return 1

    quoted = wallet.post("/tokens/by-address", {"addresses": [
        {"network": network, "address": contract} for _, network, contract, _, _ in WRAPPERS
    ]}, host="https://api.g.alchemy.com/prices/v1")
    by_contract = {
        str(row.get("address", "")).lower(): next(
            (float(p["value"]) for p in row.get("prices") or ()
             if (p.get("currency") or "").lower() == "usd"), None)
        for row in quoted.get("data") or ()
    }

    print(f"{'wrapper':<10}{'underlying':<11}{'ratio':>9}  {'aliased to':<15}verdict")
    worst = 0.0
    for name, _network, contract, underlying, alias in WRAPPERS:
        wrapped = by_contract.get(contract.lower())
        base = _coinbase(underlying)
        if wrapped is None or not base:
            print(f"{name:<10}{underlying:<11}{'—':>9}  {alias or '(not aliased)':<15}"
                  f"no quote — check the network before believing this")
            continue
        ratio = wrapped / base
        drift = abs(ratio - 1.0)
        if alias is None:
            verdict = f"not aliased — {drift:.1%} off, and that is why"
        elif drift <= args.tolerance:
            verdict = "peg holds"
        else:
            verdict = f"DRIFTED {drift:.1%} — the alias now misreports every reading"
            worst = max(worst, drift)
        print(f"{name:<10}{underlying:<11}{ratio:>9.4f}  {alias or '(not aliased)':<15}{verdict}")

    return 1 if worst else 0


if __name__ == "__main__":
    raise SystemExit(main())
