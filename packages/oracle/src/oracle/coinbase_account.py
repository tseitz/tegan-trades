"""Read what a Coinbase account holds, with a key that can do nothing else.

Read-only twice over, plus a check. The key is created in the Coinbase Developer Platform
portal with **View** ticked and nothing else — the only thing that limits a leaked key.
``private`` refuses any path outside ``PRIVATE_PATHS`` before a token is signed. And unlike
Kraken, Coinbase reports a key's own permissions, so every read asks first and refuses a key
that could trade or transfer: that catches a key scoped wrong at creation, not a stolen one.
"""

from __future__ import annotations

import math
import os
import re
import secrets
import time
from dataclasses import dataclass

import jwt
import requests
from core.env import load_env
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from oracle import http, portfolios, wallet

SOURCE = portfolios.Source(name="Coinbase", command="coinbase-sync", domain="crypto")

HOST = "api.coinbase.com"

# Widening either of these is the one change that lets this module do more than read. A test
# pins both. `/accounts` is left out on purpose: it cannot see staked funds
# (`scripts/probe_coinbase_account.py`), so the breakdown is the only read worth making.
PRIVATE_PATHS = frozenset({"/api/v3/brokerage/key_permissions"})
PORTFOLIO_PATH = re.compile(
    r"^/api/v3/brokerage/portfolios/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

TOKEN_SECONDS = 120
ES256_CURVE = ec.SECP256R1

# Coinbase's spellings of assets this repo prices under another ticker.
RENAMES = {"CGLD": "CELO"}

FOREIGN_FIAT = frozenset({"EUR", "GBP", "CAD", "JPY", "CHF", "AUD", "SGD"})

LIQUID = "ACCOUNT_TYPE_WALLET"
STAKED = "ACCOUNT_TYPE_STAKED_FUNDS"
FIAT = "ACCOUNT_TYPE_FIAT"
CONSUMER = "PLATFORM_CONSUMER"

# Holdings the breakdown can list that have no row shape here yet. Any of them non-empty raises:
# their value is inside `total_balance`, so dropping them would be a silent undercount.
UNMAPPED_LISTS = (
    "equity_positions",
    "prediction_markets_positions",
    "perp_positions",
    "futures_positions",
)

# How far the rows may drift from Coinbase's own total before the read is refused. The probe
# reconciled to the cent; the slack is for rounding across a few dozen entries.
TOLERANCE_USD = 1.0
TOLERANCE_SHARE = 0.005


class CoinbaseError(Exception):
    """A Coinbase read that failed. Never answered with an empty book."""


AccountError = CoinbaseError


class MissingCredentials(CoinbaseError):
    """The key name or private key is not in the environment or ``.env``."""


def credentials() -> tuple[str, ec.EllipticCurvePrivateKey]:
    load_env()
    name = os.environ.get("COINBASE_API_KEY_NAME")
    pem = os.environ.get("COINBASE_API_PRIVATE_KEY")
    if not name or not pem:
        raise MissingCredentials(
            "COINBASE_API_KEY_NAME / COINBASE_API_PRIVATE_KEY are not set — make a View-only "
            "ECDSA key in the Coinbase Developer Platform and put both in .env "
            "(see .env.example)"
        )
    # `load_env` keeps a value on one line, so the PEM arrives with literal `\n`.
    pem = pem.replace("\\n", "\n")
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise CoinbaseError(
            "COINBASE_API_PRIVATE_KEY is not a readable PEM private key — re-copy it"
        ) from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ES256_CURVE
    ):
        raise CoinbaseError(
            "COINBASE_API_PRIVATE_KEY needs an ECDSA P-256 key — re-create it with "
            "ECDSA chosen, not the Ed25519 default"
        )
    return name, key


def token(path: str, name: str, key: ec.EllipticCurvePrivateKey, *, now=None) -> str:
    """The query string is never part of ``uri``; parameters travel separately."""
    now = int(time.time() if now is None else now)
    return jwt.encode(
        {
            "sub": name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + TOKEN_SECONDS,
            "uri": f"GET {HOST}{path}",
        },
        key,
        algorithm="ES256",
        headers={"kid": name, "nonce": secrets.token_hex()},
    )


def allowed(path: str) -> bool:
    return path in PRIVATE_PATHS or bool(PORTFOLIO_PATH.fullmatch(path))


