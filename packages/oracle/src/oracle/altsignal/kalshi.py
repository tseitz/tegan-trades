"""Kalshi — hand-picked macro markets (Fed decisions, recession odds), as macro context.

Market data reads need no key (verified live 2026-09-03 against a real ticker) — only placing
an order needs an RSA-signed key. ``cfg/altsignal.yaml`` names specific market tickers rather
than whole series, because a series like ``KXFED`` covers every strike at every future meeting;
picking the strikes worth watching is the same hand-curation ``cfg/oracle_map.yaml`` does.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.altsignal import AltSignalReading

from oracle import http

BASE = "https://api.elections.kalshi.com/trade-api/v2/markets"

SOURCE = "kalshi"


def parse_market(payload) -> float | None:
    """The market's last traded price, read as a 0-1 probability. ``None`` when the reply
    carries no price — a market that hasn't traded yet, or a bad ticker."""
    market = (payload or {}).get("market") or {}
    raw = market.get("last_price_dollars")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def fetch(
    tickers: list[str], *, get_json=http.get_json, observed_at: datetime | None = None
) -> list[AltSignalReading]:
    """One reading per ticker. A ticker with no price is skipped, not fatal to the sweep —
    same reasoning as every other source adapter in this package."""
    at = observed_at or datetime.now(UTC)
    readings: list[AltSignalReading] = []
    for ticker in tickers:
        price = parse_market(get_json(f"{BASE}/{ticker}"))
        if price is not None:
            readings.append(
                AltSignalReading(source=SOURCE, kind="market", key=ticker, value=price, observed_at=at)
            )
    return readings
