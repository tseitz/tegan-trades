from types import SimpleNamespace

import pytest

from core.rank import (
    DEFAULT_WEIGHTS,
    RankWeights,
    agreement_signal,
    asset_rank_signal,
    build_agreement_index,
    confidence_signal,
    conviction_signal,
    corpus_span,
    recency_signal,
    score,
)


def _thesis(*, conviction="med", confidence=0.8, direction="long", published_at="2025-06-01"):
    return SimpleNamespace(
        conviction=conviction,
        direction=direction,
        source=SimpleNamespace(published_at=published_at),
        extraction=SimpleNamespace(confidence=confidence),  # confidence lives under .extraction
    )


def _resolved(*, asset="BTC", person="TraderMayne", resolved=True, rank=1):
    return SimpleNamespace(
        asset_canonical=asset,
        person_canonical=person,
        asset_resolved=resolved,
        asset_rank=rank,
    )


# ── individual signals ──────────────────────────────────────────────────────

def test_conviction_signal_maps_tiers():
    assert conviction_signal(_thesis(conviction="low")) == pytest.approx(0.33)
    assert conviction_signal(_thesis(conviction="med")) == pytest.approx(0.66)
    assert conviction_signal(_thesis(conviction="high")) == pytest.approx(1.0)


def test_confidence_signal_passes_through():
    assert confidence_signal(_thesis(confidence=0.42)) == pytest.approx(0.42)


def test_recency_signal_linear_across_corpus_span():
    # oldest -> 0, newest -> 1, midpoint -> 0.5
    assert recency_signal("2025-01-01", newest="2025-01-11", oldest="2025-01-01") == pytest.approx(0.0)
    assert recency_signal("2025-01-11", newest="2025-01-11", oldest="2025-01-01") == pytest.approx(1.0)
    assert recency_signal("2025-01-06", newest="2025-01-11", oldest="2025-01-01") == pytest.approx(0.5)


def test_recency_signal_single_day_corpus_is_full():
    # newest == oldest must not divide by zero
    assert recency_signal("2025-01-01", newest="2025-01-01", oldest="2025-01-01") == pytest.approx(1.0)


def test_recency_signal_tolerates_full_timestamp():
    assert recency_signal("2025-01-06T12:00:00Z", newest="2025-01-11", oldest="2025-01-01") == pytest.approx(0.5)


def test_recency_signal_undated_thesis_scores_zero():
    # An undated thesis can't claim recency, but must never crash the ranker.
    # channel.hydrate models published_at as str|None, so blanks are a real input.
    assert recency_signal("", newest="2025-01-11", oldest="2025-01-01") == pytest.approx(0.0)
    assert recency_signal(None, newest="2025-01-11", oldest="2025-01-01") == pytest.approx(0.0)
    assert recency_signal("not-a-date", newest="2025-01-11", oldest="2025-01-01") == pytest.approx(0.0)


def test_recency_signal_undated_corpus_span_is_full():
    # No usable span (every thesis undated) degrades to the single-day case, not a crash.
    assert recency_signal("2025-01-06", newest="", oldest="") == pytest.approx(1.0)


def test_corpus_span_ignores_undated_entries():
    # One blank must not poison the span: min() over raw strings would make '' the oldest.
    assert corpus_span(["2025-06-01", "", "2025-01-01", None]) == ("2025-06-01", "2025-01-01")


def test_corpus_span_all_undated_is_none():
    assert corpus_span(["", None]) is None


def test_score_survives_an_undated_thesis():
    thesis = _thesis(published_at="")
    s = score(thesis, _resolved(), build_agreement_index([]), newest="2025-01-11", oldest="2025-01-01")
    assert 0.0 <= s <= 1.0


def test_agreement_signal_never_saturates_so_more_voices_always_rank_higher():
    """It used to clamp at ``cap``, which pinned it at 1.0 for 12 of 13 daily setup rows while
    recorded counts ran to 12 — at n>=3 the term carried no information at all (§11). The
    hyperbola is the same shape ``freshness_signal`` and ``approach_to`` already use."""
    assert agreement_signal(0) == pytest.approx(0.0)
    assert agreement_signal(3) == pytest.approx(0.5)      # cap is now the half-way point
    assert agreement_signal(1, cap=2) == pytest.approx(1 / 3)

    counts = [1, 2, 3, 4, 6, 8, 12]
    signals = [agreement_signal(c) for c in counts]
    assert signals == sorted(signals)
    assert len(set(signals)) == len(counts)               # every count is distinguishable
    assert all(s < 1.0 for s in signals)


