import functools
import json
from datetime import date, timedelta

import pytest
from core.transactions import InvestmentTransaction
from oracle import cache
from oracle.benchmarks import (
    DEFAULT_DOMAIN,
    HeldFlat,
    Unresolved,
    Windows,
    benchmark_for,
    benchmark_refs,
    report,
    resolve,
    since_inception_days,
)
from oracle.portfolios import Benchmark
from oracle.series import Bar, PriceSeries


def _txn(d, type_, subtype, *, amount=0.0, quantity=None, ticker=None, security_id=None):
    return InvestmentTransaction(
        id="t", account_id="a", security_id=security_id, ticker=ticker, date=d,
        quantity=quantity, price=None, amount=amount, fees=None, type=type_, subtype=subtype,
    )


def _series(*pairs, symbol="^GSPC", source="yahoo"):
    bars = tuple(
        Bar(date=date.fromisoformat(d), open=c, high=c, low=c, close=c) for d, c in pairs
    )
    return PriceSeries(symbol=symbol, source=source, bars=bars)


def _use_cache_root(monkeypatch, root):
    """`cache.load`'s `root` default is bound at import time, so patching
    `cache.DATA_ROOT` afterward doesn't redirect it — patch the function itself instead,
    the same way `benchmarks.resolve()`/`report()` call it (no root of their own to inject)."""
    monkeypatch.setattr(cache, "load", functools.partial(cache.load, root=root))


# ── domain-keyed face, unchanged in shape and values ──────────────────────────────────────

def test_benchmark_for_crypto_still_returns_btc():
    assert benchmark_for("crypto") == ("coinbase", "BTC-USD")


def test_benchmark_for_default_still_returns_sp500():
    assert benchmark_for(DEFAULT_DOMAIN) == ("yahoo", "^GSPC")


def test_benchmark_for_unknown_domain_falls_back_to_default():
    assert benchmark_for("options") == benchmark_for(DEFAULT_DOMAIN)


def test_benchmark_refs_includes_eth_alongside_the_original_two():
    assert benchmark_refs() == {
        ("yahoo", "^GSPC"), ("coinbase", "BTC-USD"), ("coinbase", "ETH-USD"),
    }


# ── resolve() ───────────────────────────────────────────────────────────────────────────

def test_resolve_symbol_with_cached_series_returns_it(tmp_path, monkeypatch):
    _use_cache_root(monkeypatch, tmp_path)
    series = _series(("2025-01-01", 100.0))
    cache.save(series, root=tmp_path)
    resolved = resolve(Benchmark(type="symbol", key="sp500"))
    assert resolved == series


def test_resolve_symbol_with_unknown_key_is_unresolved():
    resolved = resolve(Benchmark(type="symbol", key="nope"))
    assert isinstance(resolved, Unresolved)


def test_resolve_symbol_with_no_cached_series_is_unresolved(tmp_path, monkeypatch):
    _use_cache_root(monkeypatch, tmp_path)
    resolved = resolve(Benchmark(type="symbol", key="sp500"))
    assert isinstance(resolved, Unresolved)


def test_resolve_flat_rate_returns_the_rate_with_no_io(monkeypatch):
    def _boom(*args, **kwargs):
        raise FileNotFoundError("resolve(flat_rate) must never touch the cache")
    monkeypatch.setattr(cache, "load", _boom)
    assert resolve(Benchmark(type="flat_rate", rate=3.1)) == 3.1


HELD_FLAT_MANDATE = "retirement"
HELD_FLAT_ANCHOR = date(2025, 1, 1)


def _held_flat_price_on(prices):
    return lambda ticker, day: prices.get((ticker, day))


def test_resolve_held_flat_with_no_inputs_supplied_is_unresolved():
    resolved = resolve(Benchmark(type="held_flat"))
    assert isinstance(resolved, Unresolved)


def test_resolve_held_flat_with_no_transaction_history_is_unresolved(tmp_path):
    resolved = resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 10.0}, transactions=(),
        price_on=_held_flat_price_on({}), mandate_name=HELD_FLAT_MANDATE,
        anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(resolved, Unresolved)


