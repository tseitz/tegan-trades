#!/usr/bin/env bash
# Regenerates `web/src/api/schema.d.ts` from the FastAPI app's own OpenAPI schema.
#
# Goes through a file rather than a live server: `--print-openapi` needs no port and no
# running process, so this can run in CI and pre-commit without ever binding a socket.
set -euo pipefail
cd "$(dirname "$0")/.."

# Not $TMPDIR: it is normally unset on Linux and this runs under `set -u` on the droplet.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

uv run dashboard --print-openapi > "$TMP/openapi.json"

# `pnpm --dir web exec` runs the command with `web/` as its cwd, so paths given to
# openapi-typescript must be relative to `web/`, not the repo root this script itself runs from.
if [[ "${1:-}" == "--check" ]]; then
  pnpm --dir web exec openapi-typescript "$TMP/openapi.json" -o "$TMP/schema.d.ts"
  diff -u web/src/api/schema.d.ts "$TMP/schema.d.ts" || {
    echo "web/src/api/schema.d.ts is stale — run scripts/gen-api-types.sh to regenerate." >&2
    exit 1
  }
  echo "schema.d.ts is current."
else
  pnpm --dir web exec openapi-typescript "$TMP/openapi.json" -o src/api/schema.d.ts
  echo "wrote web/src/api/schema.d.ts"
fi
