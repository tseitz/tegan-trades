from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from core.canon import Registry, load_registry
from networth import cli, exposure
from review.cli import CONFIG_DIR

REGISTRY = Registry(assets={"eth": "ETH", "weth": "ETH", "purr": "PURR", "bmnr": "BMNR",
                            "hype": "HYPE", "btc": "BTC"})


def _reading(ticker, lean_from=None):
    return SimpleNamespace(holding=SimpleNamespace(ticker=ticker), lean_from=lean_from)


def _write(tmp_path, text):
    path = tmp_path / "exposure.yaml"
    path.write_text(text)
    return path


def test_grouper_applies_alias_then_wrap_then_underlying():
    group_of = exposure.grouper(REGISTRY, {"PURR": "HYPE", "BMNR": "ETH"})
    assert group_of(_reading("WETH")) == "ETH"
    assert group_of(_reading("HODL", lean_from="BTC")) == "BTC"
    assert group_of(_reading("PURR")) == "HYPE"
    assert group_of(_reading("BMNR")) == "ETH"
    assert group_of(_reading("NVDA")) == "NVDA"


def test_a_missing_file_is_empty(tmp_path):
    assert exposure.load_underlying(tmp_path / "nope.yaml", REGISTRY) == {}


@pytest.mark.parametrize("text, needle", [
    ("underlying: [PURR]\n", "mapping"),
    ("underlying: {PURR: [HYPE]}\n", "PURR"),
    ("underlying: {weth: HYPE}\n", "weth"),
    ("underlying: {PURR: weth}\n", "weth"),
])
def test_a_malformed_file_names_the_key(tmp_path, text, needle):
    with pytest.raises(ValueError, match=needle):
        exposure.load_underlying(_write(tmp_path, text), REGISTRY)


def test_by_asset_records_nothing_and_rejects_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_books", lambda warn: [])
    monkeypatch.setattr(cli.exposure_view, "assemble", lambda books, *, as_of, warn: None)
    ledger = tmp_path / "networth.jsonl"
    assert cli.main(["--by-asset", "--ledger", str(ledger)]) == 0
    assert not ledger.exists()
    with pytest.raises(SystemExit):
        cli.main(["--by-asset", "--record", "--ledger", str(ledger)])


def test_committed_cfg_groups_the_known_holdings():
    registry = load_registry(CONFIG_DIR)
    underlying = exposure.load_underlying(exposure.EXPOSURE_PATH, registry)
    wraps = yaml.safe_load((CONFIG_DIR / "oracle_map.yaml").read_text())["wraps"]
    group_of = exposure.grouper(registry, underlying)

    def group(ticker):
        canonical = exposure.resolve_asset(ticker, registry)[0]
        return group_of(_reading(ticker, lean_from=wraps.get(canonical)))

    expected = {"WETH": "ETH", "STKAAVE": "AAVE", "ETHB": "ETH", "BSOL": "SOL", "HODL": "BTC",
                "ZCSH": "ZEC", "BMNR": "ETH", "PURR": "HYPE"}
    assert {t: group(t) for t in expected} == expected
    assert not set(underlying) & set(wraps)
