from datetime import UTC, datetime

from core.altsignal import AltSignalReading
from oracle import altsignal_cli
from oracle.altsignal_config import AltSignalConfig, ChainEntry, MarketEntry
from oracle.http import FetchError

AT = datetime(2026, 9, 3, 6, 15, tzinfo=UTC)

CFG = AltSignalConfig(
    chains=(ChainEntry(asset="SOL", chain="solana"),),
    markets=(
        MarketEntry(platform="kalshi", key="KXFED-26DEC-T3.75", why="Fed decision"),
        MarketEntry(platform="polymarket", key="us-recession-by-end-of-2026", why="Recession odds"),
    ),
)


def _reading(source, key="solana"):
    return AltSignalReading(source=source, kind="chain_tvl", key=key, value=1.0, observed_at=AT)


def _patch_all(monkeypatch, *, pumpfun_readings=()):
    """Every source stubbed, so no test of `_snapshot` ever reaches the real network — pump.fun
    included, since its default `fetch()` would otherwise read a real key from `.env` and call
    the live API during a unit test run."""
    monkeypatch.setattr(altsignal_cli.defillama, "fetch", lambda chains, **kw: [_reading("defillama")])
    monkeypatch.setattr(altsignal_cli.kalshi, "fetch", lambda tickers, **kw: [_reading("kalshi")])
    monkeypatch.setattr(altsignal_cli.polymarket, "fetch", lambda slugs, **kw: [_reading("polymarket")])
    monkeypatch.setattr(altsignal_cli.pumpfun, "fetch", lambda **kw: list(pumpfun_readings))


def test_snapshot_calls_every_source_and_combines_results(monkeypatch):
    _patch_all(monkeypatch, pumpfun_readings=[_reading("pumpfun", key="<mint>")])

    readings = altsignal_cli._snapshot(CFG, verbose=False)
    assert {r.source for r in readings} == {"defillama", "kalshi", "polymarket", "pumpfun"}


def test_one_source_failing_does_not_cost_the_others(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise FetchError("defillama is down")

    _patch_all(monkeypatch)
    monkeypatch.setattr(altsignal_cli.defillama, "fetch", boom)

    readings = altsignal_cli._snapshot(CFG, verbose=True)
    assert {r.source for r in readings} == {"kalshi", "polymarket"}
    assert "defillama is down" in capsys.readouterr().out


def test_pumpfun_failing_does_not_cost_the_others(monkeypatch, capsys):
    def boom(**kwargs):
        raise FetchError("SOLANATRACKER_API_KEY is not set")

    _patch_all(monkeypatch)
    monkeypatch.setattr(altsignal_cli.pumpfun, "fetch", boom)

    readings = altsignal_cli._snapshot(CFG, verbose=True)
    assert {r.source for r in readings} == {"defillama", "kalshi", "polymarket"}
    assert "SOLANATRACKER_API_KEY" in capsys.readouterr().out


def test_pumpfun_runs_even_when_nothing_else_is_configured(monkeypatch):
    """Unlike the other three, pump.fun is a global feed — not gated on cfg/altsignal.yaml."""
    calls = []
    _patch_all(monkeypatch)
    monkeypatch.setattr(altsignal_cli.pumpfun, "fetch", lambda **kw: calls.append("pumpfun") or [])

    altsignal_cli._snapshot(AltSignalConfig(chains=(), markets=()), verbose=False)
    assert calls == ["pumpfun"]


def test_empty_config_fetches_nothing_from_the_configured_sources(monkeypatch):
    calls = []
    _patch_all(monkeypatch)
    monkeypatch.setattr(altsignal_cli.defillama, "fetch", lambda chains, **kw: calls.append("defillama") or [])
    readings = altsignal_cli._snapshot(AltSignalConfig(chains=(), markets=()), verbose=False)
    assert readings == []
    assert calls == []
