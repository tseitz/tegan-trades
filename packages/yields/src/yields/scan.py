"""For each token you hold, anywhere, what could it be earning (#95).

Pure, mirroring ``review/yield_note.py``'s shape — a scan's already-fetched output goes in, a
ranked/assembled structure comes out. No network I/O of its own; ``store_read`` is injected the
same way ``review.altsignal``/``treasury.book`` already inject it.

**AC 2 is upstream of the gate, not at it.** ``core.venue_facts.pool_candidates`` drops a
wrapper whose pool has never been fetched, and ``core.safety.rank`` drops an outlier or a gate
failure — a held wrapper can be dropped at any of the three points before a verdict exists. So
the held set is assembled first, from ``altsignal_cfg.wrappers`` x positions — pure config, no
store — then left-joined against whatever the store produced. A held wrapper with no reading
becomes a ``not_fetched`` option; a held wrapper that fails the gate or trips ``outlier`` is kept
anyway, since dropping either would silently imply nothing is held.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

from core import safety
from core.canon import Registry, load_registry, resolve_asset
from core.venue_facts import pool_candidates, protocol_facts
from oracle import altsignal_store
from oracle.altsignal_config import AltSignalConfig, WrapperEntry

# src/yields/scan.py -> src/yields -> src -> yields -> packages -> <repo root>
CONFIG_DIR = Path(__file__).resolve().parents[4] / "cfg"

# A receipt token's on-chain address — what "already held" is read from. Copied from
# `review.yield_note`'s own gate, since a Bloomberg-style figi means this book cannot say what
# is already wrapped.
_CONTRACT_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# A configured wrapper whose pool the store has never actually seen — distinct from a real gate
# failure (facts were read and did not clear it). Copied from `treasury.book.NOT_FETCHED` rather
# than imported — a view may not import a view, ADR-0004.
NOT_FETCHED = "not_fetched"


class ReadingsAsOf(NamedTuple):
    """The freshest and oldest ``observed_at`` behind every option below — copied from
    ``treasury.book.ReadingsAsOf``, not imported, for the same reason ``NOT_FETCHED`` is."""
    freshest: datetime | None
    oldest: datetime | None


@dataclass(frozen=True, slots=True)
class YieldOption:
    """One pool a wrapper could sit in — flat, so ``yields.render`` never recomputes a gate or
    a score (AC 4). ``facts``/``gate``/``score`` are all ``None`` together exactly when
    ``state`` is ``NOT_FETCHED``: the pool has never been read, so nothing to report on it
    exists yet."""
    wrapper: str
    pool_id: str | None
    facts: safety.VenueFacts | None
    gate: safety.GateResult | None
    score: safety.SafetyScore | None
    held: float | None      # shares held, `None` when this wrapper is not held
    state: str | None       # `NOT_FETCHED`, or `None` when a gate verdict exists


@dataclass(frozen=True, slots=True)
class AssetYield:
    """One canonical asset held anywhere, with every configured wrapper's options ranked."""
    asset: str
    mandates: tuple[str, ...]
    held_state_readable: bool
    options: tuple[YieldOption, ...]


class YieldsResult(NamedTuple):
    """Everything the yields view can say, from a single ``yields_for`` call.

    ``configured``/``matched`` back the "never print nothing" line: an empty ``wrappers:``
    block, a mistyped ``asset:``, and a genuinely fully-optimised book all look like a blank
    screen otherwise. ``configured`` is every ``WrapperEntry`` in ``altsignal_cfg.wrappers``;
    ``matched`` is however many of those name an asset actually held in ``books``.
    """
    assets: tuple[AssetYield, ...]
    as_of: date
    readings_as_of: ReadingsAsOf
    configured: int
    matched: int


def _held_shares(entry: WrapperEntry, positions) -> float | None:
    """How much of ``entry`` is already held, or ``None`` when none of ``positions`` holds it.

    Copied from ``review.yield_note._held_shares`` — matched by contract address wherever a
    position carries a ``figi``, falling back to ticker equality only for a position with none.
    """
    for position in positions:
        if position.figi:
            if position.figi.lower() == entry.contract.lower():
                return position.holding.shares
        elif position.holding.ticker == entry.wrapper:
            return position.holding.shares
    return None


def _sort_key(option: YieldOption):
    """Gate-passing (by APY descending) first, then gate-failing held, then not-fetched held —
    per the Design's "Sort" rule. An unheld option is never anything but gate-passing, since
    ``safety.rank`` already dropped every fail and outlier before this option existed."""
    if option.state == NOT_FETCHED:
        bucket = 2
    elif option.gate is not None and not option.gate.passed:
        bucket = 1
    else:
        bucket = 0
    apy = option.facts.apy if option.facts is not None and option.facts.apy is not None else None
    apy_key = -apy if apy is not None else float("inf")
    return (bucket, apy_key, option.wrapper, option.pool_id or "")


