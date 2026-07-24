"""Which symbols each exchange currently carries — cached ore, refetchable.

Listings drift (delistings, new pairs), so this is ``data/`` not ``cfg/``. Routing reads
it to decide whether Coinbase or Kraken can serve a crypto asset at all.
"""
from __future__ import annotations

import json
from pathlib import Path

from oracle import http
from oracle.cache import DATA_ROOT

LISTINGS_PATH = DATA_ROOT / "_listings.json"

COINBASE_PRODUCTS = "https://api.exchange.coinbase.com/products"
KRAKEN_PAIRS = "https://api.kraken.com/0/public/AssetPairs"


def parse_coinbase(payload) -> set[str]:
    """Base assets of online USD pairs (``BTC-USD`` -> ``BTC``)."""
    return {
        p["id"].split("-")[0]
        for p in payload or []
        if p.get("id", "").endswith("-USD") and p.get("status") == "online"
    }


def parse_kraken(payload) -> set[str]:
    """Base assets of USD pairs, read off ``wsname`` (``XBT/USD`` -> ``XBT``).

    Kraken's own keys are aliased (``XXMRZUSD``), so ``wsname`` is the only field that
    reliably gives back the symbol a human would recognize.
    """
    result = (payload or {}).get("result") or {}
    return {
        info["wsname"].split("/")[0]
        for info in result.values()
        if info.get("wsname", "").endswith("/USD")
    }


def fetch(*, get_json=http.get_json) -> dict[str, list[str]]:
    return {
        "coinbase": sorted(parse_coinbase(get_json(COINBASE_PRODUCTS))),
        "kraken": sorted(parse_kraken(get_json(KRAKEN_PAIRS))),
    }


def save(listings: dict, path: Path = LISTINGS_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(listings, indent=1), encoding="utf-8")
    return path


def load(path: Path = LISTINGS_PATH) -> dict[str, list[str]] | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_or_fetch(path: Path = LISTINGS_PATH, *, get_json=http.get_json) -> dict[str, list[str]]:
    cached = load(path)
    if cached is not None:
        return cached
    listings = fetch(get_json=get_json)
    save(listings, path)
    return listings
