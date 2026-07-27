"""Turn the funding log into something ``core.setups`` can cost a trade with.

The join nobody else can do: the log stores **venue-native** symbols (``NVDA``, ``XAUUSDT``,
``US500``) and the corpus speaks **canonical** ones (``NVDA``, ``GOLD``, ``SPX``). Mapping
between them is `cfg/venue_map.yaml`'s job, and doing it here — at read time, on the way into
the engine — is what keeps a mapping mistake from ever being written into the log itself.

An asset with no listing on the chosen venue, or with no observations, yields **no outlook**.
`core.setups` then skips the carry adjustment entirely rather than costing the trade at a
zero nobody measured. That distinction is the whole contract: absent and free are different.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.funding import FundingOutlook, summarize

from oracle import funding_store, venue_map

DEFAULT_VENUE = "hyperliquid"

# How far back to summarise. Long enough for a median to mean something, short enough that a
# regime change is not averaged away by last quarter's conditions. TUNE.
DEFAULT_WINDOW_DAYS = 30


def outlooks_for(
    assets,
    *,
    venue: str = DEFAULT_VENUE,
    window_days: int = DEFAULT_WINDOW_DAYS,
    root: Path = funding_store.DATA_ROOT,
    now: datetime | None = None,
    map_path: Path = venue_map.CFG_PATH,
) -> dict[str, FundingOutlook]:
    """Canonical asset -> what holding it has cost on ``venue``, over the last window.

    Assets absent from the result are exactly those the engine must not cost: unlisted on this
    venue, or listed but unobserved.
    """
    at = now or datetime.now(UTC)
    stored = funding_store.read(root=root, since=at - timedelta(days=window_days))
    if not stored:
        return {}

    # Group once. Doing it per asset would rescan the whole log for every symbol.
    by_symbol: dict[str, list] = {}
    for row in stored:
        # HIP-3 markets record as "hyperliquid:xyz"; they are still the hyperliquid venue.
        base = row.venue.split(":", 1)[0]
        if base != venue:
            continue
        by_symbol.setdefault(row.symbol, []).append(row)

    out: dict[str, FundingOutlook] = {}
    for asset in set(assets):
        listing = venue_map.listing(asset, venue, path=map_path)
        if listing is None:
            continue
        # The log strips the HIP-3 namespace from the symbol; the map keeps it.
        rows = by_symbol.get(listing.symbol.split(":", 1)[-1])
        if not rows:
            continue
        outlook = FundingOutlook.from_stats(venue, summarize(rows))
        if outlook is not None:
            out[asset] = outlook
    return out