def yields_for(
    books, *, altsignal_cfg: AltSignalConfig, as_of: date,
    registry: Registry | None = None, store_read=altsignal_store.read,
) -> YieldsResult:
    """One block per canonical asset held anywhere across ``books`` that has a configured
    wrapper, options ranked within it.

    ``books`` is whatever loaded every portfolio — the same escape hatch
    ``treasury.cli.load_result`` takes, so a Surface can call this without a second load.
    """
    registry = load_registry(CONFIG_DIR) if registry is None else registry
    configured = len(altsignal_cfg.wrappers)

    by_wrapper_asset: dict[str, list[WrapperEntry]] = {}
    for entry in altsignal_cfg.wrappers:
        by_wrapper_asset.setdefault(entry.asset, []).append(entry)

    if not by_wrapper_asset:
        return YieldsResult(
            assets=(), as_of=as_of, readings_as_of=ReadingsAsOf(freshest=None, oldest=None),
            configured=configured, matched=0,
        )

    holders_by_asset = {}
    for book in books:
        for position in book.positions:
            asset = resolve_asset(position.holding.ticker, registry)[0]
            holders_by_asset.setdefault(asset, []).append((book, position))

    matched = sum(
        len(entries) for asset, entries in by_wrapper_asset.items() if asset in holders_by_asset
    )

    facts_by_slug, all_rows = protocol_facts(store_read=store_read)
    all_rows = list(all_rows)

    assets_out: list[AssetYield] = []
    for asset, entries in by_wrapper_asset.items():
        holders = holders_by_asset.get(asset)
        if not holders:
            continue

        mandates = tuple(sorted({book.mandate.name for book, _position in holders}))
        positions = [position for _book, position in holders]
        readable = any(
            position.figi and _CONTRACT_RE.match(position.figi) for position in positions
        )

        held_entries: list[tuple[WrapperEntry, float]] = []
        unheld_entries: list[WrapperEntry] = []
        for entry in entries:
            shares = _held_shares(entry, positions)
            if shares is not None:
                held_entries.append((entry, shares))
            else:
                unheld_entries.append(entry)

        options: list[YieldOption] = []

        if unheld_entries:
            candidates, rows = pool_candidates(unheld_entries, facts_by_slug, store_read=store_read)
            all_rows.extend(rows)
            pool_to_entry = {
                pool_id: entry for entry in unheld_entries for pool_id in entry.llama_pools
            }
            ranked = safety.rank(candidates, as_of=as_of, by_slug=facts_by_slug)
            for ranked_venue in ranked:
                entry = pool_to_entry[ranked_venue.facts.pool_id]
                options.append(YieldOption(
                    wrapper=entry.wrapper, pool_id=ranked_venue.facts.pool_id,
                    facts=ranked_venue.facts, gate=ranked_venue.gate, score=ranked_venue.score,
                    held=None, state=None,
                ))

        for entry, shares in held_entries:
            candidates, rows = pool_candidates([entry], facts_by_slug, store_read=store_read)
            all_rows.extend(rows)
            produced = {facts.pool_id for facts in candidates}
            for facts in candidates:
                gate_result = safety.gate(facts, as_of=as_of, by_slug=facts_by_slug)
                options.append(YieldOption(
                    wrapper=entry.wrapper, pool_id=facts.pool_id, facts=facts,
                    gate=gate_result, score=safety.score(facts), held=shares, state=None,
                ))
            for pool_id in entry.llama_pools:
                if pool_id not in produced:
                    options.append(YieldOption(
                        wrapper=entry.wrapper, pool_id=pool_id, facts=None, gate=None,
                        score=None, held=shares, state=NOT_FETCHED,
                    ))

        if not options:
            continue

        options.sort(key=_sort_key)
        assets_out.append(AssetYield(
            asset=asset, mandates=mandates, held_state_readable=readable,
            options=tuple(options),
        ))

    assets_out.sort(key=lambda a: a.asset)
    return YieldsResult(
        assets=tuple(assets_out), as_of=as_of, readings_as_of=_readings_as_of(all_rows),
        configured=configured, matched=matched,
    )


def _readings_as_of(rows) -> ReadingsAsOf:
    timestamps = [r.observed_at for r in rows]
    if not timestamps:
        return ReadingsAsOf(freshest=None, oldest=None)
    return ReadingsAsOf(freshest=max(timestamps), oldest=min(timestamps))
