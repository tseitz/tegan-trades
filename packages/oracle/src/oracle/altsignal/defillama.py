"""DefiLlama — chain-level usage and liquidity, as a confirmation signal for a crypto holding.

Three endpoints, all free and unauthenticated (verified live 2026-09-03):

    /v2/historicalChainTvl/{chain}   TVL history for one chain — take the latest point
    /stablecoincharts/{chain}        stablecoin supply on one chain over time — "dry powder"
    /overview/dexs/{chain}           DEX trading volume for one chain — proves real usage,
                                      not just parked capital

DefiLlama has no "active users" endpoint. That is a real gap in what this source can answer,
not something missed here — see the Phase 5 design spec for the research behind that.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.altsignal import AltSignalReading

from oracle import http

TVL_BASE = "https://api.llama.fi/v2/historicalChainTvl"
STABLECOIN_BASE = "https://stablecoins.llama.fi/stablecoincharts"
DEX_BASE = "https://api.llama.fi/overview/dexs"

SOURCE = "defillama"


def parse_chain_tvl(payload) -> float | None:
    """The latest TVL point. Empty history (a chain DefiLlama doesn't track) is ``None``."""
    if not payload:
        return None
    return payload[-1].get("tvl")


def parse_stablecoin_supply(payload) -> float | None:
    """The latest USD-denominated stablecoin supply on the chain — a leading liquidity signal
    that often moves before TVL does."""
    if not payload:
        return None
    latest = payload[-1]
    return (latest.get("totalCirculatingUSD") or {}).get("peggedUSD")


def parse_dex_volume(payload) -> float | None:
    """24h DEX trading volume — confirms TVL growth is real usage, not parked capital."""
    if not payload:
        return None
    return payload.get("total24h")


def fetch(
    chains: list[str], *, get_json=http.get_json, observed_at: datetime | None = None
) -> list[AltSignalReading]:
    """One reading per metric per chain. A chain missing one metric still yields the others —
    a partial sweep is worth more than none, same reasoning as the funding source adapters."""
    at = observed_at or datetime.now(UTC)
    readings: list[AltSignalReading] = []

    for chain in chains:
        tvl = parse_chain_tvl(get_json(f"{TVL_BASE}/{chain}"))
        if tvl is not None:
            readings.append(
                AltSignalReading(source=SOURCE, kind="chain_tvl", key=chain, value=tvl, observed_at=at)
            )

        supply = parse_stablecoin_supply(get_json(f"{STABLECOIN_BASE}/{chain}"))
        if supply is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="stablecoin_supply", key=chain, value=supply, observed_at=at
                )
            )

        volume = parse_dex_volume(get_json(f"{DEX_BASE}/{chain}"))
        if volume is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="dex_volume", key=chain, value=volume, observed_at=at
                )
            )

    return readings
