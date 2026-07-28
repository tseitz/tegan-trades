"""The order log — append-only JSONL under ``data/execution/``.

Unlike ``oracle.decisions``, this file gets **no vault mirror**. The distinction is the one
``docs/ARCHITECTURE.md`` already draws: a decision is hand-entered judgement that nothing can
reconstruct, whereas an order is a fact the venue also holds and will happily replay. Losing
this file costs convenience, not evidence, so it stays ore.

What it is for is the join no venue statement can provide: which *candidate* an order came
from. The exchange knows an ETH long was opened; only this file knows it came from
``candidate_key`` abc123, backed by four people, at score 0.71. That link is what makes it
possible to ever ask whether the scorer predicts outcomes.

**Refusals are recorded too, not just placements.** An evening where nothing executed
because every candidate was unlisted looks identical to an evening where nothing was tried,
unless the refusals are on disk — the same argument ``setups_cli`` makes for tallying
``NotASetup`` reasons rather than discarding them.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

# packages/execution/src/execution/store.py -> ... -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PATH = REPO_ROOT / "data" / "execution" / "orders.jsonl"

PLACED = "placed"
FAILED = "failed"
REFUSED = "refused"


def _append(path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record_placement(path, plan, placement, *, network: str, at: str | None = None) -> dict:
    """Log an attempt that reached the venue, whether or not it was accepted.

    ``raw`` is kept verbatim. The parsed verdict is what gets read day to day, but a venue
    reply that this code misread is exactly the case where the original text is the only way
    to find out — and by then it is far too late to add.
    """
    record = {
        "at": at or _now(),
        "outcome": PLACED if placement.ok else FAILED,
        "network": network,
        "candidate_key": plan.candidate_key,
        "asset": plan.asset,
        "coin": plan.coin,
        "direction": plan.direction,
        "size": plan.size,
        "entry": plan.entry,
        "stop": plan.stop,
        "target": plan.target,
        # The three numbers that make a fill interpretable after the fact. Equity moves, so
        # "1% of the account" is only recoverable if the account value is written down here.
        "risk": plan.risk,
        "notional": plan.notional,
        "equity": plan.equity,
        "order_ids": list(placement.order_ids),
        "statuses": list(placement.statuses),
        "error": placement.error,
        "raw": placement.raw,
    }
    _append(path, record)
    return record


def record_refusal(path, candidate, refusal, *, network: str, at: str | None = None) -> dict:
    """Log a candidate that was approved but never sent, and why.

    Keyed the same way as a placement so both sides of the question — what executed, what was
    blocked — come out of one file with one pass.
    """
    record = {
        "at": at or _now(),
        "outcome": REFUSED,
        "network": network,
        "candidate_key": candidate.key,
        "asset": candidate.asset,
        "direction": candidate.direction,
        "reason": refusal.code,
        "detail": refusal.detail,
    }
    _append(path, record)
    return record


def load(path) -> list[dict]:
    """Every record, oldest first."""
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def placed_keys(path, *, network: str | None = None) -> set[str]:
    """Candidate keys that already have a live order behind them **on ``network``**.

    The guard against double-execution: approving the same zone twice across two sessions is
    ordinary (a deferral returning), but sending a second identical bracket is not.

    **Filtered by network, and that is not a nicety.** Rehearsing a candidate on testnet and
    then trading it for real on mainnet is the intended workflow — this whole package is
    built around it. Without the filter the rehearsal marks the key as placed and the *real*
    order is silently refused as a duplicate, which is precisely backwards: the practice run
    would veto the trade it was practising for.

    ``network=None`` returns every network's keys, which is only right for auditing.
    """
    return {
        r["candidate_key"] for r in load(path)
        if r.get("outcome") == PLACED and (network is None or r.get("network") == network)
    }
