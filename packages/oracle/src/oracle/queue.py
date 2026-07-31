"""Which candidates get put in front of a human, and what else was on screen beside them.

Split out of ``setups_cli`` because it stopped being one slice expression. The queue used to
be ``qualified[:limit]`` — the top N by score — and ``docs/IMPROVEMENTS.md`` §4 is the account
of why that quietly destroyed the only ground truth this repo has.

**The defect, in one sentence: a sitting is not a random sample of the queue.** Decided rows
are hidden from the next run, so a score-ordered cap hands each sitting a narrower slice of
the tail than the last. Measured on the three v5 sittings::

    18:38  n=25  scores 0.377-0.906   score AUC 0.856 [0.66, 1.00]
    19:53  n=12  scores 0.528-0.580   score AUC 0.222 [0.00, 0.67]
    20:00  n=11  scores 0.403-0.527   score AUC 0.458 [0.04, 0.88]

The only sitting whose interval clears chance is the only one with a wide range. That is
restricted range doing the work, not the scorer improving and then failing — **a term cannot
order what barely varies.** Worse, the threshold moves with the slice: the 19:53 sitting
approved at a median score of 0.484 while 18:38 *rejected* at a median of 0.518, so "approved"
was never an absolute label. Pooling those rows into one dataset — which every calibration
pass in this repo assumed it could do — compares judgements made against different screens.

**The fix is a head band plus a stratified tail.** The top ``HEAD_SIZE`` by score are always
shown, because the queue's other job is finding tonight's trade and a calibration scheme that
hides the best candidate would not survive contact with a real session. The remaining slots
are drawn one per equal-count stratum across everything below, so every sitting spans the full
population range and the anchor stops tracking sitting order.

**The head is not fixed by this and is not pretending to be.** It is still a score-ordered
slice that marches down between sittings exactly as before. That is why ``band`` travels with
every row: the tail alone is the comparable sample, and a mining pass can say so.

Sampling decides *which* candidates are shown. It does not touch the order they are shown in —
that stays ``collapse``'s weekly-then-score, so §19(e)'s precedence rule is unaffected.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from core.setups import Candidate

# How the sitting's rows were chosen. Recorded on every decision, because once two schemes
# exist a file that does not say which one ran cannot tell a poolable sitting from the
# top-of-queue slice §4 says is unpoolable.
STRATIFIED = "stratified"  # head band + one draw per stratum below it
TOP = "top"                # the pre-§4 behaviour: the first ``limit`` rows, verbatim
FULL = "full"              # the whole qualified population fit; nothing was sampled

BAND_HEAD = "head"  # the score-ordered head — still a slice, still marches down
BAND_TAIL = "tail"  # the stratified draw — the comparable half

# Five leaves twenty slots for the tail at the default ``--limit 25``, which is twenty strata
# over a population that has run 84 candidates. Small enough that the tail still spans the
# range, large enough that a session looking for a trade rather than a measurement is not
# rummaging for it.
HEAD_SIZE = 5


@dataclass(frozen=True)
class QueueRow:
    """One line on screen, and which half of the queue put it there."""
    candidate: Candidate
    band: str


@dataclass(frozen=True)
class QueuePosition:
    """Where a candidate sat, and what it was judged against.

    Recorded on the decision rather than derived later because the two differ exactly when it
    matters. ``decided_at`` already identifies a sitting — ``triage`` stamps one timestamp
    before the loop — so the range of *decided* rows is recoverable today. The range of rows
    that were **offered** is not: a sitting that quits early is indistinguishable from one
    that ran out of queue, and those are different amounts of evidence about the threshold.
    """
    mode: str
    band: str
    rank: int          # 1-based position on screen
    size: int          # how many rows the sitting offered
    score_min: float
    score_max: float
    population: int    # how many qualified before sampling


@dataclass(frozen=True)
class Queue:
    """The rows a sitting will be offered, in the order ``collapse`` put them in."""
    rows: tuple[QueueRow, ...]
    mode: str
    population: int

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        return tuple(row.candidate for row in self.rows)

    @property
    def score_min(self) -> float:
        self._require_rows("score_min")
        return min(row.candidate.score for row in self.rows)

    @property
    def score_max(self) -> float:
        self._require_rows("score_max")
        return max(row.candidate.score for row in self.rows)

    def _require_rows(self, what: str) -> None:
        """An empty queue has no range, and ``min()`` says so in a way nobody can act on.

        Reachable whenever the corpus produces nothing to review, which is an ordinary state
        rather than a bug — ``main`` checks ``queue.rows`` and prints "Nothing to review".
        This exists so a *different* caller that forgets to check gets told which check it
        missed rather than a bare ``min() iterable argument is empty``.
        """
        if not self.rows:
            raise ValueError(f"{what} is undefined on an empty queue — check `queue.rows` first")

    def position(self, rank: int) -> QueuePosition:
        """The context to record alongside the decision on the row at ``rank`` (1-based)."""
        return QueuePosition(
            mode=self.mode,
            band=self.rows[rank - 1].band,
            rank=rank,
            size=len(self.rows),
            score_min=self.score_min,
            score_max=self.score_max,
            population=self.population,
        )


def filter_candidates(
    candidates,
    *,
    min_score: float | None = None,
    tiers: tuple[str, ...] | None = None,
) -> list[Candidate]:
    """Drop candidates the run asked not to see.

    Capping deliberately does **not** live here any more. It moved to ``build_queue`` because
    a cap is a sampling decision and §4 is the account of what happens when it is applied as
    an afterthought — two places that both truncate is how "the top N" became the only thing
    anyone ever judged.
    """
    out = list(candidates)
    if min_score is not None:
        out = [c for c in out if c.score >= min_score]
    if tiers:
        wanted = set(tiers)
        out = [c for c in out if c.tier in wanted]
    return out


def one_per_asset(candidates) -> tuple[list[Candidate], int]:
    """Keep the best zone per ticker. Returns the kept rows and how many were dropped.

    **"Best" is not re-decided here.** ``collapse`` already orders weekly-then-score, because
    "the macro is much stronger" is a rule rather than a weight — the same rule that lets a
    weekly trend veto a direction outright in ``cross_reference``. So the best row per asset is
    simply the first one, and this filters without re-sorting. Picking by score here would
    quietly reinstate score-alone ordering and hand the slot to the daily zone, which usually
    scores higher only because ``proximity`` and ``depth`` reward being close to price.

    **Both zones used to be offered**, labelled "1 of 2" so the second was judged knowing the
    first existed — §27's sitting had mistaken adjacent zones for duplicates and the label was
    the fix. That reasoning was about *confusion*. This one is about *capital*: two approvals
    on one ticker are two commitments, and commitments turned out to be the binding constraint
    long before risk was — one 1%-risk position plus three resting orders consumed 75% of a
    $100k account on 2026-07-29.

    Keyed on the asset alone, not on (asset, direction). A long and a short on one ticker is
    the worst version of the thing being prevented, not an exemption from it.

    **The asset is already the instrument by the time this runs.** Two corpus labels for one
    instrument — RUT and IWM — used to arrive here as two tickers and survive this filter as
    two commitments on one trade. That is fixed upstream in ``collapse``, which merges them so
    their supporters count once; see ``oracle.instruments``. This stays keyed on the label
    because after that merge the label *is* the instrument.
    """
    seen: set[str] = set()
    kept = []
    for c in candidates:
        if c.asset in seen:
            continue
        seen.add(c.asset)
        kept.append(c)
    return kept, len(candidates) - len(kept)


def build_queue(
    candidates,
    *,
    limit: int | None,
    stratified: bool = True,
    head: int = HEAD_SIZE,
    rng: random.Random | None = None,
) -> Queue:
    """Choose the sitting's rows. See the module docstring for why this is not a slice.

    ``candidates`` arrives in ``collapse`` order (weekly-then-score), which is preserved in the
    result — so scores must be re-derived here rather than read off the list position.

    ``limit=None`` is the only way to say "no cap". **Zero is refused rather than honoured**,
    because the same number means opposite things two frames apart: ``--limit 0`` is the CLI's
    escape hatch *from* the cap and ``main`` translates it to ``None`` before calling here, so
    a 0 arriving at this function is a caller that skipped the translation. Taken literally it
    would build an empty queue out of a full population and report it as a ``TOP`` sitting —
    a silent no-op dressed as a decision, which is the failure mode this module exists to stop.
    """
    if limit is not None and limit < 1:
        raise ValueError(
            f"limit must be >= 1, or None for no cap (got {limit}); "
            f"the CLI's --limit 0 means no cap and is translated by setups_cli.main"
        )

    population = tuple(candidates)
    total = len(population)

    if limit is None or total <= limit:
        return Queue(tuple(QueueRow(c, FULL) for c in population), FULL, total)

    # ``limit <= head`` leaves no tail to stratify. Reported as ``TOP`` rather than as a
    # stratified sitting with a degenerate tail: the mode is what a mining pass conditions on,
    # so it has to describe what happened, not what was requested.
    if not stratified or limit <= head:
        return Queue(tuple(QueueRow(c, TOP) for c in population[:limit]), TOP, total)

    # Indices throughout, never candidates: ``Candidate.key`` is content-addressed, so two
    # genuinely distinct rows can compare equal, and the original index is also what restores
    # ``collapse``'s ordering at the end.
    ranked = sorted(range(total), key=lambda i: -population[i].score)
    head_indices = ranked[:head]
    rest = ranked[head:]

    rng = random.Random() if rng is None else rng
    chosen = set(head_indices)
    for lo, hi in _strata(len(rest), limit - head):
        chosen.add(rest[rng.randrange(lo, hi)])

    heads = set(head_indices)
    rows = tuple(
        QueueRow(population[i], BAND_HEAD if i in heads else BAND_TAIL)
        for i in sorted(chosen)
    )
    return Queue(rows, STRATIFIED, total)


def rng_for(as_of) -> random.Random:
    """The draw's randomness, fixed to the **day** rather than to the moment.

    An unseeded draw would be redrawn on every invocation, and ``scripts/nightly.sh`` runs
    ``setups --list`` unattended every night — so the digest's tail would reshuffle nightly and
    "a new candidate appeared" would be indistinguishable from "the draw moved". That is the
    same class of silent, self-hiding failure ``--list``'s counters exist to prevent.

    Fixing it to the date buys three things at once: the nightly digest is stable, ``--list``
    is an exact preview of what a sitting will offer that day, and the sitting is
    reconstructible from a date rather than from a seed nobody recorded. The tail still
    rotates, just on a daily clock instead of a per-invocation one.

    Two sittings on the same day still differ, and should: the second draws from a population
    the first has already decided rows out of, so the strata themselves have moved.
    """
    return random.Random(as_of.toordinal())


def _strata(n: int, k: int) -> list[tuple[int, int]]:
    """``k`` contiguous half-open bounds over ``range(n)``, as equal in *count* as they divide.

    Equal count, not equal score width. A width-uniform draw would over-sample wherever the
    score distribution is sparse — which is its extremes — and the extremes are precisely where
    a handful of candidates would then be re-offered every sitting.

    ``build_queue`` only reaches this with ``n > k``, so no stratum is ever empty.
    """
    return [(i * n // k, (i + 1) * n // k) for i in range(k)]
