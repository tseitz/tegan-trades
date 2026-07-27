"""Whether the recorded decisions support re-weighting ``freshness`` — they do not, globally.

Free, local, re-runnable. Reads ``data/setups/decisions.jsonl`` only — no network, no LLM,
no price fetch. Nothing here mutates anything.

**Why this probe exists.** ``freshness`` separated approvals from negatives in two sessions
(docs/IMPROVEMENTS.md §4, §16) while carrying the second-smallest weight of five, and raising
it was the one named pending scoring change. This was written to measure *how far* to raise
it rather than picking a number by argument — the rule §18 states for the ``collapse`` rep:
"needs measuring against §4's sidecar, not picking by argument".

**What it found instead: the answer depends on the zone's timeframe, and the queue has one
global weight vector for both.** ``approach`` orders the weekly population and is backwards on
the daily one; ``freshness`` orders the daily population and is near-chance on the weekly one.
The mechanism is visible in the spread the script prints — daily zones are all *near* price,
so ``approach`` barely varies there and cannot discriminate what doesn't vary; weekly
rejections are the unreachable ones from §19, which are *fresh* and heavily agreed. So a
global raise would help one population and hurt the other, which is why nothing is shipped
off this probe. See §20.

**The statistic is AUC, not the mean gap.** Every term is on a different scale and the queue
is consumed as an *ordering*, so what matters is the probability that a randomly chosen
approval outranks a randomly chosen negative — exactly the Mann-Whitney U statistic. 0.5 is a
coin flip, 1.0 is perfect separation, and **below 0.5 means the term is ordering backwards**.
A mean gap can look healthy while the distributions interleave, which is how §4's first
correlation read as "no signal" when it was really two terms cancelling.

**Read every number here as a shape, not a fit.** n is small — the script prints each cell's
sample size, and several are under 15. Two known contaminants, both stated at the point of
use: ``archived`` is mixed evidence (part asset disinterest, part staleness), and timeframe is
confounded with session because v2-v4 were weekly-only runs.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from statistics import median, pstdev

from core.rank import agreement_signal
from core.setups import DEFAULT_WEIGHTS, RR_SATURATION, SCORE_VERSION, SetupWeights

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS = REPO_ROOT / "data" / "setups" / "decisions.jsonl"

# Verdicts that are evidence about the *setup*. ``later`` is excluded because it is explicitly
# reversible ("the zone is fine, price isn't there yet") — it is a statement about timing, not
# about quality, and scoring it as either label would invent a judgement that wasn't made.
POSITIVE = ("approved",)
NEGATIVE = ("rejected", "archived")


def load(path: Path, *, version: int) -> list[dict]:
    """Decision rows on one score scale.

    Partitioning on ``score_version`` is mandatory rather than tidy: weights and terms have
    changed five times, so pooling versions compares numbers that were never on the same
    scale. See the ``SCORE_VERSION`` comment in ``core.setups``.
    """
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("score_version") == version]


def rescore(row: dict, weights: SetupWeights) -> float:
    """``core.setups._score`` recomputed from the recorded terms.

    Deliberately re-derived here rather than imported: the private helper takes the live
    candidate's fields, and the whole point is to replay *what was recorded at decision time*.
    ``agreement`` is stored as a raw head count and ``reward_risk`` as a raw ratio, so both
    need their signal transform reapplied — storing the post-transform value instead would
    have made the sidecar unable to answer questions about the transform itself.
    """
    return (
        weights.approach * row["approach"]
        + weights.reward_risk * min(row["reward_risk"] / RR_SATURATION, 1.0)
        + weights.agreement * agreement_signal(int(row["agreement"]))
        + weights.freshness * row["freshness"]
        + weights.trend_alignment * row["trend_alignment"]
    )


def reweighted(base: SetupWeights, freshness: float) -> SetupWeights:
    """``base`` with ``freshness`` set deliberately and every other term scaled pro rata.

    This is what "change one term and re-measure" has to mean once weights sum to 1: the other
    four cannot stay fixed, so the choice is which of them absorbs the difference. Scaling all
    four preserves their order and their ratios, so no *second* deliberate judgement is smuggled
    in alongside the one under test. Taking the difference out of ``reward_risk`` alone would be
    two changes, however defensible each is on its own.
    """
    others = base.approach + base.agreement + base.reward_risk + base.trend_alignment
    scale = (1.0 - freshness) / others
    return replace(
        base,
        approach=base.approach * scale,
        reward_risk=base.reward_risk * scale,
        agreement=base.agreement * scale,
        trend_alignment=base.trend_alignment * scale,
        freshness=freshness,
    )


def auc(positive: list[float], negative: list[float]) -> float:
    """P(a random positive outranks a random negative); ties count as half."""
    if not positive or not negative:
        return float("nan")
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0
        for p in positive
        for n in negative
    )
    return wins / (len(positive) * len(negative))


def inversions(rows: list[dict], scores: dict[str, float]) -> list[tuple[dict, dict]]:
    """Negative-above-positive pairs, worst-ranked first. The AUC's individual failures."""
    pos = [r for r in rows if r["decision"] in POSITIVE]
    neg = [r for r in rows if r["decision"] in NEGATIVE]
    bad = [(n, p) for n in neg for p in pos if scores[n["candidate_key"]] > scores[p["candidate_key"]]]
    return sorted(bad, key=lambda pair: -scores[pair[0]["candidate_key"]])


