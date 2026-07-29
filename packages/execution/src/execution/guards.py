"""Every reason an approved candidate must not become an order.

Separate from ``plan`` because these are the part worth reading on their own. The sandbox is
not a safety layer for this package — outbound HTTPS is unrestricted — so the guards *are*
the safety layer, and each one is unit-tested against the case it exists for.

Two design rules, both borrowed from ``core.setups.NotASetup``:

* **A refusal is a value, not an exception.** It carries a code the audit log can be
  partitioned on and a detail line a human can act on. Refusals are counted and printed, so
  "nothing executed tonight" can never be confused with "nothing was tried".
* **Refusals name themselves.** ``REFUSAL_PROXY`` and ``REFUSAL_UNLISTED`` are different
  facts about the world and stay separate, exactly as the four decision verdicts do.
"""
from __future__ import annotations

from dataclasses import dataclass

# The venue's minimum order value. Below this the order is rejected outright, so catching it
# here turns a generic venue error into a sentence explaining that the risk budget was too
# small for this market.
MIN_ORDER_NOTIONAL_USD = 10.0

REFUSAL_UNLISTED = "unlisted"
REFUSAL_PROXY = "proxy"
REFUSAL_GEOMETRY = "geometry"
REFUSAL_DUST = "dust"
REFUSAL_MIN_NOTIONAL = "min_notional"
REFUSAL_NO_BOOK = "no_book"
REFUSAL_ILLIQUID = "illiquid"
REFUSAL_TOO_BIG = "too_big_for_book"
REFUSAL_NO_LIQUIDITY_DATA = "no_liquidity_data"
REFUSAL_NO_MARGIN = "no_margin"

# Equity below which a US brokerage account cannot short at all.
#
# Reg T: selling short requires a margin account, and FINRA sets the minimum equity for one at
# $2,000. Under it Alpaca caps the account at 1x buying power and rejects the order outright.
#
# This is a **cliff, not a slope**, and it is worth knowing before funding: measured against
# the 13 approved decisions with an Alpaca listing, $1,000 places 5 of them and $2,000 places
# 11. Crossing this line does two things at once — it unlocks the short side, and it doubles
# the widest stop a whole share can carry inside a 1% budget.
MARGIN_ACCOUNT_MIN_EQUITY_USD = 2_000.0

# Floors below which a market is not somewhere to leave a stop for three weeks. Both in USD.
# Chosen against measured mainnet data (2026-07-27): they clear ``xyz:DXY`` (no book, $0 of
# both) and ``xyz:URNM`` ($133k volume) while keeping every market with genuine activity.
MIN_DAY_VOLUME_USD = 1_000_000.0
MIN_OPEN_INTEREST_USD = 1_000_000.0

# The largest share of near-touch depth one order may be.
#
# Inert today among markets that clear the floors above: at $999 equity the largest order is
# ~$200, against $33k-$1.5M of depth on those markets (0.6% at worst, on RIVN). It binds only
# on books this thin *and* already excluded by volume — URNM's $12k would put $200 at 1.7%.
# The point is that it keeps binding as equity grows, without anyone revisiting this file.
MAX_DEPTH_FRACTION = 0.01


@dataclass(frozen=True)
class Refusal:
    """Why no order was placed. ``code`` is for tallying, ``detail`` is for the human."""
    code: str
    detail: str


def check_listing(asset: str, listing, universe) -> Refusal | None:
    """Is this asset tradeable, on this network, at a price the candidate's numbers mean?

    Two separate failures that a single "can we trade it" boolean would conflate:

    * **Unlisted.** ``cfg/venue_map.yaml`` saying ``xyz:GOLD`` is not evidence the symbol
      exists where the order is about to go — testnet's ``xyz`` builder carries 68 markets
      against mainnet's 103. The live universe of the *target* network is the only authority,
      which is why ``universe`` is passed in rather than assumed.
    * **Proxy.** ``scale`` other than 1.0 means the venue instrument tracks the asset at a
      ratio, so a target price quoted on the canonical asset is not a valid order price on
      it. No Hyperliquid listing is scaled today, which is exactly why this needs a test
      rather than a comment — the first one added would otherwise be priced wrong silently.
    """
    if listing is None:
        return Refusal(REFUSAL_UNLISTED, f"{asset} has no listing on this venue")
    if listing.is_proxy:
        return Refusal(
            REFUSAL_PROXY,
            f"{asset} trades as {listing.symbol} at scale {listing.scale}; "
            f"prices quoted on {asset} are not order prices on it",
        )
    if listing.symbol not in universe:
        return Refusal(
            REFUSAL_UNLISTED,
            f"{listing.symbol} is not in this network's universe "
            f"({len(universe)} markets) — check the network, not the venue map",
        )
    return None


