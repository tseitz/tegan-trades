from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.stance import DroppedStance, Stance, build_stance, parse_stances
from core.thesis import Source

from brain.prompt import build_prompt

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 8192

_TOOL_NAME = "extract_stances"


class ExtractionFailed(Exception):
    """The model never returned a usable response within the retry budget.

    Note what this does NOT cover: individual stances failing validation. Those are
    dropped and reported in `StanceResult.dropped`, never raised — see `extract_stances`.
    """


@dataclass(frozen=True)
class StanceResult:
    """Both halves of the outcome. `dropped` is returned rather than logged in here so
    the pure path stays I/O-free and the CLI decides how to surface it."""
    stances: list[Stance] = field(default_factory=list)
    dropped: list[DroppedStance] = field(default_factory=list)


def _tool_schema() -> dict:
    from brain.schema import FLAT_STANCE_SCHEMA
    return {
        "name": _TOOL_NAME,
        "description": "Return the speaker's narrative stance on each asset they discuss.",
        "input_schema": FLAT_STANCE_SCHEMA,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_input(message) -> dict:
    for block in message.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise ExtractionFailed("model returned no tool_use block")


def extract_stances(
    transcript_text: str,
    source: Source,
    *,
    client=None,
    model: str = DEFAULT_MODEL,
    retries: int = 2,
    extracted_at: str | None = None,
) -> StanceResult:
    """Transcript -> narrative stances, validated per item.

    Retries cover *call-level* failures only: the subprocess dying, a malformed
    envelope, a response with no tool_use block. A stance that fails validation is
    deliberately NOT a retry trigger — partial success is success here, and re-reading
    a whole transcript to recover one bad item would burn tokens for nothing. This is
    the opposite of `distill.extract`, where batch validation makes a single bad thesis
    fatal to the whole video.
    """
    if client is None:  # pragma: no cover - constructs the real subscription-backed client
        from llm.claude_code import ClaudeCodeClient

        from brain.schema import FLAT_STANCE_SCHEMA
        client = ClaudeCodeClient(json_schema=FLAT_STANCE_SCHEMA)
    extracted_at = extracted_at or _now()
    system, user = build_prompt(transcript_text, source)

    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=[_tool_schema()],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": user}],
            )
            parsed = parse_stances(_tool_input(message))
            return StanceResult(
                stances=[
                    build_stance(s, source=source, model=model, extracted_at=extracted_at)
                    for s in parsed.stances
                ],
                dropped=parsed.dropped,
            )
        except Exception as exc:  # noqa: BLE001 - transport/shape errors are retryable
            last_exc = exc
    raise ExtractionFailed(str(last_exc)) from last_exc
