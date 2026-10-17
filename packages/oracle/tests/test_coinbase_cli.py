"""``coinbase-sync``'s own decisions: when the book file opts in, and when it refuses to write.

The reader is monkeypatched out; nothing here touches the network.
"""

from __future__ import annotations

import pytest
from oracle import coinbase_account, coinbase_cli, portfolios

SETTINGS = (
    "account: coinbase\ndomain: crypto\nstale_after: 7\nmin_value: 250\n\n"
    "mandate:\n  name: coinbase\n  benchmarks:\n    - type: symbol\n      key: btc\n"
    "  horizon: swing\n  risk_posture: aggressive\n\n"
    "# my own note about this book\n"
)


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "DATA_ROOT", tmp_path)

    def no_network(*a, **k):
        raise AssertionError("coinbase_account.read must be patched by the test")

    monkeypatch.setattr(coinbase_account, "read", no_network)
    return tmp_path


def _book(root, body=SETTINGS):
    path = root / "coinbase.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _row(ticker="BTC", shares=0.5, mark=60000.0, staked=None):
    return portfolios.Row(
        ticker=ticker,
        shares=shares,
        cost=None,
        domain="crypto",
        mark=mark,
        staked=staked,
    )


def _reader(monkeypatch, rows, skipped=(), cash=0.0, seen=None):
    def fake_rows_from(read_, *, min_value):
        if seen is not None:
            seen.append(min_value)
        return rows, tuple(skipped), cash

    monkeypatch.setattr(coinbase_account, "read", lambda: object())
    monkeypatch.setattr(coinbase_account, "rows_from", fake_rows_from)


def _raises(monkeypatch, exc):
    def boom():
        raise exc

    monkeypatch.setattr(coinbase_account, "read", boom)


def test_no_book_file_prints_an_example_and_exits_zero_without_writing(_root, capsys):
    assert coinbase_cli.sync([]) == 0
    out = capsys.readouterr().out
    assert "mandate:" in out
    assert "risk_posture: aggressive" in out
    assert list(_root.iterdir()) == []


def test_a_file_without_a_mandate_refuses_and_writes_nothing(_root, monkeypatch):
    path = _book(_root, "account: coinbase\ndomain: crypto\n")
    _reader(monkeypatch, [_row()])
    assert coinbase_cli.sync([]) == 1
    assert path.read_text(encoding="utf-8") == "account: coinbase\ndomain: crypto\n"


def test_missing_credentials_exit_one(_root, monkeypatch):
    _book(_root)
    _raises(monkeypatch, coinbase_account.MissingCredentials("KRAKEN_API_KEY unset"))
    assert coinbase_cli.sync([]) == 1


def test_a_coinbase_error_exits_one_and_leaves_the_file_untouched(_root, monkeypatch):
    path = _book(_root)
    _raises(monkeypatch, coinbase_account.CoinbaseError("EService:Unavailable"))
    assert coinbase_cli.sync([]) == 1
    assert path.read_text(encoding="utf-8") == SETTINGS


def test_a_permission_refusal_exits_one_and_leaves_the_file_untouched(
    _root, monkeypatch
):
    path = _book(_root)
    _raises(monkeypatch, coinbase_account.CoinbaseError("key can trade"))
    assert coinbase_cli.sync([]) == 1
    assert path.read_text(encoding="utf-8") == SETTINGS


def test_a_reconciliation_miss_exits_one_and_leaves_the_file_untouched(
    _root, monkeypatch
):
    path = _book(_root)
    monkeypatch.setattr(coinbase_account, "read", lambda: object())

    def miss(read_, *, min_value):
        raise coinbase_account.CoinbaseError("rows are $10 short of total_balance")

    monkeypatch.setattr(coinbase_account, "rows_from", miss)
    assert coinbase_cli.sync([]) == 1
    assert path.read_text(encoding="utf-8") == SETTINGS


def test_an_error_raised_while_mapping_balances_also_leaves_the_file_untouched(
    _root, monkeypatch
):
    path = _book(_root)
    monkeypatch.setattr(coinbase_account, "read", lambda: object())

    def refuse(read_, *, min_value):
        raise coinbase_account.CoinbaseError("unknown suffix .Z")

    monkeypatch.setattr(coinbase_account, "rows_from", refuse)
    assert coinbase_cli.sync([]) == 1
    assert path.read_text(encoding="utf-8") == SETTINGS


