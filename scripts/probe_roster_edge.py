"""Does the roster predict direction on its own — and does crowding predict it better inverted?

Every feature here is computed from `data/stances/**` alone and graded by walking daily bars
forward through `oracle.replay`. Nothing about a zone, a block or a sweep enters. That is the
point: if the corpus carries no directional signal by itself, layering structure on top of it
cannot rescue it, and we want to learn that for the price of one run rather than one refactor.

FINDINGS, 2026-08-26, 7,334 rows over 21 assets, 2024-08 to 2026-08

Agreement does not predict direction. `unanimous` cleared its baseline by +0.040R in-sample and
+0.064R held out, which reads well until it is broken down per asset: it beat its own baseline on
13 of 21, against 10.5 for a coin, and the spread ran -0.50R (OIL) to +0.34R (SOL). The scatter is
an order of magnitude wider than the mean, so the aggregate is noise. `flip` reversed sign between
the halves (-0.089 then +0.183) and is the same story told louder. The two strongest contributors
to `unanimous` were GOLD and SILVER, both mid-trend in this window — the roster agreeing after a
move, not before one.

The baseline itself moved -0.063R to +0.034R across the halves. The backdrop shifts the numbers
more than any feature does, which is the reason every bucket below is reported against the
baseline of its own period and never against zero.

Crowding survives, weakly, and only in one band. When an asset draws 1.6-2.5x its own normal
number of distinct voices, fading the roster beat following it on 10 of 14 assets in-sample and
10 of 14 again held out. No other band repeats: `>2.5x` went 4/8 then 9/14, and every band below
1.6x reversed outright between the halves. 10/14 twice is suggestive, not established — and the
14 are mostly crypto that trades together, so they are nowhere near 14 independent tests.

Inverting a losing bucket does NOT rescue it, and this table is the demonstration. In-sample,
fading looked like a printer: +0.121R against -0.102R for following, at every band but one. It
was an artifact of the period — the roster was simply wrong a lot in that stretch, so the
mirror of a bad half is a good half. Held out, the same fade collapsed to +0.034R against
+0.016R. Nearly nothing, and that is before costs, which a fade pays twice and which no sign
flip ever reverses.

The holdout is now spent. The monotone decline in follow-return as crowding rises (+0.257,
+0.175, +0.154, -0.043, -0.133) appears in the holdout and NOT in-sample, so it was found by
looking at the data reserved for confirming. It is a hypothesis for the next window, never
evidence from this one. Do not tune against these rows again.

CROWDING IS DEAD — the pre-registered test, 34 assets never previously read, 2026-08-26

Widening to `MIN_STANCES = 15` brought the universe to 55 assets, 30 of them off Yahoo rather
than a crypto venue, and `HYPOTHESIS_BAND` was then asked of the 34 the earlier rounds had never
touched. It failed. Fade beat follow on 9 of 16 fresh assets carrying enough rows to count,
against 8 for a coin. The aggregate looked strong (-0.169R follow against +0.088R fade) for the
same reason `unanimous` did: a handful of names carry it. Per asset the edge runs -1.58R (VVV)
to +1.62R (INTC) on 20-65 heavily overlapping rows each, which is scatter, not effect.

The cleanest single number is that crypto shows nothing at all — -0.051R follow against -0.049R
fade across 497 rows. A crowding effect that vanishes on the most retail-driven, most
sentiment-driven half of the universe is not a crowding effect. All three roster features tested
by this probe are now negative results.

CAUTION FOR WHOEVER WIDENS THIS AGAIN. Several assets in the widened set are the same instrument
twice — EUR and EURUSD, JPY and USDJPY, URA and URANIUM, DJI and YM. They inflate a breadth count
that is the whole basis for trusting a result here, so a per-asset tally over this universe reads
higher than the number of independent bets behind it.

WHAT THE NUMBERS CAN AND CANNOT SAY

`published_at` is a date, never a time — `ingestion.channel._published_at` prefers yt-dlp's
date-only `upload_date` and only falls back to the real epoch, which is never reached. 423 of
955 videos are livestreams besides, stamped at the moment the stream *started*, so a call made
in the last half hour is hours adrift from its own timestamp.

So the entry is deliberately late: the open of the first bar strictly after the stamped date,
which is up to ~48h after the words were actually spoken. Lateness is the safe direction. A
replay that acts *earlier* than real life invents edge; one that acts later only hides it.
Whatever survives this handicap is real, and its size is understated. Note the asymmetry when
reading a negative result: the handicap can bury a positive edge, but it cannot manufacture a
consistently negative one.

That slowness sets the horizon. The median one-day move is 42% the size of the median five-day
move on BTC, ETH and SPX alike, so a day of timing error eats half of a five-day window. At
twenty days it is a fifth. Hence `TAIL_DAYS`.

`TAIL_DAYS` is a measurement boundary, NOT a trade expiry — `oracle.replay`'s header rejects
inventing the latter, and this is the other thing. An OPEN row is marked to market, not
discarded, exactly as that module does it.

R IS A RULER HERE, NOT THE TRADE

Real stops come off a called low or an ICT structure point, so real R varies per trade and
cannot be reconstructed for 5,629 stances across 584 assets. Risk is fixed at one ATR instead
so that BTC and SPX are denominated alike. Targets run at both 2R and 3R because both get
traded. These brackets are a common ruler, not a claim about how anyone enters.

FADE IS RE-GRADED, NEVER NEGATED. A fade's R is not minus the follow's R, because the bracket is
asymmetric — risking one ATR to make two. Flipping the sign of a 2R win would score the fade at
-2R when its real loss was -1R, which would invent an edge for whichever side happened to lose.
Both sides are walked through `oracle.replay` separately.

READ THE BASELINE COLUMN FIRST, THEN THE PER-ASSET LINE. A bucket returning +0.30R means nothing
if every row in the same window returned +0.30R, and an aggregate means nothing if two assets
carry it. Both checks are printed because the first one alone already fooled this probe once.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "core" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "oracle" / "src"))

from core.imbalance import ATR_LOOKBACK, atr
from oracle import cache, replay
from oracle.route import OracleRef, route, the_routing_table

REPO = Path(__file__).resolve().parent.parent
STANCE_ROOT = REPO / "data" / "stances"

TAIL_DAYS = 20
RECENT_WINDOW = 30      # days of stances that count as "the roster's current view"
PRIOR_WINDOW = 30       # the window immediately before it, for detecting a flip
MIN_STANCES = 15        # per asset, below which no bucket reaches a readable count
MIN_HISTORY = 60        # prior observations before an asset's "normal" attendance is stable
BULLISH, BEARISH = "bullish", "bearish"

# The 21 assets the first two runs of this probe were read against. The crowding band below was
# chosen after seeing them, so their rows can no longer test it — only the assets outside this
# set can. Keep the list frozen even as coverage grows; widening it would quietly re-spend the
# only clean sample this probe has left.
SEEN_ASSETS = frozenset({
    "BTC", "DOGE", "DXY", "ETH", "GOLD", "HOOD", "HYPE", "LTC", "MSTR", "MU", "NDX",
    "NEAR", "NVDA", "OIL", "SILVER", "SOL", "SPX", "TSLA", "XMR", "XRP", "ZEC",
})

# The band that repeated 10/14 across both halves on the seen assets. Pre-registered here so the
# out-of-sample test below has exactly one thing to answer and cannot be widened after the fact.
HYPOTHESIS_BAND = (1.6, 2.5)

CRYPTO_SOURCES = frozenset({"coinbase", "kraken"})

# Attendance relative to the asset's own trailing normal. Open-ended at both ends so no row is
# dropped for being extreme — the extremes are the interesting part.
CROWD_BANDS = ((0.0, 0.6), (0.6, 1.0), (1.0, 1.6), (1.6, 2.5), (2.5, float("inf")))


@dataclass(frozen=True)
class Stance:
    asset: str
    when: date
    lean: str
    person: str


@dataclass(frozen=True)
class Row:
    """One (asset, day) observation: what the roster looked like, and what happened next."""
    asset: str
    when: date
    features: frozenset[str]
    lean: str
    crowd: float | None          # distinct voices vs this asset's own trailing normal
    r2: float | None
    r3: float | None
    fade2: float | None
    fade3: float | None
    fresh: bool                  # asset outside SEEN_ASSETS, so usable to test the hypothesis
    crypto: bool


def load_stances() -> list[Stance]:
    out: list[Stance] = []
    for path in STANCE_ROOT.glob("*/*.json"):
        payload = json.loads(path.read_text())
        for item in payload.get("stances", []):
            lean = item.get("lean")
            if lean not in (BULLISH, BEARISH):
                continue  # neutral and unknown carry no direction to grade
            src = item.get("source") or {}
            stamped = (src.get("published_at") or "")[:10]
            try:
                when = date.fromisoformat(stamped)
            except ValueError:
                continue
            out.append(Stance(item["asset"], when, lean, src.get("person") or "?"))
    return out


def features_for(
    recent: list[Stance], prior: list[Stance]
) -> tuple[frozenset[str], str | None, date | None]:
    """The roster's state on one day, as a set of flags plus the side it leans."""
    if not recent:
        return frozenset(), None, None

    bulls = sum(1 for s in recent if s.lean == BULLISH)
    bears = len(recent) - bulls
    lean = BULLISH if bulls > bears else BEARISH if bears > bulls else None
    if lean is None:
        return frozenset(), None, None  # a dead-even roster names no side to trade

    flags = {"any_lean"}
    voices = {s.person for s in recent}

    if bulls == 0 or bears == 0:
        flags.add("unanimous")
    if len(voices) >= 3:
        flags.add("three_voices")
    if len(recent) >= 2 and max(bulls, bears) / len(recent) >= 0.8:
        flags.add("supermajority")

    if prior:
        pb = sum(1 for s in prior if s.lean == BULLISH)
        prior_lean = BULLISH if pb > len(prior) - pb else BEARISH if pb < len(prior) - pb else None
        if prior_lean and prior_lean != lean:
            flags.add("flip")

    newest = max(s.when for s in recent)
    return frozenset(flags), lean, newest