def private(path: str, params: dict | None = None) -> dict:
    if not allowed(path):
        raise CoinbaseError(f"{path} is not a read this module is allowed to make")
    name, key = credentials()
    try:
        resp = requests.get(
            f"https://{HOST}{path}",
            params=params,
            timeout=http.TIMEOUT,
            # A redirect would carry the bearer token to wherever it points.
            allow_redirects=False,
            headers={
                "Authorization": f"Bearer {token(path, name, key)}",
                "User-Agent": http.USER_AGENT,
            },
        )
        if resp.status_code != 200:
            raise CoinbaseError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise CoinbaseError(f"{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CoinbaseError(f"{path}: unreadable answer {payload!r:.200}")
    return payload


def check_permissions(answer: dict) -> str:
    """The key's portfolio uuid, or raise. Fails closed: a missing ``can_*`` field, or one this
    code has never heard of, refuses just like a true one."""
    if answer.get("can_view") is not True:
        raise CoinbaseError("key_permissions: can_view is not true")
    for field in ("can_trade", "can_transfer"):
        if answer.get(field) is not False:
            raise CoinbaseError(
                f"key_permissions: {field}={answer.get(field)!r} — this key must be View "
                "only; re-create it with Trade and Transfer unticked"
            )
    for field, value in answer.items():
        if field.startswith("can_") and field != "can_view" and value is not False:
            raise CoinbaseError(f"key_permissions: unrecognised {field}={value!r}")
    uuid = answer.get("portfolio_uuid")
    if not isinstance(uuid, str) or not PORTFOLIO_PATH.fullmatch(
        f"/api/v3/brokerage/portfolios/{uuid}"
    ):
        raise CoinbaseError(f"key_permissions: unreadable portfolio_uuid {uuid!r}")
    return uuid


def permitted_portfolio() -> str:
    return check_permissions(private("/api/v3/brokerage/key_permissions"))


@dataclass(frozen=True, slots=True)
class Read:
    """The key's portfolio breakdown, as Coinbase returned it."""

    breakdown: dict


def read() -> Read:
    uuid = permitted_portfolio()
    breakdown = private(f"/api/v3/brokerage/portfolios/{uuid}").get("breakdown")
    if not isinstance(breakdown, dict):
        raise CoinbaseError("portfolio breakdown: no `breakdown` in the answer")
    return Read(breakdown=breakdown)


def rows_from(read_: Read, *, min_value: float = wallet.MIN_VALUE_USD):
    """``(rows, skipped, cash)`` — one row per ticker, staked entries folded into it, and the
    whole refused unless it adds up to Coinbase's own total."""
    breakdown = read_.breakdown
    for key in UNMAPPED_LISTS:
        if breakdown.get(key):
            raise CoinbaseError(
                f"{key}: {len(breakdown[key])} held — not mapped to a row yet, and dropping "
                "them would undercount the book"
            )
    total = _amount(
        (breakdown.get("portfolio_balances") or {}).get("total_balance"),
        "total_balance",
    )

    held: dict[str, dict] = {}
    cash = 0.0
    seen = 0.0
    for entry in breakdown.get("spot_positions") or []:
        asset = entry.get("asset")
        quantity = _amount(entry.get("total_balance_crypto"), f"{asset} quantity")
        value = _amount(entry.get("total_balance_fiat"), f"{asset} value")
        if not quantity and not value:
            continue
        kind, platform = entry.get("account_type"), entry.get("platform")
        if platform != CONSUMER or kind not in (LIQUID, STAKED, FIAT):
            raise CoinbaseError(
                f"{asset}: {kind} on {platform} is not mapped yet — add it to "
                "coinbase_account once you know what it is"
            )
        seen += value
        ticker = RENAMES.get(asset, asset)
        if (kind == FIAT and ticker != "USD") or ticker in FOREIGN_FIAT:
            raise CoinbaseError(
                f"{asset}: a non-USD balance has no place in this book yet"
            )
        if ticker == "USD" or ticker in wallet.STABLES:
            cash += value
            continue
        row = held.setdefault(
            ticker,
            {"shares": 0.0, "staked": 0.0, "value": 0.0, "cost": 0.0, "code": asset},
        )
        row["shares"] += quantity
        row["value"] += value
        if kind == STAKED:
            row["staked"] += quantity
        basis = entry.get("cost_basis")
        # One entry missing its basis poisons the blend, so the ticker gets no cost at all.
        if row["cost"] is not None and isinstance(basis, dict) and basis.get("value"):
            row["cost"] += _amount(basis, f"{asset} cost_basis")
        else:
            row["cost"] = None

    gap = abs(seen - total)
    if gap > max(TOLERANCE_USD, TOLERANCE_SHARE * total):
        raise CoinbaseError(
            f"positions add up to ${seen:,.2f} but Coinbase reports ${total:,.2f} — "
            "something it holds is not being read"
        )

    rows: list[portfolios.Row] = []
    skipped: list[portfolios.Skipped] = []
    for ticker, row in sorted(held.items()):
        if row["value"] < min_value:
            skipped.append(
                portfolios.Skipped(
                    ticker, f"${row['value']:,.2f}, under the value floor", "dust"
                )
            )
            continue
        shares = row["shares"]
        rows.append(
            portfolios.Row(
                ticker=ticker,
                shares=shares,
                cost=row["cost"] / shares
                if row["cost"] is not None and shares
                else None,
                domain="crypto",
                figi=row["code"],
                mark=row["value"] / shares if shares else None,
                staked=row["staked"] or None,
            )
        )
    return rows, tuple(skipped), cash


def _amount(raw, what: str) -> float:
    """Coinbase sends some money fields as floats and others as ``{value: str}``."""
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise CoinbaseError(f"{what}: unreadable amount {raw!r}") from exc
    # NaN and inf compare False against any bound, so they would pass reconciliation.
    if not math.isfinite(value):
        raise CoinbaseError(f"{what}: unreadable amount {raw!r}")
    return value
