"""``plaid-sync``'s transaction-history pull: the gate, the paging, and the single-commit
promise that makes a page-3 failure leave the cache exactly as it was.

The network is monkeypatched out, following ``test_wallet_cli.py``'s and
``test_fetch_cli.py``'s prior art: patch ``portfolios.DATA_ROOT``, ``plaid.access_token``,
``plaid.holdings`` and ``plaid.investment_transactions`` directly rather than touching sockets.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from core.transactions import InvestmentTransaction
from oracle import balance_store, plaid, plaid_cli, portfolios, transaction_store


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "DATA_ROOT", tmp_path / "portfolios")
    monkeypatch.setattr(transaction_store, "DATA_ROOT", tmp_path / "transactions")
    monkeypatch.setattr(balance_store, "DATA_ROOT", tmp_path / "balances")
    (tmp_path / "portfolios").mkdir()
    (tmp_path / "transactions").mkdir()
    return tmp_path


HELD_FLAT_MANDATE = """\
mandate:
  name: retirement
  benchmarks:
    - type: held_flat
  horizon: macro
  risk_posture: conservative
"""

NO_HISTORY_MANDATE = """\
mandate:
  name: retirement
  benchmarks:
    - type: flat_rate
      rate: 0.05
  horizon: macro
  risk_posture: conservative
