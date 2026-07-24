"""Tier-1 grading: did a call's asset move the way its author said, over their horizon?

Pure and duck-typed, exactly like ``core.rank`` — the caller supplies a price series
(anything with ``close_on``) and the corpus row; nothing here does I/O or mutates ore.

**Why direction-and-horizon rather than levels.** ``key_levels`` on a stored thesis is an
unlabeled bag of floats (a real one: ``[40, 50, 30, 21, 19, 20]``, where 40 is a trigger,
50 a target and the rest downside) and ``invalidation`` is free text with conditions baked
in. Nothing mechanical separates a target from a stop, and 69% of the corpus is
``macro_lean`` with no levels at all. Level-aware grading needs an LLM enrichment pass;
that's Tier 2. This module grades what the corpus can actually support today.

**The outcome is a sum type on purpose.** ``Pending`` (horizon hasn't elapsed) and
``Ungradeable`` (no price) are distinct from a ``Grade`` of zero. Collapsing them would
quietly convert "we don't know" into "they were wrong", which is how backtests lie.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.rank import parse_date

# A neutral/"chop" call is right when the asset went roughly nowhere. The band is the
# tolerance for "nowhere" — a v1 best-guess, tunable like core.rank's weights.
FLAT_BAND = 0.02


@dataclass(frozen=True)
class Horizons:
    """Days from publication over which each timeframe's call is judged.

    Best-guess v1 values. They materially affect every score, so ``score_cli`` sweeps them
    to check that rankings are stable rather than an artifact of these numbers.
    """
    scalp: int = 7
    swing: int = 30
    position: int = 180
    macro: int = 365

    def days_for(self, timeframe: str) -> int | None:
        return getattr(self, timeframe, None) if timeframe in _TIMEFRAMES else None


_TIMEFRAMES = frozenset({"scalp", "swing", "position", "macro"})
DEFAULT_HORIZONS = Horizons()


@dataclass(frozen=True)
class Grade:
    thesis_id: str
    asset: str
    person: str
    direction: str
    timeframe: str
    entry_date: date
    exit_date: date
    entry: float
    exit: float
    market_return: float   # the asset's own move — what buy-and-hold earned
    signed_return: float   # what the author's stated direction earned
    correct: bool
    null_return: float     # the always-long baseline on this same call
    null_correct: bool
    # Benchmark (BTC for crypto, S&P 500 for stock/macro) over the identical window.
    # None when no benchmark series was supplied.
    benchmark_return: float | None = None
    excess_return: float | None = None   # signed_return - benchmark_return


# Pending/Ungradeable carry the same descriptive fields as Grade so that *any* breakdown
# (by timeframe, by direction) can bucket the whole outcome stream. Without them, asking
# "how do this person's position calls look" crashes on the unresolved ones — and those
# are exactly the rows you most need to see in a long-horizon bucket.
@dataclass(frozen=True)
class Pending:
    thesis_id: str
    asset: str
    person: str
    resolves_on: date
    direction: str = ""
    timeframe: str = ""


@dataclass(frozen=True)
class Ungradeable:
    thesis_id: str
    asset: str
    person: str
    reason: str
    direction: str = ""
    timeframe: str = ""


Outcome = Grade | Pending | Ungradeable


def signed_return_for(direction: str, market_return: float) -> float:
    """What the stated direction earned on the asset's move.

    ``neutral`` earns nothing: it's a decision not to take a side, so crediting it with
    the market's move would reward a call its author explicitly declined to make.
    """
    if direction == "long":
        return market_return
    if direction == "short":
        return -market_return
    return 0.0


def is_correct(direction: str, market_return: float, *, flat_band: float = FLAT_BAND) -> bool:
    if direction == "neutral":
        return abs(market_return) < flat_band
    return signed_return_for(direction, market_return) > 0


def grade(
    row,
    series,
    *,
    today: date,
    horizons: Horizons = DEFAULT_HORIZONS,
    flat_band: float = FLAT_BAND,
    benchmark=None,
) -> Outcome:
    """Grade one call. ``series`` may be None when the asset has no price source.

    ``benchmark`` is an optional second series (BTC for crypto, S&P 500 otherwise) priced
    over the identical window. It exists because the always-long null is *structurally*
    zero for a long call — signed and null returns are the same number — so on a corpus
    that is 65% long, the null-model edge alone would rank almost everyone at zero. The
    benchmark answers the question that actually drives roster decisions: did following
    this person beat simply holding the market?
    """
    ident = getattr(row, "id", "")
    asset = getattr(row, "asset", "")
    person = getattr(row, "person", "")

    direction = getattr(row, "direction", "")
    timeframe = getattr(row, "timeframe", "")

    def ungradeable(reason: str) -> Ungradeable:
        return Ungradeable(thesis_id=ident, asset=asset, person=person, reason=reason,
                           direction=direction, timeframe=timeframe)

    published = parse_date(getattr(row, "published_at", None))
    if published is None:
        return ungradeable("undated")

    days = horizons.days_for(row.timeframe)
    if days is None:
        return ungradeable("unknown_timeframe")

    exit_date = published + timedelta(days=days)
    # Checked before touching prices so an unresolved call never gets misfiled as a data
    # gap — "too early to judge" and "no data" are different facts about the corpus.
    if exit_date > today:
        return Pending(thesis_id=ident, asset=asset, person=person, resolves_on=exit_date,
                       direction=direction, timeframe=timeframe)

    if series is None:
        return ungradeable("unpriceable")

    entry = series.close_on(published)
    if not entry:  # None, or a zero that would divide by zero below
        return ungradeable("no_entry_price")
    exit_price = series.close_on(exit_date)
    if exit_price is None:
        return ungradeable("no_exit_price")

    market_return = (exit_price - entry) / entry
    signed = signed_return_for(row.direction, market_return)

    benchmark_return = _benchmark_return(benchmark, published, exit_date)
    return Grade(
        thesis_id=ident,
        asset=asset,
        person=person,
        direction=row.direction,
        timeframe=row.timeframe,
        entry_date=published,
        exit_date=exit_date,
        entry=entry,
        exit=exit_price,
        market_return=market_return,
        signed_return=signed,
        correct=is_correct(row.direction, market_return, flat_band=flat_band),
        # The null model is "always long this asset over this window" — captured per call
        # so scoring can compare a person against their own exact slate.
        null_return=market_return,
        null_correct=market_return > 0,
        benchmark_return=benchmark_return,
        excess_return=None if benchmark_return is None else signed - benchmark_return,
    )


def _benchmark_return(benchmark, start: date, end: date) -> float | None:
    """The benchmark's move over the identical window, or None if it can't be priced."""
    if benchmark is None:
        return None
    open_price = benchmark.close_on(start)
    close_price = benchmark.close_on(end)
    if not open_price or close_price is None:
        return None
    return (close_price - open_price) / open_price
