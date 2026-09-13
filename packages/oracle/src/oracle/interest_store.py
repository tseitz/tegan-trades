"""Append-only open-interest log under ``data/interest/``.

**A separate log from ``data/funding/``, and this is not a style preference.**
``oracle.liveness`` decides whether a mapped market actually trades by counting rows per
(asset, venue) in the funding log and comparing a market against its cohort — a market with
observations is alive, one silent beside reporting siblings is dormant. An open-interest row
in that log would be an observation the funding poll never made, so a dead market carrying
one would read as alive and clear a gate that exists to stop a stop-loss resting on a book
that cannot fill.

Like funding, this is a record of *observations*, not ore: all three venues (Hyperliquid,
Lighter, Aster) serve open interest as a snapshot only, with no reconcilable history, so a
night missed today is unrecoverable later.

Rows are append-only and partitioned by month. Nothing is ever rewritten: a duplicate
observation is cheaper than a mutation, and ``read`` dedupes on ``(venue, symbol,
observed_at)`` so re-running a sweep is idempotent from the reader's side without the writer
needing to seek.

Keys are short, matching ``funding_store``'s convention:

    v  venue        s  symbol       n  notional (USD)
    u  volume_24h   t  observed_at (ISO-8601, UTC)
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from core.interest import OpenInterest

# src/oracle/interest_store.py -> src/oracle -> src -> oracle -> packages -> <repo root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "interest"


def partition_path(observed_at: datetime, root: Path = DATA_ROOT) -> Path:
    return Path(root) / f"{observed_at:%Y-%m}.jsonl"


def append(readings: list[OpenInterest], *, root: Path = DATA_ROOT) -> dict[Path, int]:
    """Append observations, grouped into their month partitions. Returns rows per file."""
    if not readings:
        return {}
    grouped: dict[Path, list[OpenInterest]] = defaultdict(list)
    for r in readings:
        grouped[partition_path(r.observed_at, root)].append(r)

    written: dict[Path, int] = {}
    for path, rows in grouped.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = "".join(
            json.dumps(
                {
                    "v": r.venue,
                    "s": r.symbol,
                    "n": r.notional,
                    "u": r.volume_24h,
                    "t": r.observed_at.isoformat(timespec="seconds"),
                },
                separators=(",", ":"),
            )
            + "\n"
            for r in rows
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(lines)
        written[path] = len(rows)
    return written


def read(
    *,
    root: Path = DATA_ROOT,
    venue: str | None = None,
    symbol: str | None = None,
    since: datetime | None = None,
) -> list[OpenInterest]:
    """Every stored observation matching the filters, deduped and time-ordered.

    A malformed line is skipped rather than fatal. The log is append-only and read by
    analysis code; one truncated write (a machine sleeping mid-append) should not make the
    whole history unreadable.
    """
    root = Path(root)
    if not root.exists():
        return []

    seen: set[tuple[str, str, str]] = set()
    out: list[OpenInterest] = []
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
                observed_at = datetime.fromisoformat(doc["t"])
                reading = OpenInterest(
                    venue=doc["v"],
                    symbol=doc["s"],
                    notional=float(doc["n"]),
                    volume_24h=float(doc["u"]),
                    observed_at=observed_at,
                )
            except (ValueError, KeyError, TypeError):
                continue
            if venue is not None and reading.venue != venue:
                continue
            if symbol is not None and reading.symbol != symbol:
                continue
            if since is not None and reading.observed_at < since:
                continue
            key = (reading.venue, reading.symbol, doc["t"])
            if key in seen:
                continue
            seen.add(key)
            out.append(reading)
    out.sort(key=lambda r: (r.observed_at, r.venue, r.symbol))
    return out
