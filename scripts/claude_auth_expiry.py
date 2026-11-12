"""Print ``warn|<message>`` or ``block|<message>`` when the claude login is near its end; print
nothing otherwise. Always exits 0 — the nightly turns the line into a flag, never a failed step.

    uv run python scripts/claude_auth_expiry.py
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

from core.env import load_env
from llm.auth_expiry import read_expiry, verdict


def main() -> int:
    load_env()
    found = verdict(read_expiry(os.environ), datetime.now(UTC).date())
    if found:
        print("|".join(found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
