"""Read a public wallet address by asking Zerion what it *owns*, not what it *holds*.

The twin of ``wallet.py``, and the second half of the pair. ``wallet.py`` asks Alchemy for
ERC-20 balances — a different question from "what does this address own", because an LP
share, a lending deposit, or anything locked inside a protocol that never minted a receipt
token back to the wallet is invisible to a balance read at any chain count. Zerion indexes
protocols instead, in one call across every EVM chain **and** Solana. ``scripts/probe_
zerion_wallet.py`` is the measurement that motivated building this; read it for the numbers.

WHAT ZERION GIVES, AND THE TRAPS IN IT — verified against the live API on 2026-09-20.

1. **``filter[positions]`` defaults to ``only_simple``, which excludes every DeFi position** —
   the same answer a balance read already gives. ``no_filter`` is the whole reason this module
   exists. It is a 400 on a Solana address ("currently not supported for Solana addresses"),
   so Solana gets the default instead — branch on ``wallet.is_solana``, not a shared constant.
2. **The chain a position is actually held on is ``relationships.chain.data.id``, never
   ``fungible_info.implementations``.** The latter lists every chain the token was ever
   deployed to — ETH carries 30+ — so reading the chain off there labels every row identically.
   The matching entry in ``implementations`` (filtered by that chain id) is still the right
   place to read the **contract address**, because the position itself carries none.
3. **Auth is HTTP Basic, key as the username, an EMPTY password.** ``base64(f"{key}:")`` — the
   trailing colon is load-bearing; omitting it produces a different, silently-wrong string.
4. **This endpoint does not paginate in practice** — 283 positions on this repo's busiest
   address came back in one page, and the response's ``links`` carries no ``next``. ``read``
   below still checks for one and raises rather than silently truncating, on the same
   reasoning as ``wallet.MAX_PAGES``: a paging bug on the far side should fail loud, not
   quietly drop whichever position happens to sort last.
5. **``filter[trash]=only_non_trash`` drops airdrop spam server-side**, which is strictly
   better dust filtering than Alchemy's price-less-token heuristic. The trade Alchemy does not
   make: a *real* holding Zerion mislabels as trash is dropped with no signal reaching this
   module at all — there is no local ``Skipped`` for it, unlike every other drop here. Accepted
   as a known gap rather than fought, because replicating a heuristic this module exists to
   retire would defeat the point of using an indexer. A position that already carries a value
   (``value`` is not ``None``) still gets the ordinary ``Skipped`` accounting below; only the
   server-side trash class is unaccounted for.

**There is no partial-read signal, and there cannot be one.** ``wallet.Read.failed`` exists
because Alchemy answers per-network and a network can fail independently while the others
succeed. Zerion answers with one call across every chain it indexes; if its view of one chain
is stale, that is invisible from here — there is no per-chain field to distrust. The only
guard against a bad read is the one every source already gets: ``wallet_cli.sync`` refuses to
write an empty position list (a wallet that legitimately emptied out looks identical to a
read that came back empty by accident, and the file is left alone either way).

**The AAVE/STKAAVE double-count — a real duplicate inside a single Zerion response, not just
a cross-source rename.** Measured live against this repo's own wallet: Zerion's ``no_filter``
answer for a Safety-Module staker carries *three* entries — ``staked`` in "Aave V2 Aave Pool"
(``symbol=AAVE``, using AAVE's own mainnet contract as its identity), ``reward`` at the same
pool (a smaller, genuinely separate accrual), and a plain ``wallet`` balance of the literal
``stkAAVE`` ERC-20 (``symbol=STKAAVE``, its own contract). The ``staked`` and ``wallet``
quantities matched to 16 significant digits — they are the same tokens, described twice:
staking AAVE mints stkAAVE 1:1 straight into the wallet, so Zerion's protocol layer restates
the very balance its own wallet layer already reported, unlike a lock-up vault (ether.fi,
Across V2) where the staked tokens genuinely leave the liquid balance. Folding both in as
written would double the position. ``_dedupe_receipt_positions`` below drops the ``staked``/
``locked`` entry in favor of the plain wallet balance whenever a same-chain wallet quantity
matches it almost exactly — the wallet balance is the more primitive, unambiguous fact.

**Deposits and stakes stay in their own Mandate, never Treasury.** ADR-0008 draws that line on
intent, not mechanism: "you chose to hold ETHFI/ACX, the wrapper does not change that choice."
A staked or deposited position here folds into its own ticker's row exactly like a plain
balance would — see ``position_type`` handling below. A stablecoin ``deposit`` (a lending
position, principal in dollars) becomes ``cash:`` exactly like a stablecoin sitting idle, per
ADR-0008's own "Considered and rejected": routing wallet-synced stablecoins into Treasury
needs a hand-typed-row exemption Treasury's file does not have, and was rejected for the same
reason there.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from core.env import load_env

from oracle import http, portfolios, wallet

SOURCE = portfolios.Source(name="Zerion", command="wallet-sync", domain="crypto")

Row = portfolios.Row
Skipped = portfolios.Skipped
WalletError = wallet.WalletError

HOST = "https://api.zerion.io/v1"

# The API default and the only value that surfaces DeFi positions — see docstring point 1.
# A 400 on Solana, so `read` branches rather than sending this to both address shapes.
POSITIONS_FILTER = "no_filter"

# `position_type` values that are exposure held for its yield rather than a liquid balance —
# staked SOL, an LP farm, a lending deposit that pays interest in the same asset. Folded into
# the same ticker's row via `Row.staked`, per ADR-0008: the wrapper does not change what you
# chose to be exposed to. Every other `position_type` (`wallet`, `deposit`, `reward`,
# `investment`, ...) is treated as a plain liquid balance — see the module docstring on why
# `deposit` still means "your own Mandate", not Treasury.
STAKED_TYPES = frozenset({"staked", "locked"})

MAX_PAGES = 10


class ZerionError(WalletError):
    """A Zerion-specific read failure, still catchable as ``wallet.WalletError`` by callers
    that hold one branch for both sources."""


@dataclass(frozen=True, slots=True)
class Read:
    """One address's positions, and the chains Zerion actually reported one on — informational
    only, unlike ``wallet.Read.networks``: there is nothing here to retry per chain."""

    positions: tuple[dict, ...]
    chains: tuple[str, ...] = ()


def api_key() -> str:
    load_env()
    key = os.environ.get("ZERION_API_KEY")
    if not key:
        raise ZerionError(
            "ZERION_API_KEY is not set — make a free key at dashboard.zerion.io "
            "(Developer plan, $0) and put it in .env (see .env.example)"
        )
    return key


def _auth_header(key: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{key}:".encode()).decode()
    return {"Authorization": f"Basic {encoded}", "accept": "application/json"}


def read(address: str) -> Read:
    """Every position one address holds, across every chain Zerion indexes for it."""
    key = api_key()
    wanted = "only_simple" if wallet.is_solana(address) else POSITIONS_FILTER
    collected: list[dict] = []
    url = f"{HOST}/wallets/{address}/positions/"
    params = {
        "currency": "usd",
        "filter[positions]": wanted,
        "filter[trash]": "only_non_trash",
    }
    for _ in range(MAX_PAGES):
        try:
            found = http.get_json(url, params=params, headers=_auth_header(key))
        except http.FetchError as exc:
            raise ZerionError(f"{address}: {exc}") from exc
        if not isinstance(found, dict) or "data" not in found:
            raise ZerionError(f"{address}: malformed response — no `data`")
        collected.extend(found["data"] or ())
        next_url = ((found.get("links") or {}).get("next") or "").strip()
        if not next_url:
            break
        url, params = next_url, None
    else:
        raise ZerionError(f"{address}: stopped after {MAX_PAGES} pages — more positions remain")

    chains: dict[str, None] = {}
    for item in collected:
        chain = _chain(item)
        if chain:
            chains.setdefault(chain, None)
    return Read(positions=tuple(collected), chains=tuple(chains))


def _chain(item: dict) -> str | None:
    rel = (item.get("relationships") or {}).get("chain") or {}
    return ((rel.get("data") or {}).get("id")) or None


def _contract(item: dict, chain: str | None) -> str | None:
    """The contract on the chain this position is actually held on — never the alphabetically
    first (or any other) entry in ``implementations``, which lists every chain a token was
    ever deployed to (docstring point 2). ``None`` for the chain's own native coin."""
    impls = ((item.get("attributes") or {}).get("fungible_info") or {}).get(
        "implementations"
    ) or ()
    for impl in impls:
        if impl.get("chain_id") == chain:
            return impl.get("address")
    return None


