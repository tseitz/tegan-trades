"""GeckoTerminal — on-chain DEX prices, for tokens no exchange lists.

Keyed by contract address, not ticker: the symbol is ``"<network>/<address>"``
(``solana/9cRC…pump``). An address names exactly one token, so the symbol collisions
``cfg/oracle_map.yaml`` exists to catch cannot happen here.

The pool is chosen per fetch rather than pinned in the map — the deepest by liquidity, since a
thin pool's candles are whatever one trade made them, and pinning one would break silently the
day its liquidity moves elsewhere. ``token=<address>`` asks for that token's side of the pair; the
default is the pool's base token, which is the quote half of any pool listed the other way round.

Liquidity is self-reported by whoever made the pool, so a pool paired against a token its creator
priced can fake its way to "deepest". Nothing here catches that; the holding's own mark does —
``review`` flags a price far from it as mismatched and networth leaves the row out.

Free, no key, ~30 requests a minute; two requests per symbol.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, time, timedelta

from oracle import http
from oracle.series import Bar

BASE = "https://api.geckoterminal.com/api/v2"
MAX_CANDLES = 1000

SOURCE = "geckoterminal"

_NETWORK = re.compile(r"[a-z0-9-]+")
_ADDRESS = re.compile(r"[A-Za-z0-9]{20,64}")


def split_symbol(symbol: str) -> tuple[str, str]:
    network, _, address = symbol.partition("/")
    if not _NETWORK.fullmatch(network) or not _ADDRESS.fullmatch(address):
        raise ValueError(f"geckoterminal symbol must be '<network>/<address>', got {symbol!r}")
    return network, address


def deepest_pool(payload) -> str | None:
    pools = (payload or {}).get("data") or []
    if not pools:
        return None
    best = max(pools, key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0))
    address = best["attributes"]["address"]
    if not _ADDRESS.fullmatch(address):
        raise ValueError(f"geckoterminal returned a malformed pool address: {address!r}")
    return address


def parse_ohlcv(payload) -> list[Bar]:
    rows = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    bars = [
        Bar(
            date=datetime.fromtimestamp(ts, UTC).date(),
            open=float(open_),
            high=float(high),
            low=float(low),
            close=float(close),
        )
        for ts, open_, high, low, close, *_ in rows
        if all(math.isfinite(float(v)) and float(v) > 0 for v in (open_, high, low, close))
    ]
    return sorted(bars, key=lambda b: b.date)


def fetch_daily(symbol: str, start: date, end: date, *, get_json=http.get_json) -> list[Bar]:
    network, address = split_symbol(symbol)
    pool = deepest_pool(get_json(f"{BASE}/networks/{network}/tokens/{address}/pools"))
    if pool is None:
        return []
    before = datetime.combine(end + timedelta(days=1), time.min, UTC)
    payload = get_json(
        f"{BASE}/networks/{network}/pools/{pool}/ohlcv/day",
        {
            "aggregate": 1,
            "limit": MAX_CANDLES,
            "currency": "usd",
            "token": address,
            "before_timestamp": int(before.timestamp()),
        },
    )
    return [b for b in parse_ohlcv(payload) if start <= b.date <= end]
