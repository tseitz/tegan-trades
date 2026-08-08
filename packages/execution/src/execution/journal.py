"""One line per finished trade, in the vault, where it will actually be read.

The order log under ``data/`` is the record; this is the surface. The distinction matters
because ``data/`` is gitignored ore that nobody opens by hand, and a realised outcome is the
one thing in this repo worth glancing at without running a command.

**Subordinate to the log, never a gate on it** — the same contract ``oracle.decisions`` states
for its own mirror. ``store.record_close`` has already returned by the time anything here runs,
so an unmounted vault warns and is skipped. Losing a note line is recoverable from the log;
taking out a nightly step that just recorded a real outcome is not.

WHY IT REPEATS THE ``NOT EVIDENCE`` FLAG. This note is the most-skimmed and least-scrutinised
view of the data, which makes it the place an uncredible fill does the most damage: a +2.6R that
never had to find a buyer reads exactly like performance. The flag travels with the number
wherever the number goes. See ``outcome.fill_quality``.
"""
from __future__ import annotations

from pathlib import Path

NOTE_TITLE = "# Closed Trades"

# Beside ``Setups.md``, which ``oracle.setups_cli`` writes: approvals and outcomes are the two
# halves of the same question and belong in one place.
DEFAULT_NOTE = Path.home() / "vault" / "Trading" / "Trade Logs" / "Closed Trades.md"


def _money(value) -> str:
    return f"{float(value or 0.0):+,.2f}"


def line(row: dict) -> str:
    """One markdown bullet for a closed row. Pure.

    Every numeric field is rendered defensively — this runs inside a nightly step, and a
    formatting crash there would cost the whole run's tail for a cosmetic line.
    """
    r_at_fill = row.get("r_at_fill")
    r = f"{r_at_fill:+.2f}R" if isinstance(r_at_fill, (int, float)) else "?R"
    held = row.get("held_days")
    age = f"{held:.1f}d" if isinstance(held, (int, float)) else "?d"
    entry, exit_price = row.get("entry_price"), row.get("exit_price")

    parts = [
        f"- **{row.get('asset') or '?'}** {row.get('exit_reason') or '?'}",
        f"{float(entry or 0):,.2f} → {float(exit_price or 0):,.2f}",
        f"{float(row.get('exit_qty') or 0):g}",
        f"held {age}",
        f"**${_money(row.get('pnl'))}** ({r})",
    ]
    if not row.get("credible"):
        # Read from the row rather than re-derived. This used to reconstruct the cause from the
        # numbers and could name only one of them, so a fill that failed on size AND on a
        # collapsed stop reported whichever branch happened to be checked first.
        reasons = row.get("not_evidence") or []
        why = "; ".join(str(r) for r in reasons) if reasons else "not verifiable"
        parts.append(f"_NOT EVIDENCE — {why}_")
    parts.append(f"`{row.get('candidate_key') or '?'}`")
    return " · ".join(parts)


def append(note_path, row: dict, warn=None) -> bool:
    """Add one line to the note. ``True`` if it was written.

    **Refuses to create the parent directory**, and returns ``False`` instead. ``mkdir`` here
    would scatter an empty, fake vault tree onto any machine where the real vault is not
    mounted, and the trades would look filed while living somewhere nobody reads — the same
    trap ``setups_cli.resolve_vault_note`` exists to avoid.
    """
    path = Path(note_path)
    if not path.parent.is_dir():
        if warn is not None:
            warn(f"warning: vault note {path} is not reachable "
                 f"({path.parent} does not exist); the close is in the order log only")
        return False
    try:
        body = line(row)
        if path.exists():
            path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n" + body + "\n",
                            encoding="utf-8")
        else:
            path.write_text(f"{NOTE_TITLE}\n\n{body}\n", encoding="utf-8")
    except OSError as exc:
        if warn is not None:
            warn(f"warning: could not write vault note {path}: {exc}")
        return False
    return True
