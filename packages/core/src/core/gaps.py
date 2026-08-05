"""What it costs to hold an instrument that stops trading overnight. Pure.

The counterpart to ``core.funding``, and the reason both exist: a venue comparison needs one
cost unit. A perp charges funding continuously and never gaps; an equity charges no funding and
gaps. Priced in the same unit — *fraction of notional over the hold* — they can finally be
ranked against each other. Until this module existed the router compared a priced venue
(Hyperliquid, funding) against a free one (Alpaca, nothing) and always picked the free one.

**A stop is an intent, not a bound.** Once triggered it is a market order, so on a gapped
open it fills at the open and not at the stop. Everything past the stop is loss the risk budget
never authorised, and that excess is what this module measures.

MEASURED by ``scripts/probe_gap_cost.py`` over 4,496 sessions across 23 of the queue's own
equities, at a fixed 4.53% stop (2026-07-29): **4.72% of sessions gap past it**. That figure is
TWO-SIDED — it counts gaps either way — and only the adverse half can hurt a given position, so
the one-sided rate is 2.36% and the chance of at least one adverse gap over a 21-session hold is
**39%**. Getting the two-sided/one-sided distinction wrong doubles or halves the headline, so
``rate`` here is one-sided by construction and ``adverse_excess`` is what makes it so.

**The per-asset spread is 46x and it is the whole reason to route per asset:** ``BE`` gaps past
that stop on 18.6% of sessions, ``SBSW``/``SGML`` 16.0%, ``RKLB`` 11.6%, against ``XLE``'s 0.40%
and ``WMB``/``GLNG``'s zero. A pooled number is nearly useless at that spread.

An earlier hand measurement of the same effect got the pooled claim right (3.5%, within sampling
noise on a different asset mix) and two per-asset figures wrong: it reported ``USAR`` 34% and
``XLE`` 2.4% where the same stop gives 9.16% and 0.40%. Its *ordering* was right and its
magnitudes were not — the failure mode ``probe_book_depth.py`` warns about for venue slippage.
Trust the probe, not the prose.

**The rate is violently sensitive to stop distance**, which is why it is a parameter and not a
per-asset constant: ``USAR`` goes from 2.29% one-sided at a 4.53% stop to 35.88% at a 1.0% stop.
A gap cost quoted without the stop it was measured against is meaningless.

WHY SHRINKAGE IS PERMANENT MACHINERY, NOT A STOPGAP. A per-asset rate is not estimable from the
cached window: 19 of 28 approved assets carrying bars hold under 250 sessions (``BE``, ``GLNG``,
``WMB``: 98), because ``oracle/plan.py`` scopes each fetch to that asset's *earliest thesis
mention* — price history exists to grade theses, and a thesis cannot be graded before it was
stated. At a 3.5% base rate, 98 sessions is ~3 events, and a rate estimated from 3 events is
noise. Fetching a longer window helps and should be a *separate* job, because widening
``plan_jobs`` would conflate grading with costing. But it does not solve it: a newly listed
instrument (``USAR``, ``PLUME``) can never have long history. So per-asset estimates are shrunk
toward the pool by their own sample size, permanently.

**An unmeasured asset must never price at zero.** That is the bug this module exists to fix, and
reproducing it here would be worse than not measuring at all. With no history the pooled rate is
used and ``pooled_weight`` says so; with no pool either, ``measure`` returns ``None`` — "not
measured", which the caller has to handle. ``AlpacaBroker.liquidity`` is the precedent for
returning None honestly rather than fabricating a shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

SIDES = ("long", "short")

# Pseudo-observations of the pooled rate that every per-asset estimate carries. An asset with
# exactly this many sessions is weighted half its own number and half the pool.
#
# 250 because that is the per-asset sample behind the earlier measurement (2,500 sessions, ten
# assets) — the point at which we were willing to quote a per-asset rate at all. It is a
# statement about when this data becomes trustworthy, not a tuning knob: raising it would make
# the router ignore the differences between assets that are the whole reason it routes, and
# lowering it would let 98 sessions of noise set the price.
SHRINK_SESSIONS = 250


def overnight_gaps(bars) -> tuple[float, ...]:
    """Signed close-to-open returns, one per session boundary in ``bars``.

    Duck-typed on ``.open``/``.close`` exactly like ``core.structure`` and ``core.grade``, so
    ``core`` still imports nothing local. ``bars`` must be ascending with unique dates —
    ``oracle.series.PriceSeries`` normalises that once, for every consumer.

    Positive is a gap up. The sign is kept rather than resolved here because which direction
    hurts depends on the position, not on the instrument — see ``adverse_excess``.
    """
    gaps: list[float] = []
    for previous, current in pairwise(bars):
        if previous.close <= 0:
            # A non-positive close is bad data, not a 100% gap. Dropping the observation is
            # right; letting it through would put a nonsense outlier straight into the mean.
            continue
        gaps.append((current.open - previous.close) / previous.close)
    return tuple(gaps)


def adverse_excess(gaps, stop_distance: float, side: str) -> tuple[int, float]:
    """``(sessions that gapped past the stop, mean excess per session)``.

    The excess is the part of an adverse gap that lands *beyond* the stop, as a fraction of
    entry. A gap the position's own way is a windfall and contributes nothing — it must not net
    against the losses, or a volatile instrument would look cheap for being volatile in both
    directions.

    Averaged over **every** session rather than only the bad ones, because the result is a
    per-session expectation that gets multiplied by a holding period. Averaging over only the
    bad ones would answer "how bad is a bad day", which is a different question and much larger.
    """
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")
    if not gaps:
        return 0, 0.0
    hurts = -1.0 if side == "long" else 1.0
    past = 0
    total = 0.0
    for gap in gaps:
        adverse = gap * hurts          # positive only when the gap went against the position
        excess = adverse - stop_distance
        if excess > 0:
            past += 1
            total += excess
    return past, total / len(gaps)


@dataclass(frozen=True, slots=True)
class Pool:
    """What a cohort of comparable instruments does at one stop distance.

    Carries **both** statistics because a shrunk cost needs a shrunk probability to go with it.
    Shrinking the excess alone produced a genuine contradiction in the first version of this
    module: ``CRM`` was priced at 0.274% of notional over the hold beside a 0.0% chance of the
    gap that causes it, because the cost leaned on the pool and the rate did not.
    """

    excess: float       # mean expected excess per session, fraction of entry
    rate: float         # mean fraction of sessions gapping past the stop, one-sided
    assets: int         # how many instruments stand behind it


def pooled(cohort_gaps, stop_distance: float, side: str) -> Pool | None:
    """The cohort's behaviour, evaluated at **one** stop distance. ``None`` for an empty cohort.

    THIS FUNCTION IS THE REASON THE POOL IS NOT A SCALAR, and skipping it silently corrupts every
    wide-stop asset. Excess-past-stop is violently sensitive to the stop it was measured against
    (``USAR``: 2.29% one-sided at a 4.53% stop, 35.88% at 1.0%), and approved stops across the
    queue span 0.88% to 24.36%. So a single pooled number averaged over assets *at their own
    different stops* is not a quantity anything can be shrunk toward — blending it with an
    asset's own estimate mixes two different questions. Measured: it charged ``BE``, whose stop
    is 22.72% wide and which has never once been gapped past it, the same 0.589% as everything
    else.

    Evaluating the whole cohort at the caller's stop makes both sides of the shrinkage the same
    quantity. Kept nonparametric — the cohort's real gaps re-scored, not a fitted sigma — because
    overnight gaps are fat-tailed and a normal assumption would quietly delete the tail that
    makes this cost worth pricing at all.

    Unweighted across assets rather than pooling every session into one bag: otherwise the two
    assets carrying 500 bars would set the pool for the twenty carrying 100.
    """
    excesses: list[float] = []
    rates: list[float] = []
    for gaps in cohort_gaps:
        if not gaps:
            continue
        past, excess = adverse_excess(gaps, stop_distance, side)
        excesses.append(excess)
        rates.append(past / len(gaps))
    if not excesses:
        return None
    return Pool(excess=sum(excesses) / len(excesses),
                rate=sum(rates) / len(rates),
                assets=len(excesses))


def shrink(own: float, n: int, pooled: float, *, k: int = SHRINK_SESSIONS) -> float:
    """``own`` pulled toward ``pooled`` by how little evidence stands behind it.

    The standard empirical-Bayes weighting, ``(n·own + k·pooled) / (n + k)``: an estimate from
    no sessions is entirely the pool, one from many sessions is almost entirely itself, and the
    handover is smooth rather than a threshold. A threshold would put ``BE`` at 98 sessions and
    a hypothetical 251-session asset on opposite sides of a cliff for no reason in the data.
    """
    if n < 0:
        raise ValueError(f"n must not be negative, got {n}")
    return (n * own + k * pooled) / (n + k)


@dataclass(frozen=True, slots=True)
class GapCost:
    """One instrument's gap exposure, already shrunk, in the same unit as ``carry_cost``.

    ``excess_per_session`` and ``rate`` are BOTH shrunk, on the same weight. Shrinking one and
    not the other is not a rounding difference, it is a contradiction: the first version of this
    class priced ``CRM`` at 0.274% of notional over the hold while reporting a 0.0% chance of the
    gap that produces it, and ``GLNG`` at 0.841% against the same 0.0%. The raw counts stay on
    ``sessions``/``past_stop`` so ``observed_rate`` can still answer what this instrument itself
    actually did.

    ``pooled_weight`` is the honesty of the estimate and is carried rather than derived, because
    a router that shows a cost without showing it is overstating what it knows. **``None`` means
    no pool existed to shrink toward** — distinct from ``0.0``, which means the asset's own
    history was long enough not to need one. Conflating those two let a single-asset cohort
    report 98 sessions of noise as fully evidenced.
    """

    asset: str
    sessions: int
    past_stop: int
    excess_per_session: float
    rate: float
    pooled_weight: float | None

    @property
    def observed_rate(self) -> float:
        """This instrument's own raw frequency, unshrunk — what it did, not what it is priced at.

        Kept separate from ``rate`` rather than replacing it: with 98 sessions and zero events the
        raw answer is 0.0, which is a true statement about the sample and a false one about the
        instrument.
        """
        return self.past_stop / self.sessions if self.sessions else 0.0

    @property
    def borrowed(self) -> bool:
        """Whether most of this estimate is the cohort rather than this instrument.

        The threshold is half, which is the only value that needs no justification: above it the
        number is more the pool's than the asset's, and ``SHRINK_SESSIONS`` is defined as the
        sample size at which the two are weighted equally.
        """
        return self.pooled_weight is not None and self.pooled_weight > 0.5

    def over(self, hold_days: float) -> float:
        """Expected cost as a fraction of notional over ``hold_days``.

        Linear in time, matching ``funding.carry_cost``: gaps are paid out of the position as
        they happen rather than reinvested, and consecutive overnight gaps are near enough
        independent that compounding would be false precision.
        """
        return self.excess_per_session * hold_days

    def at_least_one(self, hold_days: float) -> float:
        """Probability of at least one adverse gap past the stop across the hold.

        Reported alongside ``over`` because they answer different questions and a person needs
        both: ``over`` is what to charge the trade, this is whether to expect it to happen at
        all. Compounds — ``rate * hold_days`` exceeds 1.0 on a gappy asset and is meaningless.

        Uses the shrunk ``rate``, so it cannot contradict ``over`` — see the class docstring.
        """
        return 1.0 - (1.0 - self.rate) ** hold_days


def measure_from_gaps(asset: str, gaps, *, stop_distance: float, side: str,
                      pool: Pool | None, k: int = SHRINK_SESSIONS) -> GapCost | None:
    """As ``measure``, from gaps already extracted.

    Exists because a caller ranking a whole queue extracts each asset's gaps once to build the
    cohort and would otherwise re-walk every bar per candidate — and because the pool has to be
    recomputed at each candidate's own stop, so the per-candidate call is the hot one.

    ``pool`` is the caller's business: it is a property of the *cohort*, not of this asset, and
    computing it here would make a pure per-asset function secretly global.

    Both statistics are shrunk on the same weight, and ``pooled_weight`` is ``None`` rather than
    ``0.0`` when there was no pool — see ``GapCost`` for what each of those cost when they were
    conflated.
    """
    past, own_excess = adverse_excess(gaps, stop_distance, side)
    if not gaps and pool is None:
        return None
    own_rate = past / len(gaps) if gaps else 0.0
    if pool is None:
        weight, excess, rate = None, own_excess, own_rate
    else:
        weight = k / (len(gaps) + k)
        excess = shrink(own_excess, len(gaps), pool.excess, k=k)
        rate = shrink(own_rate, len(gaps), pool.rate, k=k)
    return GapCost(
        asset=asset,
        sessions=len(gaps),
        past_stop=past,
        excess_per_session=excess,
        rate=rate,
        pooled_weight=weight,
    )


def measure(asset: str, bars, *, stop_distance: float, side: str,
            pool: Pool | None, k: int = SHRINK_SESSIONS) -> GapCost | None:
    """This asset's gap cost from raw bars, shrunk toward ``pool``. ``None`` when nothing is
    known. See ``measure_from_gaps``, which this delegates to."""
    return measure_from_gaps(asset, overnight_gaps(bars), stop_distance=stop_distance,
                             side=side, pool=pool, k=k)
