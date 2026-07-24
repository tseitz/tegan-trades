from __future__ import annotations

from datetime import datetime, timezone

from core.thesis import Source, Thesis, ThesisExtraction, build_thesis
from distill.prompt import build_prompt

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 8192

_TOOL_NAME = "extract_theses"


class ExtractionFailed(Exception):
    """The model never returned a schema-valid extraction within the retry budget."""


def _tool_schema() -> dict:
    return {
        "name": _TOOL_NAME,
        "description": "Return all genuine trading calls found in the transcript.",
        "input_schema": ThesisExtraction.model_json_schema(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_input(message) -> dict:
    for block in message.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise ExtractionFailed("model returned no tool_use block")


def extract_theses(
    transcript_text: str,
    source: Source,
    *,
    client=None,
    model: str = DEFAULT_MODEL,
    retries: int = 2,
    extracted_at: str | None = None,
) -> list[Thesis]:
    if client is None:  # pragma: no cover - constructs the real subscription-backed client
        from distill.cli_backend import ClaudeCodeClient
        client = ClaudeCodeClient()
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
            extraction = ThesisExtraction.model_validate(_tool_input(message))
            return [
                build_thesis(e, source=source, model=model, extracted_at=extracted_at)
                for e in extraction.theses
            ]
        except Exception as exc:  # noqa: BLE001 - validation/parse errors are retryable
            last_exc = exc
    raise ExtractionFailed(str(last_exc)) from last_exc
