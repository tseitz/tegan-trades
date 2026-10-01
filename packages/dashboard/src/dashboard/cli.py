from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from dashboard.api import create_app

# Loopback only, with no flag to widen it — ADR-0010 / user story 30. A dashboard that can spend
# nothing and sign nothing is still a page anyone on the network could open if this were 0.0.0.0.
HOST = "127.0.0.1"

# src/dashboard/cli.py -> src/dashboard -> src -> dashboard -> packages -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[4]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the dashboard.")
    parser.add_argument(
        "--port", type=int, default=8000,
        help="Only for a port collision — vite.config.ts's proxy target is authoritative "
             "and does not read this flag, so changing it breaks `pnpm --dir web dev`.",
    )
    parser.add_argument(
        "--print-openapi", action="store_true",
        help="Print the OpenAPI schema and exit, without starting a server.",
    )
    parser.add_argument(
        "--reload", action="store_true",
        help="Restart the process on a source change under packages/ — what `scripts/dev.sh` "
             "runs this with. Off by default: uvicorn's watcher costs a background thread and "
             "an import-by-string that a plain run does not need.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.print_openapi:
        print(json.dumps(create_app().openapi()))
        return 0

    if args.reload:
        # Reload mode re-imports the app itself on every restart, so it needs the factory as a
        # string target rather than the object `create_app()` already built above.
        uvicorn.run("dashboard.api:create_app", factory=True, host=HOST, port=args.port,
                   reload=True, reload_dirs=[str(REPO_ROOT / "packages")])
        return 0

    uvicorn.run(create_app(), host=HOST, port=args.port)
    return 0
