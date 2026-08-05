"""Would the H1 trigger have fixed the geometry §48 measured, on the trades actually recorded?

``probe_replay`` found the defect: **69% of stops are reached on the very bar that filled the
entry**, median stop 1.9 daily ranges against a median target of 6.7. The engine entered like a
reversal — a resting limit, nothing waited for — and stopped like a continuation. §49's answer
was the trigger. This asks whether that answer works, on the same 142 decisions.

Two things are compared for every row, at the moment it was decided and with no look-ahead:

* **the recorded geometry** — the zone entry and the padded structural stop that were actually
  offered, which is what ``probe_replay`` walked;
* **the trigger geometry** — the FVG edge and the H1 swing behind the break, which is what
  would have been offered instead.

Both stated in the same unit ``probe_replay`` §5 uses: the instrument's own median daily range
over the 14 sessions before the decision. Nothing else makes a stop on ``USDJPY`` and a stop on
``DOGE`` comparable.

**The result is not obviously good in advance, and that is why it is measured.** An H1 swing is
*closer* than a daily structural level almost by construction, so the trigger could easily make
the stop tighter — which would deepen §48's defect rather than fix it. What has to redeem it is
the entry moving with it: the FVG sits further into the move, so entry and stop travel together
and the ratio is what matters, not either level alone.

Bars are sliced to the decision timestamp before the trigger is run, so a row is judged on what
was knowable when it was judged. That matters more here than in ``probe_replay``: the whole
window is 2026-07-26 to 2026-08-04, so unsliced bars would carry days of future for every row.

MEASURED 2026-08-05 over all 142 recorded decisions:

    no zone tag             69  (50%)      armed        3  (2%)
    no confirmation         47  (34%)      fired        2  (1%)
    no hourly at decision   13   (9%)      unroutable   3  (2%)

**The trigger would have offered 5 of 142 trades.** Its dominant effect is not better geometry,
it is *not trading*: half these candidates never had price in the zone at all, and a third had
price in the zone with nothing confirming on the hourly. That is the intended behaviour and it
is a large change to how much this engine does.

On the 5 that did produce both levels, and read this carefully:

    stop distance, in the instrument's own median daily ranges
      as recorded    median 2.02        (probe_replay: 1.9 over all 142)
      with trigger   median 1.17        tighter on 4 of the 5

**The trigger's stop is tighter, which is the direction §48 warns about** — and it is not the
same comparison. §48's defect was *incoherence*, not tightness: a reversal entry (a resting
limit, nothing waited for) paired with a continuation stop, the two levels drawn from different
trades. The trigger draws both from one H1 structure. IBM is the clearest case — the recorded
entry was 230.23 with price at 212.98, eight percent and several days away, while the trigger's
was 211.31 off a break 54 hours old. A narrower stop around a current entry is not the same
object as a narrower stop around a stale one, and only forward outcomes can say which survives.

Reward:risk to the same target reads 5.95 -> 16.67. **Do not read that as improvement.** It rose
because the denominator shrank, and ``core.setups`` already says what a number that high means:
"a symptom of a broken denominator, not a good trade".

**n = 5. Nothing here is conclusive**, and the sample cannot be grown backwards — H1 is held for
60 days and these decisions span eleven. The way to settle it is forward: record the trigger
state and its two levels on every decision from now, and re-run ``probe_replay``'s same-bar test
against both geometries once enough have resolved.

Run: `uv run python scripts/probe_trigger_replay.py` (reads caches only; no network, no cost).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

from core import trigger
from core.canon import load_registry
from oracle import cache, corpus, listings, trigger_feed
from oracle.route import Unpriceable, load_routing_table, route
from oracle.setups_cli import CONFIG_DIR
from probe_replay import (
    MIN_RANGE_BARS,
    RANGE_LOOKBACK,
    build_series_loader,
    load_rows,
)

DECISIONS = Path(__file__).resolve().parents[1] / "data" / "setups" / "decisions.jsonl"


def daily_range(row: dict, load_series) -> float | None:
    """The instrument's median daily high-low over the sessions before the decision.

    Same denominator ``probe_replay.span`` uses, and deliberately so: the two probes' numbers
    are meant to be read side by side, and a second definition of "one ordinary session" would
    make that comparison quietly wrong.
    """
    series, _ = load_series(row["asset"])
    if series is None or not series.bars:
        return None
    decided = datetime.fromisoformat(row["decided_at"]).date()
    prior = [b for b in series.bars if b.date <= decided][-RANGE_LOOKBACK:]
    if len(prior) < MIN_RANGE_BARS:
        return None
    return median(b.high - b.low for b in prior) or None


def hourly_as_of(ref, decided: datetime, load_cached=trigger_feed.load_cached):
    """Cached H1 truncated to what existed at ``decided`` — no look-ahead.

    Returns None when nothing is cached *or* when too little of it predates the decision to
    judge on. The whole decision window is eleven days wide, so an unsliced series would hand
    every row several days of future and report a trigger nobody could have seen.
    """
    series = load_cached(ref)
    if series is None:
        return None
    bars = tuple(b for b in series.bars if b.date <= decided)
    return bars or None


def main() -> int:
    rows = load_rows(DECISIONS)
    load_series = build_series_loader()
    # Built from the corpus's own domain consensus and the real listings, exactly as
    # ``build_candidates`` builds it. A stubbed table refuses ~90% of assets as "conflict",
    # which reads as an engine failure and is only ever a broken probe.
    registry = load_registry(CONFIG_DIR)
    corpus_rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")
    table = load_routing_table(
        CONFIG_DIR, [(r.asset, r.domain) for r in corpus_rows], listings=listings_map,
    )

    states: Counter = Counter()
    recorded_stops: list[float] = []
    trigger_stops: list[float] = []
    recorded_rr: list[float] = []
    trigger_rr: list[float] = []
    paired: list[tuple[str, float, float, float, float, float]] = []

    for row in rows:
        resolved = route(row["asset"], table)
        if isinstance(resolved, Unpriceable):
            states["unroutable"] += 1
            continue
        decided = datetime.fromisoformat(row["decided_at"])
        bars = hourly_as_of(resolved, decided)
        if bars is None:
            states["no_hourly_at_decision"] += 1
            continue
        found = trigger.detect(bars, direction=row["direction"],
                               zone_tagged=bool(row.get("inside_zone")))
        states[found.state] += 1

        unit = daily_range(row, load_series)
        if unit is None or found.entry is None or found.stop is None:
            continue
        rec_stop = abs(row["entry"] - row["stop"]) / unit
        trg_stop = abs(found.entry - found.stop) / unit
        target = row.get("target")
        recorded_stops.append(rec_stop)
        trigger_stops.append(trg_stop)
        if target is not None:
            recorded_rr.append(abs(target - row["entry"]) / abs(row["entry"] - row["stop"]))
            trigger_rr.append(abs(target - found.entry) / abs(found.entry - found.stop))
        age = (decided - found.structure_break.date).total_seconds() / 3600
        paired.append((row["asset"], rec_stop, trg_stop, row["entry"], found.entry, age))

    print(f"{len(rows)} recorded decisions\n")
    for state, count in states.most_common():
        print(f"  {state:22} {count:4}  ({count / len(rows):.0%})")

    if not paired:
        print("\nNo row produced a trigger with both levels — nothing to compare.")
        print("That is itself the finding: the trigger would have offered none of these trades.")
        return 0

    print(f"\n{len(paired)} rows produced a trigger with an entry and a stop.")
    print("\nstop distance, in the instrument's own median daily ranges:")
    print(f"  as recorded   median {median(recorded_stops):.2f}   "
          f"(probe_replay measured 1.9 over all 142)")
    print(f"  with trigger  median {median(trigger_stops):.2f}")
    tighter = sum(1 for _, r, t, *_ in paired if t < r)
    print(f"  the trigger's stop is tighter on {tighter}/{len(paired)} of them")

    if recorded_rr:
        print("\nreward:risk to the same recorded target:")
        print(f"  as recorded   median {median(recorded_rr):.2f}")
        print(f"  with trigger  median {median(trigger_rr):.2f}")

    # How stale the break was when the decision was taken. A trigger drawn off a break days
    # old is not confirmation of anything current, and comparing its geometry against a zone's
    # would be comparing two different trades.
    ages = sorted(a for *_, a in paired)
    print(f"\nhours from the structure break to the decision: "
          f"min {ages[0]:.0f}, median {median(ages):.0f}, max {ages[-1]:.0f}")

    print("\nper row (stop in daily ranges, then entry; recorded -> trigger):")
    for asset, rec, trg, rec_entry, trg_entry, age in paired:
        print(f"  {asset:8} {rec:6.2f} -> {trg:6.2f}    {rec_entry:12.6g} -> {trg_entry:12.6g}"
              f"    break {age:5.0f}h old")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
