"""One batched call per night: a single "big picture" line per roster person.

One call for everyone, not one per person — each ``claude -p`` costs ~110s of harness boot.
The payload is the in-window stances (rationale prose included) and never transcript text.
The prompt forbids adding a level that is not in it, and ``grounded`` enforces that in code:
a line holding a number absent from that person's payload is dropped.
"""

from __future__ import annotations

import json
import re

from core.stance import Stance

from digest.narrate import MAX_TOKENS, NarrationFailed, call_tool

MODEL = "claude-sonnet-5"
_TOOL_NAME = "roster_big_picture"

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"person": {"type": "string"}, "line": {"type": "string"}},
                "required": ["person", "line"],
            },
            "description": "One entry per person. Plain text, no markdown.",
        },
    },
    "required": ["lines"],
}

_SYSTEM = """You write one "big picture" line per person for a personal trading digest that is
read in under a minute.

You are given a JSON array. Each entry is one commentator and their stances from the last few
days: asset, lean, conviction, horizon, rationale, watching, published_at.

Rules:
- ONE line per person, under 25 words: their overall posture and WHY, and the one or two
  things they are watching.
- Do not list their assets. The per-asset lists print directly under your line, so naming
  more than one or two assets only repeats them.
- Use ONLY assets, levels and claims present in that person's entry. Never add a price, a
  level, a number or a reason that is not given.
- Echo each person's name exactly as given.
- No markdown, no bullets, no preamble, no sign-off."""

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class BigPictureFailed(RuntimeError):
    """The call failed, timed out, or came back in the wrong shape."""


def payload(by_person: dict[str, list[Stance]]) -> list[dict]:
    """Per-person model input. Persons with no stances are omitted."""
    return [
        {
            "person": person,
            "stances": [
                {
                    "asset": s.asset, "lean": s.lean, "conviction": s.conviction,
                    "horizon": s.horizon, "rationale": s.rationale, "watching": s.watching,
                    "published_at": s.source.published_at,
                }
                for s in stances
            ],
        }
        for person, stances in by_person.items()
        if stances
    ]


def build_prompt(payload_: list[dict]) -> tuple[str, str]:
    return _SYSTEM, json.dumps(payload_, indent=2)


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "").rstrip(".") for m in _NUMBER.findall(text)}


def grounded(lines: dict[str, str], payload_: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Keep lines whose numbers all appear in that person's payload; warn for the rest."""
    sources = {
        e["person"]: _numbers(json.dumps(e["stances"], ensure_ascii=False))
        for e in payload_
    }
    kept: dict[str, str] = {}
    warnings: list[str] = []
    for person, line in lines.items():
        invented = sorted(_numbers(line) - sources.get(person, set()))
        if invented:
            warnings.append(
                f"big picture for {person} dropped: not in their stances: {', '.join(invented)}"
            )
        else:
            kept[person] = line
    return kept, warnings


def summarize(payload_: list[dict], *, client=None, model: str = MODEL,
              retries: int = 2) -> dict[str, str]:
    """``{person: line}`` for persons in the payload. Raises ``BigPictureFailed``.

    Persons the model skipped get no entry, and persons it invented are dropped. An empty
    payload never calls the model.
    """
    if not payload_:
        return {}
    if client is None:  # pragma: no cover - constructs the real subscription-backed client
        from llm.claude_code import ClaudeCodeClient
        client = ClaudeCodeClient(json_schema=SCHEMA)

    known = {e["person"] for e in payload_}

    def extract(tool_input) -> dict[str, str]:
        items = (tool_input or {}).get("lines") or []
        out = {
            str(i["person"]).strip(): str(i["line"]).strip()
            for i in items
            if isinstance(i, dict) and str(i.get("person", "")).strip() in known
            and str(i.get("line", "")).strip()
        }
        if not out:
            raise NarrationFailed("model returned no usable lines")
        return out

    system, user = build_prompt(payload_)
    try:
        return call_tool(
            client, model=model, max_tokens=MAX_TOKENS, system=system, user=user,
            tool_name=_TOOL_NAME,
            description="Return one plain-text big-picture line per person.",
            schema=SCHEMA, extract=extract, retries=retries,
        )
    except NarrationFailed as exc:
        raise BigPictureFailed(str(exc)) from exc