def grade(series, idx: int, lean: str, reward: float) -> float | None:
    """R returned by a one-ATR-risk bracket entered at the open of bar ``idx``."""
    unit = atr(series.bars, idx - 1)
    if not unit:
        return None
    entry = series.bars[idx].open
    long = lean == BULLISH
    stop = entry - unit if long else entry + unit
    target = entry + reward * unit if long else entry - reward * unit
    outcome = replay.resolve(
        entry=entry, stop=stop, target=target,
        direction="long" if long else "short",
        bars=series.bars, from_date=series.bars[idx - 1].date,
        tail_days=TAIL_DAYS,
    )
    return outcome.r


def rows_for_asset(asset: str, stances: list[Stance], series, *, crypto: bool) -> list[Row]:
    by_date = defaultdict(list)
    for s in stances:
        by_date[s.when].append(s)

    # An asset's "normal" attendance must be built from its own past only. Taking the median
    # over the whole history would leak a quiet 2026 back into a busy 2024 and turn a flat
    # series into a signal.
    seen_counts: list[int] = []
    opposite = {BULLISH: BEARISH, BEARISH: BULLISH}

    out: list[Row] = []
    for idx in range(ATR_LOOKBACK + 1, len(series.bars)):  # past the ATR warmup
        decision = series.bars[idx - 1].date
        recent = [s for d, group in by_date.items()
                  if decision - timedelta(days=RECENT_WINDOW) <= d <= decision
                  for s in group]
        prior_lo = decision - timedelta(days=RECENT_WINDOW + PRIOR_WINDOW)
        prior_hi = decision - timedelta(days=RECENT_WINDOW + 1)
        prior = [s for d, group in by_date.items() if prior_lo <= d <= prior_hi for s in group]

        flags, lean, newest = features_for(recent, prior)
        if not lean or newest is None:
            continue

        voices = len({s.person for s in recent})
        normal = statistics.median(seen_counts) if len(seen_counts) >= MIN_HISTORY else None
        seen_counts.append(voices)
        crowd = voices / normal if normal else None

        out.append(Row(
            asset, decision, flags, lean, crowd,
            grade(series, idx, lean, 2.0), grade(series, idx, lean, 3.0),
            grade(series, idx, opposite[lean], 2.0), grade(series, idx, opposite[lean], 3.0),
            fresh=asset not in SEEN_ASSETS, crypto=crypto,
        ))
    return out


