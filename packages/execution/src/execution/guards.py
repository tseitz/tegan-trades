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

from core.identity import DIFFERS, IN_RANGE, MATCH, Comparison

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
REFUSAL_IDENTITY_MISMATCH = "identity_mismatch"
REFUSAL_IDENTITY_UNCONFIRMED = "identity_unconfirmed"

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


def check_identity(asset: str, comparison: Comparison | None, *,
                   symbol: str, venue: str) -> Refusal | None:
    """Is the instrument quoted at ``symbol`` on ``venue`` actually ``asset``?

    ``check_listing`` asks whether the venue map says this symbol exists; this asks whether the
    venue map is *right* — a name match is not evidence, since the venues list a memecoin under
    ``SPX`` at 0.33 while the index sits at 7403, and Yahoo's bare ``WTI`` prices at 3.51 (W&T
    Offshore, the E&P company) while crude trades at 81.75. Both resolve to a real, liquid,
    entirely wrong market, and only a live price comparison — done by ``core.identity.compare``,
    passed in here rather than fetched here so this package need not import ``oracle`` — can
    catch it before the order does.

    Two failures, matching ``core.identity``'s own split (see its module docstring for why
    there are four verdicts rather than two):

    * **Mismatch** (``DIFFERS``). The mark disagrees by an order of magnitude, not a percentage
      — 23x for crude against the E&P company, 4.3x for the yen against Yahoo's bare ``JPY``.
      The ratio rides along in the detail because the verdict alone can't say whether it was an
      undeclared proxy (10.03, an index tracked at a ratio nobody told this guard about) or a
      collision (4e-5, a different instrument entirely) — a human fixing the map needs to know
      which.
    * **Unconfirmed** (``IN_RANGE``, ``NO_PRICE``, or ``comparison`` itself missing). Neither a
      timing gap nor a fetch that returned nothing is evidence of agreement, and treating either
      as a pass is exactly the failure ``core.identity`` was written to prevent. ``comparison is
      None`` gets the same code as the verdicts that mean "couldn't tell" — it *is* that fact,
      one level earlier: the live fetch behind the comparison never produced one.

    ``comparison is None`` is refused for the same reason ``check_liquidity``'s missing-data
    branch is: the check runs on a price fetched live, so a failed fetch must not silently read
    as "checked and agreed" — that would turn the gate off exactly when venue trouble makes it
    matter most.
    """
    if comparison is None:
        return Refusal(
            REFUSAL_IDENTITY_UNCONFIRMED,
            f"could not fetch a price for {symbol} on {venue} to compare against {asset}, "
            f"so its identity cannot be confirmed",
        )
    if comparison.verdict == MATCH:
        return None
    if comparison.verdict == DIFFERS:
        return Refusal(
            REFUSAL_IDENTITY_MISMATCH,
            f"{symbol} on {venue} prices at {comparison.ratio:.4g}x what {asset}'s cached "
            f"close implies — that is a different instrument, not a proxy or a timing gap",
        )
    if comparison.verdict == IN_RANGE:
        return Refusal(
            REFUSAL_IDENTITY_UNCONFIRMED,
            f"{symbol} on {venue} sits inside {asset}'s recent trading range but does not "
            f"match its last close (ratio {comparison.ratio:.4g}) — probably a timing gap, "
            f"not yet a confirmed identity",
        )
    return Refusal(
        REFUSAL_IDENTITY_UNCONFIRMED,
        f"no usable price comparison between {symbol} on {venue} and {asset} — the venue "
        f"mark or the cached close was missing or zero",
    )


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


def check_shortable(direction: str, equity: float, *, can_short: bool | None = None,
                    min_equity: float = MARGIN_ACCOUNT_MIN_EQUITY_USD) -> Refusal | None:
    """Can this account take the short side at all?

    Only equities need this — a perp shorts by taking the sell side of the same contract, so
    the question does not arise there. On a US brokerage a short is a borrow, which needs a
    margin account.

    **Ask the venue; only infer when it did not say.** ``can_short`` is
    ``account.shorting_enabled``, and when it is present it is the whole answer — the equity
    test below is a *proxy* for it and the proxy has been observed wrong in the expensive
    direction. The paper account holds $99,674, clears the Reg T minimum by 50x, and cannot
    short: two CRM brackets were accepted at 04:00 ET on 2026-07-29 and rejected at the open
    for exactly this. An account can also fail to be a margin account for reasons that have
    nothing to do with its balance.

    Refused here rather than left to the venue because the two failures look different to a
    person: an Alpaca rejection names a buying-power number, while what actually happened is
    that the account is the wrong *type*, and no amount of retrying or resizing fixes it.
    """
    if direction != "short":
        return None
    if can_short is not None:
        if can_short:
            return None
        return Refusal(
            REFUSAL_NO_MARGIN,
            "this account reports shorting is not enabled — it is a cash account, so the "
            "order would be accepted now and rejected at the open. Equity is not the "
            "constraint; the account type is.",
        )
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
