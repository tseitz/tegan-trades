from __future__ import annotations

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
    thesis_type: Literal["trade"] = "trade"
    invalidation: str                                   # required — ICT "know your exit"
    key_levels: list[float] = Field(min_length=1)       # required, non-empty


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


def build_thesis(
    extracted: ExtractedThesis,
    *,
    source: Source,
    model: str,
    extracted_at: str,
    index: int,
) -> Thesis:
    """Enrich an LLM-extracted call into a stored Thesis record."""
    return Thesis(
        id=f"{source.transcript_ref}#{index}",
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
