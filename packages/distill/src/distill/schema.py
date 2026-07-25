from __future__ import annotations

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
                    "asset_heard": {"type": "string"},
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
