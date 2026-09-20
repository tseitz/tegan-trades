#!/usr/bin/env bash
# The validation gate — ruff, then the suite the pre-commit hook runs, then the web half.
#
# Two different agreements, two different shapes. Python: the marker expression here must match
# `.pre-commit-config.yaml` and CI exactly, so each caller holds its own literal copy on purpose
# — a shared script would just move the duplication, not remove it. Web: `scripts/check-web.sh`
# is the single script all three callers invoke instead, because that duplication (a skip-if-
# missing guard, a temp-dir dance, a two-command sequence) is worth collapsing. `needs_ore` wants
# a populated `data/` and `integration` wants the network; both are environmental, not regressions.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "── ruff ──"
uv run ruff check .
echo "── pytest ──"
uv run pytest -q -m "not integration and not needs_ore" "$@"
./scripts/check-web.sh --if-changed
