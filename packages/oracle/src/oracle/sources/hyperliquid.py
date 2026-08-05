"""Hyperliquid funding — the validator-run core book plus every HIP-3 builder DEX.

Two things about this venue that the shape of the code has to respect:

- **Equities, indices and commodities do not live on the core book.** They are HIP-3
  markets deployed by builders (TradeXYZ carries almost all of them), reachable only by
  passing ``dex`` to the same ``/info`` endpoint. Reading only the core book silently
  returns crypto-only, which looks like a working fetch.
- **HIP-3 symbols are namespaced** — the universe returns ``xyz:NVDA``, not ``NVDA``. Both
  halves are kept: ``dex`` distinguishes two builders listing the same ticker with
  different oracles, which is a real hazard and not a formatting detail.

Funding interval: **1 hour**. Verified empirically rather than taken from prose — Lighter
publishes its own view of Hyperliquid's rate, and its value is exactly 8.000x this one
against a documented 8-hour normalization, on every symbol checked.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.funding import FundingRate

from oracle import http

BASE = "https://api.hyperliquid.xyz/info"
VENUE = "hyperliquid"
INTERVAL_HOURS = 1.0


def parse_asset_ctxs(payload, *, dex: str = "", observed_at: datetime) -> list[FundingRate]:
    """Parse a ``metaAndAssetCtxs`` reply into funding rates.

    The reply is a two-element array — ``[meta, contexts]`` — positionally zipped. A length
    mismatch means the venue changed shape, so the pair is truncated to the shorter side
    rather than raising: a partial sweep is worth more than none, and the count is reported.
    """
    if not payload or len(payload) < 2:
        return []
    meta, ctxs = payload[0], payload[1]
    universe = (meta or {}).get("universe") or []

    rates: list[FundingRate] = []
    # strict=False to match the docstring above: a partial sweep is worth more than none, and
    # a short ``ctxs`` shows up as a lower reported count rather than as an exception.
    for asset, ctx in zip(universe, ctxs or [], strict=False):
        name = (asset or {}).get("name")
        if not name or (asset or {}).get("isDelisted"):
            continue
        raw = (ctx or {}).get("funding")
        if raw is None:
            continue
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            continue
        # HIP-3 universes namespace their tickers as "<dex>:<SYMBOL>".
        symbol = name.split(":", 1)[-1] if ":" in name else name
        rates.append(
            FundingRate(
                venue=f"{VENUE}:{dex}" if dex else VENUE,
                symbol=symbol,
                rate=rate,
                interval_hours=INTERVAL_HOURS,
                observed_at=observed_at,
            )
        )
    return rates


def parse_dexs(payload) -> list[str]:
    """Names of the HIP-3 builder DEXs. The core book is the ``null`` first element."""
    if not payload:
        return []
    return [x["name"] for x in payload if x and x.get("name")]


def parse_funding_history(payload, *, venue: str) -> list[FundingRate]:
    """Parse a ``fundingHistory`` reply. Each row is one hourly settlement that happened.

    Unlike the snapshot, these are realised payments rather than a current estimate, which
    is what makes a backfill worth more than the same number of nights of live logging.
    """
    if not payload:
        return []
    rates: list[FundingRate] = []
    for row in payload:
        if not row:
            continue
        coin, raw, ts = row.get("coin"), row.get("fundingRate"), row.get("time")
        if not coin or raw is None or ts is None:
            continue
        try:
            rate, when = float(raw), datetime.fromtimestamp(int(ts) / 1000, UTC)
        except (TypeError, ValueError, OSError):
            continue
        rates.append(
            FundingRate(
                venue=venue,
                symbol=coin.split(":", 1)[-1],
                rate=rate,
                interval_hours=INTERVAL_HOURS,
                observed_at=when,
            )
        )
    return rates


def fetch_history(
    coin: str, start_ms: int, *, venue: str = VENUE, post_json=http.post_json
) -> list[FundingRate]:
    """Realised hourly funding for one market since ``start_ms``.

    ``coin`` is the venue-native name including any HIP-3 namespace (``xyz:NVDA``); ``venue``
    is what gets recorded, so the caller keeps them consistent.
    """
    return parse_funding_history(
        post_json(BASE, {"type": "fundingHistory", "coin": coin, "startTime": start_ms}),
        venue=venue,
    )


def fetch(*, post_json=http.post_json, observed_at: datetime | None = None) -> list[FundingRate]:
    """Funding across the core book and every HIP-3 DEX that carries markets."""
    at = observed_at or datetime.now(UTC)
    rates = parse_asset_ctxs(
        post_json(BASE, {"type": "metaAndAssetCtxs"}), observed_at=at
    )
    for dex in parse_dexs(post_json(BASE, {"type": "perpDexs"})):
        rates.extend(
            parse_asset_ctxs(
                post_json(BASE, {"type": "metaAndAssetCtxs", "dex": dex}),
                dex=dex,
                observed_at=at,
            )
        )
    return rates
