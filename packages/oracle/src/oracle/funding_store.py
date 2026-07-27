"""Append-only funding log under ``data/funding/``.

**This is not ore, and the distinction matters.** ``data/prices/`` is a cache — delete it and
a refetch rebuilds it exactly. A funding log is a record of *observations*, and the venues
expose only a limited history window, so a snapshot missed today may be unrecoverable later.
It sits closer to ``data/setups/decisions.jsonl`` than to ``data/prices/`` on that axis.

It is partly regenerable — Hyperliquid and Aster both serve historical funding, which is why
``fetch-funding --backfill`` exists and why the first run is useful immediately rather than
in three weeks. Lighter serves no reconcilable history (see ``sources/lighter``), so its
column is snapshot-only and its coverage begins the day logging starts.

Rows are append-only and partitioned by month. Nothing is ever rewritten: a duplicate
observation is cheaper than a mutation, and ``read`` dedupes on ``(venue, symbol,
observed_at)`` so re-running a backfill is idempotent from the reader's side without the
writer needing to seek.

Keys are short because a nightly sweep across three venues writes ~1,300 rows a night:

    v  venue        s  symbol       r  rate
    i  interval_hours              t  observed_at (ISO-8601, UTC)
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from core.funding import FundingRate

# src/oracle/funding_store.py -> src/oracle -> src -> oracle -> packages -> <repo root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "funding"


def partition_path(observed_at: datetime, root: Path = DATA_ROOT) -> Path:
    return Path(root) / f"{observed_at:%Y-%m}.jsonl"


def append(rates: list[FundingRate], *, root: Path = DATA_ROOT) -> dict[Path, int]:
    """Append observations, grouped into their month partitions. Returns rows per file."""
    if not rates:
        return {}
    grouped: dict[Path, list[FundingRate]] = defaultdict(list)
    for r in rates:
        grouped[partition_path(r.observed_at, root)].append(r)

    written: dict[Path, int] = {}
    for path, rows in grouped.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = "".join(
            json.dumps(
                {
                    "v": r.venue,
                    "s": r.symbol,
                    "r": r.rate,
                    "i": r.interval_hours,
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
) -> list[FundingRate]:
    """Every stored observation matching the filters, deduped and time-ordered.

    A malformed line is skipped rather than fatal. The log is append-only and read by
    analysis code; one truncated write (a machine sleeping mid-append) should not make the
    whole history unreadable.
    """
    root = Path(root)
    if not root.exists():
        return []

    seen: set[tuple[str, str, str]] = set()
    out: list[FundingRate] = []
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
                observed_at = datetime.fromisoformat(doc["t"])
                rate = FundingRate(
                    venue=doc["v"],
                    symbol=doc["s"],
                    rate=float(doc["r"]),
                    interval_hours=float(doc["i"]),
                    observed_at=observed_at,
                )
            except (ValueError, KeyError, TypeError):
                continue
            if venue is not None and rate.venue != venue:
                continue
            if symbol is not None and rate.symbol != symbol:
                continue
            if since is not None and rate.observed_at < since:
                continue
            key = (rate.venue, rate.symbol, doc["t"])
            if key in seen:
                continue
            seen.add(key)
            out.append(rate)
    out.sort(key=lambda r: (r.observed_at, r.venue, r.symbol))
    return out
