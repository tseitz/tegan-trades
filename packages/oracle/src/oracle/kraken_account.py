"""Read what a Kraken account holds, with a key that can do nothing else.

**The first signed request outside ``execution`` — and it signs a read.** Custodial balances
are invisible to every chain reader, and Plaid has no Kraken connection, so this is the only
way in. Read-only twice over: the key must be created with **Query Funds** ticked and nothing
else, and ``PRIVATE_PATHS`` refuses any private endpoint but ``BalanceEx`` before a request is
built. Kraken has no endpoint that reports a key's permissions, so the key page on kraken.com is
the only evidence for the first half; proving a key *cannot* trade would mean calling a trade
endpoint from here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse
from dataclasses import dataclass

import requests
from core.env import load_env

from oracle import http, portfolios, wallet

SOURCE = portfolios.Source(name="Kraken", command="kraken-sync", domain="crypto")

API = "https://api.kraken.com"

# Widening this set is the one change that turns this module into something that can move
# money. A test pins it.
PRIVATE_PATHS = frozenset({"/0/private/BalanceEx"})

# Balance-code suffixes, from `scripts/probe_kraken_account.py`. Earn variants are the same coin
# put to work, so they fold into its row as `staked`. `.HOLD` is on hold, not earning. Anything
# else — `.CORE`, `.INK`, `.TEMPO` are chain-wrapped variants nobody here has held yet — raises
# rather than being guessed at.
EARN_SUFFIXES = frozenset({"S", "M", "P", "B", "F"})
LIQUID_SUFFIXES = frozenset({"", "HOLD"})

# Kraken's spellings of assets this repo prices under another ticker.
RENAMES = {"XBT": "BTC", "XDG": "DOGE", "ETH2": "ETH"}

# A non-USD balance has no place in a dollar book; landing as a "crypto" row would price EUR
# as a coin. Raises instead.
FOREIGN_FIAT = frozenset({"EUR", "GBP", "CAD", "JPY", "CHF", "AUD", "AED"})

# Fee credits: spendable only on Kraken's own trading fees, never a position.
FEE_CREDITS = frozenset({"KFEE", "FEE"})


class KrakenError(Exception):
    """A Kraken read that failed. Never answered with an empty book."""


class MissingCredentials(KrakenError):
    """The key or secret is not in the environment or ``.env``."""


def credentials() -> tuple[str, str]:
    load_env()
    key = os.environ.get("KRAKEN_API_KEY")
    secret = os.environ.get("KRAKEN_API_SECRET")
    if not key or not secret:
        raise MissingCredentials(
            "KRAKEN_API_KEY / KRAKEN_API_SECRET are not set — make a Query-Funds-only key "
            "on kraken.com and put both in .env (see .env.example)"
        )
    try:
        base64.b64decode(secret, validate=True)
    except ValueError as exc:
        raise KrakenError(
            "KRAKEN_API_SECRET is not valid base64 — re-copy it from kraken.com"
        ) from exc
    return key, secret


def sign(path: str, nonce: str, postdata: str, secret: str) -> str:
    # The raw SHA-256 digest, not its hex: hex signs a different message and Kraken answers
    # `EAPI:Invalid key`, which reads like a bad key rather than a bad signature.
    message = path.encode() + hashlib.sha256((nonce + postdata).encode()).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def private(path: str) -> dict:
    if path not in PRIVATE_PATHS:
        raise KrakenError(f"{path} is not a read this module is allowed to make")
    key, secret = credentials()
    nonce = str(time.time_ns() // 1_000_000)
    postdata = urllib.parse.urlencode({"nonce": nonce})
    # One attempt, no retry: a retry resends the same nonce, which Kraken rejects. A failed
    # night is retried by the next nightly instead.
    try:
        resp = requests.post(
            API + path,
            data=postdata,
            timeout=http.TIMEOUT,
            # A redirect to another host would carry API-Key and API-Sign with it; requests
            # strips only `Authorization`.
            allow_redirects=False,
            headers={
                "API-Key": key,
                "API-Sign": sign(path, nonce, postdata, secret),
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "User-Agent": http.USER_AGENT,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise KrakenError(f"{path}: {exc}") from exc
    return _result(path, payload)


def public(path: str, params: dict | None = None, *, get_json=http.get_json) -> dict:
    try:
        payload = get_json(API + path, params)
    except http.FetchError as exc:
        raise KrakenError(f"{path}: {exc}") from exc
    return _result(path, payload)


def _result(path: str, payload) -> dict:
    # Kraken reports failures as HTTP 200 with a non-empty `error` list.
    if not isinstance(payload, dict) or payload.get("error"):
        why = payload.get("error") if isinstance(payload, dict) else payload
        raise KrakenError(f"{path}: {why}")
    return payload.get("result") or {}


@dataclass(frozen=True, slots=True)
class Read:
    """One account's balances plus the public tables they join to."""

    balances: dict  # code -> {"balance": str, "hold_trade": str}
    assets: dict  # code -> {"altname": ...}
    pairs: dict  # pair name -> {"base", "quote", "wsname", ...}
    prices: dict  # pair name -> last trade price


