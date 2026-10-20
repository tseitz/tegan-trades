#!/usr/bin/env bash
# One command for the whole dashboard dev loop: the FastAPI backend with autoreload
# (dashboard --reload) and Vite's dev server with HMR (pnpm --dir web dev). If either dies the
# other is torn down with it. The backend takes the first free port from 8000 (or
# $DASHBOARD_PORT) and hands it to Vite's /api proxy through DASHBOARD_PORT
# (web/vite.config.ts). Extra args go to Vite: ./scripts/dev.sh --port 5180
set -euo pipefail
cd "$(dirname "$0")/.."

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

port=${DASHBOARD_PORT:-8000}
while port_in_use "$port"; do
  echo "Port $port is in use, trying another one..."
  port=$((port + 1))
done
export DASHBOARD_PORT=$port

trap 'kill 0' EXIT

uv run dashboard --reload --port "$DASHBOARD_PORT" &
pnpm --dir web dev "$@" &

wait -n
