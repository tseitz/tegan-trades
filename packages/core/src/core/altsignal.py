"""One outside reading, from one of the Phase 5 alt-signal sources.

Deliberately generic, unlike ``core.funding.FundingRate``. Funding is always one number for
one venue and one symbol, so a specific dataclass pays for itself. The four alt-signal sources
don't share a shape — a DefiLlama chain TVL is a float, a pump.fun graduation is a mint address
plus a pool, a Kalshi/Polymarket market is a probability — so one field (``value``) carries
whichever of those the source produced, and ``kind`` says which. This module is pure: no I/O,
no network. Source adapters live in ``oracle.altsignal``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AltSignalReading:
    """One source's reading for one key, at one instant.

    ``key`` is whatever identifies the thing within its source: a DefiLlama chain slug, a
    pump.fun mint address, or a Kalshi/Polymarket market ticker. It is never a canonical asset
    — matching a reading to a holding is `review`'s job, via `cfg/altsignal.yaml`, the same way
    a stored `FundingRate.symbol` is venue-native and mapped to canon at read time.

    ``value`` is ``str`` for a reading whose source is itself categorical — DefiLlama's
    ``category`` field (``Derivatives``, ``Dexs``, …) — not for a number rendered as text.
    Anything stored inside a ``dict`` value must be JSON-native: ``altsignal_store.append``
    calls ``json.dumps`` on it directly, so a ``date`` raises ``TypeError`` there. Write a date
    as an ISO-8601 string instead.
    """

    source: str
    kind: str
    key: str
    value: float | str | dict
    observed_at: datetime
