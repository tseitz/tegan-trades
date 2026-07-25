from __future__ import annotations

import json
import os
import subprocess
import sys

# Long streams can take a while for a single headless call to finish.
DEFAULT_TIMEOUT = 300.0
# Defense-in-depth circuit breaker, not a real dollar cap under subscription auth
# (see ClaudeCodeClient docstring) — just stops a runaway/looping call.
DEFAULT_MAX_BUDGET_USD = 3.0


class ClaudeCodeCallFailed(Exception):
    """The `claude -p` subprocess failed, timed out, or returned no structured output."""


class _ToolBlock:
    type = "tool_use"

    def __init__(self, data: dict):
        self.input = data


class _Message:
    def __init__(self, data: dict):
        self.content = [_ToolBlock(data)]


class ClaudeCodeClient:
    """Duck-typed like anthropic.Anthropic() — exposes `.messages.create(...)` — but
    routes extraction through `claude -p` headless mode so it bills against the Max
    subscription's included usage instead of the separate pay-per-token developer API.

    Correctness-critical: ANTHROPIC_API_KEY is stripped from the subprocess
    environment on every call, so this can never silently fall back to metered API
    billing even if that var happens to be set elsewhere in the caller's shell.
    The envelope's `total_cost_usd` is a usage-equivalent value Claude Code always
    reports (used for --max-budget-usd bookkeeping); under OAuth/subscription auth
    it reflects usage against the plan's rotating allowance, not a separate charge.
    It's surfaced to stderr for visibility, per-call.
    """

    def __init__(
        self,
        *,
        json_schema: dict,
        timeout: float = DEFAULT_TIMEOUT,
        max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
        run=subprocess.run,
    ):
        self.messages = self
        # No default — a default here would let a future caller silently get
        # thesis-shaped (or whatever-shaped) output from an unrelated extraction.
        # This codebase has been bitten repeatedly by silent-wrong-value bugs, so
        # failing loudly at construction (missing required kwarg -> TypeError) is
        # the point.
        self._json_schema = json_schema
        self._timeout = timeout
        self._max_budget_usd = max_budget_usd
        self._run = run

    def create(self, *, model: str, system: str, messages: list[dict], **_ignored) -> _Message:
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        user_content = messages[0]["content"]

        argv = [
            "claude", "-p",
            "--model", model,
            "--output-format", "json",
            "--system-prompt", system,
            "--json-schema", json.dumps(self._json_schema),
            "--tools", "",
            "--max-budget-usd", str(self._max_budget_usd),
        ]
        try:
            proc = self._run(
                argv, input=user_content, capture_output=True, text=True,
                timeout=self._timeout, env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCodeCallFailed(f"timed out after {self._timeout}s") from exc

        if proc.returncode != 0:
            # `claude -p` reports its real errors (usage cap reached, auth failure) as
            # JSON on STDOUT, not stderr. Capturing stderr alone rendered the entire
            # 2026-07-24 overnight sweep undiagnosable: 466 transcripts logged as a bare
            # `exit 1:` with nothing after the colon. Keep BOTH streams.
            detail = " | ".join(
                f"{name}: {stream[-2000:]}"
                for name, stream in (("stderr", proc.stderr), ("stdout", proc.stdout))
                if stream and stream.strip()
            )
            raise ClaudeCodeCallFailed(
                f"exit {proc.returncode}: {detail or 'no output on either stream'}")

        envelope = json.loads(proc.stdout)
        if envelope.get("is_error"):
            raise ClaudeCodeCallFailed(str(envelope.get("result")))

        cost = envelope.get("total_cost_usd") or 0.0
        if cost:
            print(
                f"[claude-code-backend] usage-equivalent cost: ${cost:.4f} "
                "(counts against Max subscription usage, not a separate charge)",
                file=sys.stderr,
            )

        structured = envelope.get("structured_output")
        if structured is None:
            raise ClaudeCodeCallFailed("no structured_output in response")
        return _Message(structured)
