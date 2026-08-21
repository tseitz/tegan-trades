"""Parsing and formatting primitives shared across the digest. Pure.

Both of these existed twice before this module did — ``_instant`` in ``book`` and again
(as a string compare, wrongly) in ``diff``; ``_num`` byte-identical in ``book`` and ``render``.
Each copy carried its own claim about the world, which is how one of them came to be stale
while the other was not. One home, one claim.
"""

from __future__ import annotations

from datetime import UTC, datetime

# Sort position for a stamp that will not parse. Timezone-aware on purpose: this is compared
# directly against real stamps, which are all aware, and mixing naive with aware raises
# ``TypeError``.
UNDATED = datetime.min.replace(tzinfo=UTC)


def instant(stamp) -> datetime | None:
    """Parse either clock this repo writes, as an aware datetime. ``None`` if it will not parse.

    ``execution.store._now`` writes ``2026-08-20T06:20:11+00:00``; ``oracle.setups_cli`` writes
    ``2026-08-20T06:20:11Z``. Both name the same instant and both are valid ISO-8601, but they
    do **not** sort against each other as strings — ``Z`` (0x5A) lands after ``+`` (0x2B), so a
    plain comparison mis-orders anything inside the same second. Parsing is the only correct
    comparison; ``fromisoformat`` has understood ``Z`` since 3.11.

    A stamp carrying no offset is assumed UTC rather than returned naive. Every stamp this repo
    writes is aware, so a naive one means a hand-edited row or an external tool — and returning
    it naive would raise ``TypeError`` at the first comparison instead of at the parse, which is
    a long way from the cause.
    """
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def num(value) -> str:
    """A price, at a precision that suits its size. ``?`` for anything unrenderable.

    Prices in ``data/prices/`` span roughly 1e-7 to 1.6e5 — run
    ``scripts/probe_price_cache.py`` for the current range — so one fixed precision is wrong at
    one end or the other. Rendered defensively because this runs inside a nightly step, where a
    formatting crash would cost the run's tail for a cosmetic line.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "?"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.2f}"
    return f"{value:.6g}"
