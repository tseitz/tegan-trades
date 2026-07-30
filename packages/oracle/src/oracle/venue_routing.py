"""Assembles the inputs `core.routing` ranks on, from cached ore. The I/O half of the router.

`core.routing` is pure and knows nothing about where a funding median or a price bar comes from.
This module is the seam: it reads `cfg/venue_map.yaml` for who lists what, `data/funding/` for
carry, and `data/prices/` for the overnight gap, then hands `core.routing` one quote per venue.
Kept separate because the ranking is the part worth testing without a filesystem.

**Kraken is deliberately absent from ``ROUTED_VENUES``.** `core.routing` knows its cost shape
(spot: a taker fee, no funding, no gap) and `oracle/sources/kraken.py` prices it, but there are
zero Kraken rows in `cfg/venue_map.yaml` and no `Broker` adapter, so nothing could be *placed*
there. Offering it in the queue would rank a venue the run cannot reach — the same failure the
`(unmapped on …)` note in `setups_render` exists to prevent, one level up. Adding it is a map pass
plus an adapter, not a line here.

WHY THE ALPACA SYMBOL IS THE PRICE SYMBOL. The gap term needs bars for the instrument actually
held, and on Alpaca that is the same ticker our own close is fetched under — which is exactly why
the venue map's price check is circular for Alpaca and it is mapped by instrument instead. That
identity is a gift here: `cache.load("yahoo", listing.symbol)` is the right series by construction,
with no second routing step to keep in sync.

**The pooled gap rate is recomputed per candidate**, at that candidate's own stop, because
excess-past-stop is violently sensitive to stop distance — see `core.gaps.pooled_excess`, which
documents what a single shared scalar did to `BE`. So the cohort's *gaps* are cached once and the
*pool* is not cached at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core import gaps, routing
from core.funding import carry_cost
from core.setups import CARRY_HOLD_DAYS

from oracle import cache, carry, venue_map

# Venues the queue will actually rank. See the module docstring on Kraken's absence.
ROUTED_VENUES = (routing.ALPACA, routing.HYPERLIQUID)

# Where an Alpaca-listed equity's bars live. Alpaca quotes under the same ticker we cache under.
PRICE_SOURCE = "yahoo"


def _side(direction: str) -> str:
    """The corpus's direction vocabulary is wider than long/short; the cost model's is not."""
    return "short" if direction == "short" else "long"


def stop_distance(candidate) -> float | None:
    """The planned stop as a fraction of entry. ``None`` when it cannot be formed.

    Returned rather than defaulted because the gap term is meaningless without it and a made-up
    stop would produce a confident wrong number — the failure mode this whole slice removes.
    """
    entry, stop = getattr(candidate, "entry", None), getattr(candidate, "stop", None)
    if not entry or stop is None:
        return None
    return abs(entry - stop) / entry


def alpaca_listed(assets, *, map_path=None) -> dict[str, str]:
    """``asset -> Alpaca symbol`` for the assets Alpaca lists. Absence is a real answer."""
    out: dict[str, str] = {}
    for asset in assets:
        listing = (venue_map.listing(asset, routing.ALPACA) if map_path is None
                   else venue_map.listing(asset, routing.ALPACA, path=map_path))
        if listing is not None:
            out[asset] = listing.symbol
    return out


def all_alpaca_listed(*, map_path=None) -> dict[str, str]:
    """Every Alpaca-listed asset in the venue map, whether or not it is in tonight's queue.

    THE COHORT MUST NOT BE SCOPED TO THE SAMPLE ON SCREEN, and this function exists because the
    first version was. ``triage`` built its router from ``queue.candidates`` — a *limited* sample —
    so a six-row sitting could leave one Alpaca-listed asset in the cohort, the self-excluding pool
    came back empty, and shrinkage switched itself off without a word. Measured live: ``SMH``
    priced at its raw **0.00%** instead of the shrunk **0.31%**, and lost its "mostly pooled" flag
    on the way, because 72 sessions with zero events is 0.00% if you believe it.

    A pool is a property of the comparable instrument universe. How many of its members happen to
    be on screen tonight is not information about any of them.
    """
    everything = venue_map.load(map_path) if map_path else venue_map.load()
    return alpaca_listed(sorted(everything), map_path=map_path)


def gap_cohort(symbols_by_asset: dict[str, str], *, root=None) -> dict[str, tuple[float, ...]]:
    """``asset -> overnight gaps`` for every Alpaca-listed asset with cached bars.

    THE COHORT IS ALPACA-LISTED ONLY, and that is load-bearing rather than tidy. Gap risk is a
    property of an instrument's trading hours: pooled against cash equities, `YM=F` — a future
    trading nearly 24h — was charged 6.33% of notional over a hold. What survives here shares one
    session and one overnight, so the pool means something.
    """
    out: dict[str, tuple[float, ...]] = {}
    for asset, symbol in symbols_by_asset.items():
        series = (cache.load(PRICE_SOURCE, symbol) if root is None
                  else cache.load(PRICE_SOURCE, symbol, root=root))
        if series is None or not series.bars:
            continue
        observed = gaps.overnight_gaps(series.bars)
        if observed:
            out[asset] = observed
    return out


@dataclass(frozen=True, slots=True)
class Router:
    """Everything needed to rank venues for a candidate, loaded once per run.

    ``can_short`` is ``None`` when nobody has asked the account — which is the queue's normal
    state, since it runs free and local with no credentials. `core.routing` renders that as a
    *different* refusal from a measured "cannot short", because Alpaca has been observed reporting
    `no_shorting: false` while `shorting_enabled` was false and the queue must not assert the
    stale one.
    """

    alpaca_symbols: dict[str, str] = field(default_factory=dict)
    cohort: dict[str, tuple[float, ...]] = field(default_factory=dict)
    hl_outlooks: dict = field(default_factory=dict)
    hold_days: int = CARRY_HOLD_DAYS
    can_short: bool | None = None

    def quotes_for(self, candidate) -> list[routing.VenueQuote]:
        """One quote per venue that lists this candidate's asset.

        A term this module cannot measure is left ``None``, never 0.0 — ``core.routing`` then
        gates a venue with nothing priced out of the ranking rather than letting it win for free.
        ``crossing`` is currently ``None`` on every venue (§43): `probe_book_depth.py` measures it
        but nothing caches it.
        """
        asset, side = candidate.asset, _side(candidate.direction)
        distance = stop_distance(candidate)
        out: list[routing.VenueQuote] = []

        symbol = self.alpaca_symbols.get(asset)
        if symbol is not None:
            cost = self.gap_cost(asset, distance, side)
            # ``borrowed`` is threaded through rather than dropped: 13 of 18 Alpaca-listed assets
            # in the live queue lean more than half on the cohort, and a cost shown without that
            # is a pooled number wearing this instrument's ticker. ``core.gaps`` says as much in
            # its own docstring, and the first version of this method discarded it anyway.
            out.append(routing.quote(
                routing.ALPACA, symbol,
                gap=None if cost is None else cost.over(self.hold_days),
                borrowed=("gap",) if cost is not None and cost.borrowed else (),
            ))

        outlook = self.hl_outlooks.get(asset)
        if outlook is not None:
            out.append(routing.quote(
                routing.HYPERLIQUID, asset,
                carry=carry_cost(outlook.median, self.hold_days, side)))
        return out

    def gap_cost(self, asset: str, distance: float | None, side: str) -> gaps.GapCost | None:
        """This asset's gap exposure, or ``None`` when it cannot be formed.

        Returns the whole ``GapCost`` rather than a bare fraction so ``pooled_weight`` survives
        the trip to the display — dropping it is what let a mostly-pooled estimate read as a
        measured one.
        """
        if distance is None:
            return None
        # The asset is excluded from its own pool: shrinking toward a number it helped set would
        # make a thin history look better evidenced than it is.
        cohort = gaps.pooled(
            [g for other, g in self.cohort.items() if other != asset], distance, side)
        return gaps.measure_from_gaps(asset, self.cohort.get(asset, ()),
                                      stop_distance=distance, side=side, pool=cohort)

    def decide(self, candidate) -> routing.Decision:
        return routing.decide(candidate.asset, _side(candidate.direction),
                              self.quotes_for(candidate), can_short=self.can_short)


def build(assets, *, hold_days: int = CARRY_HOLD_DAYS, can_short: bool | None = None,
          hl_outlooks: dict | None = None) -> Router:
    """Load a ``Router`` for ``assets`` from cached ore. Free — no network.

    ``assets`` scopes only the *funding* read. The gap cohort is deliberately the whole
    Alpaca-listed universe regardless of what was asked for — see ``all_alpaca_listed`` for the
    live case where scoping it to the caller's assets silently turned shrinkage off.

    ``hl_outlooks`` is injectable because a run has usually already paid for it: ``setups`` builds
    funding outlooks for its own carry display, and re-reading the whole funding log per venue is
    the kind of quiet duplication ``carry.outlooks_for`` groups once specifically to avoid.
    """
    symbols = all_alpaca_listed()
    return Router(
        alpaca_symbols=symbols,
        cohort=gap_cohort(symbols),
        hl_outlooks=(carry.outlooks_for(sorted(assets), venue=routing.HYPERLIQUID)
                     if hl_outlooks is None else hl_outlooks),
        hold_days=hold_days,
        can_short=can_short,
    )