def _stats(subset: list[Row], field: str):
    vals = [getattr(r, field) for r in subset if getattr(r, field) is not None]
    if not vals:
        return None
    return len(vals), sum(1 for v in vals if v > 0) / len(vals) * 100, sum(vals) / len(vals)


def summarise_flags(rows: list[Row], key: str) -> None:
    base = _stats(rows, "r2")
    print(f"\n{'=' * 78}\n{key}\n{'=' * 78}")
    print(f"{'bucket':<16} {'n':>6} {'win%@2R':>9} {'meanR@2R':>10} {'meanR@3R':>10}")
    print("-" * 78)
    if base:
        b3 = _stats(rows, "r3")
        print(f"{'ALL (baseline)':<16} {base[0]:>6} {base[1]:>8.1f}% {base[2]:>10.3f} "
              f"{b3[2]:>10.3f}")
    for flag in sorted({f for r in rows for f in r.features}):
        subset = [r for r in rows if flag in r.features]
        s2, s3 = _stats(subset, "r2"), _stats(subset, "r3")
        if not (s2 and s3):
            continue
        print(f"{flag:<16} {s2[0]:>6} {s2[1]:>8.1f}% {s2[2]:>10.3f} {s3[2]:>10.3f}"
              f"   ({s2[2] - base[2]:+.3f} vs base)")


