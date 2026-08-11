"""Generate candidates as of a past date, without reading anything from after it.

The engine has only ever run at ``now``. Every probe in the repo opens with
``as_of = datetime.now(UTC).date()``, which makes each of them a snapshot; this turns the same
engine into a distribution over time, which is what any claim about edge needs.

**The arms.** A thesis contributes two separable things — *which* assets to look at and *which
way* — so the counterfactuals are a (universe x direction-rule) grid, and each factor is
isolated by holding the other fixed. Comparing a thesis arm against a structural arm over a
different universe would tangle the two and answer neither.

**A thesis is not a gate that can be switched off.** ``cross_reference`` takes one row and one
context; without a row there is no candidate at all, not an ungated one. So the counterfactual
arms *synthesise* rows — same asset, same date, direction from somewhere other than a person.

**The arms are outcome-comparable, never score-comparable, and that is by construction rather
than by convention.** A synthetic row is published on ``as_of`` (so freshness is always
maximal), carries no ``key_levels`` (so ``target_source`` always falls back to structural), and
stands alone (so ``agreement_count`` is always zero). ``TREND`` additionally takes its direction
*from* the weekly, so it can never trip ``weekly_disagrees`` — the largest gate in the engine.
Reporting a score column across arms would compare numbers built from different terms.

``RANDOM`` exists because ``TREND`` is a strong null in its own right: trend-following works, so
a trend arm that matches the thesis arm proves much less than it appears to. A seeded direction
has no edge by construction and, because it can disagree with the weekly, takes the same gate
exposure the thesis arm does. Run it across several seeds — one draw is itself a coin flip.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256

from core.rank import parse_date
from core.setups import family_of
from core.structure import BEARISH, BULLISH, trend_state

from oracle.resample import to_weekly
from oracle.series import PriceSeries

# ── direction rules ─────────────────────────────────────────────────────────

THESIS = "thesis"
TREND = "trend"
RANDOM = "random"
ALWAYS_LONG = "long"
ALWAYS_SHORT = "short"
FLAT = "flat"

SYNTHETIC_RULES = (TREND, RANDOM, ALWAYS_LONG, ALWAYS_SHORT, FLAT)

# The timeframe a synthetic row claims to speak on. ``swing`` is the corpus median and picks a
# mid-range half-life; the choice matters only through ``HalfLife``, and since a synthetic row
# is always published on ``as_of`` its freshness is maximal whatever window it gets.
SYNTHETIC_TIMEFRAME = "swing"

# Bars of history an asset needs before its structure is worth reading. **Measured, not chosen**
# — `scripts/probe_price_cache.py` sweeps it and the curve knees at 180 days and plateaus at
# 270, so past 270 every remaining refusal is structural rather than a shortage of bars. The
# 365 this was originally designed around was a guess and cost ~95 days of samplable window.
DEFAULT_WARMUP_DAYS = 270

# Refusal reason for an asset whose history is too short to read. **A data verdict, and it needs
# its own name.** Without it these assets refuse as ``no_dealing_range`` — a structure verdict
# standing in for "nobody fetched the bars", which is the same conflation that hid 256 rows
# inside ``weekly_disagrees`` until ranging was split out of it.
INSUFFICIENT_HISTORY = "insufficient_history"


@dataclass(frozen=True)
class SyntheticRow:
    """Duck-typed to match what ``cross_reference`` reads off a corpus row.

    Frozen and explicit rather than a ``SimpleNamespace`` so that adding a field to the real
    ``CorpusRow`` fails loudly here instead of silently producing a differently-gated arm.
    """
    id: str
    asset: str
    person: str
    direction: str
    timeframe: str
    published_at: str
    key_levels: tuple = ()
    domain: str = "crypto"


def live_rows(rows, as_of: date) -> list:
    """Corpus rows knowable on ``as_of``.

    ``published_at == as_of`` is **included**: something published that day was knowable by the
    end of it, which is what every ``as_of`` in ``core.structure`` already means. Undated rows
    are dropped — they cannot be placed in time, and admitting them would let a view written
    afterwards influence a date before it.
    """
    out = []
    for row in rows:
        when = parse_date(getattr(row, "published_at", None))
        if when is not None and when <= as_of:
            out.append(row)
    return out


def warmed(bars, as_of: date, *, warmup_days: int = DEFAULT_WARMUP_DAYS) -> bool:
    """Does this series carry enough history *before* ``as_of`` to read structure from?

    Measured against the earliest bar rather than a bar count, because a count cannot tell a
    thinly-traded instrument from a recently-listed one and only the second is disqualifying.
    """
    prior = [b for b in bars if b.date <= as_of]
    if not prior:
        return False
    return (as_of - prior[0].date).days >= warmup_days


def trend_direction(series: PriceSeries, as_of: date) -> str | None:
    """Which way weekly structure points on ``as_of``. None when it is ranging.

    Reads ``family_of`` rather than re-deriving the mapping: a second copy of "which states are
    bullish" would drift from the one the gates use, and then the null arm would be judged
    against a definition the thesis arm never saw.

    **Resamples the whole series and lets ``as_of`` do the filtering — do not truncate first.**
    ``to_weekly`` drops the trailing partial group, so on a series cut at ``as_of`` the final
    *complete* week is the trailing group and gets dropped with it. That silently discards a
    week that was genuinely available, and the two paths disagree: measured on a 20-week
    fixture, truncate-then-resample reads ``ranging`` where the engine reads ``uptrend``.

    Truncating is safe only because ``to_weekly`` stamps each weekly bar with its **last**
    constituent day, so a week straddling ``as_of`` is stamped after it and the filter excludes
    it whole. Were it stamped week-start, this function would be reading the future.
    """
    weekly = to_weekly(series)
    if not weekly.bars:
        return None
    family = family_of(trend_state(weekly.bars, as_of=as_of))
    if family == BULLISH:
        return "long"
    if family == BEARISH:
        return "short"
    return None


def random_direction(asset: str, as_of: date, *, seed: int) -> str:
    """A direction with no information in it, reproducibly.

    Hashed rather than drawn from ``random`` so the arm is a pure function of its inputs — a
    replayed run reproduces bar for bar, and two seeds differ everywhere rather than by an
    offset into one stream.
    """
    key = f"{asset}|{as_of.isoformat()}|{seed}".encode()
    return "long" if sha256(key).digest()[0] % 2 else "short"


def synthetic_rows(
    assets_bars: dict,
    as_of: date,
    *,
    rule: str,
    seed: int = 0,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
) -> tuple[list[SyntheticRow], dict[str, str]]:
    """-> (rows, why-not per skipped asset).

    The refusals are returned rather than dropped for the same reason ``BuildStats`` counts
    them: an arm that produced nothing and an arm that was never offered anything look
    identical otherwise, and the whole comparison rests on the arms having been offered the
    same universe.
    """
    if rule not in SYNTHETIC_RULES:
        raise ValueError(f"unknown direction rule {rule!r}; expected one of {SYNTHETIC_RULES}")

    rows: list[SyntheticRow] = []
    skipped: dict[str, str] = {}
    for asset in sorted(assets_bars):
        series = assets_bars[asset]
        if not warmed(series.bars, as_of, warmup_days=warmup_days):
            skipped[asset] = INSUFFICIENT_HISTORY
            continue
        direction = _direction_for(asset, series, as_of, rule=rule, seed=seed)
        if direction is None:
            skipped[asset] = "ranging" if rule == TREND else "no_direction"
            continue
        rows.append(SyntheticRow(
            id=f"{rule}/{seed}/{asset}/{as_of.isoformat()}",
            asset=asset,
            person=f"__{rule}__",
            direction=direction,
            timeframe=SYNTHETIC_TIMEFRAME,
            published_at=as_of.isoformat(),
        ))
    return rows, skipped


def _direction_for(asset, series, as_of, *, rule: str, seed: int) -> str | None:
    if rule == TREND:
        return trend_direction(series, as_of)
    if rule == RANDOM:
        return random_direction(asset, as_of, seed=seed)
    if rule == ALWAYS_LONG:
        return "long"
    if rule == ALWAYS_SHORT:
        return "short"
    return None            # FLAT takes no position, so it yields no rows at all


# NOTE — there is deliberately no truncate-the-series helper here, and the omission is the
# result rather than an oversight.
#
# Pre-cutting each series at ``as_of`` reads as the safe, obvious thing to do, and it is wrong.
# ``to_weekly`` drops the trailing partial group; on a series cut at ``as_of`` the last
# *complete* week IS the trailing group, so truncating throws away a week that was genuinely
# available. Measured on a 20-week fixture, the two paths disagree — ``ranging`` truncated
# against ``uptrend`` through the engine's own filter — and the truncated answer is the wrong
# one.
#
# The load-bearing guard is that every reader in ``core.structure`` filters by ``as_of``, and
# that ``to_weekly`` stamps a weekly bar with its LAST constituent day so a straddling week is
# stamped after ``as_of`` and excluded whole. ``test_asof`` proves this by appending 20 weeks of
# violent reversal after ``as_of`` and requiring the Context not to move — and proves the test
# itself has teeth by mutating the filter out and watching it go red.


def grid(start: date, end: date, *, step_days: int = 7) -> list[date]:
    """As-of dates, evenly spaced.

    Weekly by default and deliberately not daily: consecutive as-of dates share structure and
    theses almost entirely, so daily sampling yields ~7x the rows carrying nearly the same
    information. Naive intervals then shrink by sqrt(7), which is not free — it is wrong.
    """
    if step_days < 1:
        raise ValueError("step_days must be at least 1")
    out, cursor = [], start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=step_days)
    return out
