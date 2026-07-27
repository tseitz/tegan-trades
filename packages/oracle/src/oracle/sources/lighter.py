"""Lighter funding.

**The quoted rate is per 8 hours, not per hour**, and this is the single most consequential
fact in the module. Lighter's ``/funding-rates`` endpoint is a *comparison* feed: it carries
its own rate alongside Binance's, Bybit's and Hyperliquid's, all normalized to one
convention. Reading it as hourly overstates Lighter's carry by 8x — enough to invert a
venue comparison, which it did once before this was checked.

The proof is in the feed itself. Hyperliquid's API reports its own hourly funding directly;
Lighter's view of the same symbols is exactly 8.000x that value, on every symbol sampled
(ETH, HYPE, ZEC, LINK, AVAX, DOGE all at precisely 8.000; BTC 8.236 on a rate small enough
for rounding to show). So the feed is 8-hourly and Lighter's own column shares the unit.

Rows for other venues are dropped here rather than stored. They are Lighter's opinion of a
competitor's rate, not an observation of it, and mixing a second-hand figure into a log
whose whole purpose is measuring real carry would quietly corrupt the comparison.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.funding import FundingRate
from oracle import http

BASE = "https://mainnet.zklighter.elliot.ai/api/v1"
VENUE = "lighter"
INTERVAL_HOURS = 8.0


def parse_funding_rates(payload, *, observed_at: datetime) -> list[FundingRate]:
    if not payload:
        return []
    rates: list[FundingRate] = []
    for row in payload.get("funding_rates") or []:
        if not row or row.get("exchange") != VENUE:
            continue
        symbol = row.get("symbol")
        raw = row.get("rate")
        if not symbol or raw is None:
            continue
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            continue
        rates.append(
            FundingRate(
                venue=VENUE,
                symbol=symbol,
                rate=rate,
                interval_hours=INTERVAL_HOURS,
                observed_at=observed_at,
            )
        )
    return rates


def fetch(*, get_json=http.get_json, observed_at: datetime | None = None) -> list[FundingRate]:
    at = observed_at or datetime.now(UTC)
    return parse_funding_rates(get_json(f"{BASE}/funding-rates"), observed_at=at)
