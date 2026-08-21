"""What the account actually did overnight, read out of the order log. Pure.

**Reads the log; never re-runs a settle.** ``nightly.sh`` already runs ``book --reconcile``
against both venues before this does anything, so the rows are on disk by the time the digest
looks. Re-running it here would give a reporting command side effects on the one file in the
repo that holds real money's history.

Like the rest of the digest this is a diff: only rows written since the previous nightly run
appear, and a reconciliation that changed nothing is not an event. The nightly reconciles every
night, so "a resting order is still resting" would otherwise be most of this section and would
bury the rows that matter.
"""

from __future__ import annotations

from execution.store import CLOSED, FAILED, PLACED, RECONCILED, REFUSED

from digest.fmt import UNDATED, instant, num

__all__ = [
    "CLOSED",
    "FAILED",
    "PLACED",
    "RECONCILED",
    "REFUSED",
    "asset_names",
    "lines",
    "since",
]


def asset_names(all_rows) -> dict:
    """``candidate_key -> asset``, built from every row that names one.

    Needed because **``store.record_reconciliation`` writes no ``asset``** — it records the
    candidate key and what the venue said, nothing more. Fills and kills are the two most
    important lines this section can carry, and on real data both rendered as ``filled ?``
    until this join existed.

    Built from the *whole* log, deliberately, not from the night's events: the placement that
    names the asset is usually weeks older than the reconciliation that settles it, so it has
    already been filtered out by ``since`` by the time anything needs the name.
    """
    names = {}
    for row in all_rows:
        key, asset = row.get("candidate_key"), row.get("asset")
        if key and asset:
            names.setdefault(key, asset)
    return names


def since(rows, *, after: str | None):
    """Log rows written after ``after``. ``None`` means no bound — the first digest.

    A row whose stamp will not parse is **kept**, not dropped. Dropping it would quietly hide a
    real fill; keeping it puts the event in front of a human who can judge the timestamp.

    When no bound can be established this returns everything, which the caller must label as
    such: an unbounded window rendered under a plain truncation note reads as "the night was
    busy" when it actually means "the window is unknown". See ``window_unknown``.
    """
    if after is None or instant(after) is None:
        return list(rows)
    cutoff = instant(after)
    kept = []
    for row in rows:
        stamp = instant(row.get("at"))
        if stamp is None or stamp > cutoff:
            kept.append(row)
    return kept


def lines(rows, *, names: dict | None = None, limit: int | None = None,
          window_unknown: bool = False) -> tuple[list[str], str | None]:
    """``(events, notice)`` — one line per event worth reading, oldest first.

    The notice is returned SEPARATELY rather than prepended into the list. It lived in the list
    once, and ``render._book_subject`` counted it, so a capped night reported "13 book" for
    twelve events — wrong rather than absent, on the one line the subject exists to make
    dependable.

    ``limit`` keeps the first digest readable: with no previous run to bound the window,
    ``since`` returns the entire order history. **The cap announces itself** — a section that
    silently truncates reads as "this is everything", which is the one thing it is not.

    ``window_unknown`` says the bound could not be established at all. That is a different fact
    from "there was a lot tonight", and rendering it as a plain truncation would present old
    history as the night's activity.
    """
    ordered = sorted(rows, key=lambda r: (instant(r.get("at")) or UNDATED))
    out = []
    for row in ordered:
        rendered = _line(row, names or {})
        if rendered:
            out.append(rendered)

    notice = None
    if limit is not None and len(out) > limit:
        dropped = len(out) - limit
        # Newest kept, not oldest: on a long window the recent events are the ones still
        # actionable, and the notice carries what was cut.
        notice = f"({dropped} older event(s) not shown — see `uv run book --closed`)"
        out = out[-limit:]
    if window_unknown:
        scope = notice or "(showing all recorded events)"
        notice = f"WINDOW UNKNOWN — no previous run stamp, so this is not one night. {scope}"
    return out, notice


def _line(row: dict, names: dict) -> str | None:
    outcome = row.get("outcome")
    # The row's own asset wins; the join is a fallback for rows that carry no name at all.
    asset = row.get("asset") or names.get(row.get("candidate_key")) or "?"

    if outcome == CLOSED:
        return _closed(row, asset)

    if outcome == RECONCILED:
        # ``placed`` only ever meant "the venue accepted the submission". The buying-power and
        # account-type checks run at the open, hours later — three of eight orders died that way
        # on 2026-07-29 while the log still said ``placed``. So a reconciliation is news only
        # when it changed something.
        if row.get("failed"):
            return f"killed   {asset:<8} venue says {row.get('status') or 'rejected'}"
        if _positive(row.get("filled_qty")):
            price = row.get("filled_avg_price")
            at = f" @ {price:,.4g}" if isinstance(price, (int, float)) else ""
            return f"filled   {asset:<8} {row.get('filled_qty')}{at}"
        return None

    if outcome == PLACED:
        return (f"placed   {asset:<8} {row.get('direction') or '?'} "
                f"entry {num(row.get('entry'))} · stop {num(row.get('stop'))} · "
                f"target {num(row.get('target'))}")

    if outcome == FAILED:
        return f"failed   {asset:<8} {row.get('error') or 'no reason recorded'}"

    if outcome == REFUSED:
        return f"refused  {asset:<8} {row.get('reason') or '?'}"

    # An outcome kind this module does not know about. Surfaced rather than dropped: these are
    # order outcomes on a real account, and adding a new kind to ``execution.store`` would
    # otherwise delete it from the digest permanently with nothing anywhere saying so.
    return f"{outcome or '?'!s:<8} {asset:<8} unrecognised outcome — see `uv run book`"


def _closed(row: dict, asset: str) -> str:
    """How a trade ended.

    **Net where the venue's costs were readable, gross only as a fallback** — the same choice
    ``execution.journal.line`` makes. This is a skimmed surface, and showing gross is exactly
    where that error does the most damage: one SOL short moved -5.67 and cost -9.28 to hold, so
    a gross figure understated the loss by 0.36R right where a reader stops looking.
    """
    net, gross = row.get("pnl_net"), row.get("pnl")
    kept = net if isinstance(net, (int, float)) else gross
    money = f"{float(kept):+,.2f}" if isinstance(kept, (int, float)) else "?"

    r_value = row.get("r_net_at_fill")
    if not isinstance(r_value, (int, float)):
        r_value = row.get("r_at_fill")
    r = f" ({r_value:+.2f}R)" if isinstance(r_value, (int, float)) else ""

    line = f"closed   {asset:<8} {row.get('exit_reason') or '?'} · ${money}{r}"
    # A fill that never had to find a buyer reads exactly like performance. The flag travels
    # with the number wherever the number goes — see ``execution.journal``.
    if "credible" in row and not row.get("credible"):
        why = "; ".join(str(r) for r in (row.get("not_evidence") or [])) or "not verifiable"
        line += f"  NOT EVIDENCE — {why}"
    return line


def _positive(value) -> bool:
    return isinstance(value, (int, float)) and value > 0
