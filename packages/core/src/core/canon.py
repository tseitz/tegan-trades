"""Read-time canonicalization — resolve drifting person/asset labels to canonical
entities. Pure `resolve` (a non-mutating lens over a stored Thesis) + a `load_registry`
loader for the I/O. Deterministic: registries + dict lookups + a CoinGecko snapshot,
no LLM. The raw Thesis on disk is never touched."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Sentinels: labels that are themes/baskets, not tradeable tickers. Canonicalized
# deliberately so they never masquerade as a real asset in grouping/scoring.
BASKET = "__basket__"
MACRO = "__macro__"
SENTINELS = frozenset({BASKET, MACRO})


def normalize(label: str) -> str:
    """Lookup key: lowercase, trimmed, whitespace-collapsed. `btc` == `BTC` == `Bitcoin `."""
    return " ".join(label.strip().lower().split())


@dataclass(frozen=True)
class Registry:
    people: dict[str, str] = field(default_factory=dict)        # normalized alias -> canonical name
    members: dict[str, list[str]] = field(default_factory=dict)  # canonical -> members (multi-author feeds)
    assets: dict[str, str] = field(default_factory=dict)        # normalized alias -> canonical ticker/sentinel
    tickers: dict[str, dict] = field(default_factory=dict)      # UPPER symbol -> {name, market_cap_rank}


@dataclass(frozen=True)
class ResolvedThesis:
    person_canonical: str
    person_resolved: bool
    asset_canonical: str
    asset_resolved: bool       # recognized (alias hit, sentinel, or a bare known ticker)
    asset_rank: int | None     # CoinGecko market-cap rank when crypto-in-snapshot; else None


def resolve_person(name: str, registry: Registry) -> tuple[str, bool]:
    canon = registry.people.get(normalize(name))
    return (canon, True) if canon is not None else (name, False)


def resolve_asset(label: str, registry: Registry) -> tuple[str, bool, int | None]:
    """-> (canonical, resolved, market_cap_rank). resolved = recognized (curated alias,
    sentinel, or a bare ticker CoinGecko knows). rank is the crypto market-cap rank when
    available, else None — a confidence signal, NOT a validity gate. Curated assets.yaml
    entries are trusted even without a rank (stocks/indices/commodities aren't in CoinGecko)."""
    canon = registry.assets.get(normalize(label))
    if canon is not None:
        if canon in SENTINELS:
            return canon, True, None
        return canon, True, (registry.tickers.get(canon.upper()) or {}).get("market_cap_rank")
    # not aliased — but maybe it's already a bare ticker CoinGecko knows
    info = registry.tickers.get(label.strip().upper())
    if info is not None:
        return label.strip().upper(), True, info.get("market_cap_rank")
    return label, False, None


def resolve(thesis, registry: Registry) -> ResolvedThesis:
    """Non-mutating lens: reads thesis.source.person + thesis.asset, returns a
    resolution. The stored Thesis is never modified."""
    person_c, person_ok = resolve_person(thesis.source.person, registry)
    asset_c, asset_ok, rank = resolve_asset(thesis.asset, registry)
    return ResolvedThesis(
        person_canonical=person_c, person_resolved=person_ok,
        asset_canonical=asset_c, asset_resolved=asset_ok, asset_rank=rank,
    )


# ── loader (I/O) ────────────────────────────────────────────────────────────

def _load_people(path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    people: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    if not path.exists():
        return people, members
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for person in data.get("people", []):
        name = person.get("name")
        if not name:
            continue
        people[normalize(name)] = name
        for alias in person.get("aliases", []) or []:
            people[normalize(alias)] = name
        if person.get("members"):
            members[name] = person["members"]
    return people, members


def _load_assets(path: Path) -> dict[str, str]:
    assets: dict[str, str] = {}
    if not path.exists():
        return assets
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for canonical, aliases in data.items():
        assets[normalize(canonical)] = canonical
        for alias in aliases or []:
            assets[normalize(alias)] = canonical
    return assets


def _load_tickers(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8")) or {}
    return {sym.upper(): info for sym, info in data.items()}


def load_registry(config_dir) -> Registry:
    config_dir = Path(config_dir)
    people, members = _load_people(config_dir / "watchlist.yaml")
    return Registry(
        people=people,
        members=members,
        assets=_load_assets(config_dir / "assets.yaml"),
        tickers=_load_tickers(config_dir / "tickers.json"),
    )
