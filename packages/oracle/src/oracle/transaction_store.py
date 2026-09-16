"""Cached Plaid investment transactions under ``data/transactions/`` — one JSON document per
account, mirroring ``oracle.cache``'s shape.

**JSON document, not an append-only log, and this is the load-bearing call.**
``interest_store``'s docstring states the rule it follows: those venues serve snapshots with
no reconcilable history, so every observation must be kept forever. Plaid is the opposite — it
re-serves any past window on demand, and it *corrects and removes* transactions after the
fact. An append-only log would accumulate superseded rows the reader could not tell from real
ones, so this keeps one current document instead.

**The write is a window replace, not a union merge.** ``replace_window`` drops every cached row
dated inside ``[start, end]`` and writes what came back in its place. A union merge with
incoming-wins cannot express a *removal* — a transaction Plaid has deleted would sit in the
document forever, indistinguishable from a live one. Rows outside the window are untouched.

There is no ``PriceSeries``-style container deduping on construction the way ``cache.merge``
leans on (``cache.py:80-86``), so ``replace_window`` dedupes on ``investment_transaction_id``
itself.

The document also records ``reaches_back_to`` — the earliest ``start`` of a window that
completed. That is what lets a caller ask honestly how far the feed actually reaches, rather
than inferring it from whatever happens to be cached.

Deleting the tree is always safe; a resync rebuilds it.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core.transactions import InvestmentTransaction

# src/oracle/transaction_store.py -> src/oracle -> src -> oracle -> packages -> <repo root>
DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "transactions"


def store_path(portfolio: str, root: Path = DATA_ROOT) -> Path:
    return Path(root) / f"{portfolio}.json"


def _to_doc(rows: tuple[InvestmentTransaction, ...], *, reaches_back_to: date | None) -> dict:
    return {
        "reaches_back_to": reaches_back_to.isoformat() if reaches_back_to else None,
        # Short keys: a synced retirement book can carry thousands of rows over two years.
        "rows": [
            {
                "id": r.id, "acct": r.account_id, "sec": r.security_id, "t": r.ticker,
                "d": r.date.isoformat(), "q": r.quantity, "p": r.price, "a": r.amount,
                "f": r.fees, "ty": r.type, "st": r.subtype,
            }
            for r in rows
        ],
    }


def _from_row(doc: dict) -> InvestmentTransaction:
    return InvestmentTransaction(
        id=doc["id"], account_id=doc["acct"], security_id=doc.get("sec"), ticker=doc.get("t"),
        date=date.fromisoformat(doc["d"]), quantity=doc.get("q"), price=doc.get("p"),
        amount=doc["a"], fees=doc.get("f"), type=doc["ty"], subtype=doc["st"],
    )


def save(portfolio: str, rows: tuple[InvestmentTransaction, ...], *,
         reaches_back_to: date | None, root: Path = DATA_ROOT) -> Path:
    path = store_path(portfolio, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_doc(rows, reaches_back_to=reaches_back_to)), encoding="utf-8")
    return path


def load(portfolio: str, *, root: Path = DATA_ROOT
         ) -> tuple[tuple[InvestmentTransaction, ...], date | None] | None:
    """``(rows, reaches_back_to)``, or ``None`` when nothing has been cached yet — distinct
    from a cache file with zero rows, which returns ``((), None-or-a-date)``."""
    path = store_path(portfolio, root)
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    reaches = doc.get("reaches_back_to")
    rows = tuple(_from_row(r) for r in doc.get("rows", []))
    return rows, (date.fromisoformat(reaches) if reaches else None)


def replace_window(portfolio: str, rows: tuple[InvestmentTransaction, ...], *,
                   start: date, end: date, root: Path = DATA_ROOT) -> Path:
    """Drop every cached row dated inside ``[start, end]`` and write ``rows`` in its place.
    Rows outside the window are untouched. ``reaches_back_to`` becomes ``start`` when that is
    further back than whatever was already recorded — a short recent-window refresh must never
    move the floor forward and make an already-reached backfill look shallower than it is."""
    existing = load(portfolio, root=root)
    kept = tuple(t for t in (existing[0] if existing else ())
                if not (start <= t.date <= end))
    prior_floor = existing[1] if existing else None
    floor = start if prior_floor is None else min(start, prior_floor)

    # Incoming rows dedupe on id, keeping the last one seen — a page fetched twice at a window
    # seam must not double-count.
    by_id: dict[str, InvestmentTransaction] = {t.id: t for t in kept}
    for t in rows:
        by_id[t.id] = t
    combined = tuple(sorted(by_id.values(), key=lambda t: (t.date, t.id)))
    return save(portfolio, combined, reaches_back_to=floor, root=root)
