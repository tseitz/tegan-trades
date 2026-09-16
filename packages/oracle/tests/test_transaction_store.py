"""``transaction_store``'s own contract: a window replace that can express a deletion, which a
union merge (``oracle.cache.merge``'s shape) cannot.
"""
from __future__ import annotations

from datetime import date

from core.transactions import InvestmentTransaction
from oracle import transaction_store


def _txn(id_, d, *, amount=100.0, kind=("buy", "buy")):
    type_, subtype = kind
    return InvestmentTransaction(
        id=id_, account_id="a1", security_id="s1", ticker="VTI", date=d,
        quantity=1.0, price=amount, amount=amount, fees=None, type=type_, subtype=subtype,
    )


def test_round_trip_returns_the_same_rows(tmp_path):
    rows = (_txn("t1", date(2026, 1, 1)), _txn("t2", date(2026, 1, 2)))
    transaction_store.save("retirement", rows, reaches_back_to=date(2024, 1, 1), root=tmp_path)
    loaded, reaches = transaction_store.load("retirement", root=tmp_path)
    assert {t.id for t in loaded} == {"t1", "t2"}
    assert reaches == date(2024, 1, 1)


def test_replace_window_removes_a_row_plaid_no_longer_sends(tmp_path):
    """The deletion case a union merge cannot express — the reason this shape was chosen."""
    transaction_store.save("retirement", (_txn("t1", date(2026, 1, 5)),),
                           reaches_back_to=date(2026, 1, 1), root=tmp_path)
    transaction_store.replace_window(
        "retirement", (), start=date(2026, 1, 1), end=date(2026, 1, 10), root=tmp_path)
    loaded, _ = transaction_store.load("retirement", root=tmp_path)
    assert loaded == ()


def test_replace_window_leaves_a_row_outside_the_window_alone(tmp_path):
    outside = _txn("old", date(2020, 1, 1))
    transaction_store.save("retirement", (outside,), reaches_back_to=date(2020, 1, 1),
                           root=tmp_path)
    transaction_store.replace_window(
        "retirement", (_txn("new", date(2026, 1, 5)),),
        start=date(2026, 1, 1), end=date(2026, 1, 10), root=tmp_path)
    loaded, _ = transaction_store.load("retirement", root=tmp_path)
    assert {t.id for t in loaded} == {"old", "new"}


def test_a_corrected_row_keeps_the_incoming_value(tmp_path):
    transaction_store.save("retirement", (_txn("t1", date(2026, 1, 5), amount=100.0),),
                           reaches_back_to=date(2026, 1, 1), root=tmp_path)
    transaction_store.replace_window(
        "retirement", (_txn("t1", date(2026, 1, 5), amount=250.0),),
        start=date(2026, 1, 1), end=date(2026, 1, 10), root=tmp_path)
    loaded, _ = transaction_store.load("retirement", root=tmp_path)
    assert loaded[0].amount == 250.0


def test_load_on_a_missing_file_returns_none_not_empty(tmp_path):
    assert transaction_store.load("retirement", root=tmp_path) is None


def test_replace_window_widens_reaches_back_to_but_never_narrows_it(tmp_path):
    """A short recent-window refresh (the corrected-settlement case) must not make an
    already-reached backfill look shallower than it is."""
    transaction_store.save("retirement", (), reaches_back_to=date(2024, 1, 1), root=tmp_path)
    transaction_store.replace_window(
        "retirement", (_txn("t1", date(2026, 1, 5)),),
        start=date(2025, 12, 1), end=date(2026, 1, 10), root=tmp_path)
    _, reaches = transaction_store.load("retirement", root=tmp_path)
    assert reaches == date(2024, 1, 1)
