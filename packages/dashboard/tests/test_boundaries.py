"""Pins that are cheap now and unrecoverable later — ADR-0010.

Both checks exist because they catch different things: the AST scan sees a direct import this
package writes, but `review` imports `oracle`, so a `sys.modules` check cannot see a direct
violation against that name. The `sys.modules` check sees a *transitive* arrival an AST scan of
this package alone cannot: nothing here imports `execution`, but if `review` ever grew an import
of `oracle.execute`, only a runtime check would catch it.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN = {"oracle", "execution", "llm", "ingestion", "distill", "brain"}
SRC = Path(__file__).resolve().parents[1] / "src" / "dashboard"


def _imported_top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_forbidden_package_is_imported_by_dashboard_source():
    for path in SRC.rglob("*.py"):
        found = _imported_top_level_names(path) & FORBIDDEN
        assert not found, f"{path} imports forbidden package(s): {found}"


def test_execution_never_arrives_transitively():
    for name in ("oracle", "execution", "llm", "ingestion", "distill", "brain"):
        sys.modules.pop(name, None)

    import dashboard.api as api

    api.create_app()

    assert "execution" not in sys.modules


def test_host_is_loopback_with_no_flag_to_change_it():
    import dashboard.cli as cli

    assert cli.HOST == "127.0.0.1"

    dests = {action.dest for action in cli._build_parser()._actions}
    assert "host" not in dests
