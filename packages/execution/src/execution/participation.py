"""How much of a thin equity market one order would be. Pure.

The perp liquidity gate in ``guards.check_liquidity`` cannot answer this. Its three refusal
codes are named for perp facts — open interest, a book snapshot, a near-touch depth fraction —
and two of the three do not exist for an equity at all. So this is its own check rather than a
widening of that one, and it measures the only thing an equity venue does publish: sessions.

**It caps rather than refuses**, which is the substantive difference from the perp gate. The
size is derived from a risk budget, so a market too thin to carry that budget is not
automatically a market not worth trading — it is a market that can carry *less*. Shrinking the
order and showing the new number lets the person at the prompt decide, and the shrunken risk
is itself the clearest statement of how thin the market is: ``INTL`` capped to 1% of its
median session risks 0.15% of the account instead of the 1% asked for.

WHY THE MEDIAN, AND WHY 30 SESSIONS. ``INTL``'s daily volume spans 7,966 to 222,872 shares
inside one month — a 28x range, with the two spike sessions ~9x the typical one. Sizing off
the latest session would have called it liquid on 2026-07-28 (222,872 shares) and dust on
2026-07-27 (12,088), a day apart. The same reasoning as ``core``'s move to a monthly median
for funding: a statistic taken from too few sessions describes the sample, not the market.

MEASURED 2026-07-28, Alpaca SIP, 30 sessions. Participation of the risk-derived order against
the median session, across the eight candidates then queued and mapped to Alpaca:

    INTL   6.6%   <- median 24,707 sh/day,        175 trades/day
    SBSW   0.04%  <- median 5,358,410 sh/day,  26,862 trades/day
    the other six: 0.00%

The distribution is not a continuum, it is one outlier and a floor. That is what makes a
ceiling easy to set: anything from 0.1% to 5% separates them, so 1% is chosen to sit far from
both edges rather than tuned to either.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from execution.guards import Refusal

# A market that reported sessions but no trading in them. Distinct from the perp gate's
# ``no_book`` — that means the venue published nothing, this means the venue published
# "nothing happened", which is a stronger and more alarming statement.
REFUSAL_NO_DEPTH = "no_depth"

# Fraction of a median session this order may be. See the module docstring for the measurement
# behind it; the short version is that 1% is inert on every candidate the engine has produced
# except one, and that one is 150x worse than the next.
MAX_PARTICIPATION = 0.01


@dataclass(frozen=True)
class Depth:
    """What a typical session in this market looks like. All medians, never means.

    ``median_trades`` is carried alongside volume because they answer different questions and
    disagree usefully: volume says how much changed hands, trade count says how many chances
    there were to get out. ``INTL`` and ``SBSW`` differ by 200x in volume but by 150x in trade
    count, and it is the second number that says a stop there has nothing to fill against.
    """
    sessions: int
    median_volume: float          # shares
    median_trades: float
    median_dollar_volume: float

    @property
    def dollars_per_trade(self) -> float:
        return self.median_dollar_volume / self.median_trades if self.median_trades else 0.0


def depth_from_bars(bars) -> Depth | None:
    """Medians over daily bars, or ``None`` if none were usable.

    ``None`` and a measured zero are deliberately different returns — see ``check_depth``. A
    bar missing or malforming any of its three fields is skipped rather than defaulted to
    zero, because a zero would drag the median toward "dead" on the strength of a parse error.
    """
    if not bars:
        return None

    volumes, trades, dollars = [], [], []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        try:
            volume = float(bar["v"])
            count = float(bar["n"])
            # VWAP where the feed gives it, close otherwise. Either beats a session's own
            # volume times some other session's price.
            price = float(bar.get("vw") or bar["c"])
        except (KeyError, TypeError, ValueError):
            continue
        volumes.append(volume)
        trades.append(count)
        dollars.append(volume * price)

    if not volumes:
        return None

    return Depth(
        sessions=len(volumes),
        median_volume=median(volumes),
        median_trades=median(trades),
        # Taken as the median of per-session products, not the product of two medians: the
        # latter multiplies one session's volume by a different session's price and describes
        # a session that never happened.
        median_dollar_volume=median(dollars),
    )


def max_shares(depth: Depth, ceiling: float = MAX_PARTICIPATION) -> float:
    """The largest order that stays within ``ceiling`` of a median session. Unrounded —
    ``shares.round_shares`` floors it, same as every other size in this package."""
    return depth.median_volume * ceiling


def check_depth(depth: Depth | None) -> Refusal | None:
    """Refuse a market that measurably does not trade. ``None`` depth is **not** a refusal.

    This asymmetry is the whole point and it is load-bearing. Treating "not measured" as a
    refusal is exactly what forced ``Config.liquidity_enforced`` off for this venue: an
    unmeasured market refused, ``AlpacaBroker.liquidity`` could never measure, so the gate
    would have refused every equity forever. A fetch that did not answer must degrade to "no
    cap applied", never to "no trade" — the failure mode of a wrong cap is a smaller order,
    and the failure mode of a wrong refusal is no strategy at all.
    """
    if depth is None:
        return None
    if depth.median_volume > 0:
        return None
    return Refusal(
        REFUSAL_NO_DEPTH,
        f"{depth.sessions} sessions measured and the median one traded nothing — "
        f"an order here would rest forever and a stop could not fill at any price",
    )
