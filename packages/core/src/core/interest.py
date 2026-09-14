"""Open interest — the one number in `probe_perp_venue_fundamentals.py` that vanishes if a
nightly run misses it.

Unlike `core.altsignal.AltSignalReading`, this gets a specific dataclass for the same reason
`core.funding.FundingRate` does: it is always one number for one venue and one symbol, so a
generic shape would just be indirection. Venue adapters live in `oracle.sources`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class OpenInterest:
    """One venue's open interest for one symbol, at one instant.

    ``notional`` is USD, not base units. All three venues (Hyperliquid, Lighter, Aster) report
    ``openInterest`` in base units of the underlying, so a raw pass-through would mean "1,200
    ETH" on one row and "40,000 DOGE" on another — not comparable across symbols, let alone
    venues. It is always base units times mark price, computed at read time.

    ``symbol`` is venue-native, mapped to canon at read time — the same convention as
    ``FundingRate.symbol``.

    ``volume_24h`` rides along because both figures come from the same response on every venue;
    it is the turnover check (volume / open interest) that flags a wash-traded market.
    """

    venue: str
    symbol: str
    notional: float
    volume_24h: float
    observed_at: datetime
