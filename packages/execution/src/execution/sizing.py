"""How many contracts — pure arithmetic off the stop the engine already computed.

Sizing off the stop rather than a fixed notional is the whole reason this is worth a module.
``Candidate`` zones vary by an order of magnitude in width — a weekly order block against a
daily one — so a fixed dollar size risks wildly different amounts trade to trade. Anchoring
to ``|entry - stop|`` makes every trade risk the same fraction of the account, which is the
only way a decision log full of wins and losses can be read as a strategy rather than as a
record of position sizes.

**The risk budget is a ceiling the order may not reach, not a target it must hit.** Four
separate things can hold it below what the stop implies, and they are genuinely different
questions — leverage, concentration, the market's own thinness, and how full the account is.
They are applied as one ``min()`` here so that the *binding* one can be named: a size that
came out four times smaller than asked for is a fact the confirmation prompt must explain,
and "which ceiling did that" is the only useful form of the explanation.

Every refusal here raises rather than clamping. A caller that passes a zero stop distance or
a 150% risk budget has a bug or a typo, and quietly substituting a "reasonable" number would
place a real order on it.
"""
from __future__ import annotations

from collections.abc import Iterable

# Why an order came out smaller than the risk budget asked for. These are not interchangeable
# and the preview says which one applied, because each calls for a different response: raise
# the leverage ceiling, accept a smaller slice of the book, trade something more liquid, or
# cancel a resting order.
CAP_LEVERAGE = "leverage"            # notional as a multiple of equity — see max_notional_frac
CAP_CONCENTRATION = "concentration"  # one position's share of the book — see max_position_frac
CAP_PARTICIPATION = "participation"  # share of a median session — see ``participation``
CAP_BUDGET = "budget"                # what the account has left to commit — see ``budget``
# What the VENUE will hold overnight, from its own live answer rather than a written-down number.
# Distinct from CAP_LEVERAGE because the remedy differs: that one is a setting you may raise,
# this one is Reg T and nothing in cfg/ can change it. See ``account.overnight_multiplier``.
CAP_VENUE_LEVERAGE = "venue_leverage"
# What the whole book has left to risk, ACROSS venues — see ``portfolio``. The only one of the
# five that is not a fact about this venue: a position on the other venue can bind it.
CAP_PORTFOLIO = "portfolio"
# What the EDGE supports, as opposed to what the account can carry. The other six ceilings all
# answer "can this position fit"; this one answers "is this trade worth its budget", and the
# remedy is neither a setting nor a different market — it is a better trade, or a better
# engine. See ``kelly_risk_pct``.
CAP_KELLY = "kelly"


# Kelly asks for a bet that maximises long-run GROWTH, which is not the same objective as
# surviving a drawdown you chose in advance. Two constants convert one into the other.
#
# The multiplier is the important one, and the asymmetry behind it is exact rather than
# cautious: betting DOUBLE the Kelly fraction drives the long-run growth rate to exactly
# zero, while betting HALF still earns 75% of the maximum. Overbetting is a cliff and
# underbetting is a slope. A quarter buys a wide margin for `p` being wrong, which on any
# sample this engine has produced it certainly is.
KELLY_FRACTION = 0.25

# The ceiling on what one trade may risk however good it looks. Kelly is unbounded in `b`, so
# a 20R target on a tight zone asks for roughly a tenth of the account — arithmetic that is
# only correct if a 20R target is a thing that happens. It is a forecast, not a fill.
KELLY_CAP = 0.02

# Below this, a Kelly fraction is float noise and not a bet. `p - (1-p)/b` is EXACTLY zero at
# break-even in arithmetic but not in binary: b=1.83 and b=2.23 both leave 5.55e-17 behind,
# which is positive, so a plain `> 0` test sizes a trade the formula just refused. Any real
# bet is at least 1e-4 of equity, so this sits far below anything meaningful and far above the
# residue. Deleting it silently re-admits break-even trades.
KELLY_EPSILON = 1e-12


