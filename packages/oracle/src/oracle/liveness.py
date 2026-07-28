"""Which mapped markets are listed but not actually trading. Reads the funding log.

`cfg/venue_map.yaml` answers *what is this called here* and nothing else. It is hand-curated
because naming is stable — ``xyz:NVDA`` will still be ``xyz:NVDA`` next quarter. Whether that
market **trades** is not stable, and putting the answer in the same file would make a config
edit the thing standing between a stop-loss and a book that cannot fill it.

So dormancy is derived, from a measurement already being collected. `data/funding/` is polled
nightly across every mapped (asset, venue) pair, and the log separates the living from the
dead without being asked to. Measured 2026-07-27: each of the 17 ``xyz:`` markets in the map
carries **502 observations**; ``xyz:DXY`` carries **zero**. That gap is the signal, it costs
nothing to read, and it corrects itself the night a market wakes up or dies.

**Dormancy is a comparison, never an absolute count.** "Zero rows" is also what a night the
fetcher never ran looks like, and a gate built on the absolute number would refuse the whole
queue while reporting it as market structure. So a market is dormant only when its *cohort*
— the same venue, and for HIP-3 the same dex — reported while it did not. Silence shared with
its siblings is an outage and yields no verdict, which is the same instinct as
``check_liquidity`` treating a failed fetch as a refusal rather than a pass.

This is deliberately NOT in ``venue_map``, which imports nothing and must stay that way, and
NOT in ``execution``, which knows nothing of the map or the corpus. It is a third fact about
an (asset, venue) pair and it lives with the other two joins over the funding log.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from oracle import funding_store, venue_map
from oracle.carry import DEFAULT_VENUE, DEFAULT_WINDOW_DAYS


def log_coordinates(listing) -> tuple[str, str]:
    """``(venue, symbol)`` as the funding log records them, for a map listing.

    The map keeps the HIP-3 namespace in the symbol (``xyz:NVDA``); the writer splits it,
    filing the dex under the venue (``hyperliquid:xyz``) and the bare ticker under the
    symbol. Mirrors ``funding_cli._backfill`` exactly — the join has to be the inverse of the
    write or it silently finds nothing and calls every market dead.
    """
    if ":" in listing.symbol:
        dex, symbol = listing.symbol.split(":", 1)
        return f"{listing.venue}:{dex}", symbol
    return listing.venue, listing.symbol


def dormant(
    asset: str,
    venue: str = DEFAULT_VENUE,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    root: Path = funding_store.DATA_ROOT,
    now: datetime | None = None,
    map_path: Path = venue_map.CFG_PATH,
) -> bool:
    """Is this market mapped, polled alongside markets that reported, and silent anyway?

    False for anything that is merely *unlisted* — that is a different fact, and
    ``guards.REFUSAL_UNLISTED`` already names it. False, too, whenever the log cannot support
    a verdict: no cohort reporting means no conclusion, never a refusal by default.
    """
    listing = venue_map.listing(asset, venue, path=map_path)
    if listing is None:
        return False

    at = now or datetime.now(UTC)
    log_venue, log_symbol = log_coordinates(listing)
    cohort = {
        row.symbol
        for row in funding_store.read(root=root, since=at - timedelta(days=window_days))
        if row.venue == log_venue
    }
    if not cohort:
        return False
    return log_symbol not in cohort
