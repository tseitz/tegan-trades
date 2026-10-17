"""Net worth by underlying asset: the one chain the `networth --by-asset` terminal view and the
dashboard page both call, so they cannot disagree."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml
from core import networth
from core.canon import Registry, load_registry, resolve_asset
from review.cli import CONFIG_DIR, review_for
from treasury.cli import load_result

EXPOSURE_PATH = CONFIG_DIR / "exposure.yaml"


def load_underlying(path: Path, registry: Registry) -> dict[str, str]:
    """`{company: coin}` from `cfg/exposure.yaml`; `{}` when the file is absent. Raises
    `ValueError` naming the offending key rather than grouping by a typo."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("underlying") if isinstance(data, dict) else None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: `underlying` must be a mapping of ticker to ticker")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"{path}: underlying entry {key!r}: {value!r} must be ticker: ticker")
        if resolve_asset(key, registry)[0] != key:
            raise ValueError(f"{path}: underlying key {key!r} is not a canonical ticker")
        if resolve_asset(value, registry)[0] != value:
            raise ValueError(f"{path}: underlying {key!r} -> {value!r}: value is not canonical")
        out[key] = value
    return out


def grouper(registry: Registry, underlying: dict[str, str]) -> Callable[[object], str]:
    """`group_of(reading)`: alias, then wrap, then underlying."""
    def group_of(reading) -> str:
        canonical = resolve_asset(reading.holding.ticker, registry)[0]
        base = reading.lean_from or canonical
        return underlying.get(base, base)
    return group_of


def assemble(books, *, as_of: date, warn: Callable[[str], None]) -> networth.Exposure | None:
    """Treasury load warnings go to `warn`; the caller owns the books' own."""
    registry = load_registry(CONFIG_DIR)
    results = review_for(books, as_of=as_of, registry=registry)
    treasury = load_result(books=tuple(books), as_of=as_of, warn=warn)
    parked = 0.0 if treasury is None else treasury.total
    return networth.exposure(
        results, parked=parked,
        group_of=grouper(registry, load_underlying(EXPOSURE_PATH, registry)))
