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
# What the venue said had become of a ``placed`` order when someone went back and asked.
RECONCILED = "reconciled"
# How the trade ENDED. The outcome the log had no way to express: ``reconciled`` settles the
# *entry*, so before this existed a candidate left every work list at entry fill and its
# realised P/L was recorded nowhere. See ``execution.outcome``.
CLOSED = "closed"


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
        # What the risk budget asked for before a ceiling cut it, and which ceiling did.
        # ``None`` on an ordinary order. Recorded because the residual in §40 — whether a
        # shrunk order is approved and held like a full-size one — cannot be asked at all
        # unless the shrinking is on disk, and by the time someone wants to ask, it is far
        # too late to start writing it down.
        "capped_from": plan.capped_from,
        "cap_reason": plan.cap_reason,
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


def record_reconciliation(path, state, *, network: str, at: str | None = None) -> dict:
    """Log what the venue says became of an order this file already called ``placed``.

    **``placed`` means "the venue accepted the submission", and that is weaker than it reads.**
    A GTC bracket sent while the market is shut comes back ``accepted``; the buying-power and
    account-type checks run at the open, hours later. On 2026-07-29 three of eight orders were
    rejected that way and this file went on saying ``placed`` for all three.

    Appended rather than corrected in place, because the log is append-only and both facts are
    true: the submission *was* accepted, and it *was* later killed. Overwriting the first would
    lose the timing, which is the whole story.
    """
    record = {
        "at": at or _now(),
        "outcome": RECONCILED,
        "network": network,
        "candidate_key": state.candidate_key,
        # The venue's word, unmodified. ``failed`` is this repo's reading of it, kept beside
        # rather than instead of, so a status Alpaca adds later is still on disk to interpret.
        "status": state.status,
        "failed": state.failed,
        "filled_qty": state.filled_qty,
        "filled_avg_price": state.filled_avg_price,
        "leg_statuses": list(state.leg_statuses),
    }
    _append(path, record)
    return record


def record_close(path, close, realized, quality, *, network: str, asset: str,
                 entry_price: float, entry_at=None, reconstructed: bool = False,
                 at: str | None = None) -> dict:
    """Log how a trade ENDED — the outcome row, and the only one a calibration pass can regress.

    Appended like every other row, so a candidate that traded twice carries two closes and the
    sequence stays readable.

    **Both R denominators are written**, because an entry is a limit and can fill better than
    planned, at which point the risk budgeted and the risk taken are different numbers. See
    ``outcome.realized`` — the INTL trade is +2.60R against its budget and +3.68R against its
    fill, and neither is the wrong answer.

    **``credible`` is part of the record, not commentary.** A paper fill, or one above the
    participation ceiling, never had to compete for liquidity; pooled with real fills it raises
    the estimate of every setup that looked like it. See ``outcome.fill_quality``.

    ``reconstructed`` marks a close derived from the venue's history rather than captured as it
    happened — ``oracle.decisions`` module docstring, "never present a backfill as captured
    live". Every close the nightly finds is reconstructed; the flag exists so a future live
    capture is distinguishable from it.
    """
    held_days = None
    if entry_at is not None and close.at is not None:
        held_days = (close.at - entry_at).total_seconds() / 86_400

    record = {
        "at": at or _now(),
        "outcome": CLOSED,
        "network": network,
        "candidate_key": close.candidate_key,
        "asset": asset,
        "symbol": close.symbol,
        # Which leg took it, read from the order id rather than guessed from the price.
        "exit_reason": close.reason,
        "exit_order_id": close.order_id,
        "exit_qty": close.qty,
        "exit_price": close.price,
        "exit_at": close.at.isoformat() if close.at is not None else None,
        "exit_prints": close.prints,
        "entry_price": entry_price,
        "entry_at": entry_at.isoformat() if entry_at is not None else None,
        "held_days": held_days,
        "pnl": realized.pnl,
        "risk_planned": realized.risk_planned,
        "risk_at_fill": realized.risk_at_fill,
        "r_planned": realized.r_planned,
        "r_at_fill": realized.r_at_fill,
        # Whether any of the above is evidence.
        "participation": quality.participation,
        "paper": quality.paper,
        "credible": quality.credible,
        "reconstructed": reconstructed,
    }
    _append(path, record)
    return record