def test_resolve_held_flat_with_empty_basket_is_unresolved(tmp_path):
    # Bought after the anchor, so anchor_shares reconstructs zero shares for it.
    transactions = (_txn(date(2025, 2, 1), "buy", "buy", quantity=5.0, ticker="AAA", amount=500.0),)
    resolved = resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 5.0}, transactions=transactions,
        price_on=_held_flat_price_on({}), mandate_name=HELD_FLAT_MANDATE,
        anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(resolved, Unresolved)


def test_resolve_held_flat_with_negative_reconstruction_is_unresolved(tmp_path):
    transactions = (_txn(date(2025, 2, 1), "buy", "buy", quantity=5.0, ticker="AAA", amount=500.0),)
    resolved = resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 2.0}, transactions=transactions,
        price_on=_held_flat_price_on({}), mandate_name=HELD_FLAT_MANDATE,
        anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(resolved, Unresolved)


def test_resolve_held_flat_with_unclassified_row_is_unresolved(tmp_path):
    transactions = (
        _txn(HELD_FLAT_ANCHOR, "transfer", "transfer", amount=-300.0, security_id="s1"),
    )
    resolved = resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 10.0}, transactions=transactions,
        price_on=_held_flat_price_on({("AAA", HELD_FLAT_ANCHOR): 100.0}),
        mandate_name=HELD_FLAT_MANDATE, anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(resolved, Unresolved)
    assert "unclassified" in resolved.reason


def test_resolve_held_flat_with_unpriced_anchor_holding_is_unresolved(tmp_path):
    transactions = (_txn(HELD_FLAT_ANCHOR, "cash", "deposit", amount=-100.0),)
    resolved = resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 10.0}, transactions=transactions,
        price_on=_held_flat_price_on({}), mandate_name=HELD_FLAT_MANDATE,
        anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(resolved, Unresolved)
    assert "unpriced" in resolved.reason


def test_resolve_held_flat_resolving_case_returns_a_held_flat(tmp_path):
    transactions = (_txn(HELD_FLAT_ANCHOR, "cash", "deposit", amount=-100.0),)
    resolved = resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 10.0}, transactions=transactions,
        price_on=_held_flat_price_on({("AAA", HELD_FLAT_ANCHOR): 100.0}),
        mandate_name=HELD_FLAT_MANDATE, anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(resolved, HeldFlat)
    assert resolved.basket.anchor == HELD_FLAT_ANCHOR
    assert resolved.basket.shares == {"AAA": 10.0}


def test_resolve_held_flat_anchor_does_not_move_on_a_later_deeper_backfill(tmp_path):
    """AC 1: a later, deeper transaction pull must not re-baseline an already-reported anchor."""
    anchor_root = tmp_path / "anchors.json"
    shallow_transactions = (_txn(HELD_FLAT_ANCHOR, "cash", "deposit", amount=-100.0),)
    price_on = _held_flat_price_on({("AAA", HELD_FLAT_ANCHOR): 100.0})

    first = resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 10.0}, transactions=shallow_transactions,
        price_on=price_on, mandate_name=HELD_FLAT_MANDATE, anchor_root=anchor_root,
    )
    assert isinstance(first, HeldFlat)
    assert first.basket.anchor == HELD_FLAT_ANCHOR

    deeper_anchor = date(2024, 1, 1)
    deeper_transactions = (
        *shallow_transactions, _txn(deeper_anchor, "cash", "deposit", amount=-50.0),
    )
    second = resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 10.0}, transactions=deeper_transactions,
        price_on=price_on, mandate_name=HELD_FLAT_MANDATE, anchor_root=anchor_root,
    )
    assert isinstance(second, HeldFlat)
    assert second.basket.anchor == HELD_FLAT_ANCHOR


# ── report() ────────────────────────────────────────────────────────────────────────────

AS_OF = date(2026, 1, 1)


