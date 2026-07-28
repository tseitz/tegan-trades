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

from execution import guards
from execution.rounding import round_price, round_size
from execution.sizing import risk_of, size_for_risk


@dataclass(frozen=True)
class Market:
    """What the venue says about one market. Fetched per network — see ``universe``."""
    coin: str            # venue-native name, including any HIP-3 namespace ("xyz:GOLD")
    sz_decimals: int
    max_leverage: int | None = None


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

    @property
    def is_buy(self) -> bool:
        return self.direction == "long"

    @property
    def leverage(self) -> float:
        return self.notional / self.equity if self.equity else 0.0


def build(
    candidate,
    *,
    markets: dict[str, Market],
    listing,
    equity: float,
    risk_pct: float,
    liquidity=None,
    enforce_liquidity: bool = True,
    max_notional_frac: float | None = None,
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

    entry = round_price(candidate.entry, market.sz_decimals)
    stop = round_price(candidate.stop, market.sz_decimals)
    target = round_price(candidate.target, market.sz_decimals)

    # Rounding three prices onto a coarse grid can collapse two of them together — a very
    # tight zone on a market with few allowed decimals. Re-checking is not redundant: the
    # geometry that held on the raw numbers is not guaranteed to hold on the rounded ones,
    # and this is the last point before a size is divided by their difference.
    rounded_refusal = guards.check_geometry(candidate.direction, entry, stop, target)
    if rounded_refusal is not None:
        return rounded_refusal

    size = round_size(
        size_for_risk(
            equity=equity, risk_pct=risk_pct, entry=entry, stop=stop,
            max_notional_frac=max_notional_frac,
        ),
        market.sz_decimals,
    )
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
    )
