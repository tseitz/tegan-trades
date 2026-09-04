"""Append-only alt-signal log under ``data/altsignal/``.

Mirrors ``funding_store.py`` exactly, for the same reason: this is a record of observations
from third-party APIs that serve limited history, not a cache that a refetch rebuilds. A
snapshot missed today may be unrecoverable later.

Rows are append-only and partitioned by month. Nothing is ever rewritten: a duplicate
observation is cheaper than a mutation, and ``read`` dedupes on ``(source, kind, key,
observed_at)`` so re-running a snapshot is idempotent from the reader's side.

Keys are short because a nightly sweep across four sources writes many rows a night:

    src  source        kind  kind          k  key
    v    value                            t  observed_at (ISO-8601, UTC)
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from core.altsignal import AltSignalReading

# src/oracle/altsignal_store.py -> src/oracle -> src -> oracle -> packages -> <repo root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "altsignal"


def partition_path(observed_at: datetime, root: Path = DATA_ROOT) -> Path:
    return Path(root) / f"{observed_at:%Y-%m}.jsonl"


def append(readings: list[AltSignalReading], *, root: Path = DATA_ROOT) -> dict[Path, int]:
    """Append observations, grouped into their month partitions. Returns rows per file."""
    if not readings:
        return {}
    grouped: dict[Path, list[AltSignalReading]] = defaultdict(list)
    for r in readings:
        grouped[partition_path(r.observed_at, root)].append(r)

    written: dict[Path, int] = {}
    for path, rows in grouped.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = "".join(
            json.dumps(
                {
                    "src": r.source,
                    "kind": r.kind,
                    "k": r.key,
                    "v": r.value,
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
    source: str | None = None,
    kind: str | None = None,
    key: str | None = None,
    since: datetime | None = None,
) -> list[AltSignalReading]:
    """Every stored observation matching the filters, deduped and time-ordered.

    A malformed line is skipped rather than fatal — the log is append-only and read by
    analysis code, so one truncated write should not make the whole history unreadable.
    """
    root = Path(root)
    if not root.exists():
        return []

    seen: set[tuple[str, str, str, str]] = set()
    out: list[AltSignalReading] = []
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
                observed_at = datetime.fromisoformat(doc["t"])
                reading = AltSignalReading(
                    source=doc["src"],
                    kind=doc["kind"],
                    key=doc["k"],
                    value=doc["v"],
                    observed_at=observed_at,
                )
            except (ValueError, KeyError, TypeError):
                continue
            if source is not None and reading.source != source:
                continue
            if kind is not None and reading.kind != kind:
                continue
            if key is not None and reading.key != key:
                continue
            if since is not None and reading.observed_at < since:
                continue
            dedupe_key = (reading.source, reading.kind, reading.key, doc["t"])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(reading)
    out.sort(key=lambda r: (r.observed_at, r.source, r.key))
    return out
