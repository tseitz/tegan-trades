"""Alt-signal in `review`: DefiLlama confirms a holding, Kalshi/Polymarket confirm macro context.

Pure, mirroring ``review/levels.py``'s shape — a scan's already-fetched output goes in, a
ranked/formatted structure comes out. No network I/O of its own; ``store_read`` is injected the
same way ``fetch()``'s ``get_json`` is, so tests never touch a file or the network.

**Missing ``cfg/altsignal.yaml`` is not an error — both functions just return nothing**, the
same convention ``oracle.route`` uses for a missing ``cfg/oracle_map.yaml``: a fresh checkout
has no market list curated yet, and `review` should print nothing extra rather than raise.
"""
from __future__ import annotations

from dataclasses import dataclass

from oracle import altsignal_store
from oracle.altsignal_config import AltSignalConfig

# How many rows a MACRO block shows per configured market before it starts counting instead —
# a Polymarket price-target event alone can expand to dozens of strikes (77 readings across
# three configured events, measured 2026-09-03).
MACRO_SHOWN = 3

_LABELS = {
    "chain_tvl": "chain TVL",
    "stablecoin_supply": "stablecoins",
    "dex_volume": "24h DEX volume",
}


def _fmt_usd(value: float) -> str:
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"${value / threshold:,.2f}{suffix}"
    return f"${value:,.0f}"


@dataclass(frozen=True, slots=True)
class ChainLine:
    """One holding, and the DefiLlama lines confirming (or not) its chain's usage."""
    reading: object    # core.review.Reading
    lines: tuple[str, ...]


def chain_lines(
    readings, assets, *, altsignal_cfg: AltSignalConfig, store_read=altsignal_store.read
) -> tuple[ChainLine, ...]:
    """One ``ChainLine`` per holding whose canonical asset has a `chains:` entry, and whose
    chain has at least one stored reading. Readings ride in already zipped with `readings` —
    same shape as `review.levels.shortlist`'s `[(reading, levels), ...]` pairs."""
    by_asset = {c.asset: c.chain for c in altsignal_cfg.chains}
    out: list[ChainLine] = []
    for reading, asset in zip(readings, assets, strict=True):
        chain = by_asset.get(asset)
        if chain is None:
            continue
        stored = store_read(source="defillama", key=chain)
        if not stored:
            continue
        latest_by_kind = {r.kind: r for r in stored}
        lines = tuple(
            f"{chain.title()} {_LABELS.get(kind, kind)}: {_fmt_usd(r.value)}"
            for kind, r in latest_by_kind.items()
        )
        out.append(ChainLine(reading=reading, lines=lines))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class MacroRow:
    """One configured market/event, its top readings, and how many more it stands for."""
    why: str
    top: tuple[tuple[str, float], ...]
    others: int


def macro_block(
    *, altsignal_cfg: AltSignalConfig, store_read=altsignal_store.read, limit: int = MACRO_SHOWN
) -> tuple[MacroRow, ...]:
    """One ``MacroRow`` per configured market entry with stored data.

    Kalshi entries match a stored reading by exact key (one ticker, one reading). Polymarket
    entries match by the ``<event slug>:`` prefix ``oracle.altsignal.polymarket`` writes — an
    event can expand to many strikes, so this caps, the same "one row stands for several, the
    rest are counted" shape `review.levels.shortlist` uses.

    **Ranked by distance from 50%, not by raw value.** A price-target event's highest-value
    strikes are the ones nobody doubts ("will BTC dip below $60k" prices near 100%) — measured
    live 2026-09-04, ranking by value surfaced only those, which is the least informative
    reading a probability can give. The market disagreeing with itself is the signal.
    """
    rows: list[MacroRow] = []
    for entry in altsignal_cfg.markets:
        stored = store_read(source=entry.platform)
        if entry.platform == "kalshi":
            matches = [r for r in stored if r.key == entry.key]
        else:
            prefix = f"{entry.key}:"
            matches = [r for r in stored if r.key.startswith(prefix)]
        if not matches:
            continue
        latest_by_key = {r.key: r for r in matches}
        ranked = sorted(latest_by_key.values(), key=lambda r: abs(r.value - 0.5))
        top = tuple((r.key, r.value) for r in ranked[:limit])
        rows.append(MacroRow(why=entry.why, top=top, others=max(0, len(ranked) - limit)))
    return tuple(rows)
