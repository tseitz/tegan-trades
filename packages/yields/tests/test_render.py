import ast
from pathlib import Path

from core import safety
from yields.render import render
from yields.scan import NOT_FETCHED, AssetYield, ReadingsAsOf, YieldOption, YieldsResult

RENDER_PATH = Path(__file__).resolve().parents[1] / "src" / "yields" / "render.py"


def test_render_imports_neither_gate_nor_score_from_core_safety():
    """AC 4: a renderer never recomputes a gate or a score — every ``GateResult``/
    ``SafetyScore`` printed already rode in on the ``YieldOption`` it was attached to."""
    tree = ast.parse(RENDER_PATH.read_text())
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "core.safety"
        for alias in node.names
    }
    assert "gate" not in imported_names
    assert "score" not in imported_names


def _gate(passed=True, reasons=()):
    return safety.GateResult(passed=passed, reasons=reasons, required_age_days=274,
                             lineage=safety.STANDALONE)


def _score():
    return safety.SafetyScore(incentive_share=None, apy_volatility=0.1, observations=1000,
                              incidents=0)


def _facts(pool_id, apy):
    return safety.VenueFacts(slug="lido", pool_id=pool_id, audited=True, apy=apy)


def test_summary_line_always_prints():
    result = YieldsResult(assets=(), as_of=None, readings_as_of=ReadingsAsOf(None, None),
                          configured=3, matched=0)
    assert render(result) == "0/3 configured wrapper(s) matched a holding"


def test_a_held_option_is_marked_plainly():
    option = YieldOption(wrapper="STETH", pool_id="pool-steth", facts=_facts("pool-steth", 2.25),
                         gate=_gate(), score=_score(), held=1.0, state=None)
    asset = AssetYield(asset="ETH", mandates=("crypto",), held_state_readable=True,
                       options=(option,))
    result = YieldsResult(assets=(asset,), as_of=None, readings_as_of=ReadingsAsOf(None, None),
                          configured=1, matched=1)
    text = render(result)
    assert "HELD" in text
    assert "STETH" in text


def test_a_gate_failing_held_option_shows_its_reasons():
    option = YieldOption(wrapper="STETH", pool_id="pool-steth", facts=_facts("pool-steth", 2.25),
                         gate=_gate(passed=False, reasons=("no audit on record",)), score=_score(),
                         held=1.0, state=None)
    asset = AssetYield(asset="ETH", mandates=("crypto",), held_state_readable=True,
                       options=(option,))
    result = YieldsResult(assets=(asset,), as_of=None, readings_as_of=ReadingsAsOf(None, None),
                          configured=1, matched=1)
    assert "no audit on record" in render(result)


def test_a_not_fetched_held_option_is_marked_as_such():
    option = YieldOption(wrapper="STETH", pool_id="pool-steth", facts=None, gate=None, score=None,
                         held=1.0, state=NOT_FETCHED)
    asset = AssetYield(asset="ETH", mandates=("crypto",), held_state_readable=True,
                       options=(option,))
    result = YieldsResult(assets=(asset,), as_of=None, readings_as_of=ReadingsAsOf(None, None),
                          configured=1, matched=1)
    assert "not fetched yet" in render(result)


def test_unknown_held_state_prints_once_on_the_asset_line():
    option = YieldOption(wrapper="STETH", pool_id="pool-steth", facts=_facts("pool-steth", 2.25),
                         gate=_gate(), score=_score(), held=None, state=None)
    asset = AssetYield(asset="ETH", mandates=("crypto",), held_state_readable=False,
                       options=(option,))
    result = YieldsResult(assets=(asset,), as_of=None, readings_as_of=ReadingsAsOf(None, None),
                          configured=1, matched=1)
    assert "unknown" in render(result)