# Each term's signal transform, plus the raw sidecar field it needs. The field is named
# explicitly because the sidecar has changed shape four times and older rows are genuinely
# missing columns — v2 recorded ``proximity`` instead of ``approach`` and omitted
# ``reward_risk`` entirely (§4a, §4d). Those rows are *dropped from that term's cell* and the
# reduced n is printed, rather than being backfilled: a re-run yields today's value, not the
# decision-time one, which is the trap §4a spells out.
TERMS = (
    ("approach", "approach", lambda r: r["approach"]),
    ("freshness", "freshness", lambda r: r["freshness"]),
    ("agreement", "agreement", lambda r: agreement_signal(int(r["agreement"]))),
    ("reward_risk", "reward_risk", lambda r: min(r["reward_risk"] / RR_SATURATION, 1.0)),
    ("trend_alignment", "trend_alignment", lambda r: r["trend_alignment"]),
)


def by_timeframe(path: Path) -> None:
    """Per-term AUC on the weekly and daily populations separately — the headline result.

    Pools **every** score version, which is sound here and is not for the composite score:
    a term is the same measurement on any version, whereas ``score`` has been on five scales.
    ``approach`` is the one exception and is handled — v2 recorded ``proximity`` and silently
    omitted ``depth`` (§4a), so v2 rows are dropped from that row of the table rather than
    being reconstructed from a residual nobody can decompose.
    """
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    decided = [r for r in rows if r["decision"] in POSITIVE + NEGATIVE]

    print("=== per-term AUC by zone timeframe (all sessions pooled; >0.5 orders correctly) ===")
    print("    CAVEAT: timeframe is confounded with session — v2-v4 were weekly-only runs, so")
    print("    'weekly' is mostly those and 'daily' is mostly v5. v5 splits the same way")
    print("    internally, but its weekly arm has 1 approval, so that control is thin.\n")
    print(f"  {'timeframe':>9} {'n':>7}  " + "".join(f"{name:>16}" for name, _, _ in TERMS))
    for timeframe in ("weekly", "daily"):
        cells = []
        for _, field, get in TERMS:
            sub = [r for r in decided if r["zone_timeframe"] == timeframe and field in r]
            p = [get(r) for r in sub if r["decision"] in POSITIVE]
            n = [get(r) for r in sub if r["decision"] in NEGATIVE]
            cells.append(f"{auc(p, n):>10.3f} {len(p)}v{len(n):<4}")
        sub = [r for r in decided if r["zone_timeframe"] == timeframe]
        pn = f"{sum(1 for r in sub if r['decision'] in POSITIVE)}v" \
             f"{sum(1 for r in sub if r['decision'] in NEGATIVE)}"
        print(f"  {timeframe:>9} {pn:>7}  " + "".join(cells))

    print("\n=== term spread by timeframe — a term that doesn't vary cannot discriminate ===")
    for timeframe in ("weekly", "daily"):
        for name, get in (("approach", lambda r: r["approach"]),
                          ("freshness", lambda r: r["freshness"])):
            vals = [get(r) for r in decided
                    if r["zone_timeframe"] == timeframe and (name != "approach" or "approach" in r)]
            print(f"  {timeframe:>7} {name:11s} n={len(vals):2d} "
                  f"min={min(vals):.3f} med={median(vals):.3f} max={max(vals):.3f} "
                  f"sd={pstdev(vals):.3f}")
    print()


