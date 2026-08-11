"""What is actually in ``data/prices/`` — depth, holes, and bars that cannot be true.

Free, local, re-runnable. Reads the daily cache only; no network, no LLM, nothing written.

**Why this exists.** Every replayed outcome in this repo rests on cached daily bars, and until
now nothing checked them. A gap in the middle of a series silently changes which level a trade
reached first; an unadjusted split is worse than a gap, because it is a real-looking 75% bar
that stops out every long in the sample and never reads as corrupt.

**Section 1 is the one that changed the code.** Measured 2026-08-10, before ``plan.floor_start``
existed: **95 of 329 series held under 90 daily bars, and 227 under 250** — because
``plan_fetches`` windowed every series to its own earliest mention minus 7 days. That is correct
for grading, which reads bars *forward* from a publish date, and wrong for structure, which
reads backward. Those assets were refusing in ``core.setups`` as ``no_dealing_range``, a
structure verdict standing in for "nobody fetched the history". After the 730-day backfill:
**7 of 329 under 90 bars, 18 under 250**, median depth 139 -> 500.

**Section 4 found a live one, and it is not the one that was expected.** ``yahoo.parse_chart``
reads ``quotes["close"]``, not ``adjclose``, and the guess was that this cost a few percent of
dividend drift. It costs more than that: **SOXS closed 1159.50 on 2026-05-22 and opened 69.00
the next session**, ordinary ranges either side — an ~18x change of units. A fresh Yahoo fetch
reproduces it bar for bar, so the cache is faithful and the *source array* is unadjusted.

The screen that catches it is range continuity, not the size of the gap and not a ratio near a
common fraction: a ratio test misses SOXS entirely, because 1/18.4 is near nothing. It cannot
separate a split from a news gap-up, which has the same shape — settling those needs
``adjclose``. **Switching the price basis is deliberately not done here.** Every cached series
and every recorded decision is drawn against ``close``; changing it under them would silently
re-scale history that hand-entered judgement already refers to. Exclude flagged series from a
replay instead, and treat the basis change as its own decision with its own migration.

**Section 5 measured the warmup constant instead of choosing one, and the guess was wrong.**
The design assumed 365 days of lookback. Sweeping it against the backfilled cache:

    90d   226 (69%)     270d  319 (97%)     545d  319 (97%)
    180d  316 (96%)     365d  319 (97%)     730d  319 (97%)

**The knee is 180 days and the plateau is 270.** Past 270 the curve is flat — every remaining
refusal is structural, not a shortage of bars, so the extra year buys nothing. This widens the
samplable as-of window materially: at ``730 - 270 - 90`` it is ~370 days rather than the ~275
the 365d guess implied, or ~52 weekly as-of dates instead of ~40.

Run this **before** trusting the same sweep on a shallow cache: measured pre-backfill it also
flattened after 180d, which reads identically and meant only that no deeper bars existed.
Mirrors ``resample.straddles_the_split``, which measures the setup rung because the asset-class
table it replaced was wrong three times.

Usage::

    uv run python scripts/probe_price_cache.py
    uv run python scripts/probe_price_cache.py --move-threshold 0.25
    uv run python scripts/probe_price_cache.py --no-warmup-sweep     # skip the slow section
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from statistics import median

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from core.setups import build_context  # noqa: E402
from oracle import cache  # noqa: E402
from oracle.resample import to_weekly  # noqa: E402
from oracle.series import Bar, PriceSeries  # noqa: E402

# Bar counts worth naming. 90 is roughly 18 weekly bars — with ``SWING_WIDTH`` at 2 that is
# only a handful of confirmable weekly swings, so below it weekly structure is guesswork
# rather than thin. 250 is a trading year.
THIN_BARS = 90
YEAR_BARS = 250

# A single-session move past this reads as a split or a bad tick rather than a market move.
# 0.35 is deliberately loose: crypto genuinely prints ±30% sessions, and the point of this
# screen is to surface candidates for a human, not to reject bars automatically.
DEFAULT_MOVE_THRESHOLD = 0.35

# A bar whose own high-low range reaches this traded through its move, whatever the size of
# the move — so it is a session, not a change of units. Calibrated against the largest real
# move in the cache: BMNR printed a 64.8% range on the day it closed +694.8%, while SOXS's
# confirmed adjustment break printed 12.1%.
LIVE_RANGE = 0.20

# Calendar days between consecutive bars past which the gap is worth naming. 5 clears an
# ordinary weekend (3) and the long weekends around most holidays (4), so what survives is
# either a market closure worth knowing about or a fetch that quietly dropped a span.
GAP_DAYS = 5

WARMUP_SWEEP = (90, 180, 270, 365, 545, 730)


def iter_cached() -> list[PriceSeries]:
    """Every daily series on disk. Intraday lives under its own subtree and is skipped."""
    out = []
    for path in sorted(cache.DATA_ROOT.rglob("*.json")):
        if path.name.startswith("_") or cache.INTRADAY_DIR in path.parts:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"  UNREADABLE {path}")
            continue
        series = cache.load(doc.get("source", path.parent.name), doc.get("symbol", ""))
        if series is not None and series.bars:
            out.append(series)
    return out


def impossible(bar: Bar) -> str | None:
    """Bars that cannot describe a real session, whatever the instrument."""
    if bar.high < bar.low:
        return "high<low"
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        return "non-positive"
    if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high):
        return "o/c outside h/l"
    if bar.date.year < 2000:
        return "pre-2000 stamp"
    return None


def gaps(series: PriceSeries, *, days: int = GAP_DAYS) -> list[tuple[date, date, int]]:
    out = []
    for prev, cur in zip(series.bars, series.bars[1:], strict=False):
        span = (cur.date - prev.date).days
        if span > days:
            out.append((prev.date, cur.date, span))
    return out


def jumps(series: PriceSeries, *, threshold: float) -> list[tuple[date, float]]:
    out = []
    for prev, cur in zip(series.bars, series.bars[1:], strict=False):
        if prev.close <= 0:
            continue
        move = cur.close / prev.close - 1.0
        if abs(move) >= threshold:
            out.append((cur.date, move))
    return out


def discontinuities(series: PriceSeries, *, threshold: float = 0.45) -> list[tuple]:
    """Close-to-close gaps whose own bar is too calm to have produced them.

    **A screen, not a verdict.** A split leaves an ordinary intraday range on both sides
    because nothing traded — only the units changed. A real move drags the range with it. The
    ``LIVE_RANGE`` floor is what keeps a genuine violent session (BMNR printed a 64.8% range
    on its +695% day) from reading as an adjustment artifact.

    It cannot separate a split from a **news gap-up**, which has the same signature: CRDO
    +47.9% on 11.7% and MXL +76.1% on 16.3% are both real earnings reactions. Settling those
    needs ``adjclose``, which ``yahoo.parse_chart`` does not read — see the module docstring.
    """
    out = []
    for prev, cur in zip(series.bars, series.bars[1:], strict=False):
        if prev.close <= 0 or cur.close <= 0:
            continue
        move = cur.close / prev.close - 1.0
        own_range = (cur.high - cur.low) / cur.close
        if abs(move) >= threshold and own_range < LIVE_RANGE:
            out.append((cur.date, move, own_range))
    return out


def depth_section(all_series: list[PriceSeries]) -> None:
    counts = sorted(len(s.bars) for s in all_series)
    thin = [s for s in all_series if len(s.bars) < THIN_BARS]
    under_year = sum(1 for c in counts if c < YEAR_BARS)
    n = len(all_series)
    print("── 1. depth " + "─" * 56)
    print(f"  series          {n}")
    print(f"  bars            median {median(counts):.0f}   min {counts[0]}   max {counts[-1]}")
    print(f"  under {THIN_BARS:<3} bars  {len(thin):>3} / {n}  ({100 * len(thin) / n:.0f}%)"
          "   too thin for weekly structure")
    print(f"  under {YEAR_BARS} bars  {under_year:>3} / {n}  ({100 * under_year / n:.0f}%)"
          "   under a trading year")
    if thin:
        worst = sorted(thin, key=lambda s: len(s.bars))[:12]
        print("  thinnest        " + "  ".join(f"{s.symbol}:{len(s.bars)}" for s in worst))


def integrity_section(all_series: list[PriceSeries]) -> None:
    print("\n── 2. bars that cannot be true " + "─" * 38)
    found = Counter()
    for s in all_series:
        for bar in s.bars:
            why = impossible(bar)
            if why:
                found[why] += 1
                if found[why] <= 3:
                    print(f"  {why:<16} {s.source}:{s.symbol}  {bar.date}  "
                          f"o={bar.open} h={bar.high} l={bar.low} c={bar.close}")
    print("  none" if not found else f"  totals: {dict(found)}")


def gap_section(all_series: list[PriceSeries]) -> None:
    print(f"\n── 3. gaps over {GAP_DAYS} calendar days " + "─" * 34)
    rows = [(s, g) for s in all_series for g in gaps(s)]
    if not rows:
        print("  none")
        return
    by_series = Counter(s.symbol for s, _ in rows)
    print(f"  {len(rows)} gaps across {len(by_series)} series")
    print("  worst: " + "  ".join(f"{sym}:{n}" for sym, n in by_series.most_common(8)))
    for s, (a, b, span) in sorted(rows, key=lambda r: -r[1][2])[:8]:
        print(f"    {s.source}:{s.symbol:<12} {a} .. {b}  ({span}d)")


def jump_section(all_series: list[PriceSeries], *, threshold: float) -> None:
    print(f"\n── 4. single-session moves >= {threshold:.0%} " + "─" * 30)
    rows = [(s, j) for s in all_series for j in jumps(s, threshold=threshold)]
    print(f"  {len(rows)} moves across {len({s.symbol for s, _ in rows})} series"
          "   — mostly real; crypto prints these")
    for s, (when, move) in sorted(rows, key=lambda r: -abs(r[1][1]))[:8]:
        print(f"    {s.source}:{s.symbol:<12} {when}  {move:+.1%}")

    print("\n  ADJUSTMENT BREAKS — gap too large for the bar's own range " + "─" * 3)
    print("  `parse_chart` reads quotes[close], not adjclose, so a corporate action Yahoo")
    print("  applied retroactively appears here as a step. A trade replayed across one")
    print("  resolves against prices that never traded.")
    flagged = [(s, d) for s in all_series for d in discontinuities(s)]
    if not flagged:
        print("    none")
        return
    for s, (when, move, own) in sorted(flagged, key=lambda r: -abs(r[1][1])):
        print(f"    {s.source}:{s.symbol:<12} {when}  {move:+8.1%}  own range {own:>5.1%}")
    print("\n    CONFIRMED by inspection 2026-08-11: SOXS 2026-05-22 closed 1159.50 and the")
    print("    next session opened 69.00, with ordinary ranges either side — an ~18x change")
    print("    of units, not a move. A fresh Yahoo fetch reproduces it exactly, so the cache")
    print("    is faithful and the source array is what is unadjusted. SSNLF prints o=h=l=c")
    print("    on an illiquid OTC quote. The rest are real news gaps; see `discontinuities`.")


def warmup_section(all_series: list[PriceSeries]) -> None:
    """How many assets can produce a dealing range, as a function of history available.

    Read the knee. The curve rising means history was the binding constraint; the curve
    flattening means everything past that point is refusing for structural reasons, which is
    the engine working rather than the cache being short.
    """
    print("\n── 5. warmup sweep: dealing ranges available by lookback " + "─" * 5)
    as_of = max(s.bars[-1].date for s in all_series)
    print(f"  as_of {as_of} (newest bar in the cache)\n")
    print(f"  {'warmup':>8} {'with a dealing range':>24} {'eligible':>10} {'errored':>9}")
    for warmup in WARMUP_SWEEP:
        floor = as_of - timedelta(days=warmup)
        eligible = ok = 0
        errored: Counter = Counter()
        for s in all_series:
            window = [b for b in s.bars if floor <= b.date <= as_of]
            if len(window) < 2:
                continue
            eligible += 1
            trimmed = PriceSeries(symbol=s.symbol, source=s.source, bars=tuple(window))
            # Narrow, and counted rather than swallowed. A series short enough to break
            # resampling is a fact about the sweep's own floor, so it belongs in the table;
            # anything else raises, because a silent zero here reads as "structure refused"
            # and would put the knee in the wrong place.
            try:
                ctx = build_context(trimmed.bars, to_weekly(trimmed).bars, as_of=as_of)
            except (IndexError, ValueError, ZeroDivisionError) as exc:
                errored[type(exc).__name__] += 1
                continue
            if ctx is not None and ctx.dealing_range is not None:
                ok += 1
        pct = f"{100 * ok / eligible:.0f}%" if eligible else "—"
        errs = " ".join(f"{k}:{v}" for k, v in errored.items()) or "—"
        print(f"  {warmup:>6}d {ok:>16} ({pct:>4}) {eligible:>10} {errs:>9}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--move-threshold", type=float, default=DEFAULT_MOVE_THRESHOLD)
    ap.add_argument("--no-warmup-sweep", action="store_true",
                    help="skip section 5, which builds a Context per series per warmup")
    args = ap.parse_args()

    all_series = iter_cached()
    if not all_series:
        print("no cached daily series — run `uv run fetch-prices` first")
        return
    depth_section(all_series)
    integrity_section(all_series)
    gap_section(all_series)
    jump_section(all_series, threshold=args.move_threshold)
    if not args.no_warmup_sweep:
        warmup_section(all_series)


if __name__ == "__main__":
    main()
