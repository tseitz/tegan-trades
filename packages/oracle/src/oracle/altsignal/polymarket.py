"""Polymarket — hand-picked macro/crypto markets via the public Gamma API.

Market data reads need no key or wallet (verified live 2026-09-03) — only order placement is
wallet-signed. ``cfg/altsignal.yaml`` names an event slug rather than a market ticker, because
Polymarket nests several binary markets under one event (a "what price will BTC hit" event
carries one market per strike) — one config row can expand to several readings.

**The stored key is namespaced ``<event slug>:<market slug>``, not the bare market slug.**
Once logged, a reading otherwise carries no way back to which configured event produced it —
the same problem Hyperliquid's HIP-3 builder markets have, and the same fix: keep both halves
(``oracle/sources/hyperliquid.py``'s ``venue=f"{VENUE}:{dex}"``). `review` needs the event slug
to group readings back under one config row's ``why``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from core.altsignal import AltSignalReading

from oracle import http

BASE = "https://gamma-api.polymarket.com/events"

SOURCE = "polymarket"


def parse_event(payload, *, event_slug: str, observed_at: datetime) -> list[AltSignalReading]:
    """One reading per market in the event. ``outcomePrices`` arrives as a JSON-encoded
    string, not a real array — a genuine quirk of this API, not a typo here. The first price
    is always the "Yes" outcome."""
    if not payload:
        return []
    event = payload[0]
    readings: list[AltSignalReading] = []
    for market in event.get("markets") or []:
        raw = market.get("outcomePrices")
        if not raw:
            continue
        try:
            yes_price = float(json.loads(raw)[0])
        except (ValueError, TypeError, IndexError, json.JSONDecodeError):
            continue
        market_slug = market.get("slug") or market.get("question", "")
        readings.append(
            AltSignalReading(
                source=SOURCE,
                kind="market",
                key=f"{event_slug}:{market_slug}",
                value=yes_price,
                observed_at=observed_at,
            )
        )
    return readings


def fetch(
    slugs: list[str], *, get_json=http.get_json, observed_at: datetime | None = None
) -> list[AltSignalReading]:
    """One or more readings per event slug — see ``parse_event`` for why the count varies."""
    at = observed_at or datetime.now(UTC)
    readings: list[AltSignalReading] = []
    for slug in slugs:
        readings.extend(
            parse_event(get_json(BASE, {"slug": slug}), event_slug=slug, observed_at=at)
        )
    return readings