def _wallet_positions_by_chain(positions) -> dict[str, list[tuple[str, float]]]:
    """Every plain ``wallet``-type balance's ``(symbol, quantity)``, grouped by chain — the
    only input ``_is_receipt_duplicate`` needs to catch the AAVE/STKAAVE hazard the module
    docstring describes."""
    by_chain: dict[str, list[tuple[str, float]]] = {}
    for item in positions:
        attrs = item.get("attributes") or {}
        if attrs.get("position_type") != "wallet":
            continue
        chain = _chain(item) or ""
        symbol = str((attrs.get("fungible_info") or {}).get("symbol") or "").strip().upper()
        quantity = ((attrs.get("quantity") or {}).get("float")) or 0.0
        by_chain.setdefault(chain, []).append((symbol, quantity))
    return by_chain


def _is_receipt_duplicate(
    chain: str | None,
    symbol: str,
    quantity: float,
    wallet_positions: dict[str, list[tuple[str, float]]],
) -> bool:
    """A ``staked``/``locked`` quantity that matches a *different-symbol* plain wallet balance
    on the same chain, to within float noise, is the same tokens described twice — see the
    module docstring. The symbol must differ: a liquid ``ACX`` balance sitting alongside a
    staked ``ACX`` position of the same size is the ordinary, intended case — two real
    holdings of one ticker, which is exactly what ``Row.staked`` exists to combine, not a
    duplicate view. Matched to a relative tolerance tight enough that two genuinely different
    holdings could not coincide by chance; the live case that motivated this matched to 16
    significant digits."""
    return any(
        other_symbol != symbol
        and abs(other_quantity - quantity) <= 1e-9 * max(abs(quantity), 1.0)
        for other_symbol, other_quantity in wallet_positions.get(chain or "", ())
    )


