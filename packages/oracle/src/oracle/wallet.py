"""Read a public wallet address, and write what it holds into the portfolio file you keep.

**The twin of ``plaid.py``, and deliberately shaped like it.** A chain is just another broker
that will tell you your positions: this fetches them, ``portfolios.write_positions`` lays them
out, and ``review`` cannot tell which sync filled the file. There is no ``wallet-link`` step,
because a public address needs no login — you write the address into the file once, by hand.

**Read-only by construction, and more strongly than Plaid is.** A public address is the string
you give someone to send you funds. It carries no authority at all, so nothing here *could*
move a coin. It is still a privacy leak — an address exposes your whole history to anyone
holding it — which is why it lives in ``data/``, gitignored, and never in ``cfg/``.

WHAT THE CHAIN GIVES, AND THE FOUR TRAPS IN IT. Measured against Alchemy's
``/assets/tokens/by-address`` on 2026-08-28; ``scripts/probe_alchemy_wallet.py`` re-runs it.

1. **The native coin arrives with NO metadata.** Your actual ETH row comes back with
   ``tokenAddress: null`` and ``symbol``/``decimals`` both null, while every ERC-20 beside it
   is fully described. Reading the symbol off the response therefore drops the one position
   you most wanted. ``NATIVE`` below supplies it per network.
2. **Zero balances are returned.** A wallet reports every token it has ever touched, most at
   ``0x0``. Vitalik's address paged 100 tokens deep and was mostly this.
3. **Junk has no price.** Airdropped scam tokens come back with ``tokenPrices: []``. That is a
   better dust filter than a name blocklist, and it needs no maintenance.
4. **Symbols are not unique and are actively impersonated.** Anyone can deploy a token called
   ``USDC``. ``portfolios.load`` allows one row per ticker, so a collision is not a cosmetic
   problem — it fails the file. ``_fold`` resolves it by price; see there.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from urllib.error import HTTPError

from core.env import load_env

from oracle import portfolios

HOST = "https://api.g.alchemy.com/data/v1"

SOURCE = portfolios.Source(name="Alchemy", command="wallet-sync", domain="crypto")

Row = portfolios.Row
Skipped = portfolios.Skipped

# Symbol and decimals for the coin a chain runs on, because the API returns neither (trap 1).
# Keyed by Alchemy's network name. An unlisted network still works for its tokens — only its
# native row is dropped, with a skip line naming it, rather than guessed at.
NATIVE = {
    "eth-mainnet": ("ETH", 18),
    "base-mainnet": ("ETH", 18),
    "arb-mainnet": ("ETH", 18),
    "opt-mainnet": ("ETH", 18),
    "matic-mainnet": ("POL", 18),
    "solana-mainnet": ("SOL", 9),
}

# Five is the API's per-address maximum, and taking all five costs the same one request as
# taking one. Narrow it with `networks:` on the address once you know which chains you use;
# starting narrow would instead hide a position on a chain nobody thought to name.
DEFAULT_EVM_NETWORKS = ("eth-mainnet", "base-mainnet", "arb-mainnet", "opt-mainnet",
                        "matic-mainnet")
# `solana-mainnet`, NOT `sol-mainnet`. Alchemy's own docs and its published agent
# reference both spell it `sol-mainnet`, and that name is rejected outright — the mistake
# reads as "Solana is not enabled on your key", which sends you to the dashboard to toggle
# something that was already on. `scripts/probe_alchemy_wallet.py` tries every variant.
DEFAULT_SOLANA_NETWORKS = ("solana-mainnet",)

# Dollars held as tokens. Reported as `cash:` rather than as positions, for the same reason
# `plaid.rows_from` drops a cash row: a dollar has no chart, so it would sit in the review
# forever with no level and no roster view. Here it is also simply true — a stablecoin in a
# wallet is exactly the money you could put into a position today.
#
# Plain, redeemable dollars only. A yield-bearing wrapper (sUSDe, sDAI) drifts from a dollar on
# purpose, which makes it a position with a thesis, not a balance.
STABLES = frozenset({
    "USDC", "USDT", "DAI", "USDS", "PYUSD", "USDE", "FDUSD", "TUSD", "BUSD", "USDG", "RLUSD",
})

# What a position has to be worth before it earns a row. A floor rather than a blocklist:
# scam tokens are minted faster than any list could track, and the ones carrying a real quoted
# price are exactly the ones a list would miss. Every drop is reported, so a floor set wrong
# announces itself; narrow or widen it per account with `min_value:` in the portfolio file.
#
# $25 and not $1, which is where this started. `scripts/probe_alchemy_wallet.py` on a busy
# public address kept 22 rows at a $1 floor, and the survivors included AIDOGE (194 billion
# units, $2.00) and BAG ($8.30) — priced junk, not positions. A review that suggests ADD and
# TRIM has nothing to say about eight dollars, so a row like that is pure noise in a section
# whose whole value is that you read all of it.
MIN_VALUE_USD = 25.0

# How far two quotes for one symbol may sit apart and still be believed to be one asset. Wide,
# because it separates "the same coin, priced a moment apart on two chains" from "a scam token
# wearing a real ticker" — and the second misses by orders of magnitude, never by 10%.
SAME_ASSET_TOLERANCE = 0.25

# Pages of 100. A cap rather than `while True`: a paging bug on the far side would otherwise
# spin this forever inside a nightly that has no one watching it.
#
# Hitting the cap is treated as a FAILED read, not a short one, and that is the important part.
# Tokens page in contract-address order, so truncating drops whichever real position happens to
# sit high in that ordering — silently, and differently each night. 100 pages is 10,000 tokens;
# the probe's public address, among the most airdropped on Ethereum, needed more than 2,500.
MAX_PAGES = 100

_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class WalletError(Exception):
    """A wallet read that failed, or a wallet configuration that cannot be trusted."""


@dataclass(frozen=True, slots=True)
class Read:
    """One wallet's worth of chain data, and everything that went wrong getting it."""
    tokens: tuple[dict, ...]
    networks: tuple[str, ...]
    # Networks the API answered for with an error. Non-empty means the read is INCOMPLETE, and
    # `wallet_cli` refuses to write on it: a chain that failed looks exactly like a chain you
    # sold out of, and overwriting the file would delete positions that still exist.
    failed: tuple[tuple[str, str], ...] = ()


