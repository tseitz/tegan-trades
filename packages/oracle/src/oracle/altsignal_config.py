"""Read ``cfg/altsignal.yaml`` — which chains and markets to track.

Missing file -> empty config (nothing tracked), same convention as
``oracle.route.load_curated`` for ``cfg/oracle_map.yaml``. A fresh checkout has no market list
curated yet; that is the expected state, not an error.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class ChainEntry:
    asset: str      # ticker as written in a portfolio file
    chain: str      # DefiLlama's slug for the same chain


@dataclass(frozen=True, slots=True)
class MarketEntry:
    platform: str   # "kalshi" | "polymarket"
    key: str        # Kalshi market ticker, or Polymarket event slug
    why: str


@dataclass(frozen=True, slots=True)
class ProtocolEntry:
    """One protocol, and the five distinct identifiers it needs across two sources.

    See ``cfg/altsignal.yaml``'s header comment for what each field is and the failure mode of
    getting it wrong — repeated there rather than here because that is where a future editor
    adding a row will actually be looking.
    """
    asset: str                             # ticker as written in a portfolio file
    llama_fees: str                        # DefiLlama parent slug, for fees/revenue
    llama_tvl: str                         # DefiLlama slug for TVL — may differ from llama_fees
    llama_oi: tuple[str, ...]              # DefiLlama child slug(s), for /overview/open-interest
    coingecko: str                         # CoinGecko coin id, for /coins/markets
    coingecko_derivatives: tuple[str, ...] # CoinGecko derivatives-exchange id(s)
    venue: str                             # reserved — no reader yet, see cfg/altsignal.yaml


@dataclass(frozen=True, slots=True)
class VenueEntry:
    """One yield venue the Safety gate (#73, ``core.safety``) can rank.

    See ``cfg/altsignal.yaml``'s header comment for what each field is and the failure mode of
    getting it wrong — repeated there rather than here for the same reason ``ProtocolEntry``'s
    docstring gives.
    """
    venue: str                     # matches a `data/treasury.yaml` row's `venue:`
    llama_protocol: str            # DefiLlama protocol slug
    llama_pools: tuple[str, ...]   # DefiLlama pool uuid(s), yields.llama.fi/pools
    asset: str                     # ticker the venue accepts, as a portfolio file writes it
    chain: str                     # DefiLlama's display chain name


@dataclass(frozen=True, slots=True)
class AltSignalConfig:
    chains: tuple[ChainEntry, ...]
    markets: tuple[MarketEntry, ...]
    protocols: tuple[ProtocolEntry, ...] = ()
    venues: tuple[VenueEntry, ...] = ()


def load(config_dir) -> AltSignalConfig:
    path = Path(config_dir) / "altsignal.yaml"
    if not path.exists():
        return AltSignalConfig(chains=(), markets=())
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    chains = tuple(
        ChainEntry(asset=row["asset"], chain=row["chain"]) for row in data.get("chains") or ()
    )
    markets = tuple(
        MarketEntry(
            platform=row["platform"],
            key=row.get("ticker") or row.get("slug"),
            why=row.get("why", ""),
        )
        for row in data.get("markets") or ()
    )
    protocols = tuple(
        ProtocolEntry(
            asset=row["asset"],
            llama_fees=row["llama_fees"],
            llama_tvl=row["llama_tvl"],
            llama_oi=tuple(row["llama_oi"]),
            coingecko=row["coingecko"],
            coingecko_derivatives=tuple(row["coingecko_derivatives"]),
            venue=row["venue"],
        )
        for row in data.get("protocols") or ()
    )
    venues = tuple(
        VenueEntry(
            venue=row["venue"],
            llama_protocol=row["llama_protocol"],
            llama_pools=tuple(row["llama_pools"]),
            asset=row["asset"],
            chain=row["chain"],
        )
        for row in data.get("venues") or ()
    )
    return AltSignalConfig(chains=chains, markets=markets, protocols=protocols, venues=venues)
