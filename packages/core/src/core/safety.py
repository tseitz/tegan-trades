"""The yield-venue Safety gate, its score, and its ranking — ADR-0006.

Lives in ``core`` because two views need it (``treasury`` now, ``review``'s #74), and a view
may not import a view (ADR-0004): the gate has to sit somewhere both can reach without one
depending on the other.

**Gate, then score — never combined into one number.** ``gate()`` answers a yes/no a venue
must clear entirely: at least one audit on record, and old enough on-chain. Both are
missing-or-not facts, so they are gated, per the repo's own rule for this fork in the road —
score a continuum, gate a rule you wrote or a fact that is simply absent (ADR-0006). A venue
that clears the gate still gets a ``SafetyScore`` alongside it — incentive mix, history
stability, incidents — surfaced, never
collapsed into the gate, because a weighted total would let a pristine incentive mix paper over
"nobody has ever audited this," exactly the outcome the gate exists to prevent.

**The gate is protocol-level, not pool-level, and that is a decision.** ADR-0006 says "old
enough on-chain", and the age signal (``first_tvl_on``) lives on the protocol, not on any one
market inside it. A four-month-old USDC pool inside ``aave-v3`` inherits aave-v3's 2022
first-TVL date and passes — the audit and the lineage are properties of the protocol, not of
one pool. The pool's own youth is real risk, so it surfaces in ``SafetyScore.observations``
(the pool's own ``count``) rather than being silently lost.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# A venue with no `venues:` row at all (an off-chain bank like SoFi, which can never have a
# DefiLlama protocol) is UNCONFIGURED and prints nothing — never "no audit on record", which
# would be a confident wrong answer about an account this gate was never meant to judge.
UNCONFIGURED = "unconfigured"

VALIDATED_FORK = "validated_fork"
STANDALONE = "standalone"
UNRESOLVED_PARENT = "unresolved_parent"

# A fork whose parent already clears this gate inherits the parent's Lindy, so it has less to
# prove on its own history. 4 months, inside the 3-6 month range ADR-0006 records, one month
# above its floor. First pass, expected to move once real candidates run through — same status
# as every `# … TUNE.` constant in `core/setups.py`.
MIN_AGE_DAYS_VALIDATED_FORK = 122

# Everything else proves itself alone: a standalone protocol, or a fork whose lineage does not
# resolve to a passing parent. 9 months, the midpoint of ADR-0006's 6-12 month range. TUNE.
MIN_AGE_DAYS_STANDALONE = 274


@dataclass(frozen=True, slots=True)
class VenueFacts:
    """What was actually read about one venue — every field optional, ``None`` meaning *not
    read*, never zero. ``audited=False`` (the payload said no audit) and ``audited=None`` (we
    have not fetched yet) both fail the gate but are distinct facts, so a renderer can say which.

    ``slug`` is the DefiLlama protocol slug — what ``forked_from`` and ``rank()``'s ``by_slug``
    key on. A record that exists only to answer a lineage lookup (a parent fetched solely to
    resolve a child's fork edge) carries the protocol-level fields alone; a record built for
    ranking carries the pool-level fields too, and ``pool_id`` names which pool it is.
    """
    slug: str
    pool_id: str | None = None

    # Protocol-level — the gate reads these.
    audited: bool | None = None
    first_tvl_on: date | None = None
    forked_from: tuple[str, ...] = ()
    incidents: int | None = None

    # Pool-level — the score and the ranking read these. `apy` is what `rank()` sorts by;
    # `apy_reward` is the incentive slice of it; `sigma`/`count` are DefiLlama's own APY
    # stability stats; `outlier` is DefiLlama's own "this number is not believable" bit.
    apy: float | None = None
    apy_reward: float | None = None
    sigma: float | None = None
    count: int | None = None
    outlier: bool | None = None
    stablecoin: bool | None = None


@dataclass(frozen=True, slots=True)
class GateResult:
    """Whether one venue clears the Safety gate, and why not when it doesn't.

    ``reasons`` is ordered so a renderer can say *which* check failed — a venue can fail both
    at once. ``required_age_days``/``lineage`` are carried through even on a pass, so a caller
    never has to re-derive what the gate actually applied.
    """
    passed: bool
    reasons: tuple[str, ...]
    required_age_days: int
    lineage: str  # VALIDATED_FORK | STANDALONE | UNRESOLVED_PARENT


@dataclass(frozen=True, slots=True)
class SafetyScore:
    """Three named components, deliberately not collapsed into one number — see the module
    docstring. Each is ``None`` when its input was not read, never a default that would read
    as a real measurement.
    """
    incentive_share: float | None  # apy_reward / apy — "a yield that is mostly reward is a
                                    # yield with an end date" (research §4)
    apy_volatility: float | None   # DefiLlama's `sigma`
    observations: int | None       # `count` behind `apy_volatility` — a pool with `count` in
                                    # the tens has no history to judge
    incidents: int | None          # count from DefiLlama's `hallmarks`


@dataclass(frozen=True, slots=True)
class RankedVenue:
    """One candidate that cleared the gate, with the gate result and score riding alongside so
    a renderer never has to recompute either."""
    facts: VenueFacts
    gate: GateResult
    score: SafetyScore


def gate(facts: VenueFacts, *, as_of: date, by_slug: dict[str, VenueFacts]) -> GateResult:
    """Both checks are hard — see the module docstring for why neither is a score.

    1. Audit on record: ``facts.audited is True``. ``None`` (not read) and ``False`` both fail.
    2. Old enough on-chain, where "old enough" depends on lineage — see ``_lineage``.

    A missing ``first_tvl_on`` fails: an absent fact is exactly what this gate is built to gate
    on, per the repo's own rule (see module docstring).
    """
    return _gate(facts, as_of=as_of, by_slug=by_slug, seen=frozenset())


def _gate(
    facts: VenueFacts, *, as_of: date, by_slug: dict[str, VenueFacts], seen: frozenset[str]
) -> GateResult:
    seen = seen | {facts.slug}
    reasons: list[str] = []
    if facts.audited is not True:
        reasons.append("no audit on record")

    lineage, required = _lineage(facts, as_of=as_of, by_slug=by_slug, seen=seen)

    if facts.first_tvl_on is None:
        reasons.append("first TVL date not read")
    else:
        age_days = (as_of - facts.first_tvl_on).days
        if age_days < required:
            reasons.append(f"{age_days} days old, needs {required} ({lineage})")

    return GateResult(
        passed=not reasons, reasons=tuple(reasons), required_age_days=required, lineage=lineage
    )


def _lineage(
    facts: VenueFacts, *, as_of: date, by_slug: dict[str, VenueFacts], seen: frozenset[str]
) -> tuple[str, int]:
    """A validated fork inherits its parent's Lindy — see the module docstring. Three guards:
    a parent absent from ``by_slug``, a lineage cycle (``seen`` catches it before it recurses
    forever), and several parents where any one passing is enough — none of these are treated
    as a hand-kept allowlist, because every fact here is looked up, never asserted.
    """
    if not facts.forked_from:
        return STANDALONE, MIN_AGE_DAYS_STANDALONE

    for parent_slug in facts.forked_from:
        if parent_slug in seen:
            continue  # a lineage cycle — third-party data, never reality, but not our crash
        parent = by_slug.get(parent_slug)
        if parent is None:
            continue
        if _gate(parent, as_of=as_of, by_slug=by_slug, seen=seen).passed:
            return VALIDATED_FORK, MIN_AGE_DAYS_VALIDATED_FORK

    return UNRESOLVED_PARENT, MIN_AGE_DAYS_STANDALONE


def score(facts: VenueFacts) -> SafetyScore:
    """Each component ``None`` when its input was not read — never ``0.0``, which would read
    as a measured absence of risk rather than an absence of data."""
    incentive_share = (
        facts.apy_reward / facts.apy
        if facts.apy_reward is not None and facts.apy
        else None
    )
    return SafetyScore(
        incentive_share=incentive_share,
        apy_volatility=facts.sigma,
        observations=facts.count,
        incidents=facts.incidents,
    )


def rank(
    candidates: tuple[VenueFacts, ...], *, as_of: date, by_slug: dict[str, VenueFacts]
) -> tuple[RankedVenue, ...]:
    """Gate every candidate, keep the passes, sort by APY descending.

    An ``outlier: true`` pool is dropped outright, never merely flagged — DefiLlama's own "this
    number is not believable" bit, and an unbelievable number sorted by size would land first.
    """
    ranked = []
    for facts in candidates:
        if facts.outlier:
            continue
        result = gate(facts, as_of=as_of, by_slug=by_slug)
        if not result.passed:
            continue
        ranked.append(RankedVenue(facts=facts, gate=result, score=score(facts)))

    ranked.sort(key=lambda r: (-(r.facts.apy if r.facts.apy is not None else float("-inf")),
                                r.facts.slug, r.facts.pool_id or ""))
    return tuple(ranked)
