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
