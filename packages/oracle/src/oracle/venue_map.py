"""Reader for ``cfg/venue_map.yaml`` — canonical asset to venue-native symbol.

Deliberately dumb. All the judgement is in the YAML; this module only refuses to invent
what the file does not say. An asset a venue does not list resolves to ``None``, never to
the canonical symbol as a fallback — that fallback is precisely how ``SPX`` becomes an order
on a memecoin.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

# src/oracle/venue_map.py -> src/oracle -> src -> oracle -> packages -> <repo root>
CFG_PATH = Path(__file__).resolve().parents[4] / "cfg" / "venue_map.yaml"

# Keys inside an asset entry that are metadata rather than a venue symbol.
_RESERVED = {"scale"}


@dataclass(frozen=True, slots=True)
class Listing:
    canonical: str
    venue: str
    symbol: str
    scale: float | None = None

    @property
    def is_proxy(self) -> bool:
        """True when the venue instrument tracks the canonical asset at a ratio other than
        1:1 — an ETF standing in for an index. A target price quoted on the canonical asset
        is not directly usable as an order price on a proxy."""
        return self.scale is not None and self.scale != 1.0


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return doc.get("assets") or {}


def load(path: Path = CFG_PATH) -> dict:
    return _load(str(path))


def listing(canonical: str, venue: str, *, path: Path = CFG_PATH) -> Listing | None:
    """The venue's symbol for an asset, or ``None`` if it does not list it."""
    entry = load(path).get(canonical)
    if not entry:
        return None
    symbol = entry.get(venue)
    if not symbol:
        return None
    scale = (entry.get("scale") or {}).get(venue)
    return Listing(
        canonical=canonical,
        venue=venue,
        symbol=symbol,
        scale=float(scale) if scale is not None else None,
    )


def venues_for(canonical: str, *, path: Path = CFG_PATH) -> list[str]:
    entry = load(path).get(canonical) or {}
    return sorted(k for k in entry if k not in _RESERVED)


def canonical_for(venue: str, symbol: str, *, path: Path = CFG_PATH) -> str | None:
    """Reverse lookup, used when reading a log back that stores venue-native symbols."""
    for canonical, entry in load(path).items():
        if entry and entry.get(venue) == symbol:
            return canonical
    return None


def unlisted(*, path: Path = CFG_PATH) -> list[str]:
    """Approved assets no venue carries — a real gap, recorded rather than rediscovered."""
    return sorted(a for a, e in load(path).items() if not (e or {}))