def kelly_risk_pct(*, win_rate: float, reward_risk: float,
                   fraction: float = KELLY_FRACTION, cap: float = KELLY_CAP) -> float:
    """Fraction of equity to RISK on one trade, from ``f = p - (1-p)/b``.

    A drop-in for the flat ``risk_pct``, and it is the same unit: what a stopped-out trade
    costs, not what the position is worth. So ``size_for_risk`` takes this result unchanged.

    **Below break-even this returns 0.0, and that is a refusal rather than a small bet.** A
    negative ``f`` does not mean "bet a little", it means the bet is pointed the wrong way —
    the trade loses money at every size, so there is no size that rescues it. Quartering a
    negative number would turn that verdict into a real order, which is why the clamp happens
    before the multiplier and not after.

    ``win_rate`` is a property of the ENGINE, measured across many trades by
    ``scripts/probe_evidence.py``. It is deliberately not a per-candidate input: nothing in
    this repo predicts which individual setup wins — the score's AUC against outcomes is
    below chance — so a per-trade ``p`` would be an invention. ``reward_risk`` is the opposite
    case and is why Kelly is worth having here at all: it is known exactly, per candidate,
    before the order goes out.

    Both bounds are raises rather than clamps. A caller passing ``40`` for "40%" has a typo,
    and a helpful substitution would place a real order on it.
    """
    if not 0 <= win_rate <= 1:
        raise ValueError(f"win_rate must be in [0, 1], got {win_rate}")
    if reward_risk <= 0:
        raise ValueError(f"reward_risk must be positive, got {reward_risk}")
    if not 0 < fraction <= 1:
        raise ValueError(f"fraction must be in (0, 1], got {fraction}")
    if not 0 < cap <= 1:
        raise ValueError(f"cap must be in (0, 1], got {cap}")

    full = win_rate - (1 - win_rate) / reward_risk
    if full <= KELLY_EPSILON:
        return 0.0
    return min(full * fraction, cap)


def breakeven_win_rate(reward_risk: float) -> float:
    """The ``p`` at which a payoff of ``b`` stops losing money: ``1 / (1 + b)``.

    Separate from ``kelly_risk_pct`` because it answers the question a person asks *before*
    sizing — "could this ever work?" — and it answers it without needing a win rate at all.
    A 1.83 R:R trade must win more than a third of the time; that is a fact about the zone
    the engine drew, available the moment it is drawn.
    """
    if reward_risk <= 0:
        raise ValueError(f"reward_risk must be positive, got {reward_risk}")
    return 1 / (1 + reward_risk)


def size_for_risk(*, equity: float, risk_pct: float, entry: float, stop: float) -> float:
    """Contracts to buy or sell so that being stopped out costs ``equity * risk_pct``.

    The size this asks for, before any ceiling. ``apply_caps`` is what holds it down; keeping
    the two apart means this function has exactly one thing to be right about, and the reason
    a size shrank is never buried inside the arithmetic that produced it.

    The returned size is **unrounded**; ``rounding.round_size`` applies the venue's lot.
    """
    if equity <= 0:
        raise ValueError(f"equity must be positive, got {equity}")
    if not 0 < risk_pct <= 1:
        raise ValueError(f"risk_pct must be in (0, 1], got {risk_pct}")

    distance = abs(entry - stop)
    if distance == 0:
        raise ValueError(
            f"entry and stop are both {entry}; a zone with no stop distance cannot be sized"
        )

    return (equity * risk_pct) / distance


def notional_ceiling(*, equity: float, entry: float, frac: float | None) -> float | None:
    """Largest size whose notional stays within ``frac`` of equity. ``None`` passes through.

    One function for two settings that share an arithmetic and not a meaning.
    ``max_notional_frac`` is a **leverage** ceiling — it exists because a stop a hair from the
    entry turns a 1% risk budget into a 30x position, and 3.0 is a perp number.
    ``max_position_frac`` is a **concentration** ceiling — it exists because the median
    approved candidate wants 17% of equity, so five of them fill a cash account and the sixth
    is refused for reasons that have nothing to do with the sixth trade.

    Priced at the entry, not at the current mark: this is a resting limit order, so the entry
    is the price the notional will actually be established at.
    """
    if frac is None:
        return None
    if frac <= 0:
        raise ValueError(f"a notional fraction must be positive, got {frac}")
    if entry <= 0:
        raise ValueError(f"entry must be positive, got {entry}")
    return (equity * frac) / entry


def apply_caps(wanted: float,
               caps: Iterable[tuple[str, float | None]]) -> tuple[float, str | None]:
    """The smallest ceiling wins, and says so. ``None`` ceilings are "not measured", not zero.

    Returns ``(size, reason)`` where ``reason`` is ``None`` when nothing bound — which is the
    ordinary case and the one the preview must stay silent about. A ceiling equal to the
    wanted size is not a cap: it changed nothing, so naming it would explain a difference that
    is not there.
    """
    size, reason = wanted, None
    for name, ceiling in caps:
        if ceiling is not None and ceiling < size:
            size, reason = ceiling, name
    return size, reason


def risk_of(size: float, *, entry: float, stop: float) -> float:
    """What a given size actually risks — the inverse, used for the confirmation preview.

    Reported rather than assumed because rounding moves it: the size that goes to the venue
    is floored to a whole lot, so the realised risk is always slightly *under* the budget and
    on a coarse-lot market can be materially under it. Showing the number that follows from
    the order being sent beats showing the number that was requested.
    """
    return size * abs(entry - stop)
