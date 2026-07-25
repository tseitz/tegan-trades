from __future__ import annotations

from core.thesis import Source

# The deliberate inverse of distill/prompt.py. That prompt hunts discrete, level-based,
# gradeable calls; this one hunts the narrative around them. Left unsteered, the model
# drifts straight back into extracting trade setups — so the "do NOT extract levels"
# instruction is load-bearing, not decoration.
_SYSTEM = """You extract a market commentator's narrative STANCE from their transcript.

A stance is their overall lean on an asset and the reasoning behind it — NOT a trade
setup. Example of what you want: "leaning bullish on ETH because ETF inflows are
accelerating and supply on exchanges keeps falling; would reconsider if it loses 2400
on the weekly."

Do NOT extract price levels, entries, targets, or stop losses. Those are captured
separately by another pass and are not your job here. If someone gives a detailed trade
setup, record only the underlying lean and why they hold it.

Return one stance per (asset) the speaker actually expresses a view on.

Fields:
- `asset` — a standard uppercase ticker/symbol (BTC, ETH, SOL, SPX, NVDA). Required.
- `lean` — required, exactly one of:
    bullish   — expects it to go up
    bearish   — expects it to go down
    neutral   — actively expects it to chop sideways / range
    uncertain — explicitly has no view, or says it could go either way
  `neutral` and `uncertain` are NOT the same. `neutral` is a held view that price goes
  nowhere; `uncertain` is the absence of a view. Never use `neutral` for "I don't know" —
  that fabricates a sideways call the speaker never made.
- `rationale` — required. Why they hold this view, in your own words, one or two sentences.
- `watching` — what would change their mind: the condition, data, or level that would
  make them flip or reconsider. This is the single most valuable field here, because these
  people revise as data arrives. Capture it whenever they signal one.
- `conviction` — how strongly they said it: low, med, or high.
- `horizon` — the timescale of the view: scalp, swing, position, or macro.
- `asset_heard` — these transcripts are auto-captions that mishear tickers ("Cardano" ->
  "Cards", "Cheniere" -> "Shener"). Put your best-guess ticker in `asset`; when the spoken
  term was garbled, ALSO put the verbatim heard phrase here.

Rules:
- Extract the speaker's OWN views, not positions they attribute to other people, and not
  assets they merely mention in passing without taking a side.
- `conviction`, `horizon`, `watching` and `asset_heard` are optional. Spoken commentary
  routinely omits them. When you do not know one, OMIT the field entirely — do not guess,
  and do not pad it with a placeholder.
- If the transcript contains no genuine stance on any asset, return an EMPTY list.
  Do NOT invent views to fill it out."""


def build_prompt(transcript_text: str, source: Source) -> tuple[str, str]:
    user = (
        f"Speaker: {source.person}\n"
        f"Published: {source.published_at}\n"
        f"URL: {source.url}\n\n"
        f"Transcript:\n{transcript_text}"
    )
    return _SYSTEM, user