def rows_from(
    read_: Read,
    *,
    min_value: float = wallet.MIN_VALUE_USD,
    prefer: dict[str, str] | None = None,
) -> tuple[tuple[Row, ...], tuple[Skipped, ...], float | None]:
    """``(rows, skipped, cash)`` — three values, not ``wallet.rows_from``'s four. There is no
    ``unpriced`` concept to plumb through here: ``wallet.rows_from`` needs one because a
    staked total arrives from a *second* source (``stake_solana``) that ``_fold`` can silently
    lose if it drops the ticker as a collision. Every Zerion position — staked or not — goes
    through ``wallet._fold`` together in this single pass, so a dropped collision already gets
    the ordinary ``Skipped(kind="collision")`` accounting; nothing here can vanish silently.
    """
    wallet_positions = _wallet_positions_by_chain(read_.positions)

    candidates: list[wallet._Candidate] = []
    skipped: list[Skipped] = []
    cash = 0.0
    held_cash = False

    for item in read_.positions:
        attrs = item.get("attributes") or {}
        fungible = attrs.get("fungible_info") or {}
        symbol = str(fungible.get("symbol") or "").strip().upper()
        if not symbol:
            skipped.append(
                Skipped(what=item.get("id", "?"), why="no symbol", kind="unreadable")
            )
            continue

        chain = _chain(item)
        contract = _contract(item, chain)
        where = f"{chain or '?'} {contract}" if contract else f"{chain or '?'} native"

        value = attrs.get("value")
        price = attrs.get("price")
        quantity = ((attrs.get("quantity") or {}).get("float")) or 0.0
        position_type = attrs.get("position_type")
        if value is None or price is None:
            # A real holding Zerion could not price — not the server-side trash class (point
            # 5), which never reaches this loop at all.
            skipped.append(
                Skipped(
                    what=f"{symbol} at {where}",
                    kind="unquoted",
                    why="Zerion returned no value for this position",
                )
            )
            continue

        if position_type in STAKED_TYPES and _is_receipt_duplicate(
            chain, symbol, quantity, wallet_positions
        ):
            skipped.append(
                Skipped(
                    what=f"{symbol} at {where}",
                    kind="duplicate",
                    why=(
                        "matches a plain wallet balance on the same chain to the same "
                        "quantity — Zerion's protocol layer restating a receipt token "
                        "(e.g. stkAAVE) its wallet layer already reported; counting both "
                        "would double the position"
                    ),
                )
            )
            continue

        if symbol in wallet.STABLES:
            cash += float(value)
            held_cash = True
            continue

        staked_amount = quantity if position_type in STAKED_TYPES else 0.0
        candidates.append(
            wallet._Candidate(
                ticker=symbol,
                units=0.0 if staked_amount else quantity,
                price=float(price),
                where=where,
                contract=contract,
                staked=staked_amount,
            )
        )

    rows, collisions = wallet._fold(
        candidates, {k.upper(): v for k, v in (prefer or {}).items()}
    )

    final_rows: list[Row] = []
    for row in rows:
        total_value = row.shares * (row.mark or 0.0)
        if total_value < min_value:
            skipped.append(
                Skipped(
                    what=row.ticker,
                    kind="dust",
                    why=f"worth ${total_value:,.2f}, under the ${min_value:,.2f} floor",
                )
            )
            continue
        final_rows.append(row)

    return tuple(final_rows), tuple(skipped + collisions), (cash if held_cash else None)
