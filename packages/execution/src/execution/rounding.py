"""Hyperliquid's tick and lot rules — pure, and the most load-bearing 40 lines here.

An order whose price or size carries too much precision does not bounce with a useful
message. It either raises inside the SDK's ``float_to_wire`` (which refuses anything beyond
8 decimal places) or is rejected by the venue with a generic error indistinguishable from a
malformed request. **The SDK does no rounding of its own** — ``Exchange.order`` passes
``sz`` and ``limit_px`` straight through — so this module is the only thing standing between
a float derived from bar arithmetic and a rejected order.

The rules, reverse-engineered from live L2 books on 2026-07-27 and reproduced exactly by
``tests/test_rounding.py``:

* **Size** rounds to the market's ``szDecimals``, always **downward**.
* **Price** obeys *both* a significant-figure cap and a decimal-place cap, whichever binds
  tighter — ``MAX_SIG_FIGS`` significant figures, and ``MAX_DECIMALS - szDecimals`` decimal
  places. On ETH (szDecimals 4) the sig-fig rule binds at 1dp though 2dp would be legal by
  the other; on a sub-cent asset the decimal rule binds instead.
* **Integers are always legal**, whatever their significant-figure count. Without this
  carve-out a six-figure BTC price would be unrepresentable.

Everything goes through ``Decimal``. Doing this in binary floats reintroduces exactly the
representation error the module exists to remove.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

# Perpetuals. Spot uses 8; this package only trades perps, and a wrong constant here is a
# silent rejection rather than a crash, so it is named rather than inlined.
MAX_DECIMALS = 6

MAX_SIG_FIGS = 5


def round_size(size: float, sz_decimals: int) -> float:
    """Round a size down to the market's lot.

    **Downward, not to nearest.** Rounding up spends more than the risk budget authorised.
    The error is small per trade and always in the same direction, which is precisely what
    makes it worth eliminating rather than tolerating.

    A size below one lot returns ``0.0`` rather than being promoted to the minimum. Zero is
    a refusal the guards act on; promoting it would place an order many times the intended
    size on exactly the assets where the lot is coarsest.
    """
    if size <= 0:
        return 0.0
    quantum = Decimal(1).scaleb(-sz_decimals)
    return float(Decimal(str(size)).quantize(quantum, rounding=ROUND_DOWN))


def round_price(price: float, sz_decimals: int, *, max_decimals: int = MAX_DECIMALS) -> float:
    """Round a price to the tightest of the venue's two precision limits.

    Rounds to *nearest* rather than in a conservative direction, deliberately. A price is
    rounded by at most half a tick, and which direction is "safe" flips between entry, stop
    and target and between long and short — four sign conventions to get right for a
    sub-tick effect. Sizing runs off the **rounded** prices instead (see ``plan``), so the
    risk budget stays exact with respect to what is actually sent.
    """
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")

    d = Decimal(str(price))

    # Integers bypass the significant-figure cap entirely — a venue rule, not an
    # optimisation. ``adjusted()`` on a large value would otherwise demand a negative number
    # of decimal places and round 123456 away to 123460.
    integral = d.to_integral_value(rounding=ROUND_HALF_UP)

    # ``adjusted()`` is floor(log10(|d|)), so a value with N digits before the point has
    # adjusted() == N-1. Allowing MAX_SIG_FIGS of them leaves this many after the point.
    sig_fig_places = MAX_SIG_FIGS - 1 - d.adjusted()
    decimal_places = max_decimals - sz_decimals

    places = min(sig_fig_places, decimal_places)
    if places <= 0:
        return float(integral)

    quantum = Decimal(1).scaleb(-places)
    return float(d.quantize(quantum, rounding=ROUND_HALF_UP))