def summarise_crowding(rows: list[Row], key: str) -> None:
    rows = [r for r in rows if r.crowd is not None]
    print(f"\n{'=' * 78}\nCROWDING — {key}\n{'=' * 78}")
    if not rows:
        print("no rows with a stable trailing normal")
        return

    base = _stats(rows, "r2")
    print("voices vs this asset's own trailing normal; follow = trade the lean, "
          "fade = trade against it")
    print(f"{'band':<14} {'n':>6} {'followR':>9} {'fadeR':>9} {'follow w%':>10} "
          f"{'fade w%':>9} {'assets fade>follow':>20}")
    print("-" * 84)
    print(f"{'ALL':<14} {base[0]:>6} {base[2]:>9.3f} "
          f"{_stats(rows, 'fade2')[2]:>9.3f} {base[1]:>9.1f}% "
          f"{_stats(rows, 'fade2')[1]:>8.1f}%")

    for lo, hi in CROWD_BANDS:
        band = [r for r in rows if r.crowd is not None and lo <= r.crowd < hi]
        f2, d2 = _stats(band, "r2"), _stats(band, "fade2")
        if not (f2 and d2) or f2[0] < 50:
            continue
        per_asset = []
        for a in {r.asset for r in band}:
            sub = [r for r in band if r.asset == a]
            fa, da = _stats(sub, "r2"), _stats(sub, "fade2")
            if fa and da and fa[0] >= 20:
                per_asset.append(da[2] > fa[2])
        tally = f"{sum(per_asset)}/{len(per_asset)}" if per_asset else "-"
        label = f"{lo:.1f}-{hi:.1f}x" if hi != float("inf") else f">{lo:.1f}x"
        print(f"{label:<14} {f2[0]:>6} {f2[2]:>9.3f} {d2[2]:>9.3f} {f2[1]:>9.1f}% "
              f"{d2[1]:>8.1f}% {tally:>20}")


