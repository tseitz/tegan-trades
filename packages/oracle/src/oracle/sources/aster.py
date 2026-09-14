"""Aster funding.

**The interval varies per symbol and must be looked up, never assumed.** Across 681 symbols
Aster runs four different schedules — 355 hourly, 253 eight-hourly, 70 four-hourly, 3
two-hourly — so there is no venue-wide constant to hard-code. Gold (``XAUUSDT``) settles
every 4 hours while ``BTCUSDT`` settles every 8; treating them alike is a 2x error on one of
them.

That mapping lives at ``/fapi/v1/fundingInfo`` and the rates at ``/fapi/v1/premiumIndex``,
so a complete reading needs both calls joined on symbol. Every one of the 518 live symbols
was present in ``fundingInfo`` when this was written, but ``DEFAULT_INTERVAL_HOURS`` still
backstops a symbol that appears in one feed and not the other — dropping the rate would lose
data, and guessing silently is what this module exists to prevent, so the default is applied
explicitly and the caller is told how often it fired.

The API is Binance-compatible, which is why ``lastFundingRate`` is the rate *per interval*
rather than an annualized or daily figure.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from core.funding import FundingRate
from core.interest import OpenInterest

from oracle import http
from oracle.http import FetchError

BASE = "https://fapi.asterdex.com/fapi/v1"
VENUE = "aster"
DEFAULT_INTERVAL_HOURS = 8.0

# There is no bulk open-interest endpoint -- one request per listed symbol (589 measured
# 2026-09-13). Eight-way concurrency finishes in ~30s and stayed under the venue's rate limit
# across repeated runs (scripts/probe_perp_venue_fundamentals.py); higher trades a few seconds
# for 429s that would look like missing markets in the coverage figure below.
OPEN_INTEREST_WORKERS = 8


def parse_funding_info(payload) -> dict[str, float]:
    """symbol -> settlement interval in hours."""
    if not payload:
        return {}
    out: dict[str, float] = {}
    for row in payload:
        if not row:
            continue
        symbol = row.get("symbol")
        hours = row.get("fundingIntervalHours")
        if not symbol or hours is None:
            continue
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            continue
        if hours > 0:
            out[symbol] = hours
    return out


def parse_premium_index(
    payload,
    intervals: dict[str, float],
    *,
    observed_at: datetime,
) -> tuple[list[FundingRate], int]:
    """Join rates to intervals. Returns the rates and a count of interval-lookup misses."""
    if not payload:
        return [], 0
    rates: list[FundingRate] = []
    defaulted = 0
    for row in payload:
        if not row:
            continue
        symbol = row.get("symbol")
        raw = row.get("lastFundingRate")
        if not symbol or raw is None:
            continue
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            continue
        hours = intervals.get(symbol)
        if hours is None:
            hours = DEFAULT_INTERVAL_HOURS
            defaulted += 1
        rates.append(
            FundingRate(
                venue=VENUE,
                symbol=symbol,
                rate=rate,
                interval_hours=hours,
                observed_at=observed_at,
            )
        )
    return rates, defaulted


def parse_funding_history(payload, interval_hours: float) -> list[FundingRate]:
    """Parse ``/fapi/v1/fundingRate`` — realised settlements, not a current estimate."""
    if not payload:
        return []
    rates: list[FundingRate] = []
    for row in payload:
        if not row:
            continue
        symbol, raw, ts = row.get("symbol"), row.get("fundingRate"), row.get("fundingTime")
        if not symbol or raw is None or ts is None:
            continue
        try:
            rate, when = float(raw), datetime.fromtimestamp(int(ts) / 1000, UTC)
        except (TypeError, ValueError, OSError):
            continue
        rates.append(
            FundingRate(
                venue=VENUE,
                symbol=symbol,
                rate=rate,
                interval_hours=interval_hours,
                observed_at=when,
            )
        )
    return rates


def fetch_history(
    symbol: str, *, limit: int = 1000, get_json=http.get_json, intervals: dict[str, float] | None = None
) -> list[FundingRate]:
    """Realised funding for one symbol. ``intervals`` is hoisted by the caller so a
    multi-symbol backfill pays for the ``fundingInfo`` lookup once rather than per symbol."""
    if intervals is None:
        intervals = parse_funding_info(get_json(f"{BASE}/fundingInfo"))
    hours = intervals.get(symbol, DEFAULT_INTERVAL_HOURS)
    payload = get_json(f"{BASE}/fundingRate", {"symbol": symbol, "limit": limit})
    return parse_funding_history(payload, hours)


def fetch(
    *, get_json=http.get_json, observed_at: datetime | None = None
) -> tuple[list[FundingRate], int]:
    at = observed_at or datetime.now(UTC)
    intervals = parse_funding_info(get_json(f"{BASE}/fundingInfo"))
    return parse_premium_index(get_json(f"{BASE}/premiumIndex"), intervals, observed_at=at)


def fetch_open_interest(
    *,
    get_json=http.get_json,
    observed_at: datetime | None = None,
    workers: int = OPEN_INTEREST_WORKERS,
) -> tuple[list[OpenInterest], float]:
    """Open interest per symbol, plus the share of 24h volume it actually resolved.

    Three reads: ``/ticker/24hr`` for volume, ``/premiumIndex`` for marks (open interest is
    base units of the underlying and is meaningless untranslated), then one ``/openInterest``
    call per symbol -- there is no bulk endpoint. A refusing symbol drops out of the rows
    rather than being counted as zero, so the coverage figure reflects what was actually
    resolved rather than a partial sum reported as a total.
    """
    at = observed_at or datetime.now(UTC)
    tickers = get_json(f"{BASE}/ticker/24hr") or []
    marks = {
        row.get("symbol"): row.get("markPrice")
        for row in (get_json(f"{BASE}/premiumIndex") or [])
        if row and row.get("symbol")
    }
    volume_all = 0.0
    for row in tickers:
        try:
            volume_all += float(row.get("quoteVolume") or 0.0)
        except (TypeError, ValueError):
            continue

    def one(row) -> OpenInterest | None:
        symbol = row.get("symbol") or ""
        if not symbol or symbol not in marks:
            return None
        # `requests` percent-encodes `params` values itself -- pre-encoding a non-ASCII
        # symbol like `龙虾USDT` here would double-encode it and silently 400.
        try:
            payload = get_json(f"{BASE}/openInterest", {"symbol": symbol})
        except FetchError:
            return None
        if not payload or payload.get("openInterest") is None:
            return None
        try:
            notional = float(payload["openInterest"]) * float(marks[symbol])
            volume = float(row.get("quoteVolume") or 0.0)
        except (TypeError, ValueError):
            return None
        return OpenInterest(
            venue=VENUE, symbol=symbol, notional=notional, volume_24h=volume, observed_at=at
        )

    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(one, tickers))

    readings = [r for r in results if r is not None]
    volume_resolved = sum(r.volume_24h for r in readings)
    coverage = volume_resolved / volume_all if volume_all else 0.0
    return readings, coverage
