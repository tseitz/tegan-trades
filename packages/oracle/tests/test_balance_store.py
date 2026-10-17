"""``balance_store``: per-account merge with carry-forward, atomic write, required root."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from oracle import balance_store

T1 = datetime(2026, 10, 16, 11, tzinfo=UTC)
T2 = datetime(2026, 10, 17, 11, tzinfo=UTC)


def _entry(current):
    return {"subtype": "savings", "available": current, "current": current}


def test_a_first_write_stamps_every_account(tmp_path):
    carried = balance_store.merge_write("sofi", {"A": _entry(1.0)}, fetched_at=T1, root=tmp_path)
    assert carried == ()
    assert balance_store.read("sofi", root=tmp_path)["accounts"]["A"]["fetched_at"] == T1.isoformat()


def test_an_account_missing_tonight_is_carried_forward_with_its_old_fetched_at(tmp_path):
    balance_store.merge_write("sofi", {"A": _entry(1.0), "B": _entry(2.0)},
                              fetched_at=T1, root=tmp_path)
    carried = balance_store.merge_write("sofi", {"A": _entry(5.0)}, fetched_at=T2, root=tmp_path)
    accounts = balance_store.read("sofi", root=tmp_path)["accounts"]
    assert carried == ("B",)
    assert accounts["A"]["current"] == 5.0 and accounts["A"]["fetched_at"] == T2.isoformat()
    assert accounts["B"]["current"] == 2.0 and accounts["B"]["fetched_at"] == T1.isoformat()


def test_read_is_none_when_there_is_no_snapshot(tmp_path):
    assert balance_store.read("sofi", root=tmp_path) is None


@pytest.mark.parametrize("text", [
    "{nope", "[]", '{"accounts": []}', '{"accounts": {"A": {}}}',
    '{"accounts": {"A": {"fetched_at": "yesterday"}}}'])
def test_a_malformed_file_raises(tmp_path, text):
    (tmp_path / "sofi.json").write_text(text, encoding="utf-8")
    with pytest.raises(balance_store.BalanceStoreError):
        balance_store.read("sofi", root=tmp_path)


def test_a_malformed_file_is_not_overwritten(tmp_path):
    (tmp_path / "sofi.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(balance_store.BalanceStoreError):
        balance_store.merge_write("sofi", {"A": _entry(1.0)}, fetched_at=T1, root=tmp_path)
    assert (tmp_path / "sofi.json").read_text(encoding="utf-8") == "{nope"


def test_no_temp_file_survives_a_failed_write(tmp_path, monkeypatch):
    """`backup.sh` uploads all of `data/`, and `*.tmp` is not excluded."""
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(balance_store.os, "replace", fail)
    with pytest.raises(balance_store.BalanceStoreError):
        balance_store.merge_write("sofi", {"A": {"current": 1.0}}, fetched_at=T1, root=tmp_path)
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == []


def test_root_is_required():
    with pytest.raises(TypeError):
        balance_store.merge_write("sofi", {}, fetched_at=T1)  # type: ignore[call-arg]


@pytest.mark.parametrize("name", ["../x", "a/b", "", "..", "."])
def test_a_portfolio_name_cannot_leave_the_root(tmp_path, name):
    with pytest.raises(balance_store.BalanceStoreError):
        balance_store.path_for(name, root=tmp_path)


def test_a_disk_error_arrives_as_a_store_error(tmp_path, monkeypatch):
    """So `plaid-sync` counts it and moves on to the next portfolio instead of aborting."""
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(balance_store.os, "replace", fail)
    with pytest.raises(balance_store.BalanceStoreError):
        balance_store.merge_write("sofi", {"A": {"current": 1.0}}, fetched_at=T1, root=tmp_path)
