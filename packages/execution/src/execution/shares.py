"""US equity tick and lot rules — pure. The equity counterpart to ``rounding``.

``rounding`` reverse-engineers Hyperliquid's grid from live books because the venue publishes
no rule. These are the opposite case: both rules are published, and the value of writing them
down is that they are *different* from the perp ones in a way that is silent when wrong.

* **Size is whole shares, floored.** Not a tick rule — an Alpaca order-class constraint. A
  fractional or notional order is rejected if it carries bracket legs (``42210000``), and this
  package only ever sends brackets. Flooring costs 0.2-0.8% of intended size across the 38
  approved decisions measured 2026-07-28; rounding up would overspend the risk budget, which
  is the direction that must never happen.
* **Price is the penny above $1.00 and the sub-penny below it**, per SEC Rule 612. The 2024
  amendment added a half-penny tier for some highly-liquid names, but $0.01 is a multiple of
  $0.005, so the penny grid stays valid everywhere and needs no per-symbol tick lookup.

Why not reuse ``round_price``: on a $29 stock its significant-figure cap allows three decimal
places, so it would quote 29.025 — finer than Rule 612 permits, and rejected. The two grids
genuinely differ and collapsing them would be a silent rejection rather than a crash.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

# The price at or above which the penny grid applies. Rule 612 reads "priced at or above
# $1.00", so the boundary belongs to the coarser grid.
SUB_DOLLAR_THRESHOLD = Decimal("1.00")

PENNY = Decimal("0.01")
SUB_PENNY = Decimal("0.0001")


def round_shares(size: float) -> float:
    """Floor a size to whole shares.

    Returns ``0.0`` below one share rather than promoting to the minimum — zero is a refusal
    ``guards.check_size`` already acts on, and promoting it would place an order larger than
    the risk budget authorised on exactly the most expensive instruments.
    """
    if size <= 0:
        return 0.0
    return float(Decimal(str(size)).quantize(Decimal(1), rounding=ROUND_DOWN))


def round_share_price(price: float) -> float:
    """Round a limit price onto the grid Rule 612 allows for that price tier.

    Rounds to *nearest*, matching ``round_price`` and for the same reason: which direction is
    "safe" flips between entry, stop and target and between long and short, and sizing runs
    off the rounded prices anyway (see ``plan``), so the risk budget stays exact with respect
    to what is actually transmitted.
    """
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    d = Decimal(str(price))
    tick = PENNY if d >= SUB_DOLLAR_THRESHOLD else SUB_PENNY
    return float(d.quantize(tick, rounding=ROUND_HALF_UP))
