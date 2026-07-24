"""Kraken — crypto fallback only, for coins Coinbase doesn't list.

Rescues ~70 theses across 19 assets (XMR, TRX, MEME, GRASS...). Deliberately *not* the
primary, for one reason:

> Kraken's OHLC endpoint hard-caps at 720 candles regardless of ``since``. On a daily
> interval that is a ~2-year window that **slides forward every day** — it already starts
> after the corpus's first thesis. Anything it can still reach should be cached now,
> because that history becomes permanently unreachable as the window advances.

Two shape traps: rows are ``[time, open, high, low, close, vwap, volume, count]`` with the
prices as *strings*, and the result is keyed by Kraken's own normalized pair name
(``XMRUSD`` comes back as ``XXMRZUSD``), not the pair you asked for.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from oracle import http
from oracle.series import Bar

BASE = "https://api.kraken.com/0/public/OHLC"
INTERVAL_DAILY = 1440
MAX_CANDLES = 720

SOURCE = "kraken"


def symbol_for(asset: str) -> str:
    return f"{asset.strip().upper()}USD"


def parse_ohlc(payload) -> list[Bar]:
    if not payload or payload.get("error"):
        return []
    result = payload.get("result") or {}
    # Take whichever key isn't the cursor — looking up the requested pair name misses,
    # because Kraken answers under its own normalized alias.
    keys = [k for k in result if k != "last"]
    if not keys:
        return []
    bars = []
    for row in result[keys[0]]:
        if len(row) < 5:
            continue
        ts, open_, high, low, close = row[0], row[1], row[2], row[3], row[4]
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
    """Daily bars from ``start``, subject to the 720-candle ceiling.

    ``end`` is accepted for interface symmetry with the other sources; Kraken always
    returns through the present, and PriceSeries/callers slice as needed.
    """
    payload = get_json(
        BASE,
        {
            "pair": symbol,
            "interval": INTERVAL_DAILY,
            "since": int(datetime.combine(start, datetime.min.time(), UTC).timestamp()),
        },
    )
    return [b for b in parse_ohlc(payload) if b.date <= end]
