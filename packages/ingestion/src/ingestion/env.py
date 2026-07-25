"""Load repo-root ``.env`` into the process environment.

``youtube._proxy_config`` reads ``WEBSHARE_PROXY_*`` straight from ``os.environ``, and
nothing populated it — so creating a ``.env`` (as ``.env.example`` and CLAUDE.md both
invite you to) had no effect at all, and transcript fetches silently ran unproxied. This
closes that gap at the CLI boundary.

Hand-rolled rather than depending on python-dotenv: the format we need is four lines of
``KEY=value`` and a dependency isn't worth it. Deliberately narrow — no interpolation, no
multiline values.
"""
from __future__ import annotations

import os
from pathlib import Path

# src/ingestion/env.py -> src/ingestion -> src -> ingestion -> packages -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[4]


def load_env(*, root: Path | None = None, filename: str = ".env") -> dict[str, str]:
    """Set any unset vars from ``<root>/.env``. Returns what it applied.

    A variable already present in the real environment always wins — exporting one is a
    deliberate override and a checked-out file must not clobber it. A missing file is
    normal, not an error: the proxy is opt-in and the unproxied path is valid.
    """
    path = Path(root or REPO_ROOT) / filename
    applied: dict[str, str] = {}
    if not path.exists():
        return applied

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)          # only the first '=' splits
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
