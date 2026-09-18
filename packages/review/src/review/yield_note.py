"""A same-asset wrapper beside a holding you already own — the yield note (#74).

Pure, mirroring ``review/altsignal.py``'s shape — a scan's already-fetched output goes in, a
ranked/formatted structure comes out. No network I/O of its own; ``store_read`` is injected the
same way ``review.altsignal``'s functions already inject it, so tests never touch a file or the
network.

**Missing ``wrappers:`` is not an error — the function just returns nothing**, the same
convention ``review.altsignal`` uses for a missing ``cfg/altsignal.yaml``.

**The silence rule is about visibility, not about work already done.** ADR-0008:41 silences an
asset only where the current state cannot be detected at all (a Solana native stake). Where a
wallet's figis make "already wrapped" a readable fact, staying silent about the rest of the
asset would hide the exposure that is not yet wrapped — see §3 of the #74 plan for the worked
example (0.0246 METH wrapped, ~0.21 ETH not).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core import safety
from core.venue_facts import pool_candidates, protocol_facts
from oracle import altsignal_store
from oracle.altsignal_config import AltSignalConfig

# A receipt token's on-chain address — what "already held" is read from. Anything else (a
# Bloomberg figi like `BBG00564XQN4`) means this book cannot say what is already wrapped, so
# the readability gate (AC 3) refuses rather than guessing.
_CONTRACT_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass(frozen=True, slots=True)
class YieldNote:
    """One canonical asset's suggested wrapper, plus what of it is already held.

    ``gate``/``score`` ride along from the ``safety.RankedVenue`` that produced this note, the
    same shape that dataclass already uses — a renderer never recomputes either.
    """
    reading: object                              # core.review.Reading
    wrapper: str
    protocol: str
    apy: float | None                             # None only if DefiLlama never reported one
    already: tuple[tuple[str, float], ...]        # (wrapper ticker, shares) already held
    gate: safety.GateResult
    score: safety.SafetyScore


def _held_shares(entry, positions) -> float | None:
    """How much of ``entry`` this book already holds, or ``None`` when it holds none.

    Matched by contract address wherever a position carries a ``figi``; falling back to ticker
    equality against ``entry.wrapper`` only for a position with none — a wallet-synced position
    always has one, so the fallback exists for a hand-kept file that never will.
    """
    for position in positions:
        if position.figi:
            if position.figi.lower() == entry.contract.lower():
                return position.holding.shares
        elif position.holding.ticker == entry.wrapper:
            return position.holding.shares
    return None


def yield_notes(
    readings, assets, positions, *, altsignal_cfg: AltSignalConfig, as_of,
    store_read=altsignal_store.read,
) -> tuple[YieldNote, ...]:
    """One ``YieldNote`` per canonical asset held that has a cheaper-exposure wrapper worth
    suggesting. ``readings``/``assets``/``positions`` all zip strictly — one entry per position,
    in file order, the same promise ``build_readings`` already makes.

    1. No `wrappers:` configured, or no position in the book carries a readable contract address
       (AC 3) -> nothing, for the whole book.
    2. Per canonical asset with a configured wrapper: drop any wrapper this book already holds
       (by contract, or by ticker where a position has no ``figi``) — suggesting what you already
       own is the nag ADR-0008 guards against. What is dropped is recorded, not discarded.
    3. Rank what remains through the Safety gate (#73). Nothing stored, or nothing clears the
       gate -> nothing for this asset (AC 4) — even when something was dropped in step 2.
    4. The highest-APY survivor becomes the note, attached to whichever position holding this
       asset has the larger ``market_value`` (ticker breaks a tie) — the row a reader is already
       looking at when they think about this asset.
    """
    if not altsignal_cfg.wrappers:
        return ()
    if not any(p.figi and _CONTRACT_RE.match(p.figi) for p in positions):
        return ()

    by_asset: dict[str, list] = {}
    for entry in altsignal_cfg.wrappers:
        by_asset.setdefault(entry.asset, []).append(entry)

    facts_by_slug, _ = protocol_facts(store_read=store_read)

    # The larger-value reading per canonical asset, ticker as the deterministic tiebreak.
    best_reading: dict[str, object] = {}
    for reading, asset in zip(readings, assets, strict=True):
        if asset not in by_asset:
            continue
        current = best_reading.get(asset)
        if current is None:
            best_reading[asset] = reading
            continue
        value = reading.market_value or 0.0
        cur_value = current.market_value or 0.0
        if value > cur_value or (
            value == cur_value and reading.holding.ticker < current.holding.ticker
        ):
            best_reading[asset] = reading

    notes: list[YieldNote] = []
    for asset, reading in best_reading.items():
        remaining = []
        already: list[tuple[str, float]] = []
        for entry in by_asset[asset]:
            shares = _held_shares(entry, positions)
            if shares is not None:
                already.append((entry.wrapper, shares))
            else:
                remaining.append(entry)

        if not remaining:
            continue

        candidates, _ = pool_candidates(remaining, facts_by_slug, store_read=store_read)
        if not candidates:
            continue

        ranked = safety.rank(candidates, as_of=as_of, by_slug=facts_by_slug)
        if not ranked:
            continue

        top = ranked[0]
        # A protocol slug is not unique across `remaining`'s entries, so the note's wrapper name
        # comes from a pool_id -> entry lookup rather than the top slug alone.
        pool_to_entry = {pool_id: entry for entry in remaining for pool_id in entry.llama_pools}
        entry = pool_to_entry[top.facts.pool_id]

        notes.append(YieldNote(
            reading=reading,
            wrapper=entry.wrapper,
            protocol=entry.llama_protocol,
            apy=top.facts.apy,
            already=tuple(already),
            gate=top.gate,
            score=top.score,
        ))

    return tuple(notes)
