"""Per-person aggregation of graded calls — the number that drives add/drop decisions.

Pure and deterministic, like ``core.rank``. Everything is computed on a person's *own
slate* of calls, never against a pooled average, so a roster where one voice trades BTC
and another trades small-caps stays comparable.

**Three edges, because one isn't enough.**

``direction_edge`` (vs always-long the same asset) is structurally zero for a long call:
being long *is* the null. On a corpus that is 65% long, this measures little except how
well someone shorts. Useful, but it cannot rank a long-only caller.

``benchmark_edge`` (vs BTC for crypto, the S&P 500 otherwise) asks whether following this
person beat simply holding the market.

``skill_edge`` is the headline, because ``benchmark_edge`` alone is confounded by a *static
directional bias*. Measured on this corpus: one feed posts a +7.2% benchmark edge, but a
robot that was permanently short — making no decisions at all — earns +11.4% on that same
person's exact calls, because the corpus window ends in a drawdown. Their edge is a bearish
stance, not a read. ``skill_edge`` subtracts the best fixed stance (always-long /
always-short / always-flat) evaluated on the person's own slate, so only call-by-call
*selection* survives. Adding this control reorders the roster completely.

The best stance is chosen in hindsight on the same data, which biases ``skill_edge``
downward. That is deliberate: it is the conservative direction for a claim of skill.

``selection_edge`` is the same idea with the caller's *style* held constant, and it exists
because ``skill_edge`` charges people twice for one bias. The best fixed stance on a falling
corpus is a short robot, so a caller who is 78% long is marked down for not doing the
opposite of what they always do. That is a fact about their stance, and ``long_share``
already reports it. This control keeps their exact directional mix and destroys only the
*pairing* between a direction and the asset it was aimed at, which leaves call-by-call
selection and nothing else. A long-only caller is therefore compared against an always-long
robot — the only honest control for someone who never shorts.

Read the two together: ``skill_edge`` asks "did you beat the best stance available",
``selection_edge`` asks "given the stance you actually take, did you aim it well". A feed
that is strong on the second and weak on the first is a good analyst with a permanent bias,
which is worth knowing rather than worth hiding.

**``selection_edge`` is structurally zero for a caller who never varies direction**, and a
0.0% there means "not measurable this way", NOT "no skill". With one direction in the mix
the blind robot makes the identical call every time, so it *is* that person and the
difference is zero by construction. It also loses power as a caller approaches one-sided:
at 78% long there is little mixing left to destroy. This is the same shape of blind spot
``direction_edge`` has, moved one step along — measuring a one-sided caller needs a
*cross-sectional* control (did they pick the right assets to be long) rather than a
directional one, and nothing here supplies that yet.

**Why every number ships with a sample size and a CI.** Per-person graded counts here run
from ~50 to ~600 and asset mixes are concentrated (BTC alone is over a quarter of the
corpus). A bare mean over 40 noisy calls looks authoritative and isn't. Below
``MIN_SAMPLE`` the score is flagged and should not be ranked at all.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from core.grade import Grade, Pending, Ungradeable, signed_return_for
from core.rank import parse_date

# Below this many graded calls, per-person differences are noise. Flag, don't rank.
MIN_SAMPLE = 30

BOOTSTRAP_ITERATIONS = 2000
CI_ALPHA = 0.05
DEFAULT_SEED = 1729


@dataclass(frozen=True)
class PersonScore:
    person: str
    n: int                  # graded calls — the only population any rate is computed over
    n_pending: int          # horizon hasn't elapsed yet
    n_ungradeable: int      # no usable price
    hit_rate: float
    null_hit_rate: float    # always-long, same slate
    mean_signed_return: float
    mean_null_return: float
    direction_edge: float
    direction_edge_ci: tuple[float, float] | None
    benchmark_edge: float | None
    benchmark_edge_ci: tuple[float, float] | None
    best_static_direction: str | None   # the fixed stance that would have done best
    best_static_edge: float | None      # what that stance earned on this same slate
    skill_edge: float | None            # benchmark_edge - best_static_edge  <- headline
    skill_edge_ci: tuple[float, float] | None
    selection_edge: float | None        # benchmark_edge - their own mix, aimed blind
    selection_edge_ci: tuple[float, float] | None
    n_with_benchmark: int
    long_share: float       # context for reading direction_edge
    insufficient_sample: bool


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def bootstrap_ci(
    values,
    *,
    seed: int = DEFAULT_SEED,
    iterations: int = BOOTSTRAP_ITERATIONS,
    alpha: float = CI_ALPHA,
) -> tuple[float, float] | None:
    """Percentile bootstrap CI for the mean. Seeded, so a report is reproducible.

    Returns None for an empty sample rather than a fabricated interval.
    """
    values = list(values)
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iterations):
        means.append(sum(rng.choice(values) for _ in range(n)) / n)
    means.sort()
    lo = means[int((alpha / 2) * iterations)]
    hi = means[min(int((1 - alpha / 2) * iterations), iterations - 1)]
    return lo, hi


STATIC_DIRECTIONS = ("long", "short", "neutral")


def static_baseline_returns(grades, direction: str) -> list[float]:
    """Per-call excess of a robot that always calls ``direction`` on this exact slate."""
    return [
        signed_return_for(direction, g.market_return) - g.benchmark_return
        for g in grades
        if g.benchmark_return is not None
    ]


def best_static_baseline(grades) -> tuple[str | None, float | None]:
    """The fixed directional stance that would have scored best here, and its edge.

    This is the bar a person has to clear to have demonstrated anything: a stance requires
    no judgement, so beating the market while *underperforming a constant stance* is
    evidence of bias matching the regime, not of skill.
    """
    scored = [
        (direction, _mean(static_baseline_returns(grades, direction)))
        for direction in STATIC_DIRECTIONS
        if static_baseline_returns(grades, direction)
    ]
    if not scored:
        return None, None
    return max(scored, key=lambda pair: pair[1])


def stance_mix(grades) -> dict[str, float]:
    """What share of a person's own calls took each direction.

    Counted over the benchmarked slate only. The mix weights a baseline that can only cover
    benchmarked calls, so counting it over a wider population would describe one group and
    apply it to another.
    """
    benchmarked = [g for g in grades if g.benchmark_return is not None]
    if not benchmarked:
        return {}
    counts: dict[str, int] = {}
    for grade_row in benchmarked:
        counts[grade_row.direction] = counts.get(grade_row.direction, 0) + 1
    return {direction: n / len(benchmarked) for direction, n in counts.items()}


def mix_baseline_returns(grades) -> list[float]:
    """Per-call excess of a robot holding this person's exact directional mix, aimed blind.

    The robot takes the same share of longs and shorts they do, but picks which one without
    looking at the asset. So it keeps their bias and throws away their judgement, and what
    a person earns above it is selection alone.

    The expected return of a random draw from the mix is a weighted sum, so this is a closed
    form rather than a shuffle. Exact where a permutation would only approximate, and
    deterministic without needing a seed.
    """
    mix = stance_mix(grades)
    if not mix:
        return []
    return [
        sum(share * signed_return_for(direction, g.market_return)
            for direction, share in mix.items())
        - g.benchmark_return
        for g in grades
        if g.benchmark_return is not None
    ]


def score_person(
    person: str,
    outcomes: Iterable,
    *,
    min_sample: int = MIN_SAMPLE,
    seed: int = DEFAULT_SEED,
) -> PersonScore:
    """Aggregate one person's outcomes. Accepts the full mixed stream of sum-type results."""
    outcomes = list(outcomes)
    grades = [o for o in outcomes if isinstance(o, Grade)]
    pending = sum(1 for o in outcomes if isinstance(o, Pending))
    ungradeable = sum(1 for o in outcomes if isinstance(o, Ungradeable))

    # Only grades whose benchmark actually priced — a benchmark gap must shrink the
    # benchmark sample, not silently contribute a zero and drag the mean toward it.
    excesses = [g.excess_return for g in grades if g.excess_return is not None]
    direction_deltas = [g.signed_return - g.null_return for g in grades]

    static_direction, static_edge = best_static_baseline(grades)
    benchmarked = [g for g in grades if g.excess_return is not None]
    skill_deltas = (
        [
            g.excess_return - static
            for g, static in zip(
                benchmarked, static_baseline_returns(grades, static_direction), strict=True
            )
        ]
        if static_direction
        else []
    )

    mix_baseline = mix_baseline_returns(grades)
    selection_deltas = (
        [g.excess_return - blind
         for g, blind in zip(benchmarked, mix_baseline, strict=True)]
        if mix_baseline
        else []
    )

    return PersonScore(
        person=person,
        n=len(grades),
        n_pending=pending,
        n_ungradeable=ungradeable,
        hit_rate=_mean(1.0 if g.correct else 0.0 for g in grades),
        null_hit_rate=_mean(1.0 if g.null_correct else 0.0 for g in grades),
        mean_signed_return=_mean(g.signed_return for g in grades),
        mean_null_return=_mean(g.null_return for g in grades),
        direction_edge=_mean(direction_deltas),
        direction_edge_ci=bootstrap_ci(direction_deltas, seed=seed),
        benchmark_edge=_mean(excesses) if excesses else None,
        benchmark_edge_ci=bootstrap_ci(excesses, seed=seed) if excesses else None,
        best_static_direction=static_direction,
        best_static_edge=static_edge,
        skill_edge=_mean(skill_deltas) if skill_deltas else None,
        skill_edge_ci=bootstrap_ci(skill_deltas, seed=seed) if skill_deltas else None,
        selection_edge=_mean(selection_deltas) if selection_deltas else None,
        selection_edge_ci=(
            bootstrap_ci(selection_deltas, seed=seed) if selection_deltas else None
        ),
        n_with_benchmark=len(excesses),
        long_share=_mean(1.0 if g.direction == "long" else 0.0 for g in grades),
        insufficient_sample=len(grades) < min_sample,
    )


