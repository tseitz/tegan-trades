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


def test_snapshot_calls_every_source_and_combines_results(monkeypatch):
    monkeypatch.setattr(altsignal_cli.defillama, "fetch", lambda chains, **kw: [_reading("defillama")])
    monkeypatch.setattr(altsignal_cli.kalshi, "fetch", lambda tickers, **kw: [_reading("kalshi")])
    monkeypatch.setattr(altsignal_cli.polymarket, "fetch", lambda slugs, **kw: [_reading("polymarket")])

    readings = altsignal_cli._snapshot(CFG, verbose=False)
    assert {r.source for r in readings} == {"defillama", "kalshi", "polymarket"}


def test_one_source_failing_does_not_cost_the_others(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise FetchError("defillama is down")

    monkeypatch.setattr(altsignal_cli.defillama, "fetch", boom)
    monkeypatch.setattr(altsignal_cli.kalshi, "fetch", lambda tickers, **kw: [_reading("kalshi")])
    monkeypatch.setattr(altsignal_cli.polymarket, "fetch", lambda slugs, **kw: [_reading("polymarket")])

    readings = altsignal_cli._snapshot(CFG, verbose=True)
    assert {r.source for r in readings} == {"kalshi", "polymarket"}
    assert "defillama is down" in capsys.readouterr().out


def test_empty_config_fetches_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(altsignal_cli.defillama, "fetch", lambda chains, **kw: calls.append("defillama") or [])
    readings = altsignal_cli._snapshot(AltSignalConfig(chains=(), markets=()), verbose=False)
    assert readings == []
    assert calls == []