def latest_rows(path, *, network: str | None = None) -> dict[str, dict]:
    """The most recent whole row for each candidate key, in file order.

    Whole rows rather than outcome strings because the two work lists ask different things of
    them: the entry list needs only the outcome, and the exit list has to know whether a
    ``reconciled`` row means the entry *traded* — which lives in ``status`` and ``filled_qty``.

    Needed at all because a candidate can legitimately go round more than once — placed,
    reconciled as rejected, then placed again once the duplicate guard released it. Anything
    that answered "has this been reconciled" from the *set* of reconciled keys would mark the
    second placement settled on the strength of the first one's verdict.
    """
    rows: dict[str, dict] = {}
    for row in load(path):
        if network is not None and row.get("network") != network:
            continue
        key = row.get("candidate_key")
        if key and row.get("outcome"):
            rows[str(key)] = row
    return rows


def latest_outcomes(path, *, network: str | None = None) -> dict[str, str]:
    """The most recent outcome recorded for each candidate key, in file order."""
    return {key: str(row["outcome"])
            for key, row in latest_rows(path, network=network).items()}


def unsettled_keys(path, *, network: str | None = None) -> set[str]:
    """Candidates this file says reached the venue and nothing has since resolved.

    The reconcile pass's work list. A key drops out of it once a ``reconciled`` row lands, so
    running the pass twice costs one round trip per still-working order and writes nothing —
    and an order that is still resting is *supposed* to stay on the list.
    """
    return {key for key, outcome in latest_outcomes(path, network=network).items()
            if outcome == PLACED}


def awaiting_exit_keys(path, *, network: str | None = None) -> set[str]:
    """Candidates whose entry filled and whose exit is not yet recorded. The close pass's list.

    **Disjoint from ``unsettled_keys`` by construction**, because both read the *latest* row and
    key on its outcome: a candidate is on the entry list while that row says ``placed`` and on
    this one once it says ``reconciled`` and traded. A candidate on both would have each phase
    writing rows about the other's business.

    The two conditions beyond the outcome are load-bearing:

    * ``status == "filled"`` — an order the venue **killed** never traded, so there is no
      position to come off. Without this every rejected order joins this list forever and the
      close pass hunts nightly for fills that cannot exist.
    * ``filled_qty > 0`` — ``filled`` with nothing filled is a contradiction, and the defensive
      reading costs nothing.

    A key leaves the list when a ``closed`` row lands, and comes back if the candidate is
    entered again — which is why this reads the latest row rather than a set of seen keys.
    """
    return {
        key for key, row in latest_rows(path, network=network).items()
        if row.get("outcome") == RECONCILED
        and str(row.get("status") or "") == "filled"
        and (isinstance(row.get("filled_qty"), (int, float))
             and not isinstance(row.get("filled_qty"), bool)
             and float(row["filled_qty"]) > 0)
    }


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


def risk_by_key(path, *, network: str | None = None) -> dict[str, float]:
    """What each placed candidate risks, in USD, keyed by candidate. Latest row wins.

    The source for the pooled risk ceiling's opening balance. **This is the only record of what
    is at stake**, because no venue reports it: a position's size is visible and its stop is a
    separate resting order, so "how much would this cost if it stopped out" exists nowhere but
    here — written at placement, when the number was known exactly.

    Latest row wins because the log is append-only and a candidate can legitimately appear
    twice: a testnet rehearsal followed by the real order, or a re-entry after a bracket
    round-tripped flat. Scoped by network for the same reason ``placed_keys`` is.

    A row with no ``risk`` is **omitted rather than counted as zero**, so a caller can see the
    difference between "nothing at stake" and "the log predates this field". Every row written
    since ``store`` existed carries it; a hand-edited or migrated log might not.
    """
    out: dict[str, float] = {}
    for row in load(path):
        if row.get("outcome") != PLACED:
            continue
        if network is not None and row.get("network") != network:
            continue
        risk = row.get("risk")
        if isinstance(risk, bool) or not isinstance(risk, (int, float)):
            out.pop(row["candidate_key"], None)
            continue
        out[row["candidate_key"]] = float(risk)
    return out