def group_scores(
    outcomes: Iterable,
    *,
    key: Callable = lambda o: o.person,
    min_sample: int = MIN_SAMPLE,
    seed: int = DEFAULT_SEED,
) -> dict[str, PersonScore]:
    """Score outcomes bucketed by ``key`` — person by default, or asset/timeframe/
    direction for the breakdowns that stop a score being one BTC opinion in disguise."""
    buckets: dict[str, list] = {}
    for outcome in outcomes:
        buckets.setdefault(key(outcome), []).append(outcome)
    return {
        name: score_person(name, group, min_sample=min_sample, seed=seed)
        for name, group in sorted(buckets.items())
    }


# ── restatement folding ─────────────────────────────────────────────────────

DEFAULT_WINDOW_DAYS = 30


def fold_restatements(rows, *, window_days: int = DEFAULT_WINDOW_DAYS) -> list:
    """Collapse repeat statements of the same call, keyed on
    ``(person, asset, direction, timeframe)`` within ``window_days``.

    These people re-state a position weekly. Ungrouped, one BTC-long view held for a
    quarter counts as a dozen independent correct calls and dominates the person's score.

    **The survivor is the *earliest* member of each cluster**, which is where this differs
    from ``triage_cli.collapse_restatements``. Triage keeps the newest because it asks
    "what do they think now"; grading asks "when did this information arrive, and what
    happened next" — so the call is credited to the day it was first made, and a stale
    restatement can't be re-graded as fresh insight.

    Undated rows are never folded: with no date there's no way to order statements.
    Mirrors ``core.rank.parse_date`` in degrading rather than raising.
    """
    grouped: dict[tuple, list] = {}
    survivors = []

    for row in sorted(rows, key=lambda r: r.published_at or ""):
        if parse_date(row.published_at) is None:
            survivors.append(row)  # undated — always its own call
            continue
        key = (row.person, row.asset, row.direction, row.timeframe)
        grouped.setdefault(key, []).append(row)

    for members in grouped.values():
        anchor = None
        for row in members:  # already oldest-first
            when = parse_date(row.published_at)
            if anchor is None or (when - anchor).days > window_days:
                anchor = when
                survivors.append(row)

    return survivors