def main() -> None:
    by_timeframe(DECISIONS)

    rows = load(DECISIONS, version=SCORE_VERSION)
    pos = [r for r in rows if r["decision"] in POSITIVE]
    neg = [r for r in rows if r["decision"] in NEGATIVE]
    later = [r for r in rows if r["decision"] == "later"]

    print(f"=== the weight sweep, on score_version {SCORE_VERSION} only ===")
    print(f"score_version {SCORE_VERSION}: {len(rows)} rows "
          f"({len(pos)} approved, {len(neg)} rejected/archived, {len(later)} later — "
          f"{len(pos) * len(neg)} pairs)")
    print("NOTE: 'archived' is mixed evidence — part asset disinterest, part staleness "
          "(§4). It is counted as negative here; see the split below.")
    print("NOTE: this table pools both timeframes, so a single optimum here is exactly the")
    print("      cancellation the table above warns about. It is kept to show the curve has")
    print("      no interior maximum — the data's answer is 'weight freshness at 1.0', which")
    print("      is degenerate and is the tell that one weight vector is fitting two"
          " populations.\n")

    baseline = {r["candidate_key"]: rescore(r, DEFAULT_WEIGHTS) for r in rows}
    stored = {r["candidate_key"]: r["score"] for r in rows}
    drift = max(abs(baseline[k] - stored[k]) for k in stored)
    print(f"replay check — max |recomputed - stored| = {drift:.2e} "
          f"({'OK' if drift < 1e-9 else 'MISMATCH: the replay is not the shipped scorer'})\n")

    print(f"{'w_fresh':>8} {'AUC':>7} {'mean+':>7} {'mean-':>7} {'gap':>7} {'inv':>4}")
    for step in range(0, 13):
        w = 0.15 + step * 0.05
        if w > 0.75:
            break
        weights = reweighted(DEFAULT_WEIGHTS, w)
        scores = {r["candidate_key"]: rescore(r, weights) for r in rows}
        p = [scores[r["candidate_key"]] for r in pos]
        n = [scores[r["candidate_key"]] for r in neg]
        mark = "  <- current" if abs(w - DEFAULT_WEIGHTS.freshness) < 1e-9 else ""
        print(f"{w:8.2f} {auc(p, n):7.3f} {sum(p)/len(p):7.3f} {sum(n)/len(n):7.3f} "
              f"{sum(p)/len(p) - sum(n)/len(n):7.3f} {len(inversions(rows, scores)):4d}{mark}")

    print("\nper-term AUC in isolation (which terms carry the ordering at all):")
    for term, _, get in TERMS:
        print(f"  {term:16s} {auc([get(r) for r in pos], [get(r) for r in neg]):.3f}")

    print("\nnegatives split by verdict (archived is the mixed bucket):")
    for verdict in ("rejected", "archived"):
        sub = [r for r in rows if r["decision"] == verdict]
        if sub:
            print(f"  {verdict:9s} n={len(sub):2d}  "
                  f"AUC vs approved {auc([baseline[r['candidate_key']] for r in pos], [baseline[r['candidate_key']] for r in sub]):.3f}")

    print("\ninversions under current weights (negative outranking an approval):")
    for n, p in inversions(rows, baseline)[:6]:
        print(f"  {n['decision']:8s} {n['asset']:7s} {baseline[n['candidate_key']]:.3f}"
              f"  >  approved {p['asset']:7s} {baseline[p['candidate_key']]:.3f}")


if __name__ == "__main__":
    main()
