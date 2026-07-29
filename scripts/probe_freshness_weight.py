"""Whether the recorded decisions support re-weighting ``freshness`` — they do not, globally.

Free, local, re-runnable. Reads ``data/setups/decisions.jsonl`` only — no network, no LLM,
no price fetch. Nothing here mutates anything.

**Why this probe exists.** ``freshness`` separated approvals from negatives in two sessions
(docs/IMPROVEMENTS.md §4) while carrying the second-smallest weight of five, and raising
it was the one named pending scoring change. This was written to measure *how far* to raise
it rather than picking a number by argument — the rule §18 states for the ``collapse`` rep:
"needs measuring against §4's sidecar, not picking by argument".

**What it found instead, after the sample doubled on 2026-07-27: almost nothing in the scorer
is distinguishable from chance.** The composite ``score`` is 0.671 with a 95% interval of
[0.48, 0.84] — it includes 0.5. ``freshness`` is the single term whose interval clears chance,
at 0.727 [0.54, 0.88], and that is roughly *half* the 0.922 the first sitting showed. Every
per-timeframe cell now carries a '?'. Nothing is shipped off this probe.

**Corrected 2026-07-28: a good part of that was the pooling, not the scorer.** ``within_sitting``
below forms the U statistic inside each sitting and pools the counts, which is the standard
remedy for the confound §4 identified — cross-sitting pairs compare two different screens, and
§4 measured them at AUC 0.444. Dropping them costs 658 of 812 pairs and *improves* every
estimate::

                        same-sitting        pooled (invalid)
    score            0.688 [0.54,0.82]     0.568 [0.41,0.71]?
    freshness        0.779 [0.66,0.88]     0.636 [0.49,0.78]?
    trend_alignment  0.429 [0.29,0.57]?    0.475 [0.35,0.60]?

So ``score`` and ``freshness`` both clear chance once the labels are put on one scale, and the
pooled tables were *understating* the scorer rather than flattering it. ``trend_alignment``
still orders backwards on both. **This is still not a mandate to re-weight** — 154 pairs from
eight sittings, and the sittings themselves were score-ordered slices. It is the reason the
queue now draws a stratified sample (``oracle.queue``): the next sittings will not need
conditioning, and then these numbers can be believed rather than merely corrected.

**The per-sitting table is the reason, and it is a flaw in the measurement rather than in the
scorer.** A sitting is not a random sample: the queue is score-ordered and capped, so the first
sitting spans a wide score range and each later one works a narrower slice of the tail. A term
cannot order what barely varies. The only sitting whose interval clears chance (18:38, range
0.397-0.812) is also the only one with a wide range; the two that followed it spanned 0.540-
0.580 and 0.403-0.527 and came back at chance. Worse, the later sitting approved candidates
scoring *below* what the earlier one rejected, so the approval threshold is not fixed between
sittings and the pooled labels are not on one scale.

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
import random
from dataclasses import replace
from pathlib import Path
from statistics import median, pstdev

from core.rank import agreement_signal
from core.setups import (
    DEFAULT_WEIGHTS,
    SCORE_VERSION,
    SetupWeights,
    reward_risk_signal,
)

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


def _scored_rr(row: dict) -> float:
    """The reward:risk ``_score`` consumed for this row, whichever generation wrote it.

    ``SCORE_VERSION`` 6 re-pointed the term at ``reward_risk_from_price`` (§19d), so a v6 row
    carries both numbers and the scored one is the new field. Rows written before that only
    have ``reward_risk``, and that genuinely *was* the scored input at the time — falling back
    to it replays what happened rather than guessing, and no row is ever backfilled.
    """
    return row.get("reward_risk_from_price", row["reward_risk"])


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
        + weights.reward_risk * reward_risk_signal(_scored_rr(row))
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


# Fixed so two runs of this probe are comparable. A CI that moves when nothing changed would
# be read as a real shift the first time it happened.
BOOTSTRAP_SEED = 11
CELL_WIDTH = 21
BOOTSTRAP_ROUNDS = 20_000

# Fewest observations per side before an interval is worth printing at all. See ``cell``.
MIN_GROUP = 3

# Fewest same-sitting comparisons before a conditional interval is worth printing. Counted in
# *pairs* rather than rows because that is what the statistic is built from, and because the
# two diverge sharply: the 77-row file offers 812 pooled pairs and only 154 survive once
# cross-sitting ones are dropped. See ``stratified_cell``.
MIN_PAIRS = 12


def auc_ci(positive: list[float], negative: list[float]) -> tuple[float, float]:
    """95% bootstrap CI for ``auc``, resampling both groups independently.

    **Load-bearing, not decoration.** Every AUC in the first version of this probe was a bare
    point estimate on 10-vs-9, and a session that scored 0.856 was read as the scorer working.
    Its CI is [0.656, 1.000] — consistent with almost anything above chance. The next session
    came back 0.397 and the pooled estimate fell to 0.671 with a CI that *includes* 0.5. The
    interval is what distinguishes "measured" from "measured once".
    """
    if not positive or not negative:
        return (float("nan"), float("nan"))
    rng = random.Random(BOOTSTRAP_SEED)
    vals = sorted(
        auc([rng.choice(positive) for _ in positive], [rng.choice(negative) for _ in negative])
        for _ in range(BOOTSTRAP_ROUNDS)
    )
    return vals[int(0.025 * BOOTSTRAP_ROUNDS)], vals[int(0.975 * BOOTSTRAP_ROUNDS)]


def stratified_auc(groups: list[tuple[list[float], list[float]]]) -> float:
    """AUC over **same-sitting pairs only** — the pooled-within-stratum Mann-Whitney statistic.

    ``auc`` above compares every approval to every negative, which silently includes pairs
    drawn from different sittings. §4 measured that those pairs are not comparable: the 19:53
    sitting approved at a median score of 0.484 while 18:38 *rejected* at a median of 0.518,
    and ``AUC(later approvals > earlier negatives) = 0.444``. A cross-sitting pair therefore
    measures how the two screens differed, not how the two candidates did.

    Conditioning is the standard remedy for exactly this confound: form the U statistic inside
    each sitting, where the anchor was fixed, and pool the counts rather than the ratios.
    Pooling ratios would weight a 1-versus-1 sitting as heavily as a 10-versus-9 one.

    It also partitions on ``score_version`` for free, which §4 requires and pooling breaks:
    a sitting is one run of one build, so no sitting spans two scoring generations.

    The price is power, and it is steep — most pairs in this file are cross-sitting, and the
    caller prints how many survive. A wide interval here is the honest reading of what 77
    hand-entered decisions actually support.
    """
    wins = total = 0.0
    for positive, negative in groups:
        if not positive or not negative:
            continue
        wins += sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positive for n in negative)
        total += len(positive) * len(negative)
    return wins / total if total else float("nan")


def stratified_auc_ci(groups: list[tuple[list[float], list[float]]]) -> tuple[float, float]:
    """95% bootstrap CI for ``stratified_auc``, resampling **within** each sitting.

    Resampling across sittings would rebuild the pooling this statistic exists to avoid.
    """
    usable = [(p, n) for p, n in groups if p and n]
    if not usable:
        return (float("nan"), float("nan"))
    rng = random.Random(BOOTSTRAP_SEED)
    vals = sorted(
        stratified_auc([([rng.choice(p) for _ in p], [rng.choice(n) for _ in n])
                        for p, n in usable])
        for _ in range(BOOTSTRAP_ROUNDS)
    )
    return vals[int(0.025 * BOOTSTRAP_ROUNDS)], vals[int(0.975 * BOOTSTRAP_ROUNDS)]


def stratified_cell(groups: list[tuple[list[float], list[float]]]) -> str:
    """``AUC [lo, hi]`` over same-sitting pairs, marked when the interval spans chance."""
    pairs = sum(len(p) * len(n) for p, n in groups)
    if pairs < MIN_PAIRS:
        return f"pairs too few ({pairs})".rjust(CELL_WIDTH)
    point = stratified_auc(groups)
    lo, hi = stratified_auc_ci(groups)
    marker = "?" if lo <= 0.5 <= hi else " "
    return f"{point:.3f} [{lo:.2f},{hi:.2f}]{marker}".rjust(CELL_WIDTH)


def cell(positive: list[float], negative: list[float]) -> str:
    """``AUC [lo, hi]``, with the interval marked when it spans chance.

    Refuses to print an interval below ``MIN_GROUP`` per side. Bootstrapping one observation
    resamples the same value every round, so a 1-vs-1 cell yields ``[0.00, 0.00]`` — a *zero
    width* interval that renders as the most confident result in the table while resting on two
    data points. Printing the n instead is the honest version of "cannot say".
    """
    if len(positive) < MIN_GROUP or len(negative) < MIN_GROUP:
        return f"n too small ({len(positive)}v{len(negative)})".rjust(CELL_WIDTH)
    point = auc(positive, negative)
    lo, hi = auc_ci(positive, negative)
    marker = "?" if lo <= 0.5 <= hi else " "
    return f"{point:.3f} [{lo:.2f},{hi:.2f}]{marker}".rjust(CELL_WIDTH)


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
    ("reward_risk", "reward_risk", lambda r: reward_risk_signal(r["reward_risk"])),
    ("rr_from_price", "reward_risk_from_price", lambda r: reward_risk_signal(r["reward_risk_from_price"])),
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
    print("    '?' marks an interval that spans 0.5 — that term is not distinguishable from"
          " chance.\n")
    print(f"  {'timeframe':>9} {'n':>7}  " + "".join(f"{name:>{CELL_WIDTH}}" for name, _, _ in TERMS))
    for timeframe in ("weekly", "daily"):
        cells = []
        for _, field, get in TERMS:
            sub = [r for r in decided if r["zone_timeframe"] == timeframe and field in r]
            cells.append(cell([get(r) for r in sub if r["decision"] in POSITIVE],
                              [get(r) for r in sub if r["decision"] in NEGATIVE]))
        sub = [r for r in decided if r["zone_timeframe"] == timeframe]
        pn = f"{sum(1 for r in sub if r['decision'] in POSITIVE)}v" \
             f"{sum(1 for r in sub if r['decision'] in NEGATIVE)}"
        print(f"  {timeframe:>9} {pn:>7}  " + "".join(cells))


def by_sitting(path: Path) -> None:
    """The same terms, one row per triage sitting — the control the pooled tables cannot be.

    **Why this matters more than it looks.** ``decided_at`` is stamped once per session, before
    the loop, so it identifies a sitting exactly. And a sitting is not a random sample of the
    queue: the queue is score-ordered and capped, so the first sitting sees a wide score range
    and each later one sees a narrower slice of the tail. A term cannot order what barely
    varies, so AUC falls toward 0.5 as you work down the queue *even if the scorer is fine*.

    Measured 2026-07-27: the first v5 sitting spanned scores 0.377-0.906 and scored 0.856; the
    second spanned 0.403-0.580 and scored 0.397. Pooling them also assumes the approval
    threshold is stable between sittings, which the score ranges suggest it is not — the second
    sitting approved candidates scoring below what the first had rejected. Read the per-sitting
    rows before believing any pooled number.
    """
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    decided = [r for r in rows if r["decision"] in POSITIVE + NEGATIVE]
    sittings = sorted({r["decided_at"][:16] for r in decided})

    print("=== per-sitting AUC (a sitting is one score-ordered slice, not a random sample) ===\n")
    print(f"  {'sitting':>16} {'n':>7} {'score range':>15}  "
          + "".join(f"{name:>{CELL_WIDTH}}" for name in ("score", "freshness", "approach")))
    for when in sittings:
        sub = [r for r in decided if r["decided_at"][:16] == when]
        p = [r for r in sub if r["decision"] in POSITIVE]
        n = [r for r in sub if r["decision"] in NEGATIVE]
        if not p or not n:
            continue
        scores = [r["score"] for r in sub]
        cells = [cell([r["score"] for r in p], [r["score"] for r in n]),
                 cell([r["freshness"] for r in p], [r["freshness"] for r in n])]
        cells.append(cell([r["approach"] for r in p if "approach" in r],
                          [r["approach"] for r in n if "approach" in r]))
        print(f"  {when:>16} {f'{len(p)}v{len(n)}':>7} "
              f"{f'{min(scores):.3f}-{max(scores):.3f}':>15}  " + "".join(cells))
    print()

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


def within_sitting(path: Path) -> None:
    """Every term's AUC over same-sitting pairs only — the valid version of the pooled table.

    The pooled numbers at the top of this probe answer "does a term separate approvals from
    negatives", and §4 established that they cannot: the two groups were labelled against
    different screens. This table asks the answerable question instead — *within one screen*,
    did the term order the candidates the way the human did.

    Read the surviving-pair count first. It is the cost of the correction and it is the whole
    story about how much this file can support.
    """
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    decided = [r for r in rows if r["decision"] in POSITIVE + NEGATIVE]
    sittings = sorted({r["decided_at"] for r in decided})

    # A sitting is one run of one build, so conditioning on it partitions score_version too.
    # Asserted rather than assumed: if it ever stops holding, the `score` row below silently
    # starts comparing two scales, which is the exact failure §4 forbids.
    for when in sittings:
        versions = {r.get("score_version") for r in decided if r["decided_at"] == when}
        assert len(versions) == 1, f"sitting {when} spans score versions {versions}"

    pooled_pairs = (sum(1 for r in decided if r["decision"] in POSITIVE)
                    * sum(1 for r in decided if r["decision"] in NEGATIVE))

    def groups_for(field, get):
        out = []
        for when in sittings:
            sub = [r for r in decided if r["decided_at"] == when and field in r]
            out.append(([get(r) for r in sub if r["decision"] in POSITIVE],
                        [get(r) for r in sub if r["decision"] in NEGATIVE]))
        return out

    kept = sum(len(p) * len(n) for p, n in groups_for("score", lambda r: r["score"]))
    print("=== per-term AUC over SAME-SITTING pairs only (the valid pooling) ===\n")
    print(f"    {len(sittings)} sittings. Of {pooled_pairs} approval-vs-negative pairs, "
          f"{kept} are within a sitting")
    print(f"    and {pooled_pairs - kept} span two screens and are dropped — §4 measured those "
          f"at AUC 0.444,")
    print("    i.e. actively misleading rather than merely noisy.\n")
    print("    '?' marks an interval spanning 0.5. Expect several: this is the honest n.\n")

    print(f"  {'term':>16}  {'same-sitting AUC':>{CELL_WIDTH}}  {'pooled (invalid)':>{CELL_WIDTH}}")
    reported = [("score", "score", lambda r: r["score"])] + list(TERMS)
    for name, field, get in reported:
        groups = groups_for(field, get)
        sub = [r for r in decided if field in r]
        pooled = cell([get(r) for r in sub if r["decision"] in POSITIVE],
                      [get(r) for r in sub if r["decision"] in NEGATIVE])
        print(f"  {name:>16}  {stratified_cell(groups)}  {pooled}")
    print()


def main() -> None:
    by_timeframe(DECISIONS)
    print()
    by_sitting(DECISIONS)
    within_sitting(DECISIONS)

    rows = load(DECISIONS, version=SCORE_VERSION)
    pos = [r for r in rows if r["decision"] in POSITIVE]
    neg = [r for r in rows if r["decision"] in NEGATIVE]
    later = [r for r in rows if r["decision"] == "later"]

    # A version bump empties this bucket by design, and everything below replays the *shipped*
    # scorer against rows judged under it — so on an empty cohort it would divide by zero while
    # looking like a result. Saying so is the whole point: the tables above are the correction
    # applied to history, and this one cannot exist until a sitting happens on the new scale.
    if not pos or not neg:
        print(f"=== the weight sweep, on score_version {SCORE_VERSION} only ===\n")
        print(f"  {len(rows)} rows at score_version {SCORE_VERSION} "
              f"({len(pos)} approved, {len(neg)} rejected/archived, {len(later)} later).")
        print("  Not enough to sweep. This is expected immediately after a version bump —")
        print("  older rows are on a different scale and pooling them is what §4 forbids.")
        print("  Run `uv run setups` for one sitting, then re-run this probe.\n")
        return

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
        print(f"  {term:16s} {cell([get(r) for r in pos], [get(r) for r in neg])}")

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
