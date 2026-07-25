from __future__ import annotations

from core.thesis import Source

_SYSTEM = """You extract trading calls from a market analyst's transcript.

Return ONLY genuine, gradeable calls via the extract_theses tool. Two kinds:

- "trade": a specific call. REQUIRES an `invalidation` (what proves it wrong),
  which may be qualitative. Example: "Long ETH off 2400, target 3000, wrong
  below 2200." Also: "Long ETH here, wrong if we lose the weekly trend."
- "macro_lean": a directional view — still gradeable by direction over time.
  Example: "I'm bullish BTC into year-end."

Rules:
- Extract the analyst's OWN calls, not markets/prices they merely mention.
- `asset` is a standard uppercase ticker/symbol (e.g. BTC, ETH, SOL, SPX, NVDA).
- These transcripts are auto-captions that mishear tickers ("Cardano" -> "Cards",
  "Cheniere" -> "Shener"). Put your best-guess ticker in `asset`; when the spoken term
  was garbled or ambiguous, ALSO put the verbatim heard phrase in `asset_heard` and lower
  `confidence`. Leave `asset_heard` unset when the ticker was stated clearly.
- `key_levels` are OPTIONAL on both kinds, and an empty list is a perfectly good
  answer. Record a number ONLY if the analyst actually said it. NEVER invent,
  infer, round, or back out a level to fill the field. What matters most from
  these transcripts is direction and conviction; a level is a bonus when one was
  genuinely stated, and a fabricated one is worse than none.
- `conviction` reflects how strongly/hedged they said it (low | med | high).
- `confidence` is YOUR certainty (0-1) that this is a real, correctly-parsed call.
- `quotes` must be verbatim snippets from the transcript (no timestamps).
- If the transcript contains no genuine calls, return an EMPTY list. Do NOT invent."""


def build_prompt(transcript_text: str, source: Source) -> tuple[str, str]:
    user = (
        f"Analyst: {source.person}\n"
        f"Published: {source.published_at}\n"
        f"URL: {source.url}\n\n"
        f"Transcript:\n{transcript_text}"
    )
    return _SYSTEM, user
