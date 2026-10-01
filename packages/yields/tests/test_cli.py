from datetime import UTC, date, datetime

import yields.cli as cli
from yields.scan import ReadingsAsOf, YieldsResult

NOW = datetime(2026, 9, 21, tzinfo=UTC)


def _empty_result():
    return YieldsResult(assets=(), as_of=date(2026, 9, 21),
                        readings_as_of=ReadingsAsOf(None, None), configured=0, matched=0)


def _unfetched_result():
    return YieldsResult(assets=(), as_of=date(2026, 9, 21),
                        readings_as_of=ReadingsAsOf(None, None), configured=1, matched=1)


def _populated_result():
    return YieldsResult(assets=(), as_of=date(2026, 9, 21),
                        readings_as_of=ReadingsAsOf(NOW, NOW), configured=1, matched=1)


def test_no_portfolios_names_where_to_write_one(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_books", lambda **kw: ())

    assert cli.main([]) == 0
    assert "no portfolios yet" in capsys.readouterr().out


def test_unfetched_store_names_the_command_that_fills_it(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_books", lambda **kw: (object(),))
    monkeypatch.setattr(cli.altsignal_config, "load", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "yields_for", lambda *a, **kw: _unfetched_result())

    assert cli.main([]) == 1
    assert "fetch-altsignal" in capsys.readouterr().out


def test_a_populated_store_renders_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_books", lambda **kw: (object(),))
    monkeypatch.setattr(cli.altsignal_config, "load", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "yields_for", lambda *a, **kw: _populated_result())

    assert cli.main([]) == 0
    assert "configured wrapper(s) matched a holding" in capsys.readouterr().out


def test_no_wrappers_configured_still_renders_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_books", lambda **kw: (object(),))
    monkeypatch.setattr(cli.altsignal_config, "load", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "yields_for", lambda *a, **kw: _empty_result())

    assert cli.main([]) == 0
    assert "0/0 configured" in capsys.readouterr().out
