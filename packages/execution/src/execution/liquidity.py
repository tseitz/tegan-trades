"""Is this market real enough to hold a position in for three weeks? Pure.

HIP-3 lets anyone with a HYPE stake deploy a perp market, and the deployer operates the
oracle. That makes "which market" a question with a wrong answer, in a way it never was on
the validator-run core book. The top builder markets are excellent — ``xyz:SP500`` quotes a
tighter spread than core-book BTC — but the same builder also carries markets with **no book
at all**, and ``cfg/venue_map.yaml`` cannot tell them apart because a symbol existing is not
the same as a symbol trading.

**The exposure is the stop, not the entry.** An entry is a resting limit that simply doesn't
fill in a dead market. A stop is a market order that must fill *at the worst possible moment*,
and ``wire.STOP_SLIPPAGE`` caps how far it will chase. In a book with nothing on the other
side that stop is decorative. Positions are held ``CARRY_HOLD_DAYS`` (21), so the real
question is durability — will this market still be quoted three weeks from now — which is
what volume and open interest measure and what a depth snapshot does not.

Measured on mainnet 2026-07-27, the spread between healthy and dead is not subtle:

    xyz:SP500   $457M 24h volume   $483M OI   0.001% spread
    xyz:URNM    $133k              $1.0M      0.255%
    xyz:DXY     $0                 $0         no quotes on either side

Applied to **every** market, not just HIP-3 ones. A core-book market can be thin too, the
healthy ones pass trivially, and a gate with an exemption list is a gate someone will
eventually route around.
"""
from __future__ import annotations

from dataclasses import dataclass

# How far either side of mid counts as "depth you could actually hit". Wide enough to mean
# something on a thin book, tight enough that a far-away wall doesn't flatter it.
DEPTH_BAND = 0.01


@dataclass(frozen=True)
class Liquidity:
    """One market's tradability. ``spread`` is None when the book is not two-sided."""
    coin: str
    day_volume: float = 0.0       # 24h notional
    open_interest: float = 0.0    # notional
    bid_depth: float = 0.0        # within DEPTH_BAND of mid
    ask_depth: float = 0.0
    spread: float | None = None   # fraction of mid

    @property
    def has_book(self) -> bool:
        return self.spread is not None

    @property
    def thinnest_side(self) -> float:
        """The side that matters is whichever is worse — a stop can need either."""
        return min(self.bid_depth, self.ask_depth)


def parse_context(ctx) -> tuple[float, float]:
    """``(24h notional volume, open interest notional)`` from a ``metaAndAssetCtxs`` entry.

    Open interest arrives in *contracts*, so it is multiplied by the mark to become money —
    without that a market in a $5 asset and one in a $500 asset are not comparable.
    """
    ctx = ctx or {}
    try:
        volume = float(ctx.get("dayNtlVlm") or 0.0)
        mark = float(ctx.get("markPx") or 0.0)
        open_interest = float(ctx.get("openInterest") or 0.0) * mark
    except (TypeError, ValueError):
        return 0.0, 0.0
    return volume, open_interest


def parse_book(levels, *, band: float = DEPTH_BAND) -> tuple[float, float, float | None]:
    """``(bid depth, ask depth, spread)`` from an ``l2Book`` reply.

    Returns a spread of None when either side is empty — that is the ``xyz:DXY`` case, and it
    is a categorically different fact from "the spread is wide". A one-sided book has no mid
    to measure against, so any number here would be invented.
    """
    if not levels or len(levels) < 2:
        return 0.0, 0.0, None
    bids, asks = levels[0] or [], levels[1] or []
    if not bids or not asks:
        return 0.0, 0.0, None

    try:
        best_bid, best_ask = float(bids[0]["px"]), float(asks[0]["px"])
    except (TypeError, ValueError, KeyError, IndexError):
        return 0.0, 0.0, None
    if best_bid <= 0 or best_ask <= 0:
        return 0.0, 0.0, None

    mid = (best_bid + best_ask) / 2
    bid_depth = _notional(bids, lambda px: px >= mid * (1 - band))
    ask_depth = _notional(asks, lambda px: px <= mid * (1 + band))
    return bid_depth, ask_depth, (best_ask - best_bid) / mid


def _notional(levels, within) -> float:
    total = 0.0
    for level in levels:
        try:
            px, sz = float(level["px"]), float(level["sz"])
        except (TypeError, ValueError, KeyError):
            continue
        if within(px):
            total += px * sz
    return total
