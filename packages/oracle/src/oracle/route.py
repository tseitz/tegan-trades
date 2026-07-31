"""Asset -> price source routing. Pure, curated-first, and designed to refuse to guess.

Mirrors ``core.canon``: a pure ``route()`` over a ``RoutingTable`` that a loader assembles
from config + live exchange listings. The stored corpus is never touched.

**Why this module is paranoid.** The obvious design — "try Coinbase, fall back to Yahoo" —
is silently catastrophic on this corpus. Coinbase lists a memecoin under the symbol ``SPX``
(SPX6900); the corpus contains 204 ``SPX`` theses that mean the S&P 500. Availability-based
routing would price 5.5% of the corpus against the wrong instrument and produce
confident, plausible, entirely fictional scores. ``META`` and ``IP`` collide the same way.

Routing on the thesis ``domain`` field alone is *also* insufficient — the LLM tags a
minority of theses inconsistently (2 SPX theses say ``crypto``, plus 16 GOLD-crypto and
1 MSTR-crypto). So the order of authority is:

1. ``cfg/oracle_map.yaml`` — hand-curated, committed, wins unconditionally.
2. Corpus-wide **majority** domain per asset — robust to a handful of mislabels.
3. No evidence either way -> ``Unpriceable(CONFLICT)``, surfaced for curation.

An asset never gets a price on a coin-flip.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.canon import BASKET, MACRO

# Unpriceable reasons — enumerated so coverage reports can break down honestly.
#
# ``EVENT`` and ``DERIVED_RATIO`` were only ever spelled in ``cfg/oracle_map.yaml`` and passed
# through as opaque strings, so nothing in code could name them. That is how they stayed
# invisible: a reason the code cannot say is a reason no report can group on.
BASKET_REASON = "basket"
MACRO_REASON = "macro"
DOMINANCE = "dominance_metric"
DERIVED_RATIO = "derived_ratio"
EVENT = "event"
PRIVATE = "private_company"
RATE = "rate"
UNMAPPED = "unmapped"
CONFLICT = "conflict"

# Not an unpriceable reason — the opposite. ``plan_fetches`` reports a ``DerivedRef`` under this
# because there is no request to make *for that asset*: it is computed, and its legs are planned
# as jobs of their own. It reads as a skip in the fetch report because it is one.
DERIVED = "derived"

# **These reasons are not equivalent, and adding them up is what hid the real gap.** The queue
# reported "183 with no price source" as one number covering all of them, which reads as 183
# missed opportunities. Measured 2026-07-28 it was nothing of the sort:
#
#   NOT_AN_ASSET   27 assets / 172 rows — correctly refused, and there is nothing to fix
#   COMPUTABLE     15 assets / 136 rows — real instruments, simply not built yet
#   NO_ROUTE      116 assets / 130 rows — the genuine gap, and mostly one-row long-tail labels
#
# The 53-row ``__basket__`` sentinel alone was a fifth of the headline while being, by
# construction, the extractor's placeholder for a thesis that is not about one thing.
NOT_AN_ASSET = frozenset({BASKET_REASON, MACRO_REASON, EVENT, PRIVATE})
COMPUTABLE = frozenset({DOMINANCE, DERIVED_RATIO, RATE})
NO_ROUTE = frozenset({UNMAPPED, CONFLICT})

CRYPTO_DOMAIN = "crypto"

# Real exchange tickers are short and unpunctuated. Labels that break this shape are
# themes, events or prose the LLM lifted from a transcript ("HARD_ASSETS", "SEMIS/AI",
# "US Rates/Inflation") — never something an exchange lists. Auto-routing them to Yahoo
# lands on whatever unrelated instrument happens to match, so they must be curated or
# declared unpriceable. This is a *structural* backstop for corpus growth; today's known
# ambiguous labels are already pinned in cfg/oracle_map.yaml.
MAX_TICKER_LEN = 5
_TICKER_PUNCT = frozenset("-.")


def _is_plausible_ticker(label: str) -> bool:
    if not label or len(label) > MAX_TICKER_LEN:
        return False
    return all(ch.isalnum() or ch in _TICKER_PUNCT for ch in label)


@dataclass(frozen=True)
class OracleRef:
    """A resolved (source, symbol) pair for one canonical asset."""
    asset: str
    source: str
    symbol: str
    curated: bool = False
    # True when the symbol is an unverified guess off a transcript — the fetch stage must
    # confirm the instrument exists before any price it returns is trusted.
    needs_validation: bool = False
    # A second instrument on the same source: the one an order can actually reach, when
    # ``symbol`` names something no venue lists. ``^DJI`` is the Dow the roster's theses are
    # about; ``DIA`` is the only Dow anyone can buy. Curated only — see ``trade_symbol``.
    tradeable: str | None = None

    @property
    def trade_symbol(self) -> str:
        """The instrument a zone, a stop and an order are quoted on.

        Splitting this from ``symbol`` is what lets an unbuyable index still produce a
        placeable order *without* regrading the corpus: ``score-roster`` keeps reading
        ``symbol`` (was this person right about the Dow?) while ``setups`` reads this one
        (where do I put a stop?). Collapsing the two is the move cfg/venue_map.yaml's
        header refuses, and for this reason.

        Safe to read unconditionally: absent a curated ``tradeable`` it *is* ``symbol``.
        """
        return self.tradeable or self.symbol


@dataclass(frozen=True)
class DerivedRef:
    """A ratio of two other assets, computed from their cached series rather than fetched.

    Curated only — there is deliberately no rule that infers a ratio from a label containing a
    slash. ``ETH/BTC`` means what it looks like; a transcript saying ``BTC/USD`` or ``S&P/gold``
    does not, and guessing is how this module's opening paragraph says the corpus gets priced
    against the wrong instrument. See ``oracle.derived`` for how the bars are built.
    """
    asset: str
    numerator: str
    denominator: str


@dataclass(frozen=True)
class Unpriceable:
    asset: str
    reason: str
    detail: str = ""


Route = OracleRef | DerivedRef | Unpriceable

# The half of ``Route`` that resolves to a series. Named because ``DerivedRef``'s arrival split
# this union three ways and a consumer matching on "not ``Unpriceable``" silently inherited the
# new member — which is precisely how ``plan_fetches`` came to read ``.source`` off a ratio and
# crash the whole fetch run. Match on what a value *is*, not on what it isn't.
Priceable = OracleRef | DerivedRef

_SENTINEL_REASONS = {BASKET: BASKET_REASON, MACRO: MACRO_REASON}


@dataclass(frozen=True)
class RoutingTable:
    """Everything ``route`` needs, assembled by ``load_routing_table`` (the I/O half)."""
    curated: Mapping[str, dict]
    coinbase_symbols: frozenset[str]
    kraken_symbols: frozenset[str]
    domain_consensus: Mapping[str, str]


def build_domain_consensus(rows: Iterable[tuple[str, str]]) -> dict[str, str]:
    """``(asset_canonical, domain)`` rows -> the majority domain per asset.

    Majority, not any-match: a handful of mislabelled theses must not be able to drag an
    asset onto the wrong exchange. Ties resolve to the alphabetically-first domain purely
    so the result is deterministic.
    """
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for asset, domain in rows:
        counts[asset][domain] += 1
    return {
        asset: max(sorted(domains), key=lambda d: domains[d])
        for asset, domains in counts.items()
    }


def route(asset: str, table: RoutingTable) -> Route:
    """Resolve one canonical asset to a price source, or explain why it can't be."""
    if asset in _SENTINEL_REASONS:
        return Unpriceable(asset=asset, reason=_SENTINEL_REASONS[asset])

    curated = table.curated.get(asset)
    if curated is not None:
        if "unpriceable" in curated:
            return Unpriceable(
                asset=asset, reason=curated["unpriceable"], detail=curated.get("detail", "")
            )
        derived = curated.get("derived")
        if derived is not None:
            return DerivedRef(
                asset=asset,
                numerator=derived["numerator"],
                denominator=derived["denominator"],
            )
        return OracleRef(
            asset=asset,
            source=curated["source"],
            symbol=curated["symbol"],
            curated=True,
            tradeable=curated.get("tradeable"),
        )

    domain = table.domain_consensus.get(asset)
    if domain is None:
        # No corpus evidence for what this asset even is. Symbol availability alone is
        # exactly the signal that mis-routes SPX, so we stop here instead.
        return Unpriceable(
            asset=asset, reason=CONFLICT, detail="no domain consensus in corpus"
        )

    if domain == CRYPTO_DOMAIN:
        if asset in table.coinbase_symbols:
            return OracleRef(asset=asset, source="coinbase", symbol=f"{asset}-USD")
        if asset in table.kraken_symbols:
            return OracleRef(asset=asset, source="kraken", symbol=f"{asset}USD")
        return Unpriceable(
            asset=asset, reason=UNMAPPED, detail="not listed on Coinbase or Kraken"
        )

    if not _is_plausible_ticker(asset):
        return Unpriceable(
            asset=asset, reason=CONFLICT, detail="not a plausible ticker; needs curation"
        )

    # stock / macro -> Yahoo, but the symbol is only a transcript guess until probed.
    return OracleRef(asset=asset, source="yahoo", symbol=asset, needs_validation=True)


# ── loader (I/O) ────────────────────────────────────────────────────────────

def load_curated(config_dir) -> dict[str, dict]:
    """Read ``cfg/oracle_map.yaml``. Missing file -> empty map (everything auto-routes)."""
    path = Path(config_dir) / "oracle_map.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("assets", {}) or {}


def load_routing_table(
    config_dir,
    domain_rows: Iterable[tuple[str, str]],
    *,
    listings: Mapping[str, Iterable[str]],
) -> RoutingTable:
    return RoutingTable(
        curated=load_curated(config_dir),
        coinbase_symbols=frozenset(listings.get("coinbase", ())),
        kraken_symbols=frozenset(listings.get("kraken", ())),
        domain_consensus=build_domain_consensus(domain_rows),
    )
