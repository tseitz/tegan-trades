"""When the droplet's ``claude`` login runs out, read early enough to renew it at a keyboard.

On 2026-11-12 the stored login's refresh token lapsed at 01:02 UTC and the 11:00 nightly lost
every LLM step. The date sat in ``~/.claude/.credentials.json`` the whole time.

Two kinds of login, checked in the order ``claude`` itself prefers them:

- ``CLAUDE_CODE_OAUTH_TOKEN`` (from ``claude setup-token``) is opaque — nothing in it says when it
  expires, so the date has to be written down beside it as ``CLAUDE_CODE_OAUTH_TOKEN_EXPIRES``.
- otherwise the ``/login`` credentials file, whose ``refreshTokenExpiresAt`` is epoch ms.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
WARN_DAYS = 14
BLOCK_DAYS = 3


@dataclass(frozen=True)
class Expiry:
    on: date | None
    source: str
    problem: str | None = None


def read_expiry(env: Mapping[str, str], credentials: Path = CREDENTIALS) -> Expiry:
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raw = env.get("CLAUDE_CODE_OAUTH_TOKEN_EXPIRES", "").strip()
        if not raw:
            return Expiry(None, "token", "CLAUDE_CODE_OAUTH_TOKEN is set but "
                          "CLAUDE_CODE_OAUTH_TOKEN_EXPIRES is not — add its date (YYYY-MM-DD, "
                          "a year after `claude setup-token`) to .env")
        try:
            return Expiry(date.fromisoformat(raw), "token")
        except ValueError:
            return Expiry(None, "token", f"CLAUDE_CODE_OAUTH_TOKEN_EXPIRES={raw!r} is not "
                          "YYYY-MM-DD")
    try:
        oauth = json.loads(credentials.read_text(encoding="utf-8"))["claudeAiOauth"]
        ms = int(oauth["refreshTokenExpiresAt"])
    except FileNotFoundError:
        return Expiry(None, "login", f"no claude login found ({credentials} missing, "
                      "CLAUDE_CODE_OAUTH_TOKEN unset)")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Expiry(None, "login", f"{credentials} unreadable for its expiry: "
                      f"{type(exc).__name__}: {exc}")
    return Expiry(datetime.fromtimestamp(ms / 1000, UTC).date(), "login")


def verdict(expiry: Expiry, today: date) -> tuple[str, str] | None:
    """``(level, message)`` with level ``warn`` or ``block``, or None when nothing is due."""
    if expiry.problem:
        return "warn", f"claude auth — {expiry.problem}"
    assert expiry.on is not None
    left = (expiry.on - today).days
    if left > WARN_DAYS:
        return None
    renew = ("`claude setup-token`, then update CLAUDE_CODE_OAUTH_TOKEN and _EXPIRES in .env"
             if expiry.source == "token" else "`claude setup-token` (lasts a year) or "
             "`claude /login`")
    when = f"expired {expiry.on}" if left < 0 else f"expires {expiry.on} ({left}d)"
    return ("block" if left <= BLOCK_DAYS else "warn"), f"claude auth {when} — run {renew}"
