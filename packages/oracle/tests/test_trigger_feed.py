"""Assembling the trigger timeframe, and choosing the rung above it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core.setups import DAILY, H12
from oracle import cache, intraday, trigger_feed
from oracle.intraday import H1, IntradayBar, IntradaySeries

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


@dataclass(frozen=True)
class Ref:
    """Just the parts of ``OracleRef`` this module reads."""
    source: str = "coinbase"
    symbol: str = "BTC-USD"
    tradeable: str | None = None

    @property
    def trade_symbol(self) -> str:
        return self.tradeable or self.symbol


def _rows(n: int, *, hour_step: int = 1, volume: float = 100.0):
    """Coinbase-shaped rows: [time, low, high, open, close, volume]."""
    base = int((NOW - timedelta(days=5)).timestamp())
    return [[base + i * 3600 * hour_step, 99.0, 101.0, 100.0, 100.5, volume] for i in range(n)]


# ── which instrument the bars are for ───────────────────────────────────────────────────────

def test_bars_are_fetched_for_the_tradeable_symbol_not_the_priced_one():
    """``^DJI`` is the Dow the theses are about; ``DIA`` is the only Dow anyone can buy, and the
    zone, stop and trigger are all quoted on what an order reaches. Computing the trigger on the
    index and placing it on the fund is the failure ``cfg/venue_map.yaml``'s header exists for.
    """
    seen = {}

    def fake_get(url, params):
        seen["url"] = url
        return {"chart": {"result": [{"timestamp": [], "indicators": {"quote": [{}]}}]}}

    trigger_feed.fetch(Ref(source="yahoo", symbol="^DJI", tradeable="DIA"),
                       now=NOW, get_json=fake_get)
    assert seen["url"].endswith("/DIA")
    assert "%5EDJI" not in seen["url"] and "^DJI" not in seen["url"]


def test_a_ref_without_a_proxy_uses_its_own_symbol():
    seen = {}

    def fake_get(url, params):
        seen["url"] = url
        return []

    trigger_feed.fetch(Ref(symbol="ETH-USD"), now=NOW, get_json=fake_get)
    assert seen["url"].endswith("/ETH-USD/candles")


def test_the_series_is_labelled_with_its_source_and_symbol():
    series = trigger_feed.fetch(Ref(symbol="BTC-USD"), now=NOW,
                                get_json=lambda url, params: _rows(30))
    assert (series.symbol, series.source, series.interval) == ("BTC-USD", "coinbase", H1)


# ── failure degrades, never raises ──────────────────────────────────────────────────────────

def test_a_source_with_no_intraday_adapter_returns_none():
    """Kraken and the derived-ratio refs. Not an error — the gate reads None as "cannot be
    computed" and declines to offer, which is the decided behaviour."""
    assert trigger_feed.fetch(Ref(source="kraken", symbol="XBTUSD"), now=NOW) is None


def test_a_fetch_that_raises_returns_none_rather_than_aborting_the_queue():
    """One unreachable symbol must not take down a whole queue build."""
    def boom(url, params):
        raise RuntimeError("network")

    assert trigger_feed.fetch(Ref(), now=NOW, get_json=boom) is None


def test_an_empty_response_is_none_not_an_empty_series():
    """Distinguishable downstream: an empty series would read as "measured, and there is
    nothing", which is a different claim from "could not measure"."""
    assert trigger_feed.fetch(Ref(), now=NOW, get_json=lambda url, params: []) is None


# ── the window asked for ────────────────────────────────────────────────────────────────────

def test_the_fetch_window_is_bounded_to_what_the_trigger_reads():
    """Nothing in the trigger looks past a ``PARTICIPATION_WINDOW`` of 80 bars plus an ATR
    window, so years of history would be cost without accuracy — 80KB per 30 days per symbol."""
    assert trigger_feed.INTRADAY_DAYS == 60
    seen = {}

    def fake_get(url, params):
        seen.update(params)
        return []

    trigger_feed.fetch(Ref(source="yahoo", symbol="AAPL"), now=NOW, get_json=fake_get)
    span = seen["period2"] - seen["period1"]
    assert span == 60 * 86400


# ── caching ─────────────────────────────────────────────────────────────────────────────────

def test_load_or_fetch_merges_into_the_cache(tmp_path):
    series = trigger_feed.load_or_fetch(Ref(), root=tmp_path, now=NOW,
                                        get_json=lambda url, params: _rows(30))
    assert series is not None and len(series.bars) == 30
    assert cache.load_intraday("coinbase", H1, "BTC-USD", root=tmp_path) is not None


def test_load_or_fetch_falls_back_to_the_cache_when_the_fetch_fails(tmp_path):
    """A network blip must degrade to yesterday's bars, not to no trigger at all — the same
    reasoning ``check_depth`` uses for an unmeasured market."""
    trigger_feed.load_or_fetch(Ref(), root=tmp_path, now=NOW,
                               get_json=lambda url, params: _rows(30))

    def boom(url, params):
        raise RuntimeError("network")

    assert trigger_feed.load_or_fetch(Ref(), root=tmp_path, now=NOW, get_json=boom) is not None


# ── the rung above ──────────────────────────────────────────────────────────────────────────

def _series(hours) -> IntradaySeries:
    bars = tuple(
        IntradayBar(date=datetime(2026, 8, 3, h, tzinfo=UTC),
                    open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
        for h in hours
    )
    return IntradaySeries(symbol="X", source="s", interval=H1, bars=bars)


def test_an_instrument_trading_around_the_clock_gets_h12():
    rung, bars = trigger_feed.setup_rung(_series(range(24)))
    assert rung == H12
    assert bars is not None and bars.interval == intraday.H12


def test_the_two_h12_constants_are_different_things_and_must_stay_different():
    """A trap worth a test rather than a comment: ``core.setups.H12`` and ``oracle.intraday.H12``
    share a name and hold different strings.

    ``core.setups.H12`` is a *zone timeframe* — it joins ``DAILY``/``WEEKLY``, and it lands
    inside ``Candidate.key``, so changing it silently re-keys every decision on disk.
    ``oracle.intraday.H12`` is an *interval label* — it joins ``H1``/``M15``, and it lands in a
    cache path. Unifying them would tie a decision-key format to a filesystem path, so they
    stay apart; this test exists so nobody helpfully collapses them.
    """
    assert H12 == "h12"
    assert intraday.H12 == "12h"
    assert H12 != intraday.H12


def test_a_session_bound_instrument_gets_the_daily_and_no_bars():
    """The daily series already exists in the price cache; re-deriving it here would be a
    second source of truth for the same rung."""
    rung, bars = trigger_feed.setup_rung(_series(range(13, 21)))
    assert rung == DAILY
    assert bars is None


def test_no_hourly_at_all_falls_back_to_the_daily():
    """Unknown degrades to the rung that always exists — never to no rung."""
    assert trigger_feed.setup_rung(None) == (DAILY, None)