def test_agreement_signal_with_a_meaningless_cap_is_zero_not_a_division_error():
    assert agreement_signal(5, cap=0) == pytest.approx(0.0)


def test_asset_rank_signal_crypto_curve():
    assert asset_rank_signal(1, resolved=True) == pytest.approx(1.0)
    assert asset_rank_signal(1000, resolved=True) == pytest.approx(0.0, abs=2e-3)  # 1-999/1000
    mid = asset_rank_signal(500, resolved=True)
    assert 0.0 < mid < 1.0


def test_asset_rank_signal_resolved_without_rank_is_neutral():
    # curated non-crypto (stock/index) — resolved but no CoinGecko rank
    assert asset_rank_signal(None, resolved=True) == pytest.approx(0.5)


def test_asset_rank_signal_unresolved_is_zero():
    assert asset_rank_signal(None, resolved=False) == pytest.approx(0.0)


# ── agreement index ─────────────────────────────────────────────────────────

def test_agreement_index_counts_distinct_persons_excluding_self():
    idx = build_agreement_index([
        ("BTC", "long", "TraderMayne"),
        ("BTC", "long", "Cowen"),
        ("BTC", "long", "Cowen"),          # duplicate person — counts once
        ("BTC", "short", "PentosH"),       # different direction
        ("ETH", "long", "Cowen"),          # different asset
    ])
    # For a Mayne BTC-long thesis: only Cowen also agrees -> 1
    assert idx.count_for("BTC", "long", "TraderMayne") == 1
    # For a Cowen BTC-long thesis: only Mayne (Cowen excluded) -> 1
    assert idx.count_for("BTC", "long", "Cowen") == 1
    # Nobody else on ETH long
    assert idx.count_for("ETH", "long", "Cowen") == 0
    # Unknown pair
    assert idx.count_for("SOL", "long", "Cowen") == 0


# ── composite score ─────────────────────────────────────────────────────────

def test_score_is_weighted_sum_of_signals():
    thesis = _thesis(conviction="high", confidence=1.0, direction="long", published_at="2025-01-11")
    resolved = _resolved(asset="BTC", person="TraderMayne", resolved=True, rank=1)
    idx = build_agreement_index([("BTC", "long", "TraderMayne"), ("BTC", "long", "Cowen")])
    s = score(thesis, resolved, idx, newest="2025-01-11", oldest="2025-01-01")
    # all signals maxed except agreement — 1 other voice, so 1/(1+3) = 0.25 on the hyperbola
    # that replaced the old min(1/3, 1.0) clamp (§11)
    expected = (0.30 * 1.0 + 0.25 * 1.0 + 0.20 * 1.0
                + 0.15 * 0.25 + 0.10 * 1.0)
    assert s == pytest.approx(expected)


def test_score_respects_weight_override():
    thesis = _thesis(conviction="high", confidence=0.0, published_at="2025-01-11")
    resolved = _resolved(rank=1)
    idx = build_agreement_index([])
    weights = RankWeights(conviction=1.0, confidence=0.0, recency=0.0, agreement=0.0, asset_rank=0.0)
    s = score(thesis, resolved, idx, weights=weights, newest="2025-01-11", oldest="2025-01-01")
    assert s == pytest.approx(1.0)   # only conviction (high=1.0) counts


def test_score_is_deterministic():
    thesis = _thesis()
    resolved = _resolved()
    idx = build_agreement_index([("BTC", "long", "Cowen")])
    a = score(thesis, resolved, idx, newest="2025-06-10", oldest="2025-05-01")
    b = score(thesis, resolved, idx, newest="2025-06-10", oldest="2025-05-01")
    assert a == b


def test_default_weights_sum_to_one():
    w = DEFAULT_WEIGHTS
    assert (w.conviction + w.confidence + w.recency + w.agreement + w.asset_rank) == pytest.approx(1.0)
