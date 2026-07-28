"""How many contracts — pure arithmetic off the stop the engine already computed.

Sizing off the stop rather than a fixed notional is the whole reason this is worth a module.
``Candidate`` zones vary by an order of magnitude in width — a weekly order block against a
daily one — so a fixed dollar size risks wildly different amounts trade to trade. Anchoring
to ``|entry - stop|`` makes every trade risk the same fraction of the account, which is the
only way a decision log full of wins and losses can be read as a strategy rather than as a
record of position sizes.

Every refusal here raises rather than clamping. A caller that passes a zero stop distance or
a 150% risk budget has a bug or a typo, and quietly substituting a "reasonable" number would
place a real order on it.
"""
from __future__ import annotations


def size_for_risk(
    *,
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
    max_notional_frac: float | None = None,
) -> float:
    """Contracts to buy or sell so that being stopped out costs ``equity * risk_pct``.

    ``max_notional_frac`` caps position notional at that fraction of equity (``1.0`` = no
    leverage, ``3.0`` = up to 3x). It exists because risk-based sizing and a very tight stop
    combine badly: as the stop approaches the entry the implied size grows without bound, so
    a narrow zone can turn a 1% risk budget into a 30x position. Left as ``None`` the cap is
    not applied at all — the caller opts in.

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

    size = (equity * risk_pct) / distance

    if max_notional_frac is not None:
        if max_notional_frac <= 0:
            raise ValueError(
                f"max_notional_frac must be positive, got {max_notional_frac}"
            )
        # Priced at the entry, not at the current mark: this is a resting limit order, so
        # the entry is the price the notional will actually be established at.
        ceiling = (equity * max_notional_frac) / entry
        size = min(size, ceiling)

    return size


def risk_of(size: float, *, entry: float, stop: float) -> float:
    """What a given size actually risks — the inverse, used for the confirmation preview.

    Reported rather than assumed because rounding moves it: the size that goes to the venue
    is floored to a whole lot, so the realised risk is always slightly *under* the budget and
    on a coarse-lot market can be materially under it. Showing the number that follows from
    the order being sent beats showing the number that was requested.
    """
    return size * abs(entry - stop)
