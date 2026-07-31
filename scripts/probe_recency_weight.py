"""Whether discounting old voices beats counting heads — §11's residual, measured.

Free, local, re-runnable. Reads ``data/setups/decisions.jsonl`` and ``data/theses/`` only —
no network, no LLM, no price fetch. Nothing here mutates anything.

**Why this probe exists.** ``agreement`` is a head count: ``agreement_signal(len(views))``,
where ``views`` is the latest statement per person on a zone. §11 observed that one ETH
candidate's seven supporters spanned 2026-01-20 to 2026-07-22, so a 186-day-old view was
counted equally with one from three days prior, and asked whether weighting each voice by
recency orders candidates better than counting them.

**It could not be asked until now, and the reason is the interesting part.** The question is
only meaningful if head-counting is itself a real signal — there is no point improving a term
that does not work. Until 2026-07-30 ``agreement`` spanned chance over same-sitting pairs, so
any "improvement" would have been noise beating noise. It now clears chance at 0.672
[0.56, 0.77] (``probe_freshness_weight.py``, 203 within-sitting pairs), which is what made a
baseline to beat exist.

## What it found — the term improves, the score does not

**§11's hypothesis is right about the term.** Discounting each voice by its age at decision
time orders candidates better than counting heads, and the gain is not marginal: at a 7-day
half-life the agreement term goes 0.672 -> 0.776, a paired **+0.103 [+0.020, +0.195]**. The
interval clears zero, it survives dropping the negatives that were rejected *for* being stale
(+0.099 [+0.013, +0.193]), and it stays clear of zero down to a 90-day half-life. The curve is
monotone — shorter half-life, better ordering — with no interior optimum inside the swept
range, so this measures a direction rather than a constant.

**And it buys nothing in the composite.** Swapping the same variant into the shipped score
moves it by **-0.038 [-0.097, +0.016]** at 7 days; every composite delta in the sweep spans
zero, and the short half-lives that help the term most are the ones that hurt the score. So
the finding does *not* license editing ``core.setups._score``.

**The reason is redundancy, and it is worse than this entry needed.** ``agreement`` already
correlates with ``freshness`` at **r = +0.771 as a bare head count** — recency weighting only
raises it to +0.78 (7d) or +0.83 (90d). The two terms were never independent: a zone more
people are on tends to be a zone somebody is on *recently*. The composite spends
``agreement`` 0.20 + ``freshness`` 0.15 = **0.35 of its weight on two views of one
quantity**, which is why handing it a third view changes nothing. Making the agreement term
sharper cannot help until that overlap is resolved, and that is a §4 re-weight question — the
one §4 refuses to answer without a mandate — not something to fix from here.

**What that leaves §11.** The honest reading is that the corroboration §11 wanted to
discount is *already* discounted, twice over, and the remaining question is not "should old
voices count less" (measured: yes) but "should the scorer carry both terms at all". A
recency-weighted agreement is the better single term of the two; whether it *replaces*
``freshness`` rather than joining it is the experiment worth running next, and it needs the
re-weight §4 gates.

## Reading these numbers

**The join is a reconstruction, not a backfill, and the probe refuses to run without proving
it.** The sidecar records ``agreement``, ``people`` and ``newest_at``, but not each view's
date, so per-voice ages have to come from ``data/theses/``. That is legitimate only because a
thesis's ``source.published_at`` is *immutable* — unlike ``score``, re-reading it cannot yield
today's value, which is the trap ``oracle.decisions`` spells out. It is proved rather than
asserted: every row's reconstructed ``people`` set, ``newest_at`` and head count must match
what was recorded, and a row that disagrees is dropped and counted. All 124 rows reconstructed
exactly on 2026-07-31; a future drop is a signal the corpus moved under the sidecar.

**Age is measured from ``decided_at``, never from today.** Anchoring on the current date would
make every re-run produce different numbers for decisions that have not changed, which is the
same unreproducibility that makes backfilling wrong.

**The sweep contains the baseline as a limit.** As the half-life grows every voice's weight
approaches 1.0 and the effective count approaches the head count, so ``h = inf`` is exactly the
shipped term. The sweep is therefore a continuum with the thing it is being compared against at
one end, which is what makes the monotone trend readable as a direction.

**The paired deltas are the test, not the two AUC columns.** Both columns' intervals overlap
the baseline's heavily, and on independently-computed intervals that reads as "no difference".
It is not what it means here: the two statistics share every row, so they are strongly
correlated and their *difference* is far better determined than either. ``paired_delta_ci``
resamples once and evaluates both on that resample, which is why it can separate what the
columns cannot.

**The statistic and its bootstrap seed are imported from ``probe_freshness_weight``** rather
than re-implemented, so the AUCs here are directly comparable with the ones there. Two probes
with independently-written Mann-Whitney code and different seeds would produce numbers that
differ for reasons nobody could attribute.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from core.rank import agreement_signal, parse_date
from core.setups import (
    DEFAULT_HALF_LIFE,
    DEFAULT_WEIGHTS,
    freshness_signal,
    reward_risk_signal,
)
from probe_freshness_weight import (
    BOOTSTRAP_ROUNDS,
    BOOTSTRAP_SEED,
    CELL_WIDTH,
    NEGATIVE,
    POSITIVE,
    _scored_rr,
    stratified_cell,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS = REPO_ROOT / "data" / "setups" / "decisions.jsonl"
THESES = REPO_ROOT / "data" / "theses"

# Half-lives to sweep, in days. The top of the range is deliberately far past anything
# defensible as a view's shelf life: the point is to show the curve has no interior maximum,
# and a sweep that stopped at 90 could not distinguish "no optimum" from "optimum above 90".
HALF_LIVES = (7, 14, 21, 30, 60, 90, 180, 360)

# Rejection reasons that are *about* age. ``freshness`` is suspected of partly measuring the
# scorer agreeing with the human about staleness rather than fresh setups performing better
# (see ``oracle.decisions``), and a recency-weighted agreement term inherits that circularity by
# construction — if the human rejected it for being old, of course the old-discounting term
# ranks it low. Excluded as a sensitivity arm rather than a correction, because the honest
# reading is "the result should survive dropping these", not "these were never evidence".
AGE_REASONS = frozenset({"stale"})


def thesis_index(root: Path) -> dict[str, tuple[str, str, str]]:
    """``thesis id -> (person, published_at, timeframe)`` over the whole thesis store.

    Only the three immutable fields are taken. Everything else on a thesis — score, status,
    the extraction metadata — either moves or is irrelevant here, and taking a moving field is
    how a reconstruction turns into a backfill.
    """
    index: dict[str, tuple[str, str, str]] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for thesis in payload.get("theses") or []:
            source = thesis.get("source") or {}
            person, published = source.get("person"), source.get("published_at")
            if thesis.get("id") and person and published:
                index[thesis["id"]] = (person, published, thesis.get("timeframe") or "")
    return index


def views_for(row: dict, index: dict[str, tuple[str, str, str]]) -> dict[str, tuple[str, str]]:
    """``person -> (published_at, timeframe)``, the latest statement per person.

    This reproduces ``core.setups.collapse``'s ``views`` exactly: the sidecar's ``thesis_ids``
    are that group's members, and ``collapse`` keeps the newest date per person. Returning the
    empty dict means the group could not be rebuilt, and the caller drops the row.
    """
    latest: dict[str, tuple[str, str]] = {}
    for thesis_id in row.get("thesis_ids") or []:
        entry = index.get(thesis_id)
        if entry is None:
            return {}
        person, published, timeframe = entry
        if person not in latest or published > latest[person][0]:
            latest[person] = (published, timeframe)
    return latest


def reconstructed(rows: list[dict], index) -> tuple[list[dict], Counter]:
    """Rows whose rebuilt view set provably matches what the decision recorded.

    The three checks are the whole licence for this probe. ``people`` proves the right voices
    were found, ``newest_at`` proves their dates are the dates the decision saw, and the head
    count proves none were silently merged or dropped. A row failing any of them is evidence
    the corpus moved — a re-distill, a thesis id change — and its per-voice ages would be
    describing a different group than the one that was judged.
    """
    kept, dropped = [], Counter()
    for row in rows:
        views = views_for(row, index)
        if not views:
            dropped["thesis id not in the store"] += 1
        elif set(views) != set(row.get("people") or []):
            dropped["people set disagrees"] += 1
        elif max(v[0] for v in views.values()) != row.get("newest_at"):
            dropped["newest_at disagrees"] += 1
        elif len(views) != row.get("agreement"):
            dropped["head count disagrees"] += 1
        else:
            kept.append({**row, "_views": views})
    return kept, dropped


def effective_count(row: dict, half_life: int | None) -> float:
    """Voices, each discounted by its age *at the time of the decision*.

    ``half_life=None`` is the shipped behaviour — every voice worth 1.0, i.e. the head count —
    and is what the sweep converges to as the half-life grows.

    A voice whose date will not parse contributes 0.0 rather than 1.0. That is the same choice
    ``core.rank.recency_signal`` makes and for the same reason: an undated view cannot be
    claimed to be recent, and defaulting it to full weight would let missing data look like
    corroboration.
    """
    if half_life is None:
        return float(len(row["_views"]))
    decided = parse_date(row["decided_at"][:10])
    total = 0.0
    for published, _ in row["_views"].values():
        at = parse_date(published)
        if at is not None and decided is not None:
            total += freshness_signal((decided - at).days, half_life)
    return total


def shipped_half_life_count(row: dict) -> float:
    """Voices discounted by ``HalfLife``, each on *its own* stated timeframe.

    The one variant that costs no new constant: ``core.setups`` already ages a view on the
    timeframe it was stated for — a scalp call at 3 days, a macro call at 360 — and this asks
    whether that existing curve, applied per voice instead of only to the freshest one, orders
    better. A view with no usable timeframe keeps full weight here rather than scoring zero,
    because the missing field is the *label*, not the date.
    """
    decided = parse_date(row["decided_at"][:10])
    total = 0.0
    for published, timeframe in row["_views"].values():
        at = parse_date(published)
        if at is None or decided is None:
            continue
        window = DEFAULT_HALF_LIFE.days_for(timeframe)
        total += 1.0 if window is None else freshness_signal((decided - at).days, window)
    return total


def rescore(row: dict, agreement_value: float) -> float:
    """The composite, with the agreement term replaced and every other term as recorded.

    Mirrors ``probe_freshness_weight.rescore`` — the terms are replayed from the sidecar rather
    than recomputed from today's candidate — but takes the agreement *signal* already
    transformed, since the whole question is what that transform should consume.
    """
    return (
        DEFAULT_WEIGHTS.approach * row["approach"]
        + DEFAULT_WEIGHTS.reward_risk * reward_risk_signal(_scored_rr(row))
        + DEFAULT_WEIGHTS.agreement * agreement_value
        + DEFAULT_WEIGHTS.freshness * row["freshness"]
        + DEFAULT_WEIGHTS.trend_alignment * row["trend_alignment"]
    )


def row_groups(rows: list[dict]) -> list[tuple[list[dict], list[dict]]]:
    """Per-sitting ``(approval rows, negative rows)`` — resampled as *rows*, not as values.

    The paired test below needs both statistics computed on the same resample, which is only
    possible if the bootstrap draws rows and evaluates the two value functions afterwards.
    """
    out = []
    for when in sorted({r["decided_at"] for r in rows}):
        sub = [r for r in rows if r["decided_at"] == when]
        out.append(([r for r in sub if r["decision"] in POSITIVE],
                    [r for r in sub if r["decision"] in NEGATIVE]))
    return [(p, n) for p, n in out if p and n]


def _paired_auc_delta(groups: list[tuple[list[tuple], list[tuple]]]) -> float:
    """``AUC(variant) - AUC(baseline)`` where each row is a precomputed ``(variant, baseline)``.

    Both statistics are formed in one pass over the same pairs, which is what makes the
    difference paired rather than a subtraction of two independent numbers.
    """
    wins = base = total = 0.0
    for positive, negative in groups:
        for pv, pb in positive:
            for nv, nb in negative:
                wins += 1.0 if pv > nv else 0.5 if pv == nv else 0.0
                base += 1.0 if pb > nb else 0.5 if pb == nb else 0.0
        total += len(positive) * len(negative)
    return (wins - base) / total if total else float("nan")


def paired_delta_ci(rows: list[dict], value, baseline) -> tuple[float, float, float]:
    """``(delta, lo, hi)`` for ``AUC(value) - AUC(baseline)``, bootstrapped on shared resamples.

    **This is the test the two-column table cannot do, and the reason it is here.** Those
    columns' intervals are computed independently and overlap heavily, which reads as "no
    difference" and is not what overlapping intervals mean when both statistics are computed on
    the *same* rows. The two AUCs are strongly positively correlated — they share every
    candidate, every sitting and most of the ordering — so the difference between them is far
    better determined than either one is on its own. Resampling rows once and evaluating both
    statistics on that resample keeps that correlation, which is exactly what makes the interval
    on the difference narrow enough to be worth reading.

    An interval excluding 0.0 means the variant genuinely orders better on this sample.

    Both values are computed **once per row, before the bootstrap**. Calling the value functions
    inside the resampling loop re-derived every voice's age 20,000 times over and took the probe
    from seconds to minutes; the statistic is identical either way.
    """
    groups = [([(value(r), baseline(r)) for r in p], [(value(r), baseline(r)) for r in n])
              for p, n in row_groups(rows)]
    delta = _paired_auc_delta(groups)
    rng = random.Random(BOOTSTRAP_SEED)
    deltas = sorted(
        _paired_auc_delta([([rng.choice(p) for _ in p], [rng.choice(n) for _ in n])
                           for p, n in groups])
        for _ in range(BOOTSTRAP_ROUNDS)
    )
    return delta, deltas[int(0.025 * BOOTSTRAP_ROUNDS)], deltas[int(0.975 * BOOTSTRAP_ROUNDS)]


def groups_by_sitting(rows: list[dict], value) -> list[tuple[list[float], list[float]]]:
    """Per-sitting ``(approvals, negatives)`` value lists — the input to the AUC.

    Conditioning on the sitting is not optional here for the reason ``stratified_auc``
    documents: the approval threshold moves between screens, so a cross-sitting pair measures
    how the two screens differed rather than how the two candidates did.
    """
    out = []
    for when in sorted({r["decided_at"] for r in rows}):
        sub = [r for r in rows if r["decided_at"] == when]
        out.append(([value(r) for r in sub if r["decision"] in POSITIVE],
                    [value(r) for r in sub if r["decision"] in NEGATIVE]))
    return out


# Sidecar fields the composite consumes. v2 rows recorded ``proximity`` instead of ``approach``
# and omitted ``reward_risk`` entirely, so they can carry the agreement term but not the score
# it feeds. They are dropped from the composite column and the reduced n is printed — the same
# choice ``probe_freshness_weight.TERMS`` makes, and for the same reason: reconstructing the
# missing column would yield today's value, not the decision-time one.
COMPOSITE_FIELDS = ("approach", "freshness", "trend_alignment")


def has_composite_terms(row: dict) -> bool:
    return (all(f in row for f in COMPOSITE_FIELDS)
            and ("reward_risk_from_price" in row or "reward_risk" in row))


def table(rows: list[dict], label: str) -> None:
    """Both the isolated term and the composite it feeds, swept over the half-life."""
    full = [r for r in rows if has_composite_terms(r)]
    pairs = sum(len(p) * len(n) for p, n in groups_by_sitting(rows, lambda r: r["score"]))
    full_pairs = sum(len(p) * len(n) for p, n in groups_by_sitting(full, lambda r: r["score"]))
    approvals = sum(1 for r in rows if r["decision"] in POSITIVE)
    negatives = sum(1 for r in rows if r["decision"] in NEGATIVE)
    print(f"=== {label} ===")
    print(f"    {len(rows)} rows, {approvals} approved v {negatives} negative, "
          f"{pairs} same-sitting pairs.")
    print(f"    composite column drops {len(rows) - len(full)} rows missing a recorded term "
          f"({full_pairs} pairs).")
    print("    '?' marks an interval spanning 0.5 — not distinguishable from chance.\n")
    print(f"  {'half-life':>12}  {'agreement term':>{CELL_WIDTH}}  {'composite score':>{CELL_WIDTH}}")

    def line(name: str, count) -> None:
        term = lambda r: agreement_signal(count(r))            # noqa: E731 - table-local
        composite = lambda r: rescore(r, agreement_signal(count(r)))  # noqa: E731
        print(f"  {name:>12}  {stratified_cell(groups_by_sitting(rows, term))}  "
              f"{stratified_cell(groups_by_sitting(full, composite))}")

    for half_life in HALF_LIVES:
        line(f"{half_life}d", lambda r, h=half_life: effective_count(r, h))
    line("per-timeframe", shipped_half_life_count)
    line("inf (shipped)", lambda r: effective_count(r, None))
    print()

    head_count = lambda r: agreement_signal(effective_count(r, None))  # noqa: E731
    print("  paired against the head count on shared resamples "
          "(an interval clear of 0.0 is a real gain):\n")
    print(f"  {'half-life':>12}  {'delta, agreement term':>25}  {'delta, composite':>25}")
    for name, count in ([(f"{h}d", lambda r, h=h: effective_count(r, h)) for h in HALF_LIVES]
                        + [("per-timeframe", shipped_half_life_count)]):
        term = lambda r, c=count: agreement_signal(c(r))  # noqa: E731
        d, lo, hi = paired_delta_ci(rows, term, head_count)
        cd, clo, chi = paired_delta_ci(
            full, lambda r, c=count: rescore(r, agreement_signal(c(r))),
            lambda r: rescore(r, head_count(r)))
        mark = " " if lo > 0 or hi < 0 else "?"
        cmark = " " if clo > 0 or chi < 0 else "?"
        print(f"  {name:>12}  {f'{d:+.3f} [{lo:+.3f},{hi:+.3f}]{mark}':>25}  "
              f"{f'{cd:+.3f} [{clo:+.3f},{chi:+.3f}]{cmark}':>25}")
    print()


def pearson(xs: list[float], ys: list[float]) -> float:
    """Plain correlation. Enough to answer "are these two terms measuring one thing"."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else float("nan")


