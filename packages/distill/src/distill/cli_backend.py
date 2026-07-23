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

# Deliberately flat — NOT core.thesis.ThesisExtraction.model_json_schema(). Claude
# Code's --json-schema validator runs in a strict JSON-Schema subset that rejects
# Pydantic's `discriminator` keyword ("strict mode: unknown keyword: discriminator"),
# so the trade/macro_lean discriminated union can't be expressed here directly. This
# schema only guides the model's shape; the real trade-requires-invalidation+key_levels
# invariant is enforced afterward by ThesisExtraction.model_validate() in extract.py,
# which retries on violation exactly like a malformed-JSON response.
FLAT_THESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "theses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "thesis_type": {"type": "string", "enum": ["trade", "macro_lean"]},
                    "domain": {"type": "string", "enum": ["crypto", "stock", "macro"]},
                    "asset": {"type": "string"},
                    "direction": {"type": "string", "enum": ["long", "short", "neutral"]},
                    "timeframe": {"type": "string",
                                  "enum": ["scalp", "swing", "position", "macro"]},
                    "conviction": {"type": "string", "enum": ["low", "med", "high"]},
                    "summary": {"type": "string"},
                    "catalyst": {"type": ["string", "null"]},
                    "invalidation": {"type": ["string", "null"]},
                    "key_levels": {"type": "array", "items": {"type": "number"}},
                    "quotes": {
                        "type": "array",
                        "items": {"type": "object",
                                  "properties": {"text": {"type": "string"}},
                                  "required": ["text"]},
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["thesis_type", "domain", "asset", "direction", "timeframe",
                             "conviction", "summary", "confidence"],
            },
        }
    },
    "required": ["theses"],
}


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
        timeout: float = DEFAULT_TIMEOUT,
        max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
        run=subprocess.run,
    ):
        self.messages = self
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
            "--json-schema", json.dumps(FLAT_THESIS_SCHEMA),
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
            raise ClaudeCodeCallFailed(f"exit {proc.returncode}: {proc.stderr[-2000:]}")

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
