"""pump.fun — recently graduated tokens, via Solana Tracker's Data API.

**Not keyless, and that was checked before this was written** — see
``scripts/probe_pumpfun_migrations.py`` for what was tried first (pump.fun's own frontend API,
Cloudflare-blocked from this environment; Solana Tracker without a key, 401). Solana Tracker's
free tier is 2,500 requests/month, no credit card, and ``GET /tokens/multi/graduated`` is a
purpose-built endpoint for exactly this signal — a nightly poll costs ~30 calls a month against
that cap. Sign up at solanatracker.io/account and put the key in ``.env`` as
``SOLANATRACKER_API_KEY`` (see ``.env.example``).

"Graduated" means a token survived its bonding curve and got a real AMM pool — the one pump.fun
signal worth tracking, per the Phase 5 alt-signal design (everything else on the platform is
thousands of new, mostly-rug launches a day). This is deliberately **not** wired into `review`
— a brand-new graduating token confirms nothing a holding or a roster thesis already said, so
it stays store-only, visible via ``fetch-altsignal --report``.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

from core.altsignal import AltSignalReading
from core.env import load_env

from oracle import http
from oracle.http import FetchError

BASE = "https://data.solanatracker.io/tokens/multi/graduated"

SOURCE = "pumpfun"


_UNSET = object()


def load_api_key() -> str | None:
    load_env()
    return os.environ.get("SOLANATRACKER_API_KEY")


def parse_graduated(payload) -> list[AltSignalReading]:
    """One reading per token that actually has a pool. A token the endpoint lists but with no
    pool yet has not graduated — skipped rather than recorded with nulls, same reasoning as
    every other adapter in this package."""
    readings: list[AltSignalReading] = []
    for item in payload or []:
        token = item.get("token") or {}
        mint = token.get("mint")
        pools = item.get("pools") or []
        if not mint or not pools:
            continue
        pool = pools[0]
        created_at = pool.get("createdAt")
        if created_at is None:
            continue
        readings.append(
            AltSignalReading(
                source=SOURCE,
                kind="graduation",
                key=mint,
                value={
                    "symbol": token.get("symbol"),
                    "name": token.get("name"),
                    "market": pool.get("market"),
                    "liquidity_usd": (pool.get("liquidity") or {}).get("usd"),
                    "market_cap_usd": (pool.get("marketCap") or {}).get("usd"),
                },
                observed_at=datetime.fromtimestamp(created_at / 1000, UTC),
            )
        )
    return readings


def fetch(*, get_json=http.get_json, api_key=_UNSET) -> list[AltSignalReading]:
    """Refuses rather than calling the network with no key — a 401 from a source with no
    per-key auth path elsewhere in this codebase is worth a clear message, not a stack trace
    from deep inside a retry loop.

    ``api_key`` defaults to a sentinel rather than ``None`` so the two calling shapes stay
    distinct: `fetch-altsignal` omits it and gets whatever ``.env`` has (possibly nothing,
    which raises below); a test passes ``api_key=None`` to assert that exact refusal without
    depending on the environment it happens to run in.
    """
    key = load_api_key() if api_key is _UNSET else api_key
    if not key:
        raise FetchError(
            "SOLANATRACKER_API_KEY is not set — sign up free at solanatracker.io/account "
            "and put the key in .env (see .env.example)"
        )
    return parse_graduated(get_json(BASE, headers={"x-api-key": key}))
