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
class AltSignalConfig:
    chains: tuple[ChainEntry, ...]
    markets: tuple[MarketEntry, ...]


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
    return AltSignalConfig(chains=chains, markets=markets)
