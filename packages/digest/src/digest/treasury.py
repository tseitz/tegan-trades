"""What the digest says about parked money and what could be earning more. Pure.

A ``TreasuryResult`` carries two independent facts, and this reduces each the way it is
actually read. **Money deployed** is standing state, not a diff — a position parked three
weeks ago produces no event tonight, so a diff would never mention it, the same reason
``_holding_section`` prints the whole book every night rather than only its moves. **A standing
yield opportunity** is the opposite: a gate-clearing pool prints in full the first night it
clears, and thereafter only as a count, the same reduction ``holdings.delta`` already makes for
a verdict that has not moved.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import safety
from treasury.book import TreasuryResult


@dataclass(frozen=True, slots=True)
class TreasuryDelta:
    """Everything the digest's Treasury section needs, computed once so the renderer never has
    to re-derive it. ``new``/``standing`` describe the opportunity half only — the deployed half
    (``rows`` through ``apy_amount``) has no diff at all, since it is standing state."""
    mandate_name: str
    rows: int
    total: float
    weighted_apy: float | None
    apy_rows: int
    apy_amount: float
    new: tuple[safety.RankedVenue, ...] = ()
    standing: int = 0
    # First night this memory key has ever been written. Mirrors ``HoldingsDelta.bootstrap``:
    # every opportunity is "new" on night one, and without this a first run reads as a violent
    # night rather than as the first time anyone looked.
    bootstrap: bool = False

    @property
    def is_quiet(self) -> bool:
        """Whether the opportunity half has nothing to print. The deployed half is unconditional
        and is not part of this — it is standing state, not something that can go quiet."""
        return not self.new and not self.standing


def has_idle(result: TreasuryResult) -> bool:
    """Non-zero idle cash — the corrected half of ``treasury.render``'s ``idle and advice``
    rule (§"When the opportunity half prints at all" in the #75 plan). ``treasury.book`` emits
    one ``IdleCash`` per account regardless of amount, so a `0.0` account must not be able to
    turn the opportunity half on."""
    return any(row.amount for row in result.idle)


def delta(result: TreasuryResult, remembered: dict[str, str], *, has_idle: bool) -> TreasuryDelta:
    """Tonight's ``TreasuryDelta``. Pure — no I/O.

    ``remembered`` is last night's ``{pool_id: slug}``. The opportunity half only ever shows
    when ``has_idle`` (non-zero idle cash) **and** ``result.advice`` is non-empty — the same
    ``idle and advice`` rule ``treasury.render._idle_block`` already applies, with a fixed
    corner: a zero-amount idle account must not be able to turn the block on. Nothing is
    invented beyond that; there is no APY threshold.

    A pool with no ``pool_id`` is skipped rather than counted — ``pool_id`` is typed
    ``str | None`` (``core.safety.VenueFacts``), and keying a memory entry on ``None`` would
    make ``state.save``'s ``json.dumps(..., sort_keys=True)`` raise.
    """
    new: list[safety.RankedVenue] = []
    standing = 0
    if has_idle and result.advice:
        for ranked in result.advice:
            pool_id = ranked.facts.pool_id
            if pool_id is None:
                continue
            if pool_id in remembered:
                standing += 1
            else:
                new.append(ranked)

    return TreasuryDelta(
        mandate_name=result.mandate.name,
        rows=len(result.rows),
        total=result.total,
        weighted_apy=result.weighted_apy,
        apy_rows=result.apy_rows,
        apy_amount=result.apy_amount,
        new=tuple(new),
        standing=standing,
        bootstrap=not remembered,
    )


def remember(result: TreasuryResult) -> dict[str, str]:
    """Tonight's ``{pool_id: slug}`` for **every** current opportunity, not only the new ones —
    the same reason ``holdings.remember`` stores every ticker rather than only the loud ones."""
    return {
        ranked.facts.pool_id: ranked.facts.slug
        for ranked in result.advice
        if ranked.facts.pool_id is not None
    }
