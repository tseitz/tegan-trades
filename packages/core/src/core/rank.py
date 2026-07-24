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
    published_at: str


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


def _parse_date(value: str) -> date:
    """Tolerant ISO parse — accepts 'YYYY-MM-DD' or a full timestamp (first 10 chars)."""
    return date.fromisoformat(value[:10])


def recency_signal(published_at: str, *, newest: str, oldest: str) -> float:
    """Linear across the corpus span: oldest -> 0.0, newest -> 1.0."""
    span = (_parse_date(newest) - _parse_date(oldest)).days
    if span <= 0:
        return 1.0
    offset = (_parse_date(published_at) - _parse_date(oldest)).days
    return max(0.0, min(1.0, offset / span))


def agreement_signal(count: int, *, cap: int = 3) -> float:
    """Distinct other-person agreement, saturating at ``cap``."""
    return min(count / cap, 1.0) if cap > 0 else 0.0


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
    newest: str,
    oldest: str,
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
