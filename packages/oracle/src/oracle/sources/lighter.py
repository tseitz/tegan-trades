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
from core.interest import OpenInterest

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


def parse_open_interest(payload, *, observed_at: datetime) -> list[OpenInterest]:
    """Parse ``/orderBookDetails`` — open interest, mark and 24h volume in one response.

    Perp markets only: the endpoint also lists spot pairs, which carry no open interest to
    speak of. ``daily_quote_token_volume`` is already USD, unlike ``open_interest``, which is
    base units of the underlying and must be multiplied by ``mark_price`` first.
    """
    if not payload:
        return []
    readings: list[OpenInterest] = []
    for row in payload.get("order_book_details") or []:
        if not row or row.get("market_type") != "perp":
            continue
        symbol = row.get("symbol")
        raw_oi, raw_mark, raw_vol = (
            row.get("open_interest"),
            row.get("mark_price"),
            row.get("daily_quote_token_volume"),
        )
        if not symbol or raw_oi is None or raw_mark is None or raw_vol is None:
            continue
        try:
            notional = float(raw_oi) * float(raw_mark)
            volume = float(raw_vol)
        except (TypeError, ValueError):
            continue
        readings.append(
            OpenInterest(
                venue=VENUE,
                symbol=symbol,
                notional=notional,
                volume_24h=volume,
                observed_at=observed_at,
            )
        )
    return readings


def fetch_open_interest(
    *, get_json=http.get_json, observed_at: datetime | None = None
) -> list[OpenInterest]:
    """The one call funding doesn't already make — ``/orderBookDetails`` is a separate
    endpoint from ``/funding-rates``, so this costs one extra request per sweep."""
    at = observed_at or datetime.now(UTC)
    return parse_open_interest(get_json(f"{BASE}/orderBookDetails"), observed_at=at)