"""


def _portfolio(root, name, mandate_block, *, extra=""):
    (root / "portfolios" / f"{name}.yaml").write_text(
        f"account: {name}\ndomain: stock\n\n{mandate_block}\n{extra}"
        f"\npositions:\n  - ticker: VTI\n    shares: 1\n", encoding="utf-8")


def _holdings_payload():
    return {
        "holdings": [{"security_id": "s1", "account_id": "a1", "quantity": 1.0,
                     "cost_basis": 100.0}],
        "securities": [{"security_id": "s1", "ticker_symbol": "VTI", "type": "equity",
                        "name": "Vanguard Total"}],
        "accounts": [{"account_id": "a1", "type": "investment", "name": "Roth", "mask": "1111",
                     "balances": {"available": 50.0}}],
    }


def _txn_payload(n, offset, total):
    return {
        "investment_transactions": [
            {"investment_transaction_id": f"t{offset + i}", "account_id": "a1",
             "security_id": None, "date": "2026-01-05", "name": "dividend",
             "quantity": None, "price": None, "amount": 1.0, "fees": None,
             "type": "cash", "subtype": "dividend"}
            for i in range(n)
        ],
        "total_investment_transactions": total,
    }


def _stub_common(monkeypatch):
    monkeypatch.setattr(plaid, "access_token", lambda name: "tok")
    monkeypatch.setattr(plaid, "holdings", lambda token: _holdings_payload())


def test_a_whole_call_failure_warns_and_leaves_the_cache_untouched(monkeypatch, _roots):
    _portfolio(_roots, "retirement", HELD_FLAT_MANDATE)
    _stub_common(monkeypatch)
    monkeypatch.setattr(plaid, "investment_transactions",
                        lambda *a, **k: (_ for _ in ()).throw(plaid.PlaidError("down")))

    path = transaction_store.store_path("retirement", root=_roots / "transactions")
    assert not path.exists()

    assert plaid_cli.sync(["retirement"]) == 0
    assert not path.exists(), "a failed fetch must not create a cache file"


def test_a_whole_call_failure_leaves_an_existing_cache_byte_identical(monkeypatch, _roots):
    _portfolio(_roots, "retirement", HELD_FLAT_MANDATE)
    _stub_common(monkeypatch)
    path = transaction_store.save(
        "retirement", (), reaches_back_to=date(2024, 1, 1), root=_roots / "transactions")
    before = path.read_bytes()

    monkeypatch.setattr(plaid, "investment_transactions",
                        lambda *a, **k: (_ for _ in ()).throw(plaid.PlaidError("down")))
    assert plaid_cli.sync(["retirement"]) == 0
    assert path.read_bytes() == before


def test_a_page_3_failure_mid_backfill_leaves_the_cache_byte_identical(monkeypatch, _roots):
    """The likelier shape of AC 4, and the reason the single-commit design exists: pages
    accumulate in memory and nothing is written until the whole walk succeeds."""
    _portfolio(_roots, "retirement", HELD_FLAT_MANDATE)
    _stub_common(monkeypatch)

    calls = {"n": 0}

    def fake(token, *, start, end, offset, count):
        calls["n"] += 1
        if calls["n"] == 3:
            raise plaid.PlaidError("timeout on page 3")
        return _txn_payload(count, offset, total=2000)

    monkeypatch.setattr(plaid, "investment_transactions", fake)
    path = transaction_store.store_path("retirement", root=_roots / "transactions")

    assert plaid_cli.sync(["retirement"]) == 0
    assert calls["n"] == 3, "the fake must actually be reached a third time for this to test anything"
    assert not path.exists()


def test_an_empty_response_over_an_already_cached_window_is_refused_not_wiped(monkeypatch, _roots):
    """A Plaid hiccup that answers 200 with zero rows must not read as 'everything in this
    window was deleted' — the same guard the positions sync already keeps for an empty
    holdings response. Without it, up to two years of cache would be wiped by one blip."""
    _portfolio(_roots, "retirement", HELD_FLAT_MANDATE)
    _stub_common(monkeypatch)
    root = _roots / "transactions"
    existing = (InvestmentTransaction(
        id="t1", account_id="a1", security_id=None, ticker=None, date=date(2026, 1, 5),
        quantity=None, price=None, amount=1.0, fees=None, type="cash", subtype="dividend"),)
    path = transaction_store.save("retirement", existing,
                                  reaches_back_to=date(2024, 1, 1), root=root)
    before = path.read_bytes()

    monkeypatch.setattr(plaid, "investment_transactions",
                        lambda *a, **k: _txn_payload(0, 0, total=0))
    assert plaid_cli.sync(["retirement"]) == 0
    assert path.read_bytes() == before


def test_a_null_total_investment_transactions_does_not_crash(monkeypatch, _roots):
    """`.get(key, default)` only substitutes when the key is absent — Plaid sending an
    explicit `null` for `total_investment_transactions` passes straight through and used to
    break the `offset >= total` comparison with a `TypeError`."""
    _portfolio(_roots, "retirement", HELD_FLAT_MANDATE)
    _stub_common(monkeypatch)

    def fake(token, *, start, end, offset, count):
        payload = _txn_payload(3, offset, total=3)
        payload["total_investment_transactions"] = None
        return payload

    monkeypatch.setattr(plaid, "investment_transactions", fake)
    assert plaid_cli.sync(["retirement"]) == 0


def test_an_account_with_no_held_flat_benchmark_makes_no_transaction_call(monkeypatch, _roots):
    _portfolio(_roots, "retirement", NO_HISTORY_MANDATE)
    _stub_common(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("investment_transactions must not be called")

    monkeypatch.setattr(plaid, "investment_transactions", _boom)
    assert plaid_cli.sync(["retirement"]) == 0


def test_wants_history_warns_by_name_when_the_file_will_not_load(monkeypatch, _roots, capsys):
    (_roots / "portfolios" / "broken.yaml").write_text("not: a-portfolio\n", encoding="utf-8")
    assert plaid_cli._wants_history("broken") is False
    assert "broken" in capsys.readouterr().err


def test_window_asks_for_the_short_window_once_the_floor_is_already_reached(_roots):
    root = _roots / "transactions"
    floor = datetime.now(UTC).date() - timedelta(days=plaid_cli._HISTORY_FLOOR_DAYS)
    transaction_store.save("retirement", (), reaches_back_to=floor, root=root)
    start, _ = plaid_cli._window("retirement", root=root)
    assert start > floor


def test_window_asks_for_the_full_span_when_reaches_back_to_is_unset(_roots):
    start, _ = plaid_cli._window("retirement", root=_roots / "transactions")
    floor = datetime.now(UTC).date() - timedelta(days=plaid_cli._HISTORY_FLOOR_DAYS)
    assert start == floor


def test_the_plaid_accounts_narrowing_reaches_the_transaction_call(monkeypatch, _roots):
    _portfolio(_roots, "retirement", HELD_FLAT_MANDATE, extra="plaid_accounts:\n  - a1\n")
    _stub_common(monkeypatch)
    seen = {}

    def fake_from(payload, *, accounts=()):
        seen["accounts"] = accounts
        return (), ()

    monkeypatch.setattr(plaid, "investment_transactions",
                        lambda *a, **k: _txn_payload(0, 0, total=0))
    monkeypatch.setattr(plaid, "transactions_from", fake_from)
    assert plaid_cli.sync(["retirement"]) == 0
    assert seen["accounts"] == ("a1",)


def _with_savings(monkeypatch):
    payload = _holdings_payload()
    payload["accounts"].append({"account_id": "s1", "type": "depository", "subtype": "savings",
                                "name": "SoFi Savings", "mask": "2222",
                                "balances": {"available": 34045.6, "current": 34045.6}})
    monkeypatch.setattr(plaid, "access_token", lambda name: "tok")
    monkeypatch.setattr(plaid, "holdings", lambda token: payload)


def test_a_sync_writes_the_snapshot_under_the_patched_root(monkeypatch, _roots, capsys):
    _portfolio(_roots, "sofi", NO_HISTORY_MANDATE)
    _with_savings(monkeypatch)
    assert plaid_cli.sync(["sofi"]) == 0
    doc = balance_store.read("sofi", root=_roots / "balances")
    assert doc["accounts"]["SoFi Savings"]["current"] == 34045.6
    assert "balances: SoFi Savings 34,045.60 (available 34,045.60)" in capsys.readouterr().out


def test_dry_run_prints_balances_and_writes_nothing(monkeypatch, _roots, capsys):
    _portfolio(_roots, "sofi", NO_HISTORY_MANDATE)
    _with_savings(monkeypatch)
    assert plaid_cli.sync(["sofi", "--dry-run"]) == 0
    assert "balances: SoFi Savings" in capsys.readouterr().out
    assert not (_roots / "balances").exists()


def test_a_sync_with_no_usable_positions_writes_no_snapshot(monkeypatch, _roots):
    _portfolio(_roots, "sofi", NO_HISTORY_MANDATE)
    payload = {"holdings": [], "securities": [], "accounts": [
        {"account_id": "s1", "type": "depository", "name": "SoFi Savings",
         "balances": {"available": 1.0, "current": 1.0}}]}
    monkeypatch.setattr(plaid, "access_token", lambda name: "tok")
    monkeypatch.setattr(plaid, "holdings", lambda token: payload)
    assert plaid_cli.sync(["sofi"]) == 1
    assert not (_roots / "balances").exists()


def test_a_malformed_snapshot_fails_the_step_and_is_left_alone(monkeypatch, _roots):
    _portfolio(_roots, "sofi", NO_HISTORY_MANDATE)
    _with_savings(monkeypatch)
    (_roots / "balances").mkdir()
    bad = _roots / "balances" / "sofi.json"
    bad.write_text("{nope", encoding="utf-8")
    assert plaid_cli.sync(["sofi"]) == 1
    assert bad.read_text(encoding="utf-8") == "{nope"
