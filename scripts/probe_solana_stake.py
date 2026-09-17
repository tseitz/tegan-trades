"""Probe for #62 slice A: does this repo's Alchemy key serve `getProgramAccounts`, and which
lamport figure is the real staked balance?

FINDINGS, measured 2026-09-16 against `DEs2iLbuF34RpeLaSXyt2s5EGq5Htdaw4und4CXUQNEM`:

1. **Alchemy answers the method, but one call alone trips its free-tier cap.** A single
   `getProgramAccounts` call returns `HTTP 429 "exceeded its compute units per second
   capacity"` — repeatable after a 3s wait, so this is the per-call CU cost of a program-wide
   scan against this key's tier, not a burst. The public `api.mainnet-beta.solana.com`
   answered the identical query in <0.2s with no key at all. **Decision for Tegan:** this
   reader uses the public endpoint as primary; Alchemy is not usable for this call without a
   paid throughput tier.
2. **The 2.3400 SOL figure is `account.lamports`, summed — not `delegation.stake`.** Today:
   `lamports` sums to 2.34032365 SOL across 10 accounts (the #49 research counted 11 two days
   earlier — one has since closed or been cashed out, ordinary wallet activity). `delegation.
   stake` sums to 2.31809895 SOL, short by each account's `rentExemptReserve`
   (2,282,880 lamports = 0.00228288 SOL), which `delegation.stake` excludes and `lamports`
   includes. `lamports` is the right field — it is the whole amount tied up, reserve included.
3. **Inactive and same-epoch accounts come back, in two shapes, and both must count.**
   - A fully-deactivated, drained account: `parsed.info.stake` is `null` (no `delegation` at
     all), lamports sitting at exactly `rentExemptReserve`.
   - A same-epoch churn account: `activationEpoch == deactivationEpoch` (1035/1035) with
     `delegation.stake` still populated in full.
   Both come through the same `getProgramAccounts` filter with no extra query needed. The
   reader sums `lamports` unconditionally (guarding only for a `null` `stake` field) rather
   than reading `delegation.stake`, so both shapes are counted — matching finding 2.
4. **The `8.91519844` in `crypto.yaml` is explained: staleness, not double-counting.** Today's
   read is 0.409598673 SOL liquid + 2.34032365 SOL staked = 2.7499 SOL. The stored figure is
   five days old (last synced 2026-09-11); the probe's liquid-token dump for this address has
   no wSOL or SOL-denominated SPL row, so it is not folding in a second source. The gap is
   ordinary wallet movement between syncs.
5. **The native SOL row carries a price quote at a true zero balance.** Verified against a
   freshly generated, never-funded address: `tokenBalance: 0x0...0`, `tokenPrices: [{currency:
   usd, value: "98.73", ...}]`. `_native_price` can rely on the native row's quote even when
   liquid SOL is zero.

RUN IT:

    uv run python scripts/probe_solana_stake.py
    uv run python scripts/probe_solana_stake.py --address <base58 pubkey>

NEEDS: `ALCHEMY_API_KEY` in `.env`. Free tier; this spends nothing — `getProgramAccounts` is a
read.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from urllib.error import HTTPError

from oracle import wallet

STAKE_PROGRAM = "Stake11111111111111111111111111111111111111"

# Offset 44 is the `withdrawer` authority in the stake account layout: 4 bytes of enum
# discriminant, then `rent_exempt_reserve` (8), then `staker` (32) at offset 12, then
# `withdrawer` (32) at offset 44. docs/research/yield-venues-and-trust-signals.md:54-59.
WITHDRAWER_OFFSET = 44

ALCHEMY_SOLANA_RPC = "https://solana-mainnet.g.alchemy.com/v2"
PUBLIC_SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# The wallet already known to hold delegated stake, per the #49 research.
DEFAULT_ADDRESS = "DEs2iLbuF34RpeLaSXyt2s5EGq5Htdaw4und4CXUQNEM"

# 32 random bytes, base58-encoded once and pinned here rather than generated fresh each run, so
# the probe is reproducible. Never funded on-chain — the point is an address the ledger has
# genuinely never seen, so "zero liquid SOL" is not a guess about a real wallet's history.
_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = []
    while n:
        n, rem = divmod(n, 58)
        out.append(_ALPHABET[rem])
    return "".join(reversed(out)) or _ALPHABET[0]


ZERO_ADDRESS = _base58(
    bytes.fromhex("0e3f2a7c9d1b5468acf0123e7d9b6a4c8f1e2d3b5a7c9f01e3d5b7a9c1f3e5d7")
)


def _post_rpc(url: str, payload: dict, *, timeout: int = 60) -> tuple[dict, float]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read()), time.monotonic() - start
    except HTTPError as exc:
        return (
            {"error": {"message": f"HTTP {exc.code}: {exc.read().decode()[:300]}"}},
            time.monotonic() - start,
        )


def _get_program_accounts(url: str, address: str) -> tuple[dict, float]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getProgramAccounts",
        "params": [
            STAKE_PROGRAM,
            {
                "encoding": "jsonParsed",
                "filters": [
                    {"memcmp": {"offset": WITHDRAWER_OFFSET, "bytes": address}}
                ],
            },
        ],
    }
    return _post_rpc(url, payload)


def _fetch_stake_accounts(address: str) -> tuple[list[dict], str]:
    """Try Alchemy's Solana RPC first, then the public endpoint. Returns (accounts, source)."""
    try:
        key = wallet.api_key()
    except wallet.WalletError as exc:
        print(f"  Alchemy key unavailable: {exc}")
        key = None

    if key:
        url = f"{ALCHEMY_SOLANA_RPC}/{key}"
        found, elapsed = _get_program_accounts(url, address)
        top = found.get("error") or {}
        if top.get("message"):
            print(f"  Alchemy   {elapsed:6.2f}s  ERROR: {top['message']}")
        else:
            print(
                f"  Alchemy   {elapsed:6.2f}s  ok — {len(found.get('result') or [])} accounts"
            )
            return found.get("result") or [], "alchemy"

    found, elapsed = _get_program_accounts(PUBLIC_SOLANA_RPC, address)
    top = found.get("error") or {}
    if top.get("message"):
        print(f"  Public    {elapsed:6.2f}s  ERROR: {top['message']}")
        return [], "none"
    print(
        f"  Public    {elapsed:6.2f}s  ok — {len(found.get('result') or [])} accounts"
    )
    return found.get("result") or [], "public"


