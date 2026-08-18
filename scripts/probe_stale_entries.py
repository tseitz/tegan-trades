"""When price walks away from a resting entry, is retiring the order worth anything?

Free, local, re-runnable. Reads ``data/setups/decisions.jsonl`` and ``data/prices/`` only — no
network, no LLM, no price fetch, and nothing is written back onto a decision row.

**Why this exists.** ``cfg/execution.yaml`` has one staleness axis, ``max_order_age_days: 14``,
and its own comment explains why it is not swept automatically: a weekly zone taking a month to
be reached is ordinary, so age retires the patient trades first. The axis that could justify a
cancel is distance — price moved toward the target without us — and nothing measures it. This
probe is the measurement that has to come before the feature.

**The quantity.** For each decision, walk bars forward and record how far price closed *past*
the entry, toward the target, in units of that trade's own risk ``R = |entry - stop|``. Call it
``x``. Closes rather than highs, for two reasons that happen to agree: a wick through a level is
not a move, and a daily bar that both fills the entry and prints an extreme cannot say which came
first, while a *close* on an unfilled bar is unambiguous. So every excursion here belongs to a
bar that demonstrably did not fill.

**The threshold under test.** Entering at spot instead of at the entry keeps the same stop and
target, so an advertised ``m:1`` becomes ``(m-x)/(1+x)``. Setting that to ``m/2`` gives
``x = m/(m+2)`` — the point at which chasing has given away half the edge. It lands at 0.50R for
a 2:1 setup, 0.60R for 3:1, 0.71R for 5:1, and never reaches 1.0. That is a derivation with one
chosen input ("halved") rather than a chosen distance, and it self-scales in the right direction:
a thin-edge setup should die sooner than a fat one. ``--flat`` sweeps fixed alternatives beside it
so the derivation has to earn its place.

Note what this threshold is and is not. A resting order's own reward:risk never degrades — it
fills at the entry or not at all. What degrades is the chance it fills for a good reason, because
price left the zone and a return is a failure rather than a retest. ``x`` is a *proxy for zone
consumption*, and ``m/(m+2)`` is that proxy normalised by the setup's own edge. The thing it
stands in for is a structure question ``core.structure`` could eventually answer directly.

**Section 3 is the answer; 1 and 2 are the setup.** Section 3 replays each rule against what the
candidate went on to do and charges it honestly: an order retired before a fill that would have
reached target costs the trade's full ``+mR``, one retired before a fill that would have stopped
saves ``+1R``, and one retired before a fill that never came is free budget. Net R is the verdict.

**What it measured, 2026-08-08, on 155 replayable decisions spanning 13 days.**

*The drift rule does nothing, and that is the result.* It fires on 11 of 155 rows and **all of
them are ``freed``** — entries that never traded. Zero saved, zero cost, net 0.00R, and that
holds across every threshold from 0.40R to 0.75R and every run length from 1 to 5. On this
sample a runaway-cancel has never once prevented a fill that would have happened. It is a
budget-release feature, not an edge feature, and it should be justified as one.

*The threshold is not the decision.* 0.40R through 0.75R fire on 8–11 rows with identical
outcomes, so ``m/(m+2)`` does not yet earn its derivation over a round number. Only 1.00R
separates, and it separates by firing on *more* (16) — the long tail is placement, not drift.

*The confound that inverted the first version.* A retest zone rests below the market by
construction, so ``x`` does not start at zero: median ``x0`` is 0.21R, p75 is 1.23R, and **41 of
142 entries were already ≥1R below the market when they were approved** (GOOG at 10.7R, entry
276.26 against a 326.83 spot). Before the split, those rows were credited to the drift rule and
made it look like it was doing 46 rows of work. Everything the rule appears to catch — all 3
saved and both cost — is in the placement bucket. **The measurement worth acting on is placement,
not staleness**, and ``proximity`` is already a scored term, so these were approved anyway.

*It also leans against the premise it was built to test.* Fills that had run ≥1.00R away first
resolved 2 of 6 to target; fills with under 0.25R of prior runaway resolved 4 of 45. That is 6
resolved rows and proves nothing, but "a return to a zone price has left is a failure" is not
what the data shows so far.

*Re-run before quoting any of it.* 66% of rows are unresolved and the ``freed`` rows have 8
forward bars at the median, so ``freed`` means "had not filled yet", not "never would".

**The sample bounds every number here and is printed first.** These decisions span days, not
months, so a large share of rows have not resolved and the censoring is not uniform: a rule that
retires early is judged against outcomes that are themselves unfinished. Read the unresolved
count before reading any net.

Run: ``uv run python scripts/probe_stale_entries.py`` (reads caches only; no network, no cost).
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median

from oracle.replay import (
    AMBIGUOUS,
    NOFILL,
    OPEN,
    RESOLVED,
    STOP,
    TARGET,
    UNREPLAYABLE,
    touches,
)
from probe_replay import (
    build_series_loader,
    classify,
    load_rows,
    realized_r,
)

DECISIONS = Path(__file__).resolve().parents[1] / "data" / "setups" / "decisions.jsonl"

# Consecutive closes beyond the line before a rule acts. The wick guard, and the reason this is
# a run-length rather than a wall-clock duration: closes are recomputable from bars, need no
# stored counter, and the nightly cadence already samples once a day.
CONSECUTIVE = 2

# Fixed thresholds swept beside the derived one, in R. Chosen to bracket it — the derived value
# lands between 0.43 and 0.71 across the reward:risk range this engine actually produces, so a
# flat rule that beat it would have to do so from outside that band.
FLAT = (0.4, 0.5, 0.6, 0.75, 1.0)

# Buckets for section 2, in R. The last is open-ended: an excursion past 1.0R means price covered
# the whole distance the trade was drawn to capture, without the entry ever trading.
BUCKETS = (0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True)
class Runaway:
    """How far price closed past an entry before the entry filled, bar by bar.

    ``excursions`` holds one value per *unfilled* bar, in R, in order. It is empty when the limit
    filled on the first bar after the decision, and it runs to the end of the series when the
    limit was never reached.
    """
    excursions: tuple[float, ...]
    filled: bool
    filled_on: date | None
    bars_before_fill: int

    @property
    def peak(self) -> float:
        return max(self.excursions, default=0.0)


def runaway(row: dict, series) -> Runaway | None:
    """Walk bars forward from the day *after* the decision, measuring closes past the entry.

    Strictly after, matching ``probe_replay.walk``: the sitting happened partway through its own
    session, so that bar's close was not knowable when the entry was chosen.

    The fill test and the excursion share one definition of which side the limit rests on, which
    is what makes the sign invariant hold — a long fills when the low touches the entry, so any
    bar that closed *below* a long's entry also filled, and every excursion recorded here is
    positive. ``None`` when risk is zero, because the denominator would be.
    """
    entry, stop = row["entry"], row["stop"]
    risk = abs(entry - stop)
    if not risk:
        return None

    long = row["direction"] == "long"
    decided = datetime.fromisoformat(row["decided_at"]).date()
    forward = [b for b in series.bars if b.date > decided]

    excursions: list[float] = []
    for i, bar in enumerate(forward):
        # A long rests below the market and fills when price trades down to it.
        if touches(bar, entry, above=not long):
            return Runaway(tuple(excursions), True, bar.date, i)
        excursions.append((bar.close - entry) / risk if long else (entry - bar.close) / risk)
    return Runaway(tuple(excursions), False, None, len(forward))


def initial_excursion(row: dict) -> float | None:
    """How far past the entry price already sat **when the decision was made**, in R.

    Not a detail — it is the confound that inverts section 3 if it is left out. A zone is a
    retest level, so every long entry rests below the market by construction and ``x`` does not
    start at zero. GOOG was approved on 2026-07-27 with an entry of 276.26 against a recorded
    spot of 326.83: 10.7R below the market before a single bar had passed. A rule reading
    absolute distance retires that order on day one, which is a true statement about the order
    and a false one about staleness.

    So the two have to be reported apart: this is *placement*, and ``peak - x0`` is *drift*.
    ``None`` for rows written before the ``price`` field existed, which cannot be assigned to
    either and must not be silently counted as zero.
    """
    spot = row.get("price")
    risk = abs(row["entry"] - row["stop"])
    if not spot or not risk:
        return None
    delta = spot - row["entry"] if row["direction"] == "long" else row["entry"] - spot
    return delta / risk


def derived_threshold(row: dict) -> float | None:
    """``m/(m+2)`` — the runaway at which chasing to spot would halve the advertised reward:risk.

    ``None`` on a degenerate zone. A zero-width stop makes R zero and m unbounded (SILVER's 0.05%
    stop is the live example), and a rule that fired at 1.0R on those would be reporting an
    artefact of a broken denominator as a finding.
    """
    risk = abs(row["entry"] - row["stop"])
    if not risk:
        return None
    m = abs(row["target"] - row["entry"]) / risk
    return m / (m + 2)


def retired_at(excursions, threshold: float, *, consecutive: int = CONSECUTIVE) -> int | None:
    """Index of the bar on which a rule would cancel, or ``None`` if it never does.

    The run must be unbroken. Counting any N closes beyond the line rather than N in a row would
    let a market that oscillates across the threshold retire on its third visit, which is the
    chop this rule exists to sit through.
    """
    run = 0
    for i, x in enumerate(excursions):
        run = run + 1 if x >= threshold else 0
        if run >= consecutive:
            return i
    return None


# ── the rules under test ────────────────────────────────────────────────────

def _rules():
    """``(label, threshold_fn)`` for each rule, derived first because it is the proposal."""
    return [("derived m/(m+2)", derived_threshold)] + [
        (f"flat {k:.2f}R", lambda _row, k=k: k) for k in FLAT
    ]


def _outcome_of(state: str, row: dict) -> tuple[str, float]:
    """What retiring before this row's fill would have been worth, and what to call it.

    Charged from the perspective of the *cancel*: a winner prevented is the trade's whole reward
    given up, a loser prevented is one R kept, and an entry that never traded costs nothing and
    releases the budget it was holding. ``AMBIGUOUS`` is charged as a stop, matching every rate
    in ``probe_replay``.
    """
    if state == TARGET:
        return "cost", -(realized_r(row, state) or 0.0)
    if state in (STOP, AMBIGUOUS):
        return "saved", 1.0
    if state == NOFILL:
        return "freed", 0.0
    return "unresolved", 0.0


# ── report ──────────────────────────────────────────────────────────────────

def _bucket(x: float) -> str:
    for edge in BUCKETS:
        if x < edge:
            return f"<{edge:.2f}"
    return f"≥{BUCKETS[-1]:.2f}"


def _quantiles(values: list[float]) -> str:
    ordered = sorted(values)
    p50, p75, p90 = (ordered[min(int(len(ordered) * q), len(ordered) - 1)] for q in (.5, .75, .9))
    return f"median {p50:6.2f}R   p75 {p75:6.2f}R   p90 {p90:6.2f}R   max {ordered[-1]:7.2f}R"


def _section_1(walks: list[tuple[dict, Runaway, str]]) -> None:
    print("\n── 1. placement vs drift: where did the entry start, and how much further did it go? ──")
    print("  x0    = distance from spot to the entry AT APPROVAL, in R (a retest zone starts >0)")
    print("  peak  = furthest close past the entry before any fill")
    print("  drift = peak - x0, the part that actually happened after the decision")

    peaks = [r.peak for _row, r, _s in walks]
    print(f"  {len(walks)} replayable | "
          f"{sum(1 for x in peaks if x <= 0)} never closed past their entry")
    print(f"  peak   {_quantiles(peaks)}")

    priced = [(row, r) for row, r, _s in walks if initial_excursion(row) is not None]
    if priced:
        x0s = [initial_excursion(row) or 0.0 for row, _r in priced]
        drifts = [r.peak - (initial_excursion(row) or 0.0) for row, r in priced]
        print(f"  x0     {_quantiles(x0s)}   ({len(priced)} rows carry a recorded spot)")
        print(f"  drift  {_quantiles(drifts)}")
        print(f"  entries already ≥1R below the market at approval: "
              f"{sum(1 for x in x0s if x >= 1.0)} of {len(priced)}")

    for name, keep in (("filled  ", True), ("unfilled", False)):
        group = sorted(r.peak for _row, r, _s in walks if r.filled is keep)
        if group:
            print(f"  {name}  n={len(group):3}  median peak {group[len(group) // 2]:6.2f}R  "
                  f"max {group[-1]:7.2f}R")


def _section_2(walks: list[tuple[dict, Runaway, str]]) -> None:
    print("\n── 2. for entries that DID fill, does prior runaway predict the outcome? ──")
    print("  the claim under test: a return to a zone price has left is a failure, not a retest")
    groups: dict[str, list[tuple[dict, str]]] = {}
    for row, r, state in walks:
        if r.filled:
            groups.setdefault(_bucket(r.peak), []).append((row, state))

    order = [f"<{e:.2f}" for e in BUCKETS] + [f"≥{BUCKETS[-1]:.2f}"]
    print(f"  {'peak runaway':>13} {'n':>4} {'target':>7} {'stop':>6} {'open':>6} "
          f"{'win rate':>9} {'median R':>9}")
    for label in order:
        rows = groups.get(label)
        if not rows:
            continue
        states = [s for _r, s in rows]
        resolved = [s for s in states if s in RESOLVED]
        wins = sum(1 for s in resolved if s == TARGET)
        rs = [value for r, s in rows if (value := realized_r(r, s)) is not None]
        rate = f"{wins / len(resolved):.0%}" if resolved else "—"
        mid = f"{median(rs):+.2f}" if rs else "—"
        print(f"  {label:>13} {len(rows):>4} {wins:>7} "
              f"{sum(1 for s in resolved if s != TARGET):>6} "
              f"{sum(1 for s in states if s == OPEN):>6} {rate:>9} {mid:>9}")
    print("  a win rate on 3 resolved rows is not a rate; read the n column first")


def _fires(walks, threshold_fn, consecutive: int):
    """Rows a rule would cancel, split by whether the order was ever near the market.

    ``at placement`` means ``x0`` was already past the threshold when the decision was made —
    the rule fires on the first bars it sees, and what it caught is an entry approved far below
    the market rather than one price walked away from. Reporting the two together was the first
    version of this table and it credited the drift rule with the placement rule's work.
    """
    placement, drift = [], []
    for row, r, state in walks:
        threshold = threshold_fn(row)
        if threshold is None:
            continue
        at = retired_at(r.excursions, threshold, consecutive=consecutive)
        if at is None:
            continue
        x0 = initial_excursion(row)
        bucket = drift if x0 is not None and x0 < threshold else placement
        bucket.append((row, state, at))
    return placement, drift


def _tally_line(label: str, fires: list) -> None:
    tally: Counter[str] = Counter()
    net = 0.0
    for row, state, _at in fires:
        kind, value = _outcome_of(state, row)
        tally[kind] += 1
        net += value
    bars = [at + 1 for _row, _s, at in fires]
    mid = f"{median(bars):.0f}" if bars else "—"
    print(f"  {label:>16} {len(fires):>6} {tally['freed']:>6} {tally['saved']:>6} "
          f"{tally['cost']:>5} {tally['unresolved']:>6} {net:>+7.2f}   {mid}")


def _section_3(walks: list[tuple[dict, Runaway, str]], consecutive: int) -> None:
    print(f"\n── 3. what would each rule have done? ({consecutive} consecutive closes beyond) ──")
    print("  split by section 1's distinction: an order the rule kills on sight was never near "
          "the\n  market, and crediting that to a staleness rule would be measuring the wrong "
          "feature.")
    for name, index in (("DRIFTED — the rule this probe is for", 1),
                        ("AT PLACEMENT — entry was already past the line on day one", 0)):
        print(f"\n  {name}")
        print(f"  {'rule':>16} {'fires':>6} {'freed':>6} {'saved':>6} {'cost':>5} {'unres':>6} "
              f"{'net R':>7}   median fire bar")
        for label, threshold_fn in _rules():
            _tally_line(label, _fires(walks, threshold_fn, consecutive)[index])
    print("\n  freed = never filled anyway (budget released, nothing given up)")
    print("  saved = prevented a fill that went on to stop   (+1R each)")
    print("  cost  = prevented a fill that went on to target (-mR each, at the advertised R:R)")
    print("  net R is censored by the unres column — those trades have not finished")


def _section_4(walks: list[tuple[dict, Runaway, str]], ) -> None:
    print("\n── 4. sensitivity: the derived rule, drifted rows only, at each run length ──")
    print(f"  {'consecutive':>16} {'fires':>6} {'freed':>6} {'saved':>6} {'cost':>5} {'unres':>6} "
          f"{'net R':>7}   median fire bar")
    for n in (1, 2, 3, 5):
        _tally_line(str(n), _fires(walks, derived_threshold, n)[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--decisions", type=Path, default=DECISIONS)
    parser.add_argument("--consecutive", type=int, default=CONSECUTIVE,
                        help=f"closes beyond the line before a rule fires (default {CONSECUTIVE})")
    args = parser.parse_args()

    rows = load_rows(args.decisions)
    load_series = build_series_loader()

    walks: list[tuple[dict, Runaway, str]] = []
    skipped: Counter[str] = Counter()
    for row in rows:
        result = classify(row, load_series)
        if result["state"] == UNREPLAYABLE:
            skipped["unreplayable"] += 1
            continue
        series, _ = load_series(row["asset"])
        found = runaway(row, series)
        if found is None:
            skipped["zero-width stop"] += 1
            continue
        walks.append((row, found, result["state"]))

    states = Counter(state for _row, _r, state in walks)
    unresolved = states[OPEN] + states[NOFILL]
    print(f"{len(rows)} decisions | {len(walks)} replayable | "
          f"skipped {dict(skipped) or 'none'}")
    print(f"outcomes: {dict(states)}")
    print(f"UNRESOLVED: {unresolved} of {len(walks)} ({unresolved / max(len(walks), 1):.0%}) "
          f"— every net below is censored by these")

    # The depth of the forward window is what `freed` really means. A NOFILL row is "had not
    # filled by the last bar", not "would never fill", and on a window this shallow those are
    # very different claims. Printed here rather than in a footnote because it caps section 3.
    depths = sorted(r.bars_before_fill for _row, r, state in walks if state == NOFILL)
    if depths:
        print(f"`freed` rows have only {depths[len(depths) // 2]:.0f} forward bars at the median "
              f"— they had not filled YET, which is weaker than never")

    _section_1(walks)
    _section_2(walks)
    _section_3(walks, args.consecutive)
    _section_4(walks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