def api_key() -> str:
    load_env()
    key = os.environ.get("ALCHEMY_API_KEY")
    if not key:
        raise WalletError(
            "ALCHEMY_API_KEY is not set — make a free app at dashboard.alchemy.com and put "
            "its key in .env (see .env.example)")
    return key


def networks_for(address: str) -> tuple[str, ...]:
    """The chains an address is worth asking about when the file does not say.

    Split on the address shape rather than configured per wallet, because asking a Solana
    network about a hex address is not a narrower query — it is a malformed one.
    """
    return DEFAULT_EVM_NETWORKS if _EVM_ADDRESS.match(address) else DEFAULT_SOLANA_NETWORKS


def post(path: str, payload: dict, *, timeout: int = 60, host: str = HOST) -> dict:
    """``host`` overrides the holdings API. Alchemy serves prices from a sibling host that takes
    the same key in the same place, and `scripts/probe_peg_drift.py` is its only caller."""
    body = json.dumps(payload).encode()
    request = urllib.request.Request(f"{host}/{api_key()}{path}", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise WalletError(f"{path} failed ({exc.code}): {exc.read().decode()[:600]}") from exc


def read(address: str, networks) -> Read:
    """Every token one address holds on the given networks, following pagination.

    ``withPrices`` is on and load-bearing twice over: the price is the dust filter (trap 3) and
    it is the ``mark`` that ``core.review.mark_disagrees`` checks our own quote against.
    """
    networks = tuple(networks)
    collected: list[dict] = []
    failed: list[tuple[str, str]] = []
    page: str | None = None

    for _ in range(MAX_PAGES):
        body: dict = {
            "addresses": [{"address": address, "networks": list(networks)}],
            "withMetadata": True,
            "withPrices": True,
        }
        if page:
            body["pageKey"] = page
        found = post("/assets/tokens/by-address", body)

        # A top-level `error` is the whole request refusing — an unsupported network, a bad
        # address. There is no partial result to keep, so it raises rather than returning half.
        top = found.get("error") or {}
        if top.get("message") and not top.get("partialErrors"):
            raise WalletError(f"{address}: {top['message']}")
        for partial in top.get("partialErrors") or ():
            failed.append((str(partial.get("network")), str(partial.get("message"))))

        data = found.get("data") or {}
        collected.extend(data.get("tokens") or ())
        page = data.get("pageKey")
        if not page:
            break
    else:
        failed.append(("*", f"stopped after {MAX_PAGES} pages — more tokens remain"))

    # Deduplicated by network: one failing chain reports once, however many pages it took to
    # say so. Keeping every repeat would make a two-line problem look like a twenty-line one.
    once: dict[str, str] = {}
    for name, why in failed:
        once.setdefault(name, why)
    return Read(tokens=tuple(collected), networks=networks, failed=tuple(once.items()))


def _units(raw, decimals: int) -> float | None:
    """Balance to whole units. Hex on EVM; accepts a plain decimal string too, because the
    Solana side of the same endpoint is not documented to use the same encoding."""
    if isinstance(raw, int | float):
        return float(raw) / (10 ** decimals)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        base = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except ValueError:
        return None
    return base / (10 ** decimals)


def _price(token: dict) -> float | None:
    for quote in token.get("tokenPrices") or ():
        if (quote.get("currency") or "").lower() == "usd":
            try:
                return float(quote["value"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _describe(token: dict) -> tuple[str | None, int | None]:
    """``(symbol, decimals)``, filling in the native coin the API describes as nothing."""
    meta = token.get("tokenMetadata") or {}
    symbol, decimals = meta.get("symbol"), meta.get("decimals")
    if token.get("tokenAddress") is None:
        native = NATIVE.get(str(token.get("network")))
        if native is None:
            return None, None
        symbol, decimals = native
    if not isinstance(symbol, str) or not symbol.strip():
        return None, None
    return symbol.strip().upper(), decimals if isinstance(decimals, int) else None


@dataclass(frozen=True, slots=True)
class _Candidate:
    ticker: str
    units: float
    price: float
    where: str          # network + contract, for a skip line someone has to act on
    contract: str | None


def _fold(candidates: list[_Candidate], prefer: dict[str, str]) -> tuple[list[Row],
                                                                        list[Skipped]]:
    """One row per ticker, summing what is genuinely one asset and refusing to guess otherwise.

    Two rows share a ticker for opposite reasons, and telling them apart is the whole job:

    * **The same coin on two chains.** ETH on mainnet and ETH on Base are one exposure to one
      chart, exactly as the same security in a Roth and a Traditional IRA is one exposure in
      ``plaid.rows_from``. Their quotes match, so they sum.
    * **A scam token wearing a real ticker.** Anyone can deploy ``LINK``. Summing it into the
      real one inflates the position by whatever the impostor's supply happens to be.

    **When the quotes disagree, this refuses rather than picking a winner, and the obvious
    tie-breaks are all wrong.** Holding the larger dollar value loses outright: a scam token
    mints nine billion units and quotes itself at $0.00002, which is "worth" $180,000 against a
    real 100 LINK at $1,500. Unit count is worse. Nothing on the chain says which contract is
    the real one, so this drops the whole ticker and names both contracts — the same choice
    ``cfg/oracle_map.yaml`` makes when a symbol is ambiguous, for the same reason: a confident
    wrong position size is worse than a missing one.

    Settle it once with ``prefer:`` in the portfolio file, mapping the ticker to the contract
    address you mean. That is a statement about an instrument, which is the only kind of
    identity claim worth trusting here.
    """
    by_ticker: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        by_ticker.setdefault(candidate.ticker, []).append(candidate)

    rows: list[Row] = []
    skipped: list[Skipped] = []
    for ticker, group in sorted(by_ticker.items()):
        pinned = str(prefer.get(ticker, "")).lower()
        if pinned:
            group, dropped = ([c for c in group if (c.contract or "").lower() == pinned],
                              [c for c in group if (c.contract or "").lower() != pinned])
            for other in dropped:
                skipped.append(Skipped(what=f"{ticker} at {other.where}", kind="pinned",
                                       why=f"`prefer:` pins {ticker} to {pinned}"))
            if not group:
                skipped.append(Skipped(what=ticker, kind="collision",
                                       why=f"`prefer:` pins it to {pinned}, which you do not hold"))
                continue

        low, high = min(c.price for c in group), max(c.price for c in group)
        if high - low > SAME_ASSET_TOLERANCE * max(abs(high), 1e-12):
            where = ", ".join(f"{c.where} at {c.price:.6g}" for c in group)
            skipped.append(Skipped(
                what=ticker, kind="collision",
                why=(f"{len(group)} tokens claim this ticker at prices that disagree ({where}) "
                     f"— pin the real one with `prefer: {{{ticker}: <contract>}}`")))
            continue

        # Richest first only now that they are known to be one asset, so `figi` and `mark`
        # come from the contract holding the most of it rather than from an arbitrary chain.
        group = sorted(group, key=lambda c: c.units * c.price, reverse=True)
        rows.append(Row(ticker=ticker, shares=sum(c.units for c in group), cost=None,
                        domain="crypto", figi=group[0].contract, mark=group[0].price))
    return rows, skipped


def rows_from(read_: Read, *, min_value: float = MIN_VALUE_USD,
              prefer: dict[str, str] | None = None
              ) -> tuple[tuple[Row, ...], tuple[Skipped, ...], float | None]:
    """``(rows, skipped, cash)`` from what one or more wallets reported.

    ``prefer`` maps a ticker to the contract address that owns it, settling the collisions
    ``_fold`` will otherwise refuse to guess at.

    ``cash`` is the stablecoin balance, which is money you could deploy today rather than a
    position with a chart — see ``STABLES``. It is None, never 0.0, when the wallets held no
    stablecoin at all, so "nothing to add with" stays distinct from "nobody said".
    """
    candidates: list[_Candidate] = []
    skipped: list[Skipped] = []
    cash = 0.0
    held_cash = False

    for token in read_.tokens:
        network = str(token.get("network") or "?")
        contract = token.get("tokenAddress")
        where = f"{network} {contract}" if contract else f"{network} native"

        symbol, decimals = _describe(token)
        if symbol is None or decimals is None:
            # Only ever reported for something that actually holds a balance: a wallet carries
            # hundreds of nameless zero rows, and naming them all would bury the one that matters.
            if _units(token.get("tokenBalance"), 18):
                skipped.append(Skipped(what=where, why="no symbol or decimals to read it by",
                                       kind="unreadable"))
            continue

        units = _units(token.get("tokenBalance"), decimals)
        if not units:
            continue                    # trap 2: an emptied token is not news

        price = _price(token)
        if price is None:
            skipped.append(Skipped(what=f"{symbol} at {where}", kind="unquoted",
                                   why="nobody quotes it — almost always an airdropped token"))
            continue

        if symbol in STABLES:
            cash += units * price
            held_cash = True
            continue

        value = units * price
        if value < min_value:
            skipped.append(Skipped(what=f"{symbol} at {where}", kind="dust",
                                   why=f"worth ${value:,.2f}, under the ${min_value:,.2f} floor"))
            continue
        candidates.append(_Candidate(ticker=symbol, units=units, price=price, where=where,
                                     contract=contract))

    rows, collisions = _fold(candidates, {k.upper(): v for k, v in (prefer or {}).items()})
    return tuple(rows), tuple(skipped + collisions), (cash if held_cash else None)
