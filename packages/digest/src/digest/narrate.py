"""The one narrated section — prose over numbers Python already computed.

**The model is given counts and names, never a transcript.** ``roster.payload`` carries lean
counts before and after, who flipped, and who spoke for the first time; it carries no
rationale text and no transcript reference. So the worst failure available here is an awkward
sentence. A wrong *number* would be a rendering bug in ``roster``, with a test behind it, not
a hallucination — which is the only arrangement under which a generated line belongs in a
digest that is read fast and trusted by default.

Everything else in this package is deterministic for the same reason. This section earns the
call because "the roster went from split to unanimous, and Pierre is the one who moved" is a
sentence, not a table, and rendering it as a table was worse.

Failure is never fatal: a dead ``claude -p`` drops the section and the rest of the digest goes
out without it. It is the least important section and the only one that can be slow, which is
why it is the one that gets dropped.
"""

from __future__ import annotations

import json
import time

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024
_TOOL_NAME = "roster_movement"

# First backoff pause; doubles per attempt. Short because the nightly is already the slowest
# thing on the machine and this is its least important section.
_BACKOFF_SECONDS = 2

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "array",
            "items": {"type": "string"},
            "description": "One line per asset that moved. Plain text, no markdown.",
        },
    },
    "required": ["summary"],
}

_SYSTEM = """You write one line per asset for a personal trading digest that is read in under
a minute, before the market opens.

You are given a JSON array. Each entry is one asset whose roster of commentators moved in the
last week: the lean counts before and after, who currently holds which lean, who changed their
mind, who spoke on it for the first time, and `stale_now`.

`stale_now` is how many of the people in `after` last spoke longer ago than their own horizon
allows — months-old views still sitting in the count. It is often a third of them.

Rules:
- ONE line per asset. Start with the ticker.
- Lead with what changed, not with the standing count. "Pierre flipped bearish to bullish" is
  the news; "4 bullish, 1 bearish" is background and only earns space if the change moved it.
- If you print a standing count and `stale_now` is not zero, you MUST say so in the same
  breath: "bearish leads 12 to 3, though 11 of those last spoke months ago". A count offered
  as current sentiment when a third of it is archaeology is the worst error you can make here.
  If you do not print the count, you do not need to mention it.
- Name people. A flip is the highest-signal event here and it is worthless unattributed.
- Use ONLY the numbers and names given. Never add a price, a level, a reason, or a claim about
  why anyone changed their mind — you have not been told why and must not guess. In particular
  never say WHICH people are the stale ones; you are given a count, not names.
- No markdown, no bullets, no preamble, no sign-off. Plain sentences.
- Under 30 words per line. This sits beside four other sections."""


class NarrationFailed(RuntimeError):
    """The call failed, timed out, or came back in the wrong shape."""


# What is worth a second attempt: transport, timeouts, and a malformed reply. Deliberately NOT
# bare ``Exception``, which is what this caught before. That retried things that cannot succeed
# on attempt two — an SDK shape change (``AttributeError``), a non-serialisable payload
# (``TypeError``), a missing ``claude`` binary (``ImportError``) — burning a second ~110s
# subprocess boot to fail identically, then collapsing the cause to a string. A permanent bug
# then reads as a quiet roster, every night, forever.
_RETRYABLE = (NarrationFailed, OSError, TimeoutError, json.JSONDecodeError)


def build_prompt(payload: list[dict]) -> tuple[str, str]:
    """System and user messages. Pure, so the prompt is inspectable without a subprocess."""
    return _SYSTEM, json.dumps(payload, indent=2)


def _tool_input(message):
    for block in message.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise NarrationFailed("model returned no tool_use block")


def narrate(payload: list[dict], *, client=None, model: str = MODEL, retries: int = 2) -> str:
    """Turn the movement payload into a few plain lines. Raises ``NarrationFailed``.

    The caller catches and drops the section — see ``cli``. Raising rather than returning an
    empty string keeps "the model had nothing to say" distinguishable from "the call broke",
    which are different facts about the night.
    """
    if not payload:
        return ""
    if client is None:  # pragma: no cover - constructs the real subscription-backed client
        from llm.claude_code import ClaudeCodeClient
        client = ClaudeCodeClient(json_schema=SCHEMA)

    system, user = build_prompt(payload)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=[{"name": _TOOL_NAME,
                        "description": "Return one plain-text line per asset that moved.",
                        "input_schema": SCHEMA}],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": user}],
            )
            lines = (_tool_input(message) or {}).get("summary") or []
            cleaned = [str(line).strip() for line in lines if str(line).strip()]
            if not cleaned:
                raise NarrationFailed("model returned no lines")
            return "\n".join(f"  {line}" for line in cleaned)
        except _RETRYABLE as exc:
            last = exc
            # Backoff, because two immediate attempts is the worst possible shape against a
            # rate limit — the second lands inside the same window as the first.
            if attempt + 1 < retries:
                time.sleep(_BACKOFF_SECONDS * (2 ** attempt))
    raise NarrationFailed(str(last)) from last
