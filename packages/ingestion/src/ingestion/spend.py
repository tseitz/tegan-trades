"""The running total of real money spent, by month.

**Written by the command that spends it.** This lived in `nightly.sh` and was written
*about* `ingest-x` after the fact, which meant it only ever saw calls the nightly cycle made
— every manual `uv run ingest-x` spent real money the ledger never recorded, and the monthly
cap gated on the difference. Measured 2026-08-06 by summing the `cost_in_usd_ticks` each raw
xAI response carries: **August tracked $3.07 against $7.18 actually spent, July $5.55 against
$7.44.** Same principle as the `[ingest-x] cost:` line — the caller that knows the number
reports it, rather than something downstream reconstructing it.

Still a floor, and unavoidably so: a call that times out bills at xAI and returns no response
to read a cost from, so nothing can record it. `x_search.search`'s docstring covers why that
trade is accepted.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

# Repo root: src/ingestion/spend.py -> src/ingestion -> src -> ingestion -> packages -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
SPEND_PATH = _REPO_ROOT / "data" / "spend.json"

# Where it lived while `nightly.sh` owned it. Under `logs/` it was safe from rotation (which
# only removes `*.log`) but it was never really a log, and it is now written by a command that
# has nothing to do with the nightly cycle.
LEGACY_PATH = _REPO_ROOT / "data" / "logs" / "nightly" / "spend.json"


def _load(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def total(month: str | None = None, *, path: Path | None = None) -> float:
    """What has been spent in `month` so far. An unseen month is 0.0, not an error."""
    path = path or SPEND_PATH
    return float(_load(path).get(month or _month(), 0.0))


def reconcile(*, raw_dir: Path | None = None, path: Path | None = None) -> dict[str, float]:
    """Rebuild monthly totals from the cost each raw xAI response reported. Returns what was
    found, and folds it into the ledger.

    The ledger is only ever a floor — a call that times out bills but returns nothing to read
    a cost from, and before `ingest-x` owned the ledger no manual run reached it at all. The
    raw responses are the closest thing to ground truth available locally: each one carries
    its own `cost_in_usd_ticks`, and they are ore that nothing prunes.

    **Takes the maximum against what is already recorded, never the replacement.** The ledger
    may legitimately hold spend whose raw file is gone; the raw files may hold spend the
    ledger missed. Only one of those directions is safe to trust, so this can only ever raise
    a month's total.

    Attribution is by file mtime, which is when the call was made. That was wrong once before
    when it was used to attribute cost to a *run* (it re-counted a day's manual runs into the
    nightly total). Attributing to a *month* is the coarser question and does not have that
    failure mode.
    """
    from ingestion.x_search import USD_PER_TICK

    raw_dir = raw_dir if raw_dir is not None else _REPO_ROOT / "data" / "raw" / "x"
    path = path or SPEND_PATH
    if not raw_dir.is_dir():
        return {}

    found: dict[str, float] = {}
    for f in sorted(raw_dir.glob("*.json")):
        try:
            ticks = (json.loads(f.read_text(encoding="utf-8")).get("usage") or {}).get(
                "cost_in_usd_ticks")
        except (OSError, json.JSONDecodeError):
            continue
        if not ticks:
            continue
        month = datetime.fromtimestamp(f.stat().st_mtime, UTC).strftime("%Y-%m")
        found[month] = round(found.get(month, 0.0) + float(ticks) * USD_PER_TICK, 4)

    data = _load(path)
    for month, amount in found.items():
        data[month] = max(data.get(month, 0.0), amount)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return found


def record(
    amount: float,
    *,
    month: str | None = None,
    path: Path | None = None,
    legacy: Path | None = None,
) -> float:
    """Add `amount` to `month`'s running total and return the new total.

    **Never raises.** This is bookkeeping attached to a command that captures unrecoverable
    data — X posts get deleted and accounts go private — so failing an ingest to protect an
    accounting file is the wrong way round. A corrupt ledger is replaced rather than merged
    into, which bounds the damage to what was already unreadable.
    """
    # The migration belongs to the real ledger only. A caller naming its own path is not
    # continuing this repo's history, and seeding it from the legacy file would silently
    # inject $3.07 of unrelated spend into whatever it was actually asking for.
    if legacy is None:
        legacy = LEGACY_PATH if path in (None, SPEND_PATH) else None
    path = path or SPEND_PATH
    month = month or _month()

    data = _load(path)
    # Carry prior months across the one time the file moves. Reading the legacy file on every
    # call would re-add its history on each write, so this is gated on the new file's absence
    # rather than on the old file's presence.
    if not data and legacy is not None and legacy.exists():
        data = _load(legacy)

    data[month] = round(data.get(month, 0.0) + float(amount), 4)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return data[month]
