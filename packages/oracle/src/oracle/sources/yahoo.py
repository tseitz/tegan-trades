"""Yahoo Finance chart API — equities, ETFs, indices, futures, FX.

Covers everything Coinbase can't: the corpus's unpriced tail is stocks (TSLA, HOOD, MSTR,
NVDA...), ETFs (URA, XLE, TLT), indices (^GSPC, ^NDX), futures (GC=F, CL=F, YM=F) and FX
(EURUSD=X, DX-Y.NYB).

``instrument_type`` is the load-bearing extra: routing uses it to *validate* that a bare
ticker off the corpus really is a tradeable instrument before trusting a price, rather
than guessing from the symbol string.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from oracle import http
from oracle.series import Bar

BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

SOURCE = "yahoo"


def _result(payload):
    if not payload:
        return None
    results = (payload.get("chart") or {}).get("result")
    # A bad/delisted ticker comes back as `result: null` with an `error` object — a normal
    # outcome when probing the corpus's long tail, so it must not raise.
    return results[0] if results else None


def instrument_type(payload) -> str | None:
    """EQUITY / ETF / INDEX / FUTURE / CURRENCY, or None when the symbol is unknown."""
    result = _result(payload)
    if result is None:
        return None
    return (result.get("meta") or {}).get("instrumentType")


def parse_chart(payload) -> list[Bar]:
    result = _result(payload)
    if result is None:
        return []
    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs = quotes.get("open") or [], quotes.get("high") or []
    lows, closes = quotes.get("low") or [], quotes.get("close") or []

    bars = []
    for i, ts in enumerate(timestamps):
        try:
            o, h, low, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            continue
        # Yahoo pads market holidays with null OHLC rather than omitting the timestamp.
        if o is None or h is None or low is None or c is None:
            continue
        bars.append(
            Bar(
                date=datetime.fromtimestamp(ts, UTC).date(),
                open=float(o),
                high=float(h),
                low=float(low),
                close=float(c),
            )
        )
    return bars


def fetch_daily(symbol: str, start: date, end: date, *, get_json=http.get_json) -> list[Bar]:
    """Daily bars over [start, end]. Yahoo serves multi-year spans in one request."""
    return parse_chart(_raw(symbol, start, end, get_json=get_json))


def _raw(symbol: str, start: date, end: date, *, get_json=http.get_json):
    return get_json(
        f"{BASE}/{symbol}",
        {
            "period1": int(datetime.combine(start, datetime.min.time(), UTC).timestamp()),
            "period2": int(datetime.combine(end, datetime.min.time(), UTC).timestamp()) + 86400,
            "interval": "1d",
        },
    )


def probe(symbol: str, *, get_json=http.get_json) -> str | None:
    """Cheap symbol-validity check used by routing — returns the instrument type or None."""
    today = datetime.now(UTC).date()
    return instrument_type(_raw(symbol, today.replace(year=today.year - 1), today, get_json=get_json))
