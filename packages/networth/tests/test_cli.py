from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from networth import cli


def _result(name, value):
    reading = SimpleNamespace(market_value=value, holding=SimpleNamespace(ticker=name.upper()))
    book = SimpleNamespace(name=name, cash=None, cash_by_account={})
    return SimpleNamespace(readings=[reading], book=book, mismatched=())


@pytest.fixture
def night(monkeypatch, tmp_path):
    """A whole night of two books and no treasury. Tests break one piece of it."""
    state = SimpleNamespace(books=["a", "b"], load_warning=None, treasury_warning=None)

    def load_books(warn):
        if state.load_warning:
            warn(state.load_warning)
        return list(state.books)

    def load_result(*, books, as_of, warn):
        if state.treasury_warning:
            warn(state.treasury_warning)
        return

    monkeypatch.setattr(cli, "load_books", load_books)
    monkeypatch.setattr(cli, "load_result", load_result)
    monkeypatch.setattr(cli, "review_for",
                        lambda books, as_of: [_result(b, 100.0) for b in books])
    monkeypatch.setattr(cli, "price_freshness",
                        lambda: SimpleNamespace(age=timedelta(hours=2), is_stale=False))
    state.ledger = tmp_path / "networth.jsonl"
    return state


def _record(night, *extra):
    return cli.main(["--record", "--ledger", str(night.ledger), *extra])


def _rows(night):
    if not night.ledger.exists():
        return []
    return [json.loads(line) for line in night.ledger.read_text().splitlines()]


def test_a_whole_night_is_recorded(night):
    assert _record(night) == 0
    [row] = _rows(night)
    assert row["total"] == 200.0
    assert row["by_book"] == {"a": 100.0, "b": 100.0}
    assert row["stale"] is False and row["prices_fetched_at"]


@pytest.mark.parametrize("break_it", [
    {"load_warning": "portfolio 'b' was skipped — bad yaml"},
    {"treasury_warning": "portfolio 'b' was skipped — bad yaml"},
    {"books": []},
])
def test_a_partial_night_writes_nothing(night, break_it, capsys):
    for key, value in break_it.items():
        setattr(night, key, value)
    assert _record(night) == 1
    assert _rows(night) == []
    assert "not recorded" in capsys.readouterr().err


def _backdate(night):
    first = _rows(night)[0]
    first["date"] = "2026-01-01"
    night.ledger.write_text(json.dumps(first) + "\n")


def test_a_changed_set_of_books_needs_saying_so(night, capsys):
    assert _record(night) == 0
    _backdate(night)
    night.books = ["a"]
    assert _record(night) == 1
    assert "books changed" in capsys.readouterr().err
    assert len(_rows(night)) == 1
    assert _record(night, "--books-changed") == 0
    assert len(_rows(night)) == 2


def test_a_treasury_that_vanished_needs_saying_so(night, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_result",
                        lambda **kw: SimpleNamespace(total=500.0))
    assert _record(night) == 0
    monkeypatch.setattr(cli, "load_result", lambda **kw: None)
    _backdate(night)
    assert _record(night) == 1
    assert "treasury held" in capsys.readouterr().err
    assert _record(night, "--books-changed") == 0


def test_a_jump_in_unpriced_holdings_is_refused(night, monkeypatch, capsys):
    assert _record(night) == 0
    _backdate(night)

    def unpriced(name):
        result = _result(name, 100.0)
        result.readings += [
            SimpleNamespace(market_value=None, holding=SimpleNamespace(ticker=f"X{i}"))
            for i in range(3)
        ]
        return result

    monkeypatch.setattr(cli, "review_for", lambda books, as_of: [unpriced(b) for b in books])
    assert _record(night) == 1
    assert "price fetch likely failed" in capsys.readouterr().err
    assert _record(night, "--unpriced-ok") == 0


def test_stale_prices_are_recorded_as_stale(night, monkeypatch):
    monkeypatch.setattr(cli, "price_freshness",
                        lambda: SimpleNamespace(age=None, is_stale=True))
    assert _record(night) == 0
    [row] = _rows(night)
    assert row["stale"] is True and row["prices_fetched_at"] is None


def test_without_record_nothing_is_written(night):
    assert cli.main(["--ledger", str(night.ledger)]) == 0
    assert _rows(night) == []
