"""``InvestmentTransaction.kind`` — the single taxonomy #71 reads instead of re-deriving one
from ``(type, subtype)``. Table-driven: one row per pair the mapping promises to resolve.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from core.transactions import InvestmentTransaction, TransactionSpan


def _txn(type_, subtype):
    return InvestmentTransaction(
        id="t1", account_id="a1", security_id=None, ticker=None,
        date=date(2026, 1, 1), quantity=None, price=None, amount=1.0, fees=None,
        type=type_, subtype=subtype,
    )


@pytest.mark.parametrize("type_,subtype,expected", [
    # Confirmed against the retirement Item by `scripts/probe_plaid_transactions.py`.
    ("buy", "buy", "buy"),
    ("sell", "sell", "sell"),
    ("cash", "dividend", "dividend"),
    ("cash", "interest", "dividend"),
    ("cash", "withdrawal", "withdrawal"),
    ("fee", "miscellaneous fee", "fee"),
    # Documented by Plaid, unconfirmed — no deposit has happened on the probed account yet.
    ("cash", "deposit", "deposit"),
    ("cancel", "cancel", "other"),
])
def test_kind_maps_type_and_subtype(type_, subtype, expected):
    assert _txn(type_, subtype).kind == expected


def test_a_same_pair_transfer_cannot_be_told_apart_and_falls_to_other():
    """M1 sends `(transfer, transfer)` for both directions of a cash movement — the probe found
    13 of them on one account. Type/subtype alone cannot say which way it went, so this must not
    guess; a caller that needs the direction reads `amount`'s sign directly."""
    assert _txn("transfer", "transfer").kind == "other"


def test_an_unknown_pair_falls_to_other():
    assert _txn("something", "plaid has not documented").kind == "other"


def test_span_of_an_empty_iterable_has_no_oldest():
    span = TransactionSpan.of([])
    assert span.oldest is None
    assert span.newest is None
    assert span.count == 0


def test_span_spans_the_full_range():
    rows = [replace(_txn("buy", "buy"), date=d)
            for d in (date(2025, 6, 1), date(2026, 1, 1), date(2025, 12, 1))]
    span = TransactionSpan.of(rows)
    assert span.oldest == date(2025, 6, 1)
    assert span.newest == date(2026, 1, 1)
    assert span.count == 3
