from datetime import UTC, datetime

from core.funding import FundingRate
from core.interest import OpenInterest
from oracle import funding_cli
from oracle.http import FetchError

AT = datetime(2026, 9, 13, 11, 0, tzinfo=UTC)


def _rate(venue, symbol="BTC"):
    return FundingRate(venue=venue, symbol=symbol, rate=1e-05, interval_hours=1.0, observed_at=AT)


def _oi(venue, symbol="BTC"):
    return OpenInterest(venue=venue, symbol=symbol, notional=1.0, volume_24h=1.0, observed_at=AT)


def _patch_all(monkeypatch, *, aster_fetch=None, aster_interest=None):
    """Every source stubbed, so `_snapshot` never reaches the real network."""
    monkeypatch.setattr(
        funding_cli.hyperliquid,
        "fetch_snapshot",
        lambda **kw: ([_rate("hyperliquid")], [_oi("hyperliquid")]),
    )
    monkeypatch.setattr(funding_cli.lighter, "fetch", lambda **kw: [_rate("lighter")])
    monkeypatch.setattr(funding_cli.lighter, "fetch_open_interest", lambda **kw: [_oi("lighter")])
    monkeypatch.setattr(
        funding_cli.aster,
        "fetch",
        aster_fetch or (lambda **kw: ([_rate("aster")], 0)),
    )
    monkeypatch.setattr(
        funding_cli.aster,
        "fetch_open_interest",
        aster_interest or (lambda **kw: ([_oi("aster")], 1.0)),
    )


def test_snapshot_logs_open_interest_for_all_three_venues(monkeypatch):
    _patch_all(monkeypatch)
    rates, interest = funding_cli._snapshot(verbose=False)
    assert {r.venue for r in rates} == {"hyperliquid", "lighter", "aster"}
    assert {r.venue for r in interest} == {"hyperliquid", "lighter", "aster"}


def test_no_interest_skips_the_open_interest_fetches_entirely(monkeypatch):
    def boom(**kw):
        raise AssertionError("aster.fetch_open_interest should not run under --no-interest")

    _patch_all(monkeypatch, aster_interest=boom)
    rates, interest = funding_cli._snapshot(with_interest=False, verbose=False)
    assert {r.venue for r in rates} == {"hyperliquid", "lighter", "aster"}
    assert interest == []


def test_aster_funding_failure_does_not_cost_the_other_venues_rates(monkeypatch, capsys):
    def boom(**kw):
        raise FetchError("aster is down")

    _patch_all(monkeypatch, aster_fetch=boom)
    rates, interest = funding_cli._snapshot(verbose=True)
    assert {r.venue for r in rates} == {"hyperliquid", "lighter"}
    # Its open-interest read is independent of the funding read failing.
    assert {r.venue for r in interest} == {"hyperliquid", "lighter", "aster"}
    assert "aster is down" in capsys.readouterr().out


def test_aster_interest_failure_does_not_cost_the_funding_rates(monkeypatch, capsys):
    def boom(**kw):
        raise FetchError("aster openInterest is down")

    _patch_all(monkeypatch, aster_interest=boom)
    rates, interest = funding_cli._snapshot(verbose=True)
    assert {r.venue for r in rates} == {"hyperliquid", "lighter", "aster"}
    assert {r.venue for r in interest} == {"hyperliquid", "lighter"}
    assert "aster openInterest is down" in capsys.readouterr().out


def test_hyperliquid_failure_costs_neither_its_funding_nor_its_interest_to_the_others(
    monkeypatch, capsys
):
    def boom(**kw):
        raise FetchError("hyperliquid is down")

    _patch_all(monkeypatch)
    monkeypatch.setattr(funding_cli.hyperliquid, "fetch_snapshot", boom)
    rates, interest = funding_cli._snapshot(verbose=True)
    assert {r.venue for r in rates} == {"lighter", "aster"}
    assert {r.venue for r in interest} == {"lighter", "aster"}
    assert "hyperliquid is down" in capsys.readouterr().out
