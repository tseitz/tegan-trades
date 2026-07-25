from __future__ import annotations

# Flat by hand rather than StanceExtraction.model_json_schema(), for the same reason
# distill/schema.py is: Claude Code's --json-schema validator runs in a strict
# JSON-Schema subset, and Pydantic emits constructs ($defs/$ref, anyOf-wrapped
# optionals) that it rejects. This schema only guides the model's shape; the real
# invariants are enforced afterward by core.stance.parse_stances.
#
# Two deliberate choices:
#
# 1. Only asset/lean/rationale are `required`. Advertising the optional fields in
#    `properties` without requiring them is the same idiom as `asset_heard` in
#    distill/schema.py — the model is told the field exists but is never forced to
#    supply one, which is what stops it inventing a horizon it never heard.
# 2. `conviction` and `horizon` carry NO enum here, only a nullable string type. An
#    enum on an optional field risks a strict-mode rejection of the whole call if the
#    model emits null; the valid values are stated in the prompt instead, and Pydantic
#    (`Conviction | None`, `Timeframe | None`) does the real enforcement per item.
#    Because validation is per item, a bad value costs one stance, not the batch.
FLAT_STANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "stances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string"},
                    "lean": {
                        "type": "string",
                        "enum": ["bullish", "bearish", "neutral", "uncertain"],
                    },
                    "rationale": {"type": "string"},
                    "watching": {"type": ["string", "null"]},
                    "conviction": {"type": ["string", "null"]},
                    "horizon": {"type": ["string", "null"]},
                    "asset_heard": {"type": ["string", "null"]},
                },
                "required": ["asset", "lean", "rationale"],
            },
        }
    },
    "required": ["stances"],
}
