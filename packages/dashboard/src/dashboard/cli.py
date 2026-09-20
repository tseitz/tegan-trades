from __future__ import annotations

import argparse
import json

import uvicorn

from dashboard.api import create_app

# Loopback only, with no flag to widen it — ADR-0010 / user story 30. A dashboard that can spend
# nothing and sign nothing is still a page anyone on the network could open if this were 0.0.0.0.
HOST = "127.0.0.1"


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
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    app = create_app()

    if args.print_openapi:
        print(json.dumps(app.openapi()))
        return 0

    uvicorn.run(app, host=HOST, port=args.port)
    return 0
