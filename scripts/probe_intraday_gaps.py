"""What makes a fair value gap on an hourly series spurious — and which fix is right.

`core.imbalance.fair_value_gaps` reads three *consecutive* bars positionally. It has no notion
of elapsed time or of how much traded, so two different things reach it wearing one label.

**Failure A — the closure gap.** A hole in the series is invisible: Friday 20:00 and Monday
13:00 look like adjacent hours. Whatever repriced while the venue was shut lands in one candle,
that body clears the displacement threshold, and the void behind it is called an imbalance. A
displacement candle that displaced nothing. (Gap-fill is a real phenomenon, so these are not
noise — but they are a *different* phenomenon with different statistics.)

**Failure B — the starved displacement candle.** `_Structure.md` names this one: "displacement
on pre-market volume is not the institutional participation the concept is about." A 140k-share
candle can clear an ATR threshold that a 9M-share session sets.

They pull in opposite directions, which is why measuring only one gives the wrong answer.
Trimming pre-market bars fixes B and causes A; keeping them fixes A and causes B.

MEASURED 2026-08-04 — AAPL, NVDA, META, HOOD, SBSW, FXI; 180 days; Alpaca SIP:

                                  bars     FVGs    closure gap    starved c2
    extended feed, as served    11,677      496     49  (10%)     94  (19%)
    trimmed to regular session   5,904      230    128  (56%)      0   (0%)

**Neither assembly is the answer; the filter belongs on gaps, not on bars.** Keep every bar —
thin pre/post-market prints are worth little as structure and a great deal as connective tissue,
because they bridge the overnight move so no void forms — then reject any gap whose displacement
candle was not real participation. That leaves **401 of 496 gaps, with 1 closure artifact left**,
because the two failures overlap almost entirely: 48 of the 49 closure gaps also had a starved
candle. A holed series and a dead pre-market are the same illiquidity seen twice.

**Test participation by volume, not by the clock.** A 13:00-20:00 UTC window is US-equity
trivia and says nothing about a market that never closes. A floor on the displacement candle's
volume against the series median says the same thing in the terms the concern is actually
about, and the numbers agree:

    c2 volume floor    rejects (of 496)    agrees with the session clock
        10% of median         23                    86%
        25% of median         57                    93%
        50% of median         74                    95%

and on crypto, where there is no session to be outside of, a 50% floor is inert — 0-2% of gaps
across BTC, ETH, SOL, LINK and AVAX over 120 days. It binds where the concern is real and
nowhere else, which is what a general rule should do and what an hour window cannot.

**The trailing window that median is taken over barely matters, above about three days.**

    trailing window     rejects (of 496)    agrees with the session clock
        14 bars               81                      97%
        24 bars               61                      93%
        48 bars               70                      94%
        80 bars               71                      95%
       160 bars               71                      95%
       336 bars               73                      95%
      whole series            73                      95%

14 and 24 bars swing between 81 and 61 rejections; from 48 up the answer is flat at 70-73. An
equity day is 16 extended bars, so anything under two days is phase-sensitive — where in the
session the candle falls decides what it is compared against. 14's 97% is the best agreement
on the table and it sits at the least stable point on the curve, which is a good reason not to
take it. **`PARTICIPATION_WINDOW = 80`** — mid-plateau, a trading week of equity bars, ~3.3 days
of crypto — chosen because the plateau makes the choice insensitive, not because 80 is special.

The median is taken over bars *strictly before* the displacement candle, mirroring
`imbalance.atr`: a candle inside the window it is judged against inflates its own threshold.

Run: `uv run python scripts/probe_intraday_gaps.py` (needs Alpaca credentials in `.env`).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from statistics import median

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

# Fractions of the series' median hourly volume to test a displacement candle against,
# as a clock-free stand-in for "was this session participation".
VOLUME_FLOORS = (0.10, 0.25, 0.50)

# Trailing windows to take that median over, in bars. Sub-2-day windows are phase-sensitive
# on a 16-bar equity day; the answer flattens from ~48 up. See the module docstring.
MEDIAN_WINDOWS = (14, 24, 48, 80, 160, 336, None)   # None = whole series


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


def classify(series: IntradaySeries) -> dict:
    """Every FVG in ``series``, split by the two ways one can be spurious.

    They are independent failures and they pull in opposite directions, which is the whole
    point of measuring both: a closure gap is an artifact of *removing* extended bars, and a
    thin displacement candle is an artifact of *keeping* them.
    """
    gaps = imbalance.fair_value_gaps(series.bars)
    volumes = sorted(b.volume for b in series.bars if b.volume is not None)
    median = volumes[len(volumes) // 2] if volumes else 0.0

    spanning = thin_middle = both = 0
    quiet = dict.fromkeys(VOLUME_FLOORS, 0)
    agree = dict.fromkeys(VOLUME_FLOORS, 0)
    for gap in gaps:
        window = series.bars[gap.index - 2:gap.index + 1]
        crosses = any(b.date - a.date > timedelta(hours=1) for a, b in pairwise(window))
        middle = series.bars[gap.middle_index]
        extended = not (SESSION_FIRST_HOUR <= middle.date.hour <= SESSION_LAST_HOUR)
        spanning += crosses
        thin_middle += extended
        both += crosses and extended
        for floor in VOLUME_FLOORS:
            starved = (middle.volume or 0.0) < floor * median
            quiet[floor] += starved
            agree[floor] += starved == extended
    return {"bars": len(series.bars), "gaps": len(gaps), "spanning": spanning,
            "thin": thin_middle, "both": both, "median": median,
            "quiet": quiet, "agree": agree}


def window_sweep(series: IntradaySeries, floor: float = 0.50) -> dict:
    """How many gaps a participation floor rejects, per trailing-window length.

    The median is taken over bars **strictly before** the displacement candle, mirroring
    `imbalance.atr`'s reasoning: a candle included in the window it is judged against inflates
    its own threshold, so the filter partly cancels itself out.
    """
    volumes = [b.volume for b in series.bars if b.volume is not None]
    out = {w: [0, 0] for w in MEDIAN_WINDOWS}          # rejected, agrees-with-clock
    for gap in imbalance.fair_value_gaps(series.bars):
        middle = series.bars[gap.middle_index]
        extended = not (SESSION_FIRST_HOUR <= middle.date.hour <= SESSION_LAST_HOUR)
        for window in MEDIAN_WINDOWS:
            lo = 0 if window is None else max(0, gap.middle_index - window)
            history = volumes[lo:gap.middle_index]
            starved = (middle.volume or 0.0) < floor * (median(history) if history else 0.0)
            out[window][0] += starved
            out[window][1] += starved == extended
    return out


def session_only(rows):
    return [r for r in rows if SESSION_FIRST_HOUR <= int(r["t"][11:13]) <= SESSION_LAST_HOUR]


def main() -> int:
    broker = AlpacaBroker(alpaca_credentials(), network="paper")
    start = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()

    keys = ("bars", "gaps", "spanning", "thin", "both")
    totals = {"extended": dict.fromkeys(keys, 0), "session": dict.fromkeys(keys, 0)}
    sweep = {"quiet": dict.fromkeys(VOLUME_FLOORS, 0), "agree": dict.fromkeys(VOLUME_FLOORS, 0)}
    windows = {w: [0, 0] for w in MEDIAN_WINDOWS}

    print(f"{'symbol':7} {'assembly':>9} {'bars':>6} {'FVGs':>5} {'closure':>8} {'thin c2':>8}")
    for symbol in SYMBOLS:
        rows = fetch_hourly(broker, symbol, start)
        if not rows:
            print(f"{symbol:7} no data returned")
            continue
        for label, subset in (("extended", rows), ("session", session_only(rows))):
            stats = classify(to_series(subset, symbol))
            for k in keys:
                totals[label][k] += stats[k]
            if label == "extended":
                for floor in VOLUME_FLOORS:
                    sweep["quiet"][floor] += stats["quiet"][floor]
                    sweep["agree"][floor] += stats["agree"][floor]
                for window, (rejected, agreed) in window_sweep(to_series(subset, symbol)).items():
                    windows[window][0] += rejected
                    windows[window][1] += agreed
            print(f"{symbol:7} {label:>9} {stats['bars']:6} {stats['gaps']:5} "
                  f"{stats['spanning']:8} {stats['thin']:8}")

    print()
    for label in ("extended", "session"):
        t = totals[label]
        total = t["gaps"] or 1
        print(f"TOTAL {label:9} bars={t['bars']:6} FVGs={t['gaps']:4}  "
              f"closure={t['spanning']:4} ({t['spanning'] / total:.0%})  "
              f"thin c2={t['thin']:4} ({t['thin'] / total:.0%})")

    # The assembly neither the spec nor this probe's first pass considered: keep every bar so
    # the overnight move stays bridged, then discard any gap whose displacement candle was not
    # session participation. A filter on gaps, not on bars.
    t = totals["extended"]
    survivors = t["gaps"] - t["spanning"] - t["thin"] + t["both"]
    print(f"\nbridged (all bars, session-only displacement): {survivors} of {t['gaps']} gaps "
          f"survive; {t['spanning'] - t['both']} closure gaps remain")

    # Can a volume floor replace the clock? An hour window is US-equity-specific and means
    # nothing to crypto; "did this candle carry real participation" is the actual concern.
    print(f"\nvolume floor vs the session clock, over {t['gaps']} extended-feed gaps:")
    for floor in VOLUME_FLOORS:
        print(f"  c2 volume < {floor:.0%} of median: rejects {sweep['quiet'][floor]:4}  "
              f"agrees with the clock on {sweep['agree'][floor] / (t['gaps'] or 1):.0%} of gaps")

    print("\ntrailing window for that median, at a 50% floor:")
    for window, (rejected, agreed) in windows.items():
        label = "whole series" if window is None else f"{window} bars"
        print(f"  {label:>12}: rejects {rejected:4}  agrees {agreed / (t['gaps'] or 1):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
