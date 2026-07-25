from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

Domain = Literal["crypto", "stock", "macro"]
Direction = Literal["long", "short", "neutral"]
Timeframe = Literal["scalp", "swing", "position", "macro"]
Conviction = Literal["low", "med", "high"]


class Quote(BaseModel):
    text: str
    timestamp: str | None = None  # v1: always None — stored transcripts are flat text


class _Extracted(BaseModel):
    """Content fields the LLM produces for a single call (pre-enrichment)."""
    domain: Domain
    asset: str
    asset_heard: str | None = None  # verbatim ASR phrasing when the ticker was misheard/guessed
    direction: Direction
    timeframe: Timeframe
    conviction: Conviction
    summary: str
    catalyst: str | None = None
    quotes: list[Quote] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class TradeThesis(_Extracted):
    """A specific call. Distinguished from a lean by having a stated invalidation, not by levels.

    ``key_levels`` used to require at least one entry, and that constraint manufactured data:
    measured on the live corpus, **388 of 1,621 readings (24%) carried levels only *behind*
    entry** — invalidations and stops stuffed into the field to satisfy the schema — and it
    rejected 13 whole-video extractions outright during a re-distill because one call in each
    had no number attached.

    What these transcripts reliably contain is sentiment and conviction; levels are a bonus when
    someone happens to state one. So levels are now optional and the prompt forbids inventing
    them. ``invalidation`` stays required, because free text can say "wrong if the trend breaks"
    honestly where a fabricated float cannot.
    """
    thesis_type: Literal["trade"] = "trade"
    invalidation: str                                   # required — ICT "know your exit"
    key_levels: list[float] = Field(default_factory=list)


class MacroLeanThesis(_Extracted):
    thesis_type: Literal["macro_lean"] = "macro_lean"
    invalidation: str | None = None
    key_levels: list[float] = Field(default_factory=list)


# Discriminated union: thesis_type selects the model, so trade-only invariants
# are enforced by the type system, not runtime branching.
ExtractedThesis = Annotated[
    Union[TradeThesis, MacroLeanThesis],
    Field(discriminator="thesis_type"),
]


class ThesisExtraction(BaseModel):
    """The tool-call payload the LLM returns for one transcript."""
    theses: list[ExtractedThesis] = Field(default_factory=list)


class Source(BaseModel):
    person: str
    platform: str
    url: str
    published_at: str
    transcript_ref: str


class Extraction(BaseModel):
    model: str
    confidence: float
    extracted_at: str


class Thesis(BaseModel):
    id: str
    schema_version: str = SCHEMA_VERSION
    thesis_type: Literal["trade", "macro_lean"]
    domain: Domain
    asset: str
    asset_heard: str | None = None  # verbatim ASR phrasing when the ticker was misheard/guessed
    direction: Direction
    timeframe: Timeframe
    conviction: Conviction
    summary: str
    catalyst: str | None = None
    invalidation: str | None = None
    key_levels: list[float] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    source: Source
    extraction: Extraction
    status: Literal["raw", "reviewed", "promoted", "acted", "archived"] = "raw"
    ext: dict = Field(default_factory=dict)


_ID_SEP = "\x1f"   # unit separator — cannot appear in extracted text
_ID_LEN = 12       # 48 bits; collisions are negligible even corpus-wide


def thesis_id(transcript_ref: str, *, thesis_type: str, asset: str, direction: str,
              timeframe: str, summary: str) -> str:
    """Content-addressed id: ``<transcript_ref>#<hash of the call itself>``.

    Ids were positional (``ref#0``), but a ``--force`` re-distill re-extracts from scratch
    and index 0 is routinely a *different* call afterwards — so anything keyed on the id
    (the triage decisions sidecar) silently re-points at the wrong thesis. Hashing the
    content means an id identifies a call, not a slot.

    Rewording by the LLM still changes the id, which fails safe: the thesis reappears in
    the queue as if new, rather than inheriting a decision made about some other call.
    Identical content yields an identical id by design — that *is* the same call.
    """
    parts = [transcript_ref, thesis_type, asset, direction, timeframe, summary]
    # Whitespace/case normalized so trivial reformatting doesn't churn the id.
    raw = _ID_SEP.join(" ".join(str(p).split()).lower() for p in parts)
    return f"{transcript_ref}#{sha256(raw.encode('utf-8')).hexdigest()[:_ID_LEN]}"


def build_thesis(
    extracted: ExtractedThesis,
    *,
    source: Source,
    model: str,
    extracted_at: str,
) -> Thesis:
    """Enrich an LLM-extracted call into a stored Thesis record."""
    return Thesis(
        id=thesis_id(
            source.transcript_ref,
            thesis_type=extracted.thesis_type,
            asset=extracted.asset,
            direction=extracted.direction,
            timeframe=extracted.timeframe,
            summary=extracted.summary,
        ),
        thesis_type=extracted.thesis_type,
        domain=extracted.domain,
        asset=extracted.asset,
        asset_heard=extracted.asset_heard,
        direction=extracted.direction,
        timeframe=extracted.timeframe,
        conviction=extracted.conviction,
        summary=extracted.summary,
        catalyst=extracted.catalyst,
        invalidation=getattr(extracted, "invalidation", None),
        key_levels=getattr(extracted, "key_levels", []),
        quotes=extracted.quotes,
        source=source,
        extraction=Extraction(
            model=model, confidence=extracted.confidence, extracted_at=extracted_at,
        ),
    )
