"""`review` ships one of these too (`packages/review/tests/test_cli.py`). `compare_for` and
`render` are exercised in depth by `test_card.py`/`test_render.py`; this covers only `main`'s
own logic — argument handling, exit codes, and the two named-failure messages.
"""
from datetime import UTC, datetime

import compare.cli as cli
from compare.card import CompareResult, UnknownAssetError
from oracle.altsignal_config import AltSignalConfig, ProtocolEntry

NOW = datetime(2026, 9, 16, tzinfo=UTC)

HYPE = ProtocolEntry(
    asset="HYPE", llama_fees="hyperliquid", llama_tvl="hyperliquid",
    llama_oi=("hyperliquid-perps",), coingecko="hyperliquid",
    coingecko_derivatives=("hyperliquid",), venue="hyperliquid",
)
LIT = ProtocolEntry(
    asset="LIT", llama_fees="lighter", llama_tvl="lighter",
    llama_oi=("lighter-perps",), coingecko="lighter",
    coingecko_derivatives=("lighter",), venue="lighter",
)
CFG = AltSignalConfig(chains=(), markets=(), protocols=(HYPE, LIT))


def _empty_result():
    return CompareResult(left=HYPE, right=LIT, metrics=(), ratios=(), freshest=(None, None), oldest=(None, None))


def _populated_result():
    return CompareResult(left=HYPE, right=LIT, metrics=(), ratios=(), freshest=(NOW, NOW), oldest=(NOW, NOW))


def test_unknown_asset_prints_the_error_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(cli.altsignal_config, "load", lambda *a, **kw: CFG)

    def raise_unknown(*args, **kwargs):
        raise UnknownAssetError("NOPE has no protocols: row in cfg/altsignal.yaml")

    monkeypatch.setattr(cli, "compare_for", raise_unknown)

    assert cli.main(["HYPE", "NOPE"]) == 1
    assert "NOPE has no protocols" in capsys.readouterr().out


def test_empty_store_names_the_command_that_fills_it(monkeypatch, capsys):
    monkeypatch.setattr(cli.altsignal_config, "load", lambda *a, **kw: CFG)
    monkeypatch.setattr(cli, "compare_for", lambda *a, **kw: _empty_result())

    assert cli.main(["HYPE", "LIT"]) == 1
    assert "fetch-altsignal" in capsys.readouterr().out


def test_a_populated_store_renders_the_card_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli.altsignal_config, "load", lambda *a, **kw: CFG)
    monkeypatch.setattr(cli, "compare_for", lambda *a, **kw: _populated_result())

    assert cli.main(["HYPE", "LIT"]) == 0
    assert "HYPE vs LIT" in capsys.readouterr().out
