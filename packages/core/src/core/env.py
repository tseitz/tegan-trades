"""Load repo-root ``.env`` into the process environment.

Every consumer of a secret — ``youtube._proxy_config`` for ``WEBSHARE_PROXY_*``,
``execution.config`` for the venue keys — reads ``os.environ`` directly, and nothing
populated it. So creating a ``.env`` (as ``.env.example`` and CLAUDE.md both invite you to)
had no effect at all: transcript fetches silently ran unproxied and every venue reported
``no_credentials`` until the caller remembered to ``source`` the file by hand. Each reader
calls this at the point it reads, so the file works the same from a shell, from ``uv run``,
and from the nightly launchd job — none of which inherit an interactive shell's exports.

Lives in ``core`` because it is the only package everything else may import; the alternative
was a copy per package, and two loaders that disagree about precedence is exactly the bug
this file exists to prevent. It reads config, like ``core.canon`` — it writes nothing.

Hand-rolled rather than depending on python-dotenv: the format we need is a handful of
``KEY=value`` lines and a dependency isn't worth it. Deliberately narrow — no interpolation,
no multiline values.
"""
from __future__ import annotations

import os
from pathlib import Path

# src/core/env.py -> src/core -> src -> core -> packages -> <repo root>
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
