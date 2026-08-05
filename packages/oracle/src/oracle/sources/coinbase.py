"""Coinbase Exchange — the crypto primary.

Free, keyless, and carries the full corpus span (2024-07 onward), which is why it beats
CoinGecko (365-day cap on the free tier) and Binance (HTTP 451 from US IPs).

Three traps encoded here:
- Rows are ``[time, low, high, open, close, volume]``. That column order is unique among
  our sources; transposing open/close would produce plausible-looking wrong returns.
- 300 candles per request, hard. A naive single call for a 2-year span returns a
  truncated window with no error, so ``fetch_daily`` tiles the range.
- The cap counts *candles*, not days. At hourly granularity one request reaches 12.5 days,
  so ``fetch_intraday`` strides by the granularity rather than by a day.

The granularity ladder is 60, 300, 900, 3600, 21600, 86400 — note the jump from 1h to 6h.
There is no native 12-hour candle here or at any venue we route to, which is why H12 is
resampled from H1 rather than fetched.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from oracle import http
from oracle.intraday import IntradayBar
from oracle.series import Bar

BASE = "https://api.exchange.coinbase.com"
GRANULARITY_HOURLY = 3600
GRANULARITY_DAILY = 86400
MAX_CANDLES = 300

SOURCE = "coinbase"

# The column order trap, named once so both parsers share one definition of it.
COL_TIME, COL_LOW, COL_HIGH, COL_OPEN, COL_CLOSE, COL_VOLUME = range(6)


def symbol_for(asset: str) -> str:
    return f"{asset.strip().upper()}-USD"


def parse_candles(payload) -> list[Bar]:
    if not payload:
        return []
    bars = []
    for row in payload:
        if not row or len(row) < 5:
            continue
        bars.append(
            Bar(
                date=datetime.fromtimestamp(row[COL_TIME], UTC).date(),
                open=float(row[COL_OPEN]),
                high=float(row[COL_HIGH]),
                low=float(row[COL_LOW]),
                close=float(row[COL_CLOSE]),
            )
        )
    return bars


def parse_intraday_candles(payload) -> list[IntradayBar]:
    """Same rows as ``parse_candles``, keeping the two fields the daily bar has nowhere to put.

    The stamp stays a full UTC datetime — truncating it to a date, as the daily parse does,
    would collapse all 24 of an hourly day's candles onto one bar. Volume is column 5, which
    has always been arriving and has always been discarded.
    """
    if not payload:
        return []
    bars = []
    for row in payload:
        if not row or len(row) < 5:
            continue
        # A short row means the source omitted volume, not that nothing traded — see
        # ``IntradayBar.volume`` for why those must stay distinguishable.
        volume = float(row[COL_VOLUME]) if len(row) > COL_VOLUME else None
        bars.append(
            IntradayBar(
                date=datetime.fromtimestamp(row[COL_TIME], UTC),
                open=float(row[COL_OPEN]),
                high=float(row[COL_HIGH]),
                low=float(row[COL_LOW]),
                close=float(row[COL_CLOSE]),
                volume=volume,
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


def fetch_intraday(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    granularity: int = GRANULARITY_HOURLY,
    get_json=http.get_json,
) -> list[IntradayBar]:
    """Intraday bars over [start, end], tiled to respect the 300-candle cap.

    The stride follows ``granularity`` rather than being a fixed day, which is the whole
    difference from ``fetch_daily``: at 3600 one request covers 12.5 days and at 900 it covers
    a little over 3, so a day-shaped stride would ask for candles the cap silently drops.
    """
    step = timedelta(seconds=granularity)
    bars: list[IntradayBar] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + step * (MAX_CANDLES - 1), end)
        payload = get_json(
            f"{BASE}/products/{symbol}/candles",
            {
                "granularity": granularity,
                "start": cursor.isoformat(),
                "end": chunk_end.isoformat(),
            },
        )
        bars.extend(parse_intraday_candles(payload))
        cursor = chunk_end + step
    return bars
