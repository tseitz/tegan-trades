"""Intrinsic ranking of theses — a pure, deterministic read-time lens (Phase 3b).

Like ``core.canon.resolve``, this never mutates a stored Thesis; it scores one against
corpus context passed in by the caller. Signals are intrinsic only (conviction, confidence,
recency, cross-roster agreement, asset rank) because 3b precedes per-person accuracy (3.3)
and synthesis (3.5). Every weight and curve here is a v1 best-guess, tuned via ``RankWeights``.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class RankWeights:
    conviction: float = 0.30
    confidence: float = 0.25
    recency: float = 0.20
    agreement: float = 0.15
    asset_rank: float = 0.10


DEFAULT_WEIGHTS = RankWeights()

CONVICTION_SCORE = {"low": 0.33, "med": 0.66, "high": 1.0}

# Linear decay across the CoinGecko top-N: rank 1 -> 1.0, rank RANK_CAP -> ~0.
RANK_CAP = 1000


# ── duck-typed inputs (a stored Thesis and a canon.ResolvedThesis both satisfy these) ──

class _ThesisLike(Protocol):
    conviction: str
    direction: str
    source: _SourceLike
    extraction: _ExtractionLike


class _SourceLike(Protocol):
    published_at: str | None


class _ExtractionLike(Protocol):
    confidence: float          # a stored Thesis keeps confidence under .extraction, not top-level


class _ResolvedLike(Protocol):
    asset_canonical: str
    person_canonical: str
    asset_resolved: bool
    asset_rank: int | None


# ── individual signals (each returns 0..1) ──────────────────────────────────

def conviction_signal(thesis: _ThesisLike) -> float:
    return CONVICTION_SCORE.get(thesis.conviction, 0.0)


def confidence_signal(thesis: _ThesisLike) -> float:
    return max(0.0, min(1.0, thesis.extraction.confidence))


def parse_date(value: str | None) -> date | None:
    """Tolerant ISO parse — accepts 'YYYY-MM-DD' or a full timestamp (first 10 chars).
    Returns None for missing/malformed input: ``published_at`` is genuinely optional
    upstream (``channel.hydrate`` types it ``str | None`` — yt-dlp doesn't always supply
    an upload date), so an undated thesis must degrade, never raise."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def corpus_span(published_dates: Iterable[str | None]) -> tuple[str, str] | None:
    """(newest, oldest) over the dated entries only, or None if nothing is dated.
    Undated entries must be filtered rather than min/max'd: '' sorts before every real
    ISO date, so a single blank would silently become the corpus 'oldest' and flatten
    every other thesis's recency score."""
    dated = [d for d in published_dates if d and parse_date(d) is not None]
    if not dated:
        return None
    return max(dated), min(dated)


def recency_signal(published_at: str | None, *, newest: str | None, oldest: str | None) -> float:
    """Linear across the corpus span: oldest -> 0.0, newest -> 1.0.
    An undated thesis scores 0.0 — we can't claim it's recent. An unusable corpus span
    (no dates at all, or a single day) degrades to 1.0 for everything dated."""
    at, hi, lo = parse_date(published_at), parse_date(newest), parse_date(oldest)
    if at is None:
        return 0.0
    if hi is None or lo is None:
        return 1.0
    span = (hi - lo).days
    if span <= 0:
        return 1.0
    return max(0.0, min(1.0, (at - lo).days / span))


def agreement_signal(count: float, *, cap: int = 3) -> float:
    """Distinct other-person agreement, with diminishing returns and no ceiling.

    ``cap`` is the **half-way point**, not a clamp: three voices score 0.5, and every further
    voice adds less than the one before without ever reaching 1.0.

    ``count`` is a float because the curve is defined for any non-negative real and the ``int``
    it used to be annotated as described the only caller rather than the function. §11 asks
    whether a voice should count for less as it ages, which makes the count fractional;
    ``scripts/probe_recency_weight.py`` needs to feed it exactly this transform, and a probe
    that re-implemented the formula locally would silently stop matching if it ever changed.
    Nothing about the shipped head-count path changes — an ``int`` is still an ``int`` here.

    It used to be ``min(count / cap, 1.0)``, and §11 is the account of why that failed. Recorded
    agreement counts run to 12 while the clamp bit at 3, so the term sat pinned at 1.0 for **12
    of 13 daily setup rows** — at n>=3 it carried no information whatsoever, and a §4 correlation
    against it could only ever have measured noise. A term that cannot vary cannot order
    anything, which is the same defect ``oracle.queue`` fixes one level up in the queue itself.

    The hyperbola is deliberately the shape already used by ``freshness_signal`` and
    ``approach_to`` (§19c), so "more is better, with diminishing returns" has one idiom in this
    codebase rather than three. It also keeps the thing agreement is actually claiming honest:
    the seventh voice on a zone genuinely is worth less than the second, but it is not worth
    *nothing*, which is what the clamp asserted.
    """
    return count / (count + cap) if cap > 0 else 0.0


def asset_rank_signal(rank: int | None, *, resolved: bool) -> float:
    """Crypto rank -> linear-decay confidence; resolved non-crypto -> neutral 0.5;
    unresolved -> 0.0 (we don't even know what the asset is)."""
    if not resolved:
        return 0.0
    if rank is None:
        return 0.5
    return max(0.0, 1.0 - (rank - 1) / RANK_CAP)


# ── cross-roster agreement index ────────────────────────────────────────────

@dataclass(frozen=True)
class AgreementIndex:
    """Maps ``(asset_canonical, direction)`` -> set of canonical persons who hold that view."""
    _by_view: Mapping[tuple[str, str], frozenset[str]]

    def count_for(self, asset_canonical: str, direction: str, self_person: str) -> int:
        members = self._by_view.get((asset_canonical, direction), frozenset())
        return len(members - {self_person})


def build_agreement_index(
    rows: Iterable[tuple[str, str, str]],
) -> AgreementIndex:
    """Build from ``(asset_canonical, direction, person_canonical)`` rows across the corpus."""
    acc: dict[tuple[str, str], set[str]] = defaultdict(set)
    for asset_canonical, direction, person_canonical in rows:
        acc[(asset_canonical, direction)].add(person_canonical)
    return AgreementIndex({key: frozenset(persons) for key, persons in acc.items()})


# ── composite score ─────────────────────────────────────────────────────────

def score(
    thesis: _ThesisLike,
    resolved: _ResolvedLike,
    index: AgreementIndex,
    *,
    weights: RankWeights = DEFAULT_WEIGHTS,
    newest: str | None,
    oldest: str | None,
) -> float:
    """Weighted sum of intrinsic signals. Deterministic given the same corpus context."""
    agreement_count = index.count_for(
        resolved.asset_canonical, thesis.direction, resolved.person_canonical
    )
    return (
        weights.conviction * conviction_signal(thesis)
        + weights.confidence * confidence_signal(thesis)
        + weights.recency * recency_signal(thesis.source.published_at, newest=newest, oldest=oldest)
        + weights.agreement * agreement_signal(agreement_count)
        + weights.asset_rank * asset_rank_signal(resolved.asset_rank, resolved=resolved.asset_resolved)
    )