def test_an_empty_read_exits_one_and_leaves_the_file_untouched(_root, monkeypatch):
    path = _book(_root)
    _reader(monkeypatch, [])
    assert coinbase_cli.sync([]) == 1
    assert path.read_text(encoding="utf-8") == SETTINGS


def test_a_good_read_keeps_the_settings_half_and_stamps_the_banner(_root, monkeypatch):
    path = _book(_root)
    _reader(monkeypatch, [_row(), _row("ETH", 2.0, 3000.0, staked=1.0)], cash=125.5)
    assert coinbase_cli.sync([]) == 0

    text = path.read_text(encoding="utf-8")
    assert SETTINGS.rstrip("\n") in text
    assert "# Synced from Coinbase" in text
    book = portfolios.load("coinbase", root=_root)
    assert {p.holding.ticker for p in book.positions} == {"BTC", "ETH"}


def test_min_value_in_the_file_is_what_the_reader_is_given(_root, monkeypatch):
    _book(_root)
    seen: list[float] = []
    _reader(monkeypatch, [_row()], seen=seen)
    assert coinbase_cli.sync([]) == 0
    assert seen == [250.0]


def test_the_min_value_flag_beats_the_file(_root, monkeypatch):
    _book(_root)
    seen: list[float] = []
    _reader(monkeypatch, [_row()], seen=seen)
    assert coinbase_cli.sync(["--min-value", "5"]) == 0
    assert seen == [5.0]


def test_dry_run_writes_nothing(_root, monkeypatch, capsys):
    path = _book(_root)
    _reader(monkeypatch, [_row()])
    assert coinbase_cli.sync(["--dry-run"]) == 0
    assert path.read_text(encoding="utf-8") == SETTINGS
    assert "would write" in capsys.readouterr().out


def test_a_name_argument_selects_a_different_book(_root, monkeypatch):
    other = _root / "coinbase2.yaml"
    other.write_text(
        SETTINGS.replace("name: coinbase", "name: coinbase2"), encoding="utf-8"
    )
    _reader(monkeypatch, [_row()])
    assert coinbase_cli.sync(["coinbase2"]) == 0
    assert "# Synced from Coinbase" in other.read_text(encoding="utf-8")
    assert not (_root / "coinbase.yaml").exists()


def test_unpriced_rows_fee_credits_and_dust_are_all_reported(
    _root, monkeypatch, capsys
):
    _book(_root)
    _reader(
        monkeypatch,
        [_row(), _row("NEWCOIN", 10.0, mark=None)],
        skipped=[
            portfolios.Skipped("KFEE", "fee credits, not a position", "fee"),
            portfolios.Skipped("A", "$1.00, under the value floor", "dust"),
            portfolios.Skipped("B", "$2.00, under the value floor", "dust"),
        ],
    )
    assert coinbase_cli.sync([]) == 0
    out = capsys.readouterr().out
    assert "UNPRICED NEWCOIN" in out
    assert "dropped KFEE" in out
    assert "dropped 2 under the value floor" in out
    assert "dropped A" not in out


def test_new_and_gone_tickers_are_named(_root, monkeypatch, capsys):
    path = _book(_root)
    _reader(monkeypatch, [_row("BTC")])
    assert coinbase_cli.sync([]) == 0
    capsys.readouterr()

    _reader(monkeypatch, [_row("ETH", 1.0, 3000.0)])
    assert coinbase_cli.sync([]) == 0
    out = capsys.readouterr().out
    assert "new   ETH" in out
    assert "gone  BTC" in out
    assert path.exists()


def test_a_min_value_that_is_not_a_number_refuses_rather_than_falling_back(
    _root, capsys
):
    path = _root / "coinbase.yaml"
    path.write_text(coinbase_cli.EXAMPLE.format(name="coinbase") + "min_value: lots\n")
    before = path.read_text()
    assert coinbase_cli.sync([]) == 1
    assert "not a number" in capsys.readouterr().err
    assert path.read_text() == before