def _print_accounts(accounts: list[dict]) -> float:
    total_lamports = 0
    total_delegated = 0.0
    for entry in accounts:
        info = (
            entry.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        )
        lamports = entry.get("account", {}).get("lamports", 0)
        meta = info.get("meta") or {}
        stake_field = info.get("stake")
        delegation = (stake_field or {}).get("delegation") or {}
        stake = delegation.get("stake")
        rent = meta.get("rentExemptReserve")
        voter = delegation.get("voter")
        activation = delegation.get("activationEpoch")
        deactivation = delegation.get("deactivationEpoch")
        print(
            f"    {entry.get('pubkey', '?'):<44} lamports={lamports:>12} "
            f"delegation.stake={stake!s:>12} rentExemptReserve={rent!s:>10} "
            f"activation={activation} deactivation={deactivation} voter={voter}"
        )
        if stake_field is None:
            print(f"        stake field is null — raw info: {json.dumps(info)}")
        total_lamports += lamports
        if stake is not None:
            total_delegated += float(stake)
    print(
        f"    {'TOTAL':<44} lamports={total_lamports:>12} "
        f"delegation.stake sum={total_delegated / 1e9:.8f} SOL "
        f"lamports sum={total_lamports / 1e9:.8f} SOL"
    )
    return total_delegated / 1e9


def _print_liquid_rows(address: str) -> None:
    try:
        read_ = wallet.read(address, ("solana-mainnet",))
    except wallet.WalletError as exc:
        print(f"  wallet.read failed: {exc}")
        return
    rows, skipped, _cash, _unpriced = wallet.rows_from(read_, min_value=0.0)
    for row in rows:
        print(
            f"    {row.ticker:<10} {row.shares:>18.9f} @ {row.mark} "
            f"{'native' if row.figi is None else row.figi}"
        )
    for miss in skipped:
        print(f"    dropped {miss.what}: {miss.why} [{miss.kind}]")
    native = [t for t in read_.tokens if t.get("tokenAddress") is None]
    if native:
        print(f"\n  native row, verbatim:\n{json.dumps(native[0], indent=2)}")
    else:
        print("\n  no native row returned at all")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    args = parser.parse_args(argv)

    print(f"1-3. Fetching stake accounts for {args.address}")
    accounts, source = _fetch_stake_accounts(args.address)
    print(f"  answered by: {source}")
    if accounts:
        staked_sol = _print_accounts(accounts)
    else:
        staked_sol = 0.0
        print("    (no stake accounts returned)")

    print(
        f"\n4. Liquid token rows for {args.address} (reconcile against crypto.yaml's SOL row)"
    )
    _print_liquid_rows(args.address)
    print(f"\n  staked total from delegation.stake: {staked_sol:.8f} SOL")

    print(f"\n5. Zero-liquid-SOL address: {ZERO_ADDRESS}")
    _print_liquid_rows(ZERO_ADDRESS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
