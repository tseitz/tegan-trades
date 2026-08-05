"""``Candidate`` → ``OrderPlan``, or a ``Refusal`` saying why not. Pure.

Returning a union mirrors ``core.setups.cross_reference``, which yields ``Setup | NotASetup``
for the same reason: the refusal is an outcome worth reporting, not an error to raise past.

The candidate is read **structurally** — ``asset``, ``direction``, ``entry``, ``stop``,
``target``, ``key``. It is not imported or isinstance-checked, so the tests build a small
stub instead of assembling an ``OrderBlock`` and a tuple of ``View``s to exercise arithmetic
that touches neither.

**Order of operations matters here.** Prices are rounded to the venue's grid *first*, and the
size is then computed from the rounded entry and stop. Sizing off the raw prices and rounding
afterwards would leave the realised risk off by up to a tick's worth in an unpredictable
direction; this way the risk budget is exact with respect to what is actually transmitted.
"""
from __future__ import annotations

from dataclasses import dataclass

from execution import budget as budget_module
from execution import guards
from execution import portfolio as portfolio_module
from execution.participation import Depth, check_depth, max_shares
from execution.rounding import round_price, round_size
from execution.shares import round_share_price, round_shares
from execution.sizing import (
    CAP_BUDGET,
    CAP_CONCENTRATION,
    CAP_LEVERAGE,
    CAP_PARTICIPATION,
    CAP_PORTFOLIO,
    CAP_VENUE_LEVERAGE,
    apply_caps,
    notional_ceiling,
    risk_of,
    size_for_risk,
)

# Which set of tick/lot rules a market obeys. Two venues, two genuinely different grids, and
# applying the wrong one is silent rather than loud: the perp rule allows three decimal places
# on a $29 stock, which is finer than SEC Rule 612 permits and is rejected by Alpaca with a
# generic error. See ``rounding`` and ``shares`` for the rules themselves.
PERP_GRID = "perp"
SHARE_GRID = "share"


@dataclass(frozen=True)
class Market:
    """What the venue says about one market. Fetched per network — see ``universe``."""
    coin: str            # venue-native name, including any HIP-3 namespace ("xyz:GOLD")
    sz_decimals: int
    max_leverage: int | None = None
    # Defaults to the perp grid so every existing construction keeps its meaning; the equity
    # broker is the only caller that sets it.
    grid: str = PERP_GRID


def grid_for(market: Market):
    """The (price, size) rounding pair this market's venue actually enforces.

    Returned as functions rather than branched on inside ``build`` so that the choice is made
    once, at the top, and cannot drift between the price that is quoted and the size that is
    sized off it.
    """
    if market.grid == SHARE_GRID:
        # Both take ``sz_decimals`` for a uniform signature and ignore it — the equity grid is
        # fixed by regulation and by the bracket order class, not reported per market.
        return (lambda price, _d: round_share_price(price)), (lambda size, _d: round_shares(size))
    return round_price, round_size


@dataclass(frozen=True)
class OrderPlan:
    """A fully-priced, fully-sized bracket, ready to transmit and worth showing to a human.

    Every number here is post-rounding — this is what goes on the wire, so the confirmation
    prompt and the audit log can both read straight off it with nothing left to recompute.
    """
    asset: str           # canonical
    coin: str            # venue-native
    direction: str       # long | short
    size: float
    entry: float
    stop: float
    target: float
    risk: float          # what being stopped out actually costs, post-rounding
    notional: float
    equity: float
    candidate_key: str
    # Set only when a ceiling actually bound — the size the risk budget alone asked for,
    # before whatever cut it down. ``None`` when nothing was cut, rather than a copy of
    # ``size``, so the render can key off it to decide whether there is anything to explain.
    capped_from: float | None = None
    # Which ceiling did it, one of ``sizing.CAP_*``. Carried beside the number because the
    # number alone does not say what to do about it: a concentration cut is the policy working
    # as intended, a budget cut means something else needs cancelling, and a participation cut
    # is the market's own answer. Set exactly when ``capped_from`` is.
    cap_reason: str | None = None
    depth: Depth | None = None

    @property
    def is_buy(self) -> bool:
        return self.direction == "long"

    @property
    def leverage(self) -> float:
        return self.notional / self.equity if self.equity else 0.0


