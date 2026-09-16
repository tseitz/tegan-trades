import functools
import json
from datetime import date, timedelta

import pytest
from oracle import cache
from oracle.benchmarks import (
    DEFAULT_DOMAIN,
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


def test_resolve_held_flat_is_unresolved_and_names_71():
    resolved = resolve(Benchmark(type="held_flat"))
    assert isinstance(resolved, Unresolved)
    assert "#71" in resolved.reason


# ── report() ────────────────────────────────────────────────────────────────────────────

AS_OF = date(2026, 1, 1)


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
    anchor_root = tmp_path / "anchors.json"
    benchmark = Benchmark(type="flat_rate", rate=3.1)

    first = report(benchmark, mandate_name="sofi", as_of=date(2026, 1, 1), anchor_root=anchor_root)
    assert isinstance(first, dict)
    assert first["since_inception"] == pytest.approx(0.0)
    assert json.loads(anchor_root.read_text()) == {"sofi": "2026-01-01"}

    second = report(benchmark, mandate_name="sofi", as_of=date(2026, 1, 11), anchor_root=anchor_root)
    assert isinstance(second, dict)
    assert second["since_inception"] == pytest.approx(3.1 / 100 * (10 / 365))
    assert json.loads(anchor_root.read_text()) == {"sofi": "2026-01-01"}

    third = report(
        benchmark, mandate_name="treasury", as_of=date(2026, 1, 11), anchor_root=anchor_root,
    )
    assert isinstance(third, dict)
    assert third["since_inception"] == pytest.approx(0.0)
    assert json.loads(anchor_root.read_text()) == {
        "sofi": "2026-01-01", "treasury": "2026-01-11",
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