def check_geometry(direction: str, entry: float, stop: float, target: float) -> Refusal | None:
    """Stop and target on the correct sides of entry for the direction.

    A backstop rather than a routine filter: all 77 decisions recorded as of 2026-07-27
    satisfy this. That is the point — if it ever fires, something upstream is wrong and the
    right response is to not send the order, because an inverted stop becomes a market order
    the instant it is accepted.
    """
    if direction == "long":
        ok = stop < entry < target
    elif direction == "short":
        ok = target < entry < stop
    else:
        return Refusal(REFUSAL_GEOMETRY, f"unknown direction {direction!r}")
    if ok:
        return None
    return Refusal(
        REFUSAL_GEOMETRY,
        f"{direction} with entry {entry:g}, stop {stop:g}, target {target:g} "
        f"is not ordered correctly",
    )


def check_liquidity(
    liquidity,
    notional: float,
    *,
    min_volume: float = MIN_DAY_VOLUME_USD,
    min_open_interest: float = MIN_OPEN_INTEREST_USD,
    max_depth_fraction: float = MAX_DEPTH_FRACTION,
) -> Refusal | None:
    """Is this a market worth leaving a stop in? See ``execution.liquidity`` for why.

    ``liquidity`` being None is itself a refusal. The check runs on data fetched live, so a
    failed fetch must not silently become a pass — that would turn the gate off in exactly
    the conditions (venue trouble) where it matters most.
    """
    if liquidity is None:
        return Refusal(
            REFUSAL_NO_LIQUIDITY_DATA,
            "could not read this market's liquidity, so it cannot be cleared for trading",
        )

    # Distinct from "thin": a one-sided book has nothing for a stop to fill against at any
    # price, so the protective order is decorative rather than merely expensive.
    if not liquidity.has_book:
        return Refusal(
            REFUSAL_NO_BOOK,
            f"{liquidity.coin} has no two-sided book — a stop here could not fill at all",
        )

    if liquidity.day_volume < min_volume:
        return Refusal(
            REFUSAL_ILLIQUID,
            f"{liquidity.coin} traded ${liquidity.day_volume:,.0f} in 24h, under the "
            f"${min_volume:,.0f} floor — too quiet to hold a stop in",
        )

    if liquidity.open_interest < min_open_interest:
        return Refusal(
            REFUSAL_ILLIQUID,
            f"{liquidity.coin} has ${liquidity.open_interest:,.0f} open interest, under the "
            f"${min_open_interest:,.0f} floor — too few participants to exit against",
        )

    ceiling = liquidity.thinnest_side * max_depth_fraction
    if notional > ceiling:
        return Refusal(
            REFUSAL_TOO_BIG,
            f"${notional:,.2f} is more than {max_depth_fraction:.0%} of {liquidity.coin}'s "
            f"${liquidity.thinnest_side:,.0f} near-touch depth",
        )
    return None


def check_shortable(direction: str, equity: float,
                    *, min_equity: float = MARGIN_ACCOUNT_MIN_EQUITY_USD) -> Refusal | None:
    """Can this account take the short side at all?

    Only equities need this — a perp shorts by taking the sell side of the same contract, so
    the question does not arise there. On a US brokerage a short is a borrow, which needs a
    margin account, which needs ``min_equity``.

    Stated here rather than discovered from the venue because the two failures look different
    to a person: an Alpaca rejection names a buying-power number, while what actually happened
    is that the account is the wrong *type*, and no amount of retrying or resizing fixes it.
    """
    if direction != "short":
        return None
    if equity >= min_equity:
        return None
    return Refusal(
        REFUSAL_NO_MARGIN,
        f"shorting needs a margin account (Reg T minimum ${min_equity:,.0f} equity) and this "
        f"account holds ${equity:,.2f} — it is restricted to 1x buying power, long only",
    )


def check_size(size: float, notional: float,
               *, min_notional: float = MIN_ORDER_NOTIONAL_USD) -> Refusal | None:
    """Did rounding leave anything to send, and is it big enough for the venue to accept?

    ``size`` is post-rounding. On a coarse-lot market a small risk budget floors to zero, and
    zero is a refusal rather than something to round up — promoting it to one lot would place
    an order many times the size the risk budget authorised.
    """
    if size <= 0:
        return Refusal(
            REFUSAL_DUST,
            "the risk budget rounds to zero contracts at this market's lot size",
        )
    if notional < min_notional:
        return Refusal(
            REFUSAL_MIN_NOTIONAL,
            f"notional ${notional:,.2f} is below the venue minimum ${min_notional:,.2f}",
        )
    return None