# **A resting bracket is live at the open, and a gapped open is not the trade that was
# approved.** A marketable limit fills at the open, not at the limit — so the entry moves toward
# a stop that does not move with it, and the planned distance collapses. Observed 2026-07-29:
# `VRT` closed 269.56 and opened 244.33 (-9.4%); an entry planned at 266.52 filled at 243.33
# against a stop 9.5% away that was now 0.9% away, and the position round-tripped flat in **49
# seconds** (`client_order_id 5936e2345d35`). The gap consumed 91% of the stop distance.
#
# Nothing here can prevent that, and it is not a defect in `build`: the order is already resting
# when the open happens, so the comparison has to be made *at* the open by something scheduled,
# not at placement by something pure. `core.gaps` measures how often this is in play — 2.36% of
# sessions adversely, 39% over a 21-session hold — and `execution.session` now says in the
# preview that the stop is an intent rather than a bound.

def build(
    candidate,
    *,
    markets: dict[str, Market],
    listing,
    equity: float,
    risk_pct: float,
    liquidity=None,
    enforce_liquidity: bool = True,
    depth: Depth | None = None,
    max_participation: float | None = None,
    max_notional_frac: float | None = None,
    max_position_frac: float | None = None,
    venue_multiplier: float | None = None,
    headroom: float | None = None,
    book: portfolio_module.Book | None = None,
    min_budget_fill: float = budget_module.MIN_BUDGET_FILL,
    can_short: bool | None = None,
    min_notional: float = guards.MIN_ORDER_NOTIONAL_USD,
    min_volume: float = guards.MIN_DAY_VOLUME_USD,
    min_open_interest: float = guards.MIN_OPEN_INTEREST_USD,
) -> OrderPlan | guards.Refusal:
    """Price, size and vet one candidate. Returns the plan, or the first refusal that applies.

    ``markets`` is the target network's live market metadata, keyed by venue-native coin — it
    serves as both the universe the listing is checked against and the source of the lot size.
    Passing one object rather than a market plus a universe removes the case where a caller
    hands over a market for a coin that does not exist.

    Guards run in a deliberate order — cheapest and most fundamental first. There is no point
    reporting that a size rounds to dust on a market that does not exist on this network.
    """
    listing_refusal = guards.check_listing(candidate.asset, listing, markets)
    if listing_refusal is not None:
        return listing_refusal

    # Safe to index: ``check_listing`` has established the symbol is present.
    market = markets[listing.symbol]

    geometry_refusal = guards.check_geometry(
        candidate.direction, candidate.entry, candidate.stop, candidate.target
    )
    if geometry_refusal is not None:
        return geometry_refusal

    # Equities only, and before any arithmetic: a short this account cannot take is refused
    # for what it is — the wrong account type — rather than resized or left to the venue,
    # whose rejection names a buying-power number and not the cause.
    if market.grid == SHARE_GRID:
        margin_refusal = guards.check_shortable(
            candidate.direction, equity, can_short=can_short
        )
        if margin_refusal is not None:
            return margin_refusal

    round_px, round_sz = grid_for(market)

    entry = round_px(candidate.entry, market.sz_decimals)
    stop = round_px(candidate.stop, market.sz_decimals)
    target = round_px(candidate.target, market.sz_decimals)

    # Rounding three prices onto a coarse grid can collapse two of them together — a very
    # tight zone on a market with few allowed decimals. Re-checking is not redundant: the
    # geometry that held on the raw numbers is not guaranteed to hold on the rounded ones,
    # and this is the last point before a size is divided by their difference.
    rounded_refusal = guards.check_geometry(candidate.direction, entry, stop, target)
    if rounded_refusal is not None:
        return rounded_refusal

    # Before sizing, because a market that measurably does not trade cannot carry any size —
    # unlike the ceiling below, which only decides how much. ``None`` depth passes: see
    # ``participation.check_depth`` for why an unmeasured market must not refuse.
    depth_refusal = check_depth(depth)
    if depth_refusal is not None:
        return depth_refusal

    wanted = size_for_risk(equity=equity, risk_pct=risk_pct, entry=entry, stop=stop)

    # Every ceiling applies to the *unrounded* size, so the venue's lot is applied exactly
    # once. Capping after rounding would floor twice and lose up to a share for no reason.
    #
    # Three of the four are properties of the trade and settle together; the budget is the odd
    # one out and comes after, because it is the only ceiling that can refuse rather than
    # shrink, and the fraction it is judged against has to be measured after the other three
    # have had their say — otherwise a thin market's participation cut would be counted again
    # as a budget shortfall and the order refused for the wrong reason.
    allowed, reason = apply_caps(wanted, (
        (CAP_LEVERAGE, notional_ceiling(equity=equity, entry=entry, frac=max_notional_frac)),
        # The venue's own overnight limit, beside the configured one rather than folded into it,
        # because the two call for different responses: ``max_notional_frac`` is a setting that
        # may be raised, and this is Reg T. §36 asked for a per-venue ceiling instead of a lower
        # global one — this is the venue stating it, so nothing is written down to drift.
        (CAP_VENUE_LEVERAGE,
         notional_ceiling(equity=equity, entry=entry, frac=venue_multiplier)),
        (CAP_CONCENTRATION,
         notional_ceiling(equity=equity, entry=entry, frac=max_position_frac)),
        (CAP_PARTICIPATION,
         max_shares(depth, max_participation)
         if depth is not None and max_participation is not None else None),
        # The one ceiling here that is not a property of this venue: risk pools across venues
        # because losing 1% on each of two books is losing 2% of one person's account, so a
        # position resting on Hyperliquid can bind an Alpaca order. Buying power deliberately
        # does not pool — see ``portfolio`` for why the two are different quantities.
        (CAP_PORTFOLIO,
         None if book is None else book.size_ceiling(entry=entry, stop=stop)),
    ))

    # Compared *after* rounding, not before. A ceiling that shaves a fraction of a share off
    # changes nothing that goes on the wire, and reporting "capped from 99 to 99" would train
    # the reader to skip the line that matters.
    size = round_sz(wanted, market.sz_decimals)
    capped_from: float | None = None
    cap_reason: str | None = None
    capped = round_sz(allowed, market.sz_decimals)
    if capped < size:
        capped_from, size, cap_reason = size, capped, reason

    # The portfolio floor, judged only when the pooled ceiling is what bound. If concentration
    # or participation cut deeper, the order is not small *because* the book is full and saying
    # so would name the wrong cause — the same reasoning ``budget.check_fill`` applies to
    # ``wanted``. Before the buying-power check because a full book and a full account call for
    # different acts, and the more general one should be reported first.
    if book is not None and cap_reason == CAP_PORTFOLIO:
        # ``needed`` is RISK, not notional, and that is not a detail — the number it is compared
        # against is risk left in the book. ``budget`` passes a notional there because buying
        # power is a notional; mixing the two produced "leaving $8.27 against the $0.00 this
        # order needs" on a live run, which is two different quantities and a rounded-away size.
        # Measured from ``capped_from``: what the risk budget asked for, before this cap took it.
        intended = capped_from if capped_from is not None else size
        portfolio_refusal = book.check_fill(
            fitted=size, wanted=intended,
            needed=risk_of(intended, entry=entry, stop=stop),
            min_fill=min_budget_fill,
        )
        if portfolio_refusal is not None:
            return portfolio_refusal

    if headroom is not None:
        fitted = round_sz(
            min(allowed, budget_module.affordable(headroom, entry)), market.sz_decimals
        )
        headroom_refusal = budget_module.check_fill(
            fitted=fitted, wanted=size, headroom=headroom, needed=size * entry,
            min_fill=min_budget_fill,
        )
        if headroom_refusal is not None:
            return headroom_refusal
        if fitted < size:
            # ``capped_from`` keeps naming what the risk budget asked for, even when a second
            # ceiling had already cut it — the reader wants the distance from the intended
            # trade, not from an intermediate number nothing ever proposed to send.
            capped_from = size if capped_from is None else capped_from
            size, cap_reason = fitted, CAP_BUDGET

    notional = size * entry

    size_refusal = guards.check_size(size, notional, min_notional=min_notional)
    if size_refusal is not None:
        return size_refusal

    # Last, because it is the only guard needing the final notional — and because it is the
    # one whose data costs a live fetch, so there is no sense paying for it on a candidate
    # already refused for having no listing or no stop distance.
    # ``enforce_liquidity`` is False on the rehearsal venue, where books are mock and the
    # measurement means nothing. The caller still computes and reports it — see
    # ``session.liquidity_verdict`` — so "mainnet would refuse this" stays visible.
    if enforce_liquidity:
        liquidity_refusal = guards.check_liquidity(
            liquidity, notional, min_volume=min_volume, min_open_interest=min_open_interest,
        )
        if liquidity_refusal is not None:
            return liquidity_refusal

    return OrderPlan(
        asset=candidate.asset,
        coin=market.coin,
        direction=candidate.direction,
        size=size,
        entry=entry,
        stop=stop,
        target=target,
        risk=risk_of(size, entry=entry, stop=stop),
        notional=notional,
        equity=equity,
        candidate_key=candidate.key,
        capped_from=capped_from,
        cap_reason=cap_reason,
        depth=depth,
    )
