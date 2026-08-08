"""The closed-trade note in the vault — one line per finished trade, somewhere it gets read.

Same subordinate-mirror contract as ``oracle.decisions``: the order log is the record and this
is a convenience, so a vault that cannot be written warns and is skipped. It must never be able
to destroy or block the row that was just written.
"""
from __future__ import annotations

from execution import journal

ROW = {
    "outcome": "closed", "network": "paper", "candidate_key": "19ba232b91ce", "asset": "INTL",
    "exit_reason": "manual", "exit_price": 31.21, "exit_qty": 1639.0, "entry_price": 29.621233,
    "held_days": 9.0, "pnl": 2603.99, "risk_planned": 999.79, "risk_at_fill": 706.79,
    "r_planned": 2.6046, "r_at_fill": 3.6842, "participation": 0.0851, "paper": True,
    "credible": False, "reconstructed": True,
}


def test_the_line_carries_the_trade_and_its_r():
    line = journal.line(ROW)
    assert "INTL" in line
    assert "manual" in line
    assert "+2,603.99" in line
    assert "+3.68R" in line
    assert "9.0d" in line


def test_an_uncredible_fill_says_so_in_the_line():
    """The whole reason this note is worth writing: a +2.6R that never had to find a buyer
    should not read like performance in the one place it gets skimmed."""
    assert "not evidence" in journal.line(ROW).lower()


def test_a_credible_fill_carries_no_warning():
    line = journal.line({**ROW, "credible": True, "paper": False})
    assert "not evidence" not in line.lower()


def test_a_missing_r_does_not_break_the_line():
    """A stop sitting on the entry leaves R undefined. The line still has to render — this runs
    inside a nightly step and a formatting crash there costs the whole run's tail."""
    line = journal.line({**ROW, "r_at_fill": None})
    assert "INTL" in line
    assert "?R" in line


def test_the_note_is_created_with_a_title(tmp_path):
    note = tmp_path / "Closed Trades.md"
    assert journal.append(note, ROW) is True
    text = note.read_text()
    assert text.startswith("# Closed Trades")
    assert "INTL" in text


def test_a_second_close_appends_rather_than_replacing(tmp_path):
    note = tmp_path / "Closed Trades.md"
    journal.append(note, ROW)
    journal.append(note, {**ROW, "asset": "SBSW", "candidate_key": "other"})
    text = note.read_text()
    assert "INTL" in text
    assert "SBSW" in text
    assert text.count("# Closed Trades") == 1


def test_an_unreachable_vault_warns_and_does_not_raise(tmp_path):
    """The mirror is subordinate to the log, never a gate on it. An unmounted vault must not
    take out the nightly step that just recorded a real outcome."""
    warnings = []
    note = tmp_path / "no-such-dir" / "Closed Trades.md"
    assert journal.append(note, ROW, warn=warnings.append) is False
    assert warnings
    assert "vault" in warnings[0].lower()


def test_an_unreachable_vault_is_not_created(tmp_path):
    """``mkdir(parents=True)`` here would scatter a fake vault tree onto any machine where the
    real one is not mounted, and the trades would look filed while living somewhere nobody
    reads. Same reasoning as ``setups_cli.resolve_vault_note``."""
    note = tmp_path / "no-such-dir" / "Closed Trades.md"
    journal.append(note, ROW, warn=lambda _: None)
    assert not note.parent.exists()
