"""Read a Solana wallet's delegated stake accounts — the SOL a token-balance endpoint cannot see.

Alchemy's `/assets/tokens/by-address` (`wallet.py`) returns SPL token balances and the native
lamport balance, never a delegated stake account: a stake account is owned by the Stake program
(`Stake11111111111111111111111111111111111111`), holding lamports of its own, invisible to any
token-balance endpoint on any provider. `scripts/probe_solana_stake.py` measured the findings
below; cross-reference it rather than repeating its narrative here.

**Public RPC, not Alchemy.** A `getProgramAccounts` scan over the whole Stake program is
expensive enough that one call alone trips this repo's Alchemy tier (`HTTP 429`, confirmed
after a retry) — probe finding 1. `api.mainnet-beta.solana.com` answers the identical query in
under 0.2s with no key at all, so this reader talks to it directly rather than through
`wallet.post`, which always inserts an Alchemy key into the URL. Decided with Tegan on
2026-09-16: a public-RPC dependency over a paid Alchemy throughput tier.

Two findings drive `staked_sol` below:

1. **`account.lamports`, not `delegation.stake`, is the staked total.** `delegation.stake`
   excludes each account's `rentExemptReserve` — money still tied up, not spendable until the
   account is fully unstaked and closed.
2. **A fully-deactivated account has `stake: null`, no `delegation` object at all**, and a
   same-epoch-churned account can carry a full `delegation.stake` with `activationEpoch ==
   deactivationEpoch`. Both still count: their `lamports` are tied up in a stake account,
   whatever state the delegation is in.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from urllib.error import HTTPError

from oracle import wallet

HOST = "https://api.mainnet-beta.solana.com"

STAKE_PROGRAM = "Stake11111111111111111111111111111111111111"

# Offset 44 is the `withdrawer` authority in the stake account layout: 4 bytes of enum
# discriminant, then `rent_exempt_reserve` (8), then `staker` (32) at offset 12, then
# `withdrawer` (32) at offset 44. docs/research/yield-venues-and-trust-signals.md:54-59.
WITHDRAWER_OFFSET = 44


@dataclass(frozen=True, slots=True)
class StakeRead:
    """One address's stake accounts.

    `failed` mirrors `wallet.Read`'s shape, though a single RPC call has no partial-success
    case to report — `read` raises `wallet.WalletError` on any failure instead, so this stays
    empty today. Kept for a caller that wants to handle both readers the same way.
    """

    accounts: tuple[dict, ...]
    failed: tuple[tuple[str, str], ...] = ()


def read(address: str, *, timeout: int = 15) -> StakeRead:
    """Every stake account delegated with `address` as its withdraw authority.

    `timeout` defaults far below `wallet.post`'s 60s: the probe measured this call answering
    in under 0.2s, so a long hang here means the endpoint is stuck, not busy.
    """
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
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        HOST, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            found = json.loads(response.read())
    except HTTPError as exc:
        raise wallet.WalletError(
            f"getProgramAccounts failed ({exc.code}): {exc.read().decode()[:600]}"
        ) from exc

    # A JSON-RPC error arrives inside a 200 — the identical shape wallet.py:180-183 handles on
    # Alchemy's REST API. A missing `result` key is a second, distinct failure: `result: []` is
    # the valid "no stake accounts" answer, so only an *absent* key is malformed.
    top = found.get("error") or {}
    if top.get("message"):
        raise wallet.WalletError(f"{address}: {top['message']}")
    if "result" not in found:
        raise wallet.WalletError(
            f"{address}: malformed response — no `result` and no `error`"
        )
    return StakeRead(accounts=tuple(found["result"] or ()))


def staked_sol(read_: StakeRead) -> float | None:
    """Total whole SOL tied up across every stake account, `None` when none were found.

    Sums `account.lamports` unconditionally — including a fully-deactivated account's
    `rentExemptReserve` and a same-epoch-churned account's full delegation, per findings 1-2.
    """
    if not read_.accounts:
        return None
    total_lamports = sum(
        entry.get("account", {}).get("lamports", 0) for entry in read_.accounts
    )
    return total_lamports / 1e9