def test_report_held_flat_five_windows_come_back_as_numbers(tmp_path):
    prices = {
        ("AAA", HELD_FLAT_ANCHOR): 100.0,
        ("AAA", AS_OF - timedelta(days=7)): 105.0,
        ("AAA", AS_OF - timedelta(days=30)): 102.0,
        ("AAA", AS_OF - timedelta(days=90)): 98.0,
        ("AAA", AS_OF - timedelta(days=365)): 90.0,
        ("AAA", AS_OF): 110.0,
    }
    transactions = (_txn(HELD_FLAT_ANCHOR, "cash", "deposit", amount=-100.0),)
    result = report(
        Benchmark(type="held_flat"), mandate_name=HELD_FLAT_MANDATE, as_of=AS_OF,
        anchor_root=tmp_path / "anchors.json",
        holdings={"AAA": 10.0}, transactions=transactions, price_on=_held_flat_price_on(prices),
    )
    assert isinstance(result, dict)
    for key in ("7d", "30d", "90d", "1y", "since_inception"):
        assert isinstance(result[key], float)


def test_report_held_flat_since_inception_pins_to_the_anchor_distance(tmp_path):
    prices = {("AAA", HELD_FLAT_ANCHOR): 100.0, ("AAA", AS_OF): 110.0}
    transactions = (_txn(HELD_FLAT_ANCHOR, "cash", "deposit", amount=-100.0),)
    result = report(
        Benchmark(type="held_flat"), mandate_name=HELD_FLAT_MANDATE, as_of=AS_OF,
        anchor_root=tmp_path / "anchors.json", windows=Windows(1, 1, 1, 1),
        holdings={"AAA": 10.0}, transactions=transactions, price_on=_held_flat_price_on(prices),
    )
    assert isinstance(result, dict)
    assert result["since_inception"] is not None


def test_report_flat_price_series_returns_zero_for_every_window(tmp_path, monkeypatch):
    _use_cache_root(monkeypatch, tmp_path)
    series = _series(*[
        ((AS_OF - timedelta(days=n)).isoformat(), 100.0) for n in range(400)
    ])
    cache.save(series, root=tmp_path)
    result = report(
        Benchmark(type="symbol", key="sp500"), mandate_name="m", as_of=AS_OF,
        anchor_root=tmp_path / "anchors.json",
    )
    assert result == {"7d": 0.0, "30d": 0.0, "90d": 0.0, "1y": 0.0, "since_inception": 0.0}


