#!/usr/bin/env bash
# The validation gate — ruff, then the suite the pre-commit hook runs.
#
# Exists because the two commands are easy to type separately and easy to type *differently*:
# the marker expression here must match `.pre-commit-config.yaml` and CI exactly, or the thing
# you ran locally is not the thing that gates the commit. `needs_ore` wants a populated `data/`
# and `integration` wants the network; both are environmental, not regressions.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "── ruff ──"
uv run ruff check .
echo "── pytest ──"
uv run pytest -q -m "not integration and not needs_ore" "$@"
