"""Narrative synthesis over an already-computed `RosterView`.

The division of labour here is the whole design: **the counts are arithmetic, done in
`retrieve.summarize_split`, and handed to the model as fact.** The model's job is to
explain the disagreement and pick supporting quotes — never to tally. A model asked to
count across twelve stances will occasionally miscount, and a confidently wrong "5
bullish, 2 bearish" is worse than no answer at all.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from brain.retrieve import LEANS, RosterView

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096
_TOOL_NAME = "answer"

SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "published_at": {"type": ["string", "null"]},
                    "quote": {"type": "string"},
                },
                "required": ["person", "quote"],
            },
        },
    },
    "required": ["answer"],
}

_SYSTEM = """You explain where a curated roster of market commentators currently stands
on an asset.

The split has ALREADY been counted for you and is given below. Do NOT recount, re-derive,
or contradict those numbers — restate them exactly as given. Your job is the part
arithmetic cannot do: explain WHY they disagree, what the disagreement hinges on, and
what would resolve it.

Write for someone who follows these people and wants the state of play in a few
sentences. Be concrete: name who holds what and on what reasoning.

Rules:
- Ground every claim in the stances and evidence provided. Do not add outside knowledge
  about the asset, and do not offer your own market opinion.
- A feed marked as multi-author is ONE feed with several speakers. Never describe it as
  multiple independent voices agreeing.
- Quotes must be verbatim from the evidence passages provided. If there are no evidence
  passages, return an empty citations list rather than inventing quotes.
- Where someone has changed their lean, say so and say when.
- If the roster is thin or one-sided, say that plainly instead of manufacturing balance."""


class SynthesisFailed(Exception):
    """The model never returned a usable answer within the retry budget."""


# Observed live on the first real run: the model closed its answer with literal
# `</answer>` and then serialised the citations as `<parameter name="citations">[...]`
# INSIDE the answer string, leaving the real citations field empty. Structured output is
# still model output — untrusted data crossing a boundary — so it gets validated here
# rather than rendered raw into the report.
_LEAK_RE = re.compile(
    r"</answer>\s*(?:<parameter\s+name=\"citations\">\s*(?P<citations>\[.*?\])\s*)?.*",
    re.DOTALL,
)


def _clean(payload: dict) -> tuple[str, list[dict]]:
    answer = str(payload.get("answer") or "")
    citations = list(payload.get("citations") or [])

    match = _LEAK_RE.search(answer)
    if match:
        answer = answer[: match.start()].rstrip()
        if not citations and match.group("citations"):
            try:
                recovered = json.loads(match.group("citations"))
                if isinstance(recovered, list):
                    citations = [c for c in recovered if isinstance(c, dict)]
            except json.JSONDecodeError:
                pass  # the answer text is the valuable part; citations are optional

    return answer.strip(), [c for c in citations if isinstance(c, dict) and c.get("quote")]


@dataclass(frozen=True)
class Synthesis:
    answer: str
    citations: list[dict] = field(default_factory=list)


def build_synthesis_prompt(view: RosterView, question: str) -> tuple[str, str]:
    split = view.split
    counts = " · ".join(f"{lean} {split.counts[lean]}" for lean in LEANS)

    parts = [f"Question: {question}", "", f"Asset: {view.asset or 'unspecified'}",
             f"COUNTED SPLIT (authoritative, do not recount): {counts}", ""]

    if view.multi_author:
        parts.append("Multi-author feeds — each is ONE voice:")
        for name, members in view.multi_author.items():
            parts.append(f"  - {name} = {', '.join(members)} speaking together")
        parts.append("")

    parts.append("Current stances:")
    for f in view.folded:
        s = f.current
        meta = ", ".join(x for x in (s.lean, s.conviction, s.horizon) if x)
        parts.append(f"  - {f.person_canonical} ({s.source.published_at or 'undated'}) "
                     f"[{meta}] on {s.asset}: {s.rationale}")
        if s.watching:
            parts.append(f"      would change their mind: {s.watching}")
        if f.restated:
            parts.append(f"      (restated {f.restated}x since {f.restated_since})")

    if view.changed:
        parts += ["", "Changed since their previous statement:"]
        for f in view.changed:
            parts.append(f"  - {f.person_canonical}: {f.previous.lean} -> {f.current.lean}")

    if view.evidence:
        parts += ["", "Evidence passages (quote verbatim from these only):"]
        for i, hit in enumerate(view.evidence, 1):
            text = " ".join(str(getattr(hit, "text", "")).split())
            parts.append(f"  [{i}] {getattr(hit, 'person', '?')} "
                         f"{getattr(hit, 'published_at', '') or ''}: {text}")
    else:
        parts += ["", "No evidence passages available — return an empty citations list."]

    return _SYSTEM, "\n".join(parts)


def _tool_input(message):
    for block in message.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise SynthesisFailed("model returned no tool_use block")


def synthesize(
    view: RosterView,
    *,
    question: str,
    client=None,
    model: str = DEFAULT_MODEL,
    retries: int = 2,
) -> Synthesis:
    if client is None:  # pragma: no cover - constructs the real subscription-backed client
        from llm.claude_code import ClaudeCodeClient
        client = ClaudeCodeClient(json_schema=SYNTHESIS_SCHEMA)

    system, user = build_synthesis_prompt(view, question)
    last: Exception | None = None
    for _ in range(retries):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=[{"name": _TOOL_NAME, "description": "Return the synthesized answer.",
                        "input_schema": SYNTHESIS_SCHEMA}],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": user}],
            )
            answer, citations = _clean(_tool_input(message) or {})
            if not answer:
                raise SynthesisFailed("model returned no answer")
            return Synthesis(answer=answer, citations=citations)
        except Exception as exc:  # noqa: BLE001 - transport/shape errors are retryable
            last = exc
    raise SynthesisFailed(str(last)) from last
