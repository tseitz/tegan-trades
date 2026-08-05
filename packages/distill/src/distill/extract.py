from __future__ import annotations

from datetime import UTC, datetime

from core.thesis import ExtractedThesis, Source, Thesis, ThesisExtraction, build_thesis
from pydantic import TypeAdapter, ValidationError

from distill.prompt import build_prompt

# Validates one thesis at a time. ``ThesisExtraction.model_validate`` validates the whole
# payload, so a single bad row raised and discarded every other thesis in the document — hit
# twice in two days on live data (Capital Flows `udGgR-6lyCQ`, krillin
# `x/LSDinmycoffee-2026-07-24`), both a `trade` with `invalidation: None`. The call is paid for
# either way, so throwing away the good rows bought nothing. Expected to bite hardest on X,
# where a chart post carrying levels but no stated stop is the ordinary case.
_THESIS = TypeAdapter(ExtractedThesis)

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
    return datetime.now(UTC).isoformat()


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
    on_drop=None,
) -> list[Thesis]:
    """Extract theses from one document.

    ``on_drop`` is called once per thesis that fails validation while others survive. It is a
    callback rather than a return value so the signature stays a plain list for the four
    existing call sites — but it is not optional in spirit: a document that quietly lost half
    its theses and one that genuinely had two are indistinguishable without it, which is the
    same reason ``core.setups`` keeps its ``NotASetup`` tally as a first-class output.
    """
    if client is None:  # pragma: no cover - constructs the real subscription-backed client
        from llm.claude_code import ClaudeCodeClient

        from distill.schema import FLAT_THESIS_SCHEMA
        client = ClaudeCodeClient(json_schema=FLAT_THESIS_SCHEMA)
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
            payload = _tool_input(message)
        except Exception as exc:  # noqa: BLE001 - transport/parse errors are retryable
            last_exc = exc
            continue

        rows = payload.get("theses") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            # Not a shape we can salvage rows out of — the model ignored the tool schema.
            last_exc = ExtractionFailed(f"tool input had no theses list: {payload!r:.200}")
            continue

        kept, dropped = _validate_each(rows, on_drop=on_drop)
        if rows and not kept:
            # Every row failed. That points at the prompt or the schema rather than at one
            # awkward call, so it keeps the retry it always had. One bad row among good ones
            # does not: the document is usable and re-asking would cost a second call to lose
            # the same row again.
            last_exc = ExtractionFailed(f"all {len(rows)} theses failed validation: {dropped[0]}")
            continue

        return [
            build_thesis(e, source=source, model=model, extracted_at=extracted_at)
            for e in kept
        ]
    raise ExtractionFailed(str(last_exc)) from last_exc


def _validate_each(rows, *, on_drop) -> tuple[list, list[str]]:
    """Validate theses one at a time, keeping what stands up and reporting what doesn't."""
    kept, dropped = [], []
    for i, row in enumerate(rows):
        try:
            kept.append(_THESIS.validate_python(row))
        except ValidationError as exc:
            reason = f"thesis[{i}] {_first_error(exc)}"
            dropped.append(reason)
            if on_drop is not None:
                on_drop(reason)
    return kept, dropped


def _first_error(exc: ValidationError) -> str:
    """The one field that failed, not pydantic's full multi-line report.

    A discriminated union reports an error per branch, so the raw message is mostly noise about
    the branch that was never going to match.
    """
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "trade")
        return f"{loc or '?'}: {err.get('msg', 'invalid')}"
    return "invalid"