def collinearity(rows: list[dict]) -> None:
    """How much of the recency-weighted term is already in ``freshness``.

    The measurement that explains the split result above, rather than leaving it to argument.
    ``freshness`` is ``max(s.freshness for s in members)`` — the *freshest* voice on the zone —
    so it is already a recency statistic on the same group of people. If discounting the head
    count mostly re-derives it, the composite cannot gain from being handed it twice, and the
    term-level improvement is real but redundant at the point where it would be spent.
    """
    sub = [r for r in rows if "freshness" in r]
    fresh = [r["freshness"] for r in sub]
    print("=== is the recency-weighted term already in `freshness`? ===\n")
    print(f"    {len(sub)} rows. Correlation of each agreement variant with `freshness`:\n")
    for name, count in ([("head count", lambda r: effective_count(r, None))]
                        + [(f"{h}d", lambda r, h=h: effective_count(r, h)) for h in (7, 30, 90)]):
        vals = [agreement_signal(count(r)) for r in sub]
        print(f"  {name:>12}  r = {pearson(vals, fresh):+.3f}")
    print()


def main() -> None:
    index = thesis_index(THESES)
    raw = [json.loads(line) for line in DECISIONS.read_text().splitlines() if line.strip()]
    decided = [r for r in raw if r["decision"] in POSITIVE + NEGATIVE]
    rows, dropped = reconstructed(decided, index)

    print(f"thesis store: {len(index)} theses indexed")
    print(f"decisions: {len(decided)} decided rows, {len(rows)} reconstructed exactly")
    if dropped:
        print("  dropped — the corpus has moved under these rows:")
        for reason, count in dropped.most_common():
            print(f"    {count:3d}  {reason}")
    else:
        print("  every row's people, newest_at and head count match what was recorded.")
    print()

    # The 'inf' line reproduces the shipped term, so it must match what the other probe reports
    # for ``agreement``. If it ever doesn't, one of the two is no longer replaying the scorer.
    table(rows, "recency-weighted agreement, all negatives")

    kept = [r for r in rows
            if r["decision"] in POSITIVE or r.get("reason") not in AGE_REASONS]
    excluded = len(rows) - len(kept)
    table(kept, f"the same, excluding {excluded} negatives rejected *for* being stale")

    collinearity(rows)

    print("Read the 'inf (shipped)' row as the baseline — it is the head count, and every")
    print("finite half-life is an attempt to beat it. The paired deltas are the test that")
    print("matters: the two columns' own intervals overlap heavily because they are computed")
    print("on the same rows, and overlapping intervals do not mean no difference there.")


if __name__ == "__main__":
    main()
