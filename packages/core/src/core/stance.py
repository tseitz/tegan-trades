"""Context-tier schema — narrative *stance*, as opposed to the Calls tier's discrete
`TradeThesis`. Where a thesis is "long ETH, entry 2400, invalidation 2200", a stance is
"leaning bullish because supply is drying up, watching for a weekly close under 2400".

`architecture.md` §D specified both tiers; Phase 2 built Calls only. Stance is what the
Brain head reads to answer "where is my roster on ETH, and where do they disagree".

Deliberately **permissive**. `TradeThesis.key_levels` has `min_length=1`, and combined
with all-or-nothing batch validation that meant one bad item destroyed a whole video's
extraction (13 such failures in the Phase-2 re-distill). Narrative stance has no
equivalent hard invariant, so only `asset`, `lean` and `rationale` are required and
`parse_stances` validates **each item independently** — a malformed stance is dropped
and reported, never taking its siblings with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from core.thesis import Conviction, Source, Timeframe

SCHEMA_VERSION = "1"

# `uncertain` is distinct from `neutral`: neutral is a held view that price goes
# sideways; uncertain is the explicit absence of a view ("I have no idea here").
# Collapsing them would fabricate conviction the speaker never expressed.
Lean = Literal["bullish", "bearish", "neutral", "uncertain"]


class ExtractedStance(BaseModel):
    """One narrative stance the LLM found for a single (person, asset, video).

    Every field beyond the three required ones is optional *on purpose* — spoken
    commentary routinely omits horizon and conviction, and forcing the model to supply
    them would only invite invention.
    """

    asset: str
    lean: Lean
    rationale: str  # why they hold the view
    conviction: Conviction | None = None
    horizon: Timeframe | None = None  # narrative often doesn't say
    watching: str | None = None  # what would change their mind
    asset_heard: str | None = None  # verbatim ASR phrasing when the ticker was misheard


class StanceExtraction(BaseModel):
    """The tool-call payload shape. Note this model is NOT how stances are parsed in
    production — see `parse_stances`, which validates item-by-item. This exists for
    schema generation and for callers that genuinely want all-or-nothing semantics."""

    stances: list[ExtractedStance] = Field(default_factory=list)


class Provenance(BaseModel):
    """Mirrors `core.thesis.Extraction`, minus `confidence` — a narrative lean has no
    meaningful per-item confidence score to report."""

    model: str
    extracted_at: str


class Stance(BaseModel):
    """A stored stance record. Carries `.asset` and `.source` so `canon.resolve` — which
    is duck-typed on exactly those two — works on it unchanged, no adapter needed."""

    id: str
    schema_version: str = SCHEMA_VERSION
    asset: str
    lean: Lean
    rationale: str
    conviction: Conviction | None = None
    horizon: Timeframe | None = None
    watching: str | None = None
    asset_heard: str | None = None
    source: Source
    extraction: Provenance
    ext: dict = Field(default_factory=dict)


# ── per-item parsing ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DroppedStance:
    """A single item that failed validation. Kept — with the raw payload — so the
    caller can log exactly what was thrown away. Silent drops are the failure mode
    this whole module is designed against."""

    raw: object
    error: str


@dataclass(frozen=True)
class StanceParse:
    stances: list[ExtractedStance] = field(default_factory=list)
    dropped: list[DroppedStance] = field(default_factory=list)


def parse_stances(payload: dict | None) -> StanceParse:
    """Validate each stance independently; never raise.

    A missing/empty/wrong-shaped payload degrades to an empty result rather than an
    exception, matching the tolerant idiom in `rank._parse_date` and `canon.resolve`.
    An item that fails validation lands in `dropped` with its raw form and the reason.
    """
    items = (payload or {}).get("stances") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return StanceParse()

    kept: list[ExtractedStance] = []
    dropped: list[DroppedStance] = []
    for item in items:
        try:
            kept.append(ExtractedStance.model_validate(item))
        except ValidationError as exc:
            dropped.append(DroppedStance(raw=item, error=_summarize(exc)))
    return StanceParse(stances=kept, dropped=dropped)


def _summarize(exc: ValidationError) -> str:
    """One line per failed field — enough for a log to be actionable, short enough
    that a bad batch doesn't flood the run summary."""
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
        for e in exc.errors()
    )


# ── content-addressed ids ───────────────────────────────────────────────────

_ID_SEP = "\x1f"  # unit separator — cannot appear in extracted text
_ID_LEN = 12  # 48 bits; collisions are negligible even corpus-wide


def stance_id(transcript_ref: str, *, asset: str, lean: str, rationale: str) -> str:
    """Content-addressed id: ``<transcript_ref>#<hash of the stance itself>``.

    Same reasoning as `core.thesis.thesis_id` (see `abe46ac`): positional ids silently
    re-point at a different record after a `--force` re-extract, breaking anything keyed
    on them. Rewording still changes the id, which fails safe — the stance reappears as
    new rather than inheriting something said about an unrelated view.

    `conviction`/`horizon`/`watching` are deliberately **excluded** from the hash: they
    are qualifiers on a view, not the view itself, and including them would churn the id
    every time the model phrased a hedge slightly differently.
    """
    parts = [transcript_ref, asset, lean, rationale]
    # Whitespace/case normalized so trivial reformatting doesn't churn the id.
    raw = _ID_SEP.join(" ".join(str(p).split()).lower() for p in parts)
    return f"{transcript_ref}#{sha256(raw.encode('utf-8')).hexdigest()[:_ID_LEN]}"


def build_stance(
    extracted: ExtractedStance,
    *,
    source: Source,
    model: str,
    extracted_at: str,
) -> Stance:
    """Enrich an LLM-extracted stance into a stored Stance record."""
    return Stance(
        id=stance_id(
            source.transcript_ref,
            asset=extracted.asset,
            lean=extracted.lean,
            rationale=extracted.rationale,
        ),
        asset=extracted.asset,
        lean=extracted.lean,
        rationale=extracted.rationale,
        conviction=extracted.conviction,
        horizon=extracted.horizon,
        watching=extracted.watching,
        asset_heard=extracted.asset_heard,
        source=source,
        extraction=Provenance(model=model, extracted_at=extracted_at),
    )
