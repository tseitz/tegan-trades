"""Series computed from other series, rather than fetched from anywhere.

`ETH/BTC` is 29 corpus rows and both its legs are already in `data/prices/`, so it is a
division and not a new data source (§6f). The same is true of any pair ratio the roster talks
about in terms of one asset priced in another.

**The hard part is the candle, not the arithmetic**, and getting it wrong would feed invented
structure to an engine that reads swings and order blocks off highs and lows.

A daily bar records four numbers at three kinds of moment: `open` and `close` happen at the
bar's boundaries, so both legs are quoted *simultaneously* and their ratio is a real price that
genuinely existed. `high` and `low` happen at unknown times that differ between the two legs —
ETH's high and BTC's high are not the same instant — so **the ratio's true high is not
computable from daily OHLC**. Three ways to fake it, and why this module refuses all of them:

* `n.high / d.high` is not even well-formed: it can come out *below* `n.low / d.low`, producing
  a bar whose high is under its low.
* `n.high / d.low` is a true upper bound, but a wildly loose one for correlated legs. ETH and
  BTC mostly rise and fall together, so when ETH prints its high BTC is usually near its own
  high too, and the bound assumes the opposite every single day. It would systematically
  inflate every range the structure layer measures — wider order blocks, noisier swings, a
  fatter ATR — and none of it would be real.
* Collapsing to closes (`o == h == l == c`) invents nothing but gives every bar zero height,
  and a zero-height order block is refused as `degenerate_zone`. Every candidate would die.

So the bars here are **body-only**: open and close are exact, and high/low are just the larger
and smaller of those two. Every number is a ratio that really occurred at a moment that really
existed. The cost is stated plainly — wick information is gone, so a day that spiked and
retraced reads as a quiet day, and structure built on this series is blind to intraday sweeps.
That is a known absence rather than a fabricated presence, which is the trade this repo makes
everywhere else (see `series.close_on` refusing to carry a price too far).
"""

from __future__ import annotations

from oracle.series import Bar, PriceSeries


def ratio(numerator: PriceSeries, denominator: PriceSeries, *, symbol: str) -> PriceSeries:
    """``numerator / denominator`` as a body-only daily series. See the module docstring.

    Only dates present in **both** legs produce a bar. A leg missing a day is not carried
    forward here: ``close_on`` already owns the carry policy and applies it with a bound, and
    quietly reusing yesterday's BTC against today's ETH would manufacture a move in the ratio
    that neither leg made.

    A non-positive denominator close is skipped rather than raising. It cannot happen with real
    price data, and a series that drops one impossible bar is more useful than one that refuses
    to build at all.
    """
    by_date = {bar.date: bar for bar in denominator.bars}
    bars = []
    for num in numerator.bars:
        den = by_date.get(num.date)
        if den is None or den.open <= 0 or den.close <= 0:
            continue
        opened, closed = num.open / den.open, num.close / den.close
        bars.append(Bar(
            date=num.date,
            open=opened,
            high=max(opened, closed),
            low=min(opened, closed),
            close=closed,
        ))
    return PriceSeries(symbol=symbol, source=DERIVED_SOURCE, bars=tuple(bars))


# Named so a cached-looking series can never be mistaken for a fetched one — nothing writes
# these to ``data/prices/``, and the source string is what a reader checks first.
DERIVED_SOURCE = "derived"