def summarise_hypothesis(rows: list[Row]) -> None:
    """One pre-registered question, asked of assets this probe has never been read against.

    The claim under test is narrow on purpose: inside ``HYPOTHESIS_BAND``, fading the roster
    returns more than following it. Anything wider would be a search, and a search over the
    only unspent sample left is how the last two rounds produced numbers that did not repeat.
    """
    lo, hi = HYPOTHESIS_BAND
    band = [r for r in rows if r.crowd is not None and lo <= r.crowd < hi]

    print(f"\n{'=' * 78}\nPRE-REGISTERED TEST — fade > follow at {lo}-{hi}x crowding\n{'=' * 78}")

    def report(label: str, subset: list[Row]) -> None:
        f2, d2 = _stats(subset, "r2"), _stats(subset, "fade2")
        if not (f2 and d2):
            print(f"{label:<26} (no rows)")
            return
        per_asset = []
        for a in sorted({r.asset for r in subset}):
            sub = [r for r in subset if r.asset == a]
            fa, da = _stats(sub, "r2"), _stats(sub, "fade2")
            if fa and da and fa[0] >= 20:
                per_asset.append(da[2] > fa[2])
        tally = f"{sum(per_asset)}/{len(per_asset)}" if per_asset else "-"
        verdict = "fade" if d2[2] > f2[2] else "follow"
        print(f"{label:<26} n={f2[0]:>5}  follow={f2[2]:>+7.3f}  fade={d2[2]:>+7.3f}  "
              f"assets fade>follow={tally:>7}  -> {verdict}")

    report("FRESH assets (the test)", [r for r in band if r.fresh])
    report("  fresh, non-crypto", [r for r in band if r.fresh and not r.crypto])
    report("  fresh, crypto", [r for r in band if r.fresh and r.crypto])
    report("SEEN assets (spent)", [r for r in band if not r.fresh])
    print("\nSEEN is printed for comparison only — the band was chosen on those rows, so it "
          "cannot\nconfirm anything. Read the FRESH line.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--holdout-from", default="2026-03-01",
                    help="rows on or after this date are reported separately")
    args = ap.parse_args()
    cut = date.fromisoformat(args.holdout_from)

    by_asset = defaultdict(list)
    for s in load_stances():
        by_asset[s.asset].append(s)

    table = the_routing_table()
    rows: list[Row] = []
    skipped: list[str] = []

    for asset, group in sorted(by_asset.items()):
        if len(group) < MIN_STANCES:
            continue
        ref = route(asset, table)
        # A DerivedRef is a ratio built from two other series and an Unpriceable is a refusal.
        # Only a plain (source, symbol) pair has bars to walk.
        if not isinstance(ref, OracleRef):
            skipped.append(f"{asset}({type(ref).__name__})")
            continue
        series = cache.load(ref.source, ref.symbol)
        if not series or len(series.bars) < ATR_LOOKBACK + TAIL_DAYS:
            skipped.append(f"{asset}(no bars)")
            continue
        rows.extend(rows_for_asset(asset, group, series,
                                   crypto=ref.source in CRYPTO_SOURCES))

    if skipped:
        print(f"skipped {len(skipped)}: {', '.join(skipped)}", file=sys.stderr)

    print(f"rows={len(rows)}  assets={len({r.asset for r in rows})}  "
          f"span={min(r.when for r in rows)} -> {max(r.when for r in rows)}")
    print(f"entry = open of the first bar after the stance date; risk = 1 ATR({ATR_LOOKBACK}); "
          f"window = {TAIL_DAYS}d")

    early = [r for r in rows if r.when < cut]
    late = [r for r in rows if r.when >= cut]
    summarise_flags(early, f"IN-SAMPLE  (before {cut})")
    summarise_flags(late, f"HOLDOUT    (from {cut})")
    summarise_crowding(early, f"in-sample, before {cut}")
    summarise_crowding(late, f"holdout, from {cut}")
    summarise_hypothesis(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
