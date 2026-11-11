"""The one narrated section — prose over numbers Python already computed.

**The model is given counts and names, never a transcript.** ``roster.payload`` carries the
standing lean per term, who flipped, and who spoke for the first time; it carries no
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

from llm.claude_code import ClaudeCodeCallFailed

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
last week: who changed their mind (`flips`), who spoke on it for the first time
(`new_voices`), who spoke on a new horizon (`new_horizons`), and where the roster stands now.

Where it stands is split by TERM: `near` (days to ~6 weeks), `long` (months to years), `unset`
(no horizon given). `standing` has lean counts for a term only when enough people hold a
current view to make a count mean something; `voices` names who holds which lean in every
term. Views older than their horizon allows are already left out. A `crosswind` is one person
leaning one way near-term and the other way long-term.

Rules:
- ONE line per asset. Start with the ticker.
- Lead with what changed, not with where it stands. "Pierre flipped bearish to bullish" is
  the news; "near-term bullish 4 to 1" is background and only earns space if it helps.
- Counts are per term. Say which term ("near-term bearish 6 to 4, longer-term bullish 12 to
  3") and NEVER add terms together — one person can be in both.
- Never say a count rose, fell or changed. You are given where it stands now, not where it
  was; only the listed flips and new voices changed.
- Never print a count for a term that is not in `standing`. Name people instead.
- Name a crosswind only when that person is already in the line ("Mayne flipped bearish
  near-term but stays bullish longer-term").
- Name people. A flip is the highest-signal event here and it is worthless unattributed.
- Use ONLY the numbers and names given. Never add a price, a level, a reason, or a claim about
  why anyone changed their mind — you have not been told why and must not guess.
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


def call_tool(client, *, model: str, max_tokens: int, system: str, user: str, tool_name: str,
              description: str, schema: dict, extract, retries: int):
    """Forced tool call with retry and backoff. ``extract`` maps the tool input to the result
    and raises ``NarrationFailed`` for an unusable reply. Raises ``NarrationFailed`` when
    attempts run out. Shared with ``bigpicture``."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=[{"name": tool_name, "description": description, "input_schema": schema}],
                tool_choice={"type": "tool", "name": tool_name},
                messages=[{"role": "user", "content": user}],
            )
            return extract(_tool_input(message))
        except ClaudeCodeCallFailed as exc:
            # Converted because callers only survive ``NarrationFailed``; anything else kills the
            # email. Not retried: an expired login fails identically on attempt two.
            raise NarrationFailed(str(exc)) from exc
        except _RETRYABLE as exc:
            last = exc
            # Backoff, because two immediate attempts is the worst possible shape against a
            # rate limit — the second lands inside the same window as the first.
            if attempt + 1 < retries:
                time.sleep(_BACKOFF_SECONDS * (2 ** attempt))
    raise NarrationFailed(str(last)) from last


def _extract_summary(tool_input) -> str:
    lines = (tool_input or {}).get("summary") or []
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    if not cleaned:
        raise NarrationFailed("model returned no lines")
    return "\n".join(f"  {line}" for line in cleaned)


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
    return call_tool(
        client, model=model, max_tokens=MAX_TOKENS, system=system, user=user,
        tool_name=_TOOL_NAME, description="Return one plain-text line per asset that moved.",
        schema=SCHEMA, extract=_extract_summary, retries=retries,
    )
