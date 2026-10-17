"""Per-portfolio snapshot of depository balances, written by ``plaid-sync``.

Ore, not configuration: ``data/balances/<portfolio>.json`` is regenerated nightly and read by
``treasury`` to price rows that name a Plaid account instead of typing an amount.

**Merges are per account.** An account missing from tonight's payload keeps its old
``fetched_at`` instead of vanishing, so a Plaid blip or a rename ages the row rather than
deleting it.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "balances"


class BalanceStoreError(Exception):
    """A snapshot file that exists but cannot be read as one."""


def path_for(portfolio: str, *, root: Path) -> Path:
    if not portfolio or Path(portfolio).name != portfolio or portfolio in (".", ".."):
        raise BalanceStoreError(f"{portfolio!r} is not a portfolio name")
    return root / f"{portfolio}.json"


def read(portfolio: str, *, root: Path) -> dict | None:
    """The stored document, or None when no snapshot exists yet."""
    path = path_for(portfolio, root=root)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BalanceStoreError(f"{path}: {exc}") from exc
    accounts = doc.get("accounts") if isinstance(doc, dict) else None
    if not isinstance(accounts, dict):
        raise BalanceStoreError(f"{path}: no `accounts` mapping")
    for name, entry in accounts.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("fetched_at"), str):
            raise BalanceStoreError(f"{path}: account {name!r} has no fetched_at")
        try:
            datetime.fromisoformat(entry["fetched_at"])
        except ValueError as exc:
            raise BalanceStoreError(f"{path}: account {name!r} fetched_at: {exc}") from exc
    return doc


def merge_write(portfolio: str, balances: Mapping[str, Mapping], *, fetched_at: datetime,
                root: Path) -> tuple[str, ...]:
    """Overwrite every account in ``balances``; carry the rest forward untouched.

    Returns the carried-forward names. A malformed existing file raises rather than being
    overwritten, so the failure is seen instead of silently resetting every age.
    """
    existing = read(portfolio, root=root)
    accounts = dict(existing["accounts"]) if existing else {}
    carried = tuple(sorted(set(accounts) - set(balances)))
    for name, entry in balances.items():
        accounts[name] = {**entry, "fetched_at": fetched_at.isoformat()}

    path = path_for(portfolio, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps({"accounts": accounts}, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise BalanceStoreError(f"{path}: could not write — {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
    return carried
