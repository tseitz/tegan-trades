"""`data/networth.jsonl`: one row per night, appended by `networth --record` on the droplet.

It cannot be rebuilt — holdings move between nights — so it is in `oracle.mirror.SYNCED` and
merged line by line, never copied over. Row logic lives in `core.networth`.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import networth

LEDGER = Path(__file__).resolve().parents[4] / "data" / "networth.jsonl"


def read(path: Path = LEDGER, *, warn) -> list[networth.Row]:
    """Every row, in file order. A missing file is an empty ledger; a bad line costs that line
    and a warning, never the rest of the history."""
    if not path.is_file():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(networth.parse(json.loads(line)))
        except (json.JSONDecodeError, networth.RowError) as exc:
            warn(f"warning: unreadable net worth row at {path.name}:{number}: {exc}")
    return rows


def append(row: networth.Row, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row.to_json()) + "\n")
