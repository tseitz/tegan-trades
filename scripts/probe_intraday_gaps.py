"""Does a market closure masquerade as a fair value gap on an equity hourly series?

`core.imbalance.fair_value_gaps` reads three *consecutive* bars positionally. It has no notion
of elapsed time, so a hole in the series is invisible to it: bar N at Friday's close and bar
N+1 at Monday's open look exactly like two adjacent hours. Whatever repricing happened while
the market was shut is attributed to a single candle, that candle's body is large enough to
clear the displacement threshold, and the void behind it is labelled an imbalance.

That is not a distinction without a difference. A displacement FVG is cut by aggressive
one-sided flow — someone lifting every offer — and the thesis for trading it is that unfilled
orders sit in the void. In a closure gap nothing traded at all; the void is empty because the
venue was shut. Gap-fill is a real equity phenomenon, so these are not noise — but they are a
*different* phenomenon with different statistics, and right now both arrive under one label.

MEASURED 2026-08-04 — AAPL, NVDA, META, HOOD, SBSW, FXI; 180 calendar days; Alpaca SIP:

                                     bars     FVGs   spanning a closure
    extended feed, as served        11,677     496      49  (10%)
    filtered to regular session      5,904     230     128  (56%)

**Filtering to the regular session more than doubles the artifact rate.** The extended feed
runs 08:00-23:00 UTC and its thin pre/post-market bars *bridge* the overnight move: price
walks there continuously, so no three-candle void forms. Cutting those bars out leaves a
17-hour jump from 20:00 to 13:00, and the first session bar absorbs the entire overnight
repricing into one large body — a textbook displacement candle that displaced nothing.

So: **take the equity hourly feed as served and do not filter it to the session.** The
intuition that pre-market bars are junk to be trimmed is exactly backwards for this purpose.
They are worth little as structure and a great deal as connective tissue.

The residual 10% is not evenly spread — it is concentrated in names with no meaningful US
pre-market: SBSW 22/80 and FXI 19/68, against 1-3 of ~90 for AAPL, NVDA, META and HOOD. The
bridge only exists where someone is trading, which is the same thinness the participation gate
already judges. A discontinuity check on the three candles behind a gap would close the rest;
this probe exists to say whether that is worth building, and on these numbers it is a second
order fix behind assembling the series correctly.

Run: `uv run python scripts/probe_intraday_gaps.py` (needs Alpaca credentials in `.env`).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from core import imbalance
from execution.alpaca_broker import DATA_URL, AlpacaBroker
from execution.config import alpaca_credentials
from oracle.intraday import H1, IntradayBar, IntradaySeries

SYMBOLS = ["AAPL", "NVDA", "META", "HOOD", "SBSW", "FXI"]
LOOKBACK_DAYS = 180

# The regular US cash session in UTC. 13:00 covers the 13:30 summer open and 14:00 the winter
# one; 20:00 carries the closing auction, which is why it is included despite being past the
# summer close. This window is what the probe argues *against* filtering to.
SESSION_FIRST_HOUR = 13
SESSION_LAST_HOUR = 20


def fetch_hourly(broker: AlpacaBroker, symbol: str, start: str) -> list[dict]:
    """Every hourly bar since ``start``, following pagination.

    Without the page loop this returns ~189 bars — about twelve sessions — which is few enough
    to draw a confident conclusion from and quiet enough about it to be believed.
    """
    rows: list[dict] = []
    token = None
    while True:
        params = {"symbols": symbol, "timeframe": "1Hour", "start": start,
                  "feed": "sip", "limit": 10_000}
        if token:
            params["page_token"] = token
        payload = broker._transport("GET", "/v2/stocks/bars", params=params, base=DATA_URL)
        rows.extend((payload.get("bars") or {}).get(symbol) or [])
        token = payload.get("next_page_token")
        if not token:
            return rows


def to_series(rows, symbol: str) -> IntradaySeries:
    return IntradaySeries(
        symbol=symbol, source="alpaca", interval=H1,
        bars=tuple(
            IntradayBar(
                date=datetime.fromisoformat(r["t"].replace("Z", "+00:00")),
                open=r["o"], high=r["h"], low=r["l"], close=r["c"], volume=float(r["v"]),
            )
            for r in rows
        ),
    )


def gaps_spanning_a_closure(series: IntradaySeries) -> tuple[int, int]:
    """(total gaps, how many were cut across a hole in the series)."""
    gaps = imbalance.fair_value_gaps(series.bars)
    position = {bar.date: i for i, bar in enumerate(series.bars)}
    spanning = 0
    for gap in gaps:
        end = position[gap.date]                    # a gap is dated at its third candle
        window = series.bars[end - 2:end + 1]
        if any(b.date - a.date > timedelta(hours=1) for a, b in pairwise(window)):
            spanning += 1
    return len(gaps), spanning


def main() -> int:
    broker = AlpacaBroker(alpaca_credentials(), network="paper")
    start = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()

    totals = {"extended": [0, 0, 0], "session": [0, 0, 0]}
    print(f"{'symbol':7} {'assembly':>10} {'bars':>6} {'FVGs':>5} {'spanning':>9}")
    for symbol in SYMBOLS:
        rows = fetch_hourly(broker, symbol, start)
        if not rows:
            print(f"{symbol:7} no data returned")
            continue
        session_rows = [
            r for r in rows
            if SESSION_FIRST_HOUR <= int(r["t"][11:13]) <= SESSION_LAST_HOUR
        ]
        for label, subset in (("extended", rows), ("session", session_rows)):
            series = to_series(subset, symbol)
            total, spanning = gaps_spanning_a_closure(series)
            totals[label] = [
                a + b for a, b in zip(totals[label], (len(series.bars), total, spanning), strict=True)
            ]
            print(f"{symbol:7} {label:>10} {len(series.bars):6} {total:5} {spanning:9}")

    print()
    for label in ("extended", "session"):
        bars, total, spanning = totals[label]
        share = f"{spanning / total:.0%}" if total else "n/a"
        print(f"TOTAL {label:9} bars={bars:6} FVGs={total:4} spanning={spanning:4} ({share})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
