"""Coinbase Exchange — the crypto primary.

Free, keyless, and carries the full corpus span (2024-07 onward), which is why it beats
CoinGecko (365-day cap on the free tier) and Binance (HTTP 451 from US IPs).

Two traps encoded here:
- Rows are ``[time, low, high, open, close, volume]``. That column order is unique among
  our sources; transposing open/close would produce plausible-looking wrong returns.
- 300 candles per request, hard. A naive single call for a 2-year span returns a
  truncated window with no error, so ``fetch_daily`` tiles the range.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from oracle import http
from oracle.series import Bar

BASE = "https://api.exchange.coinbase.com"
GRANULARITY_DAILY = 86400
MAX_CANDLES = 300

SOURCE = "coinbase"


def symbol_for(asset: str) -> str:
    return f"{asset.strip().upper()}-USD"


def parse_candles(payload) -> list[Bar]:
    if not payload:
        return []
    bars = []
    for row in payload:
        if not row or len(row) < 5:
            continue
        ts, low, high, open_, close = row[0], row[1], row[2], row[3], row[4]
        bars.append(
            Bar(
                date=datetime.fromtimestamp(ts, UTC).date(),
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
            )
        )
    return bars


def fetch_daily(symbol: str, start: date, end: date, *, get_json=http.get_json) -> list[Bar]:
    """Daily bars over [start, end], tiled to respect the 300-candle cap."""
    bars: list[Bar] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=MAX_CANDLES - 1), end)
        payload = get_json(
            f"{BASE}/products/{symbol}/candles",
            {
                "granularity": GRANULARITY_DAILY,
                "start": cursor.isoformat(),
                "end": chunk_end.isoformat(),
            },
        )
        bars.extend(parse_candles(payload))
        cursor = chunk_end + timedelta(days=1)
    return bars
