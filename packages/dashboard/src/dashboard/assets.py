"""Serves the built front end, with an SPA fallback so a deep link survives a reload.

Three things here were measured against starlette 1.3.1 and each fails silently if written the
obvious way — see `.claude/plans/issue-85-dashboard-shell.md` task 5 for the measurements:
`StaticFiles(directory=...)` raises at construction on a missing directory, an unguarded
catch-all swallows a typo'd `/api/*` path as `200 text/html`, and Vite writes some assets
(`vite.svg`) to `dist/` root rather than `dist/assets/`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parents[4]
WEB_DIST = REPO_ROOT / "web" / "dist"


def mount_web(app: FastAPI) -> None:
    """Registers one `/{path:path}` catch-all under one function name regardless of which
    branch runs, so `--print-openapi`'s operationId does not depend on whether `web/dist`
    happens to exist on this machine — CI never builds it, and a schema that drifted by
    machine would make `gen-api-types.sh --check` fail for reasons that have nothing to do
    with an actual API change.
    """
    has_dist = WEB_DIST.is_dir()
    if has_dist:
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def catch_all(path: str) -> Response:
        if not has_dist:
            return PlainTextResponse(
                "web/dist is missing — run `pnpm --dir web build` first.",
                status_code=503,
            )

        if path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)

        # `path` is attacker-controlled (e.g. `..%2f..%2fpyproject.toml` decodes to a `..`
        # segment Starlette's `{path:path}` converter does not strip), and `Path.__truediv__`
        # both follows `..` and discards the left side entirely for an absolute right side —
        # either one walks `candidate` outside `WEB_DIST`. `is_relative_to` after `resolve()`
        # is the guard; do not drop it for a simpler-looking join.
        candidate = (WEB_DIST / path).resolve()
        if path and candidate.is_relative_to(WEB_DIST) and candidate.is_file():
            return FileResponse(candidate)

        return FileResponse(WEB_DIST / "index.html")