def test_report_known_start_and_end_close_produces_expected_pct(tmp_path, monkeypatch):
    _use_cache_root(monkeypatch, tmp_path)
    series = _series(("2025-12-25", 100.0), ("2026-01-01", 110.0))
    cache.save(series, root=tmp_path)
    result = report(
        Benchmark(type="symbol", key="sp500"), mandate_name="m", as_of=AS_OF,
        windows=Windows(seven_day=7, thirty_day=30, ninety_day=90, one_year=365),
        anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(result, dict)
    assert result["7d"] == pytest.approx(0.10)


def test_report_window_older_than_series_span_is_none_for_that_key_only(tmp_path, monkeypatch):
    _use_cache_root(monkeypatch, tmp_path)
    series = _series(("2025-12-20", 100.0), ("2026-01-01", 100.0))
    cache.save(series, root=tmp_path)
    result = report(
        Benchmark(type="symbol", key="sp500"), mandate_name="m", as_of=AS_OF,
        anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(result, dict)
    assert result["1y"] is None
    assert result["7d"] == pytest.approx(0.0)


def test_report_since_inception_for_symbol_matches_the_series_span(tmp_path, monkeypatch):
    _use_cache_root(monkeypatch, tmp_path)
    series = _series(("2025-11-01", 100.0), ("2026-01-01", 100.0))
    cache.save(series, root=tmp_path)
    result = report(
        Benchmark(type="symbol", key="sp500"), mandate_name="m", as_of=AS_OF,
        anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(result, dict)
    expected_days = (AS_OF - date(2025, 11, 1)).days
    assert since_inception_days(series, mandate_name="m", as_of=AS_OF) == expected_days
    assert result["since_inception"] == pytest.approx(0.0)


def test_report_unresolved_benchmark_returns_unresolved_not_a_partial_dict(tmp_path):
    result = report(
        Benchmark(type="held_flat"), mandate_name="m", as_of=AS_OF,
        anchor_root=tmp_path / "anchors.json",
    )
    assert isinstance(result, Unresolved)


# ── flat_rate anchor persistence ───────────────────────────────────────────────────────────

def test_flat_rate_anchor_set_on_first_call_grows_on_second_and_is_per_mandate(tmp_path):
    """Key is widened to "mandate:benchmark_type" (see `_anchor`) so a mandate's `flat_rate`
    and `held_flat` entries never share one anchor — verified separately below."""
    anchor_root = tmp_path / "anchors.json"
    benchmark = Benchmark(type="flat_rate", rate=3.1)

    first = report(benchmark, mandate_name="sofi", as_of=date(2026, 1, 1), anchor_root=anchor_root)
    assert isinstance(first, dict)
    assert first["since_inception"] == pytest.approx(0.0)
    assert json.loads(anchor_root.read_text()) == {"sofi:flat_rate": "2026-01-01"}

    second = report(benchmark, mandate_name="sofi", as_of=date(2026, 1, 11), anchor_root=anchor_root)
    assert isinstance(second, dict)
    assert second["since_inception"] == pytest.approx(3.1 / 100 * (10 / 365))
    assert json.loads(anchor_root.read_text()) == {"sofi:flat_rate": "2026-01-01"}

    third = report(
        benchmark, mandate_name="treasury", as_of=date(2026, 1, 11), anchor_root=anchor_root,
    )
    assert isinstance(third, dict)
    assert third["since_inception"] == pytest.approx(0.0)
    assert json.loads(anchor_root.read_text()) == {
        "sofi:flat_rate": "2026-01-01", "treasury:flat_rate": "2026-01-11",
    }


def test_corrupt_anchor_file_raises_rather_than_silently_resetting(tmp_path):
    """A missing file means "first sight" and is fine; a file that exists but fails to parse
    is a torn write, and must not be read as "no anchors yet" — that would silently re-anchor
    every mandate on the next call with no error anywhere."""
    anchor_root = tmp_path / "anchors.json"
    anchor_root.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        report(
            Benchmark(type="flat_rate", rate=3.1), mandate_name="sofi",
            as_of=date(2026, 1, 1), anchor_root=anchor_root,
        )


def test_flat_rate_and_held_flat_anchors_on_one_mandate_do_not_share_a_key(tmp_path):
    anchor_root = tmp_path / "anchors.json"
    report(
        Benchmark(type="flat_rate", rate=3.1), mandate_name=HELD_FLAT_MANDATE,
        as_of=date(2026, 1, 1), anchor_root=anchor_root,
    )
    transactions = (_txn(HELD_FLAT_ANCHOR, "cash", "deposit", amount=-100.0),)
    resolve(
        Benchmark(type="held_flat"), holdings={"AAA": 10.0}, transactions=transactions,
        price_on=_held_flat_price_on({("AAA", HELD_FLAT_ANCHOR): 100.0}),
        mandate_name=HELD_FLAT_MANDATE, anchor_root=anchor_root,
    )
    assert json.loads(anchor_root.read_text()) == {
        f"{HELD_FLAT_MANDATE}:flat_rate": "2026-01-01",
        f"{HELD_FLAT_MANDATE}:held_flat": HELD_FLAT_ANCHOR.isoformat(),
    }


# ── AC3: a mandate with two benchmarks asks both of its entire balance ────────────────────

def test_two_benchmarks_on_one_mandate_resolve_independently(tmp_path, monkeypatch):
    """No shared state or slice parameter between two `report()` calls for the same mandate —
    each asks the mandate's whole pot, matching the real robinhood.yaml shape."""
    _use_cache_root(monkeypatch, tmp_path)
    cache.save(_series(("2025-12-25", 100.0), ("2026-01-01", 110.0), symbol="^GSPC", source="yahoo"),
               root=tmp_path)
    cache.save(_series(("2025-12-25", 200.0), ("2026-01-01", 220.0), symbol="BTC-USD", source="coinbase"),
               root=tmp_path)

    sp500_report = report(
        Benchmark(type="symbol", key="sp500"), mandate_name="robinhood", as_of=AS_OF,
        anchor_root=tmp_path / "anchors.json",
    )
    btc_report = report(
        Benchmark(type="symbol", key="btc"), mandate_name="robinhood", as_of=AS_OF,
        anchor_root=tmp_path / "anchors.json",
    )

    assert isinstance(sp500_report, dict) and isinstance(btc_report, dict)
    assert sp500_report["7d"] == pytest.approx(0.10)
    assert btc_report["7d"] == pytest.approx(0.10)