def read() -> Read:
    balances = private("/0/private/BalanceEx")
    assets = public("/0/public/Assets")
    pairs = public("/0/public/AssetPairs")
    usd = _usd_pairs(pairs)
    wanted = sorted(
        {
            usd[base]
            for code, fields in balances.items()
            if _quantity(code, fields) and (base := _base_code(code, assets)) in usd
        }
    )
    # Batched on purpose, and safe only because every name comes from AssetPairs: one unknown
    # pair fails the whole call (`EQuery:Unknown asset pair`).
    ticker = public("/0/public/Ticker", {"pair": ",".join(wanted)}) if wanted else {}
    try:
        prices = {name: float(t["c"][0]) for name, t in ticker.items()}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise KrakenError(f"/0/public/Ticker: unreadable answer ({exc!r})") from exc
    return Read(balances=balances, assets=assets, pairs=pairs, prices=prices)


def rows_from(read_: Read, *, min_value: float = wallet.MIN_VALUE_USD):
    """``(rows, skipped, cash)`` — one row per ticker, every earn variant folded into it."""
    usd = _usd_pairs(read_.pairs)
    held: dict[str, dict] = {}
    skipped: list[portfolios.Skipped] = []
    cash = 0.0
    for code, fields in sorted(read_.balances.items()):
        quantity = _quantity(code, fields)
        if not quantity:
            continue
        suffix = code.partition(".")[2]
        if suffix not in EARN_SUFFIXES | LIQUID_SUFFIXES:
            raise KrakenError(
                f"{code}: unknown balance suffix .{suffix} — add it to "
                "kraken_account's suffix sets once you know what it is"
            )
        base = _base_code(code, read_.assets)
        pair = usd.get(base)
        ticker = _ticker(base, read_.assets, read_.pairs.get(pair) if pair else None)
        if code in FEE_CREDITS or ticker in FEE_CREDITS:
            skipped.append(
                portfolios.Skipped(code, "fee credits, not a position", "fee")
            )
            continue
        if ticker in FOREIGN_FIAT:
            raise KrakenError(
                f"{code}: {quantity:g} {ticker} held — a non-USD balance has "
                "no place in this book yet"
            )
        mark = read_.prices.get(pair) if pair else None
        if ticker == "USD" or ticker in wallet.STABLES:
            cash += quantity * (mark or 1.0)
            continue
        row = held.setdefault(
            ticker, {"shares": 0.0, "staked": 0.0, "mark": mark, "code": base}
        )
        row["shares"] += quantity
        if suffix in EARN_SUFFIXES:
            row["staked"] += quantity

    rows: list[portfolios.Row] = []
    for ticker, row in sorted(held.items()):
        mark = row["mark"]
        if mark is not None and row["shares"] * mark < min_value:
            skipped.append(
                portfolios.Skipped(
                    ticker,
                    f"${row['shares'] * mark:,.2f}, under the value floor",
                    "dust",
                )
            )
            continue
        rows.append(
            portfolios.Row(
                ticker=ticker,
                shares=row["shares"],
                cost=None,
                domain="crypto",
                figi=row["code"],
                mark=mark,
                staked=row["staked"] or None,
            )
        )
    return rows, tuple(skipped), cash


def _quantity(code: str, fields) -> float:
    try:
        return float(fields["balance"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KrakenError(f"{code}: unreadable balance {fields!r}") from exc


def _usd_pairs(pairs: dict) -> dict[str, str]:
    """Base code -> its USD pair name. `.d` names are the dark-pool twins of real pairs."""
    return {
        info.get("base"): name
        for name, info in pairs.items()
        if info.get("quote") == "ZUSD" and info.get("base") and not name.endswith(".d")
    }


def _base_code(code: str, assets: dict) -> str:
    """The asset code a balance belongs to. A suffixed balance is spelled with the *altname*
    (`XBT.M`), while pairs key on the asset code (`XXBT`), so the prefix needs translating."""
    prefix = code.partition(".")[0]
    if prefix in assets:
        return prefix
    for asset_code, info in assets.items():
        if info.get("altname") == prefix:
            return asset_code
    return prefix


def _ticker(base: str, assets: dict, pair_info: dict | None) -> str:
    if pair_info and pair_info.get("wsname"):
        name = pair_info["wsname"].split("/")[0]
    else:
        name = (assets.get(base) or {}).get("altname") or base
    return RENAMES.get(name, name)
