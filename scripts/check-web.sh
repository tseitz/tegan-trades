#!/usr/bin/env bash
# The web half of the gate — schema drift, then tsc. One script rather than three copies:
# `check.sh`, the pre-commit hook, and CI each need this, and unlike ruff/pytest this is the one
# place that does not have to hold a literal copy per caller.
#
# The trigger reaches past `web/` on purpose: a Python edit to `packages/dashboard/`'s wire
# models (or a dependency bump touching `uv.lock`/`pyproject.toml`) is what stales the schema,
# not a `.tsx` edit — see `.claude/plans/issue-86-conditional-web-gate.md`.
set -euo pipefail
cd "$(dirname "$0")/.."

IF_CHANGED=0
SKIP_IF_MISSING=0
for arg in "$@"; do
  case "$arg" in
    --if-changed) IF_CHANGED=1 ;;
    --skip-if-missing) SKIP_IF_MISSING=1 ;;
    *)
      echo "check-web.sh: unknown flag: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$IF_CHANGED" == "1" ]]; then
  TRIGGER_PATHS=(web packages/dashboard uv.lock pyproject.toml)
  changed="$(git diff --name-only HEAD -- "${TRIGGER_PATHS[@]}")"
  untracked="$(git ls-files --others --exclude-standard -- "${TRIGGER_PATHS[@]}")"
  if [[ -z "$changed" && -z "$untracked" ]]; then
    echo "check-web.sh: no web-affecting changes, skipping."
    exit 0
  fi
fi

if ! command -v pnpm >/dev/null 2>&1 || [[ ! -d web/node_modules ]]; then
  echo "check-web.sh: pnpm toolchain not installed — run pnpm --dir web install." >&2
  if [[ "$SKIP_IF_MISSING" == "1" ]]; then
    exit 0
  fi
  exit 1
fi

echo "── schema drift ──"
./scripts/gen-api-types.sh --check
echo "── tsc ──"
pnpm --dir web typecheck
