#!/usr/bin/env bash
# One command for the whole dashboard dev loop: the FastAPI backend with autoreload
# (dashboard --reload) and Vite's dev server with HMR (pnpm --dir web dev), torn down together
# on Ctrl-C. Vite proxies /api to 127.0.0.1:8000 (web/vite.config.ts) — that port is hardcoded
# there, not read from this script, so don't change the backend's port here without changing it
# there too.
set -euo pipefail
cd "$(dirname "$0")/.."

trap 'kill 0' EXIT

uv run dashboard --reload &
pnpm --dir web dev &

wait
