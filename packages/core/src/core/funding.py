"""Funding — what it costs to *hold* a perpetual, as distinct from what it costs to enter one.

A perpetual has no expiry, so nothing forces its price to converge on spot. Funding is the
mechanism that does: a recurring payment between the two sides of the book, sized off the
perp's premium to its index price. It is a transfer between traders, not a venue fee, which
is why a zero-fee venue can still be the expensive place to hold a position.

**The sign convention is the load-bearing fact in this module.** All three venues we read
(Hyperliquid, Lighter, Aster) quote it the same way:

    rate > 0  ->  longs pay shorts   (the crowd is long; being short is paid to wait)
    rate < 0  ->  shorts pay longs   (the crowd is short)

Inverting it would flip the direction of every conclusion downstream while still producing
entirely plausible-looking numbers, so it is asserted by test rather than left to a comment.

**Three units are in play and conflating them is the other easy mistake:**

    rate            the per-interval fraction the venue quotes  (e.g. 3.2e-05)
    interval_hours  how often that rate is actually charged     (1 on HL/Lighter, 1 *or* 8
                    on Aster — it varies per symbol, so it cannot be assumed per venue)
    annualized      rate / interval_hours * 24 * 365 — the only unit comparable across venues

Never store an annualized figure alone, and never store a rate without its interval: the pair
is the observation. A bare 8h rate read as hourly is wrong by 8x, and 8x on a carry term is
the difference between a setup that clears R:R and one that doesn't.

This module is pure: no I/O, no network. Venue adapters live in ``oracle.sources``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median as _median

HOURS_PER_YEAR = 24 * 365

SIDES = ("long", "short")


def annualized(rate: float, interval_hours: float) -> float:
    """Convert a per-interval funding rate into a fraction of notional per year.

    The result is a *rate*, not a compounded return — funding is paid out of margin rather
    than reinvested, and treating it as compounding would overstate long holds.
    """
    if interval_hours <= 0:
        raise ValueError(f"interval_hours must be positive, got {interval_hours}")
    return rate / interval_hours * HOURS_PER_YEAR


@dataclass(frozen=True, slots=True)
class FundingRate:
    """One venue's funding rate for one symbol, at one instant.

    ``symbol`` is deliberately the *venue-native* ticker, not a canonical one. Mapping to
    canon happens at read time so that a mapping bug can never corrupt the stored log —
    the same reason ``oracle_map.yaml`` refuses to guess rather than routing by name match.
    """

    venue: str
    symbol: str
    rate: float
    interval_hours: float
    observed_at: datetime

    @property
    def annualized(self) -> float:
        return annualized(self.rate, self.interval_hours)


def carry_cost(annual_rate: float, days: float, side: str) -> float:
    """Fraction of notional paid (positive) or received (negative) over ``days``.

    A long pays when the rate is positive; a short pays when it is negative. Linear in time,
    because funding accrues per interval and is not reinvested.
    """
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")
    cost = annual_rate * (days / 365.0)
    return cost if side == "long" else -cost


@dataclass(frozen=True, slots=True)
class CarryAdjustedRR:
    """A reward:risk ratio with the holding cost of the position folded in.

    Carry hits both legs and in opposite directions, which is why it moves R:R so much more
    than its raw size suggests: it is subtracted from the reward *and* added to the risk.
    """

    nominal: float
    reward: float
    risk: float
    carry: float
    ratio: float | None
    carry_dominates: bool


def carry_adjusted_rr(
    reward: float,
    risk: float,
    annual_rate: float,
    days: float,
    side: str,
) -> CarryAdjustedRR:
    """Re-derive R:R after paying (or collecting) funding for ``days``.

    ``reward`` and ``risk`` are positive fractions of entry price — the distance to target
    and to stop respectively. Both must be positive; a zero-risk trade is degenerate and is
    refused here for the same reason ``core.setups`` refuses a ``degenerate_zone``.

    Two outcomes are not ordinary numbers and are flagged rather than smoothed over:

    - **Carry eats the target** (``reward`` < 0). The ratio goes negative. The setup loses
      money at its own target, which is a real and actionable verdict.
    - **Credit exceeds the stop** (``risk`` <= 0). The position collects more than it can
      lose. ``ratio`` is ``None`` rather than infinity — the trade is genuinely unbounded on
      this metric and a sentinel number would silently win every ranking it entered.
    """
    if risk <= 0:
        raise ValueError(f"risk must be positive, got {risk}")

    carry = carry_cost(annual_rate, days, side)
    net_reward = reward - carry
    net_risk = risk + carry

    if net_risk <= 0:
        ratio = None
    else:
        ratio = net_reward / net_risk

    return CarryAdjustedRR(
        nominal=reward / risk,
        reward=net_reward,
        risk=net_risk,
        carry=carry,
        ratio=ratio,
        carry_dominates=net_risk <= 0 or net_reward <= 0,
    )


@dataclass(frozen=True, slots=True)
class FundingStats:
    """The empirical distribution of an annualized rate over a window of observations.

    The spread is the point. A single snapshot of funding is worth very little — funding
    mean-reverts and its tails are where positions die — so the usable quantities are a
    central estimate to price the trade at and an upper tail to stress it against.
    """

    n: int
    median: float | None
    mean: float | None
    p10: float | None
    p90: float | None


@dataclass(frozen=True, slots=True)
class FundingOutlook:
    """What one venue's funding on one asset has actually been doing, ready to price a trade.

    Distinct from ``FundingStats`` on purpose: stats describe a sample, an outlook is the
    thing a setup is costed against. It names its venue because carry is only real on the
    venue you would actually trade — pricing a Hyperliquid-depth position at Aster's zero
    would flatter every candidate for a book you cannot get size into.

    ``median`` prices the trade, ``p90`` stresses it. ``n`` travels with them so a reader can
    tell a measured rate from one observation.
    """

    venue: str
    median: float
    p90: float
    n: int = 0

    @classmethod
    def from_stats(cls, venue: str, stats: FundingStats) -> FundingOutlook | None:
        """None when there is nothing to price with — the caller then skips the adjustment
        rather than costing a trade at an invented zero."""
        if not stats.n or stats.median is None or stats.p90 is None:
            return None
        return cls(venue=venue, median=stats.median, p90=stats.p90, n=stats.n)


def _percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile. No interpolation: with the handful of samples a nightly
    logger accumulates in its first weeks, interpolating invents precision we don't have."""
    if len(values) == 1:
        return values[0]
    idx = max(0, min(len(values) - 1, round(q * (len(values) - 1))))
    return values[idx]


def summarize(rates: list[FundingRate]) -> FundingStats:
    """Collapse observations into a distribution, normalizing intervals across venues."""
    if not rates:
        return FundingStats(n=0, median=None, mean=None, p10=None, p90=None)
    annual = sorted(r.annualized for r in rates)
    return FundingStats(
        n=len(annual),
        median=_median(annual),
        mean=sum(annual) / len(annual),
        p10=_percentile(annual, 0.10),
        p90=_percentile(annual, 0.90),
    )
