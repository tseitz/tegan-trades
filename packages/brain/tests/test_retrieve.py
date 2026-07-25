from dataclasses import dataclass

import pytest

from brain.retrieve import fold_stances, retrieve, summarize_split
from core.canon import Registry
from core.stance import build_stance, ExtractedStance
from core.thesis import Source

REGISTRY = Registry(
    people={
        "benjamin cowen": "Benjamin Cowen",
        "the defi report": "The DeFi Report (Michael Nadeau)",
        "the defi report (michael nadeau)": "The DeFi Report (Michael Nadeau)",
        "technical roundup (cryptocred + donalt)": "Technical Roundup (CryptoCred + DonAlt)",
    },
    members={"Technical Roundup (CryptoCred + DonAlt)": ["CryptoCred", "DonAlt"]},
    assets={"eth": "ETH", "ethereum": "ETH", "btc": "BTC"},
    tickers={"ETH": {"market_cap_rank": 2}, "BTC": {"market_cap_rank": 1}},
)


def stance(person, asset, lean, published, *, horizon="swing", rationale=None, watching=None):
    src = Source(
        person=person, platform="youtube", url=f"https://youtu.be/{published}",
        published_at=published, transcript_ref=f"youtube/{person[:3]}{published}",
    )
    return build_stance(
        ExtractedStance.model_validate({
            "asset": asset, "lean": lean, "horizon": horizon,
            "rationale": rationale or f"{person} on {asset} at {published}",
            **({"watching": watching} if watching else {}),
        }),
        source=src, model="m", extracted_at="2026-07-24T00:00:00+00:00",
    )


# ── folding to the current view ─────────────────────────────────────────────

def test_folds_repeat_statements_to_the_most_recent():
    stances = [
        stance("Benjamin Cowen", "ETH", "bullish", "2026-01-01"),
        stance("Benjamin Cowen", "ETH", "bullish", "2026-03-01"),
        stance("Benjamin Cowen", "ETH", "bullish", "2026-02-01"),
    ]
    folded = fold_stances(stances, REGISTRY)
    assert len(folded) == 1
    assert folded[0].current.source.published_at == "2026-03-01"
    assert folded[0].restated == 2
    assert folded[0].restated_since == "2026-01-01"


def test_lean_is_not_part_of_the_fold_key_so_a_flip_is_visible():
    """The decisive difference from `collapse_restatements`, where `direction` IS in the
    key. For a stance, the lean changing is the signal we are after — keying on it would
    hide every flip as two unrelated entries."""
    stances = [
        stance("Benjamin Cowen", "ETH", "bullish", "2026-01-01"),
        stance("Benjamin Cowen", "ETH", "bearish", "2026-03-01"),
    ]
    folded = fold_stances(stances, REGISTRY)
    assert len(folded) == 1
    assert folded[0].current.lean == "bearish"
    assert folded[0].previous is not None
    assert folded[0].previous.lean == "bullish"
    assert folded[0].flipped is True


def test_a_restatement_of_the_same_lean_is_not_a_flip():
    stances = [
        stance("Benjamin Cowen", "ETH", "bullish", "2026-01-01"),
        stance("Benjamin Cowen", "ETH", "bullish", "2026-03-01"),
    ]
    assert fold_stances(stances, REGISTRY)[0].flipped is False


def test_horizon_stays_in_the_key_because_people_hold_both_at_once():
    """Observed live: Cowen is bullish BTC.D on swing and bearish on macro in the same
    video. Folding those together would invent a flip that never happened."""
    stances = [
        stance("Benjamin Cowen", "BTC", "bullish", "2026-03-01", horizon="swing"),
        stance("Benjamin Cowen", "BTC", "bearish", "2026-03-01", horizon="macro"),
    ]
    folded = fold_stances(stances, REGISTRY)
    assert len(folded) == 2
    assert {f.current.lean for f in folded} == {"bullish", "bearish"}
    assert all(f.flipped is False for f in folded)


def test_different_people_never_fold_together():
    stances = [
        stance("Benjamin Cowen", "ETH", "bullish", "2026-03-01"),
        stance("Pierre", "ETH", "bearish", "2026-03-01"),
    ]
    assert len(fold_stances(stances, REGISTRY)) == 2


def test_person_aliases_fold_together_via_canon():
    """`The DeFi Report` and `The DeFi Report (Michael Nadeau)` are one person — the
    alias split previously fragmented agreement counts."""
    stances = [
        stance("The DeFi Report", "ETH", "bullish", "2026-01-01"),
        stance("The DeFi Report (Michael Nadeau)", "ETH", "bearish", "2026-03-01"),
    ]
    folded = fold_stances(stances, REGISTRY)
    assert len(folded) == 1
    assert folded[0].flipped is True
    assert folded[0].person_canonical == "The DeFi Report (Michael Nadeau)"


def test_asset_aliases_fold_together_via_canon():
    stances = [
        stance("Benjamin Cowen", "ethereum", "bullish", "2026-01-01"),
        stance("Benjamin Cowen", "ETH", "bearish", "2026-03-01"),
    ]
    folded = fold_stances(stances, REGISTRY)
    assert len(folded) == 1
    assert folded[0].asset_canonical == "ETH"


def test_undated_stances_are_kept_but_never_folded():
    """Without a date there is no way to say which statement is current — mirrors
    `collapse_restatements`."""
    stances = [
        stance("Benjamin Cowen", "ETH", "bullish", ""),
        stance("Benjamin Cowen", "ETH", "bearish", ""),
        stance("Benjamin Cowen", "ETH", "neutral", "2026-03-01"),
    ]
    folded = fold_stances(stances, REGISTRY)
    assert len(folded) == 3
    assert sum(1 for f in folded if f.restated) == 0


def test_folding_an_empty_corpus_returns_empty():
    assert fold_stances([], REGISTRY) == []


# ── the split ───────────────────────────────────────────────────────────────

def test_split_counts_each_person_once_not_each_statement():
    stances = [
        stance("Benjamin Cowen", "ETH", "bullish", "2026-01-01"),
        stance("Benjamin Cowen", "ETH", "bullish", "2026-02-01"),
        stance("Benjamin Cowen", "ETH", "bullish", "2026-03-01"),
        stance("Pierre", "ETH", "bearish", "2026-03-01"),
    ]
    split = summarize_split(fold_stances(stances, REGISTRY))
    assert split.counts["bullish"] == 1
    assert split.counts["bearish"] == 1
    assert split.people["bullish"] == ["Benjamin Cowen"]


def test_split_reports_all_four_leans_even_at_zero():
    split = summarize_split(fold_stances(
        [stance("Pierre", "ETH", "bullish", "2026-03-01")], REGISTRY))
    assert set(split.counts) == {"bullish", "bearish", "neutral", "uncertain"}
    assert split.counts["neutral"] == 0


def test_a_person_holding_two_horizons_is_counted_under_each_lean_once():
    stances = [
        stance("Benjamin Cowen", "ETH", "bullish", "2026-03-01", horizon="swing"),
        stance("Benjamin Cowen", "ETH", "bearish", "2026-03-01", horizon="macro"),
    ]
    split = summarize_split(fold_stances(stances, REGISTRY))
    assert split.counts["bullish"] == 1 and split.counts["bearish"] == 1
    assert split.total_people == 1  # still one voice, two views


# ── retrieval: filters + merge ──────────────────────────────────────────────

@dataclass(frozen=True)
class Hit:
    transcript_ref: str
    person: str
    published_at: str
    text: str
    score: float


def _search_fn(hits):
    captured = {}

    def fn(*, k, person, since, assets):
        captured.update(k=k, person=person, since=since, assets=assets)
        return list(hits)

    fn.captured = captured
    return fn


CORPUS = [
    stance("Benjamin Cowen", "ETH", "bullish", "2026-03-01"),
    stance("Benjamin Cowen", "BTC", "bearish", "2026-03-01"),
    stance("Pierre", "ETH", "bearish", "2026-02-01"),
    stance("Technical Roundup (CryptoCred + DonAlt)", "ETH", "bullish", "2026-01-15"),
]


def test_retrieve_filters_stances_to_the_requested_asset():
    r = retrieve(stances=CORPUS, registry=REGISTRY, asset="eth", search_fn=_search_fn([]))
    assert r.asset == "ETH"
    assert {f.person_canonical for f in r.folded} == {
        "Benjamin Cowen", "Pierre", "Technical Roundup (CryptoCred + DonAlt)"}


def test_retrieve_labels_multi_author_feeds_so_a_split_is_not_misread():
    r = retrieve(stances=CORPUS, registry=REGISTRY, asset="ETH", search_fn=_search_fn([]))
    assert r.multi_author == {
        "Technical Roundup (CryptoCred + DonAlt)": ["CryptoCred", "DonAlt"]}


def test_retrieve_passes_the_facets_through_to_the_vector_leg():
    fn = _search_fn([])
    retrieve(stances=CORPUS, registry=REGISTRY, asset="ETH", person="Pierre",
             since="2026-02-01", k=7, search_fn=fn)
    assert fn.captured["assets"] == ["ETH"]
    assert fn.captured["person"] == "Pierre"
    assert fn.captured["since"] == "2026-02-01"
    assert fn.captured["k"] == 7


def test_evidence_corroborated_by_a_stance_outranks_an_equally_scored_stray():
    """A chunk from a transcript that actually produced a stance on this asset is
    stronger evidence than one that merely mentions it — the `assets` column is a
    transcript-level pre-filter, not a claim about the chunk."""
    ref = CORPUS[0].source.transcript_ref  # Cowen's ETH transcript
    hits = [
        Hit("youtube/unrelated", "Someone", "2026-03-01", "stray mention", 0.9),
        Hit(ref, "Benjamin Cowen", "2026-03-01", "corroborated", 0.9),
    ]
    r = retrieve(stances=CORPUS, registry=REGISTRY, asset="ETH", search_fn=_search_fn(hits))
    assert r.evidence[0].text == "corroborated"


def test_higher_score_still_wins_among_equally_corroborated_evidence():
    ref = CORPUS[0].source.transcript_ref
    hits = [Hit(ref, "Benjamin Cowen", "2026-03-01", "weaker", 0.2),
            Hit(ref, "Benjamin Cowen", "2026-03-01", "stronger", 0.8)]
    r = retrieve(stances=CORPUS, registry=REGISTRY, asset="ETH", search_fn=_search_fn(hits))
    assert r.evidence[0].text == "stronger"


def test_retrieve_reports_what_changed():
    corpus = [
        stance("Benjamin Cowen", "ETH", "bullish", "2026-01-01"),
        stance("Benjamin Cowen", "ETH", "bearish", "2026-03-01"),
        stance("Pierre", "ETH", "bearish", "2026-02-01"),
    ]
    r = retrieve(stances=corpus, registry=REGISTRY, asset="ETH", search_fn=_search_fn([]))
    assert [f.person_canonical for f in r.changed] == ["Benjamin Cowen"]


def test_retrieve_without_an_asset_covers_everything():
    r = retrieve(stances=CORPUS, registry=REGISTRY, search_fn=_search_fn([]))
    assert r.asset is None
    assert len(r.folded) == 4


def test_retrieve_filters_by_person_and_since():
    r = retrieve(stances=CORPUS, registry=REGISTRY, asset="ETH", person="Benjamin Cowen",
                 search_fn=_search_fn([]))
    assert {f.person_canonical for f in r.folded} == {"Benjamin Cowen"}

    r2 = retrieve(stances=CORPUS, registry=REGISTRY, asset="ETH", since="2026-02-01",
                  search_fn=_search_fn([]))
    assert {f.person_canonical for f in r2.folded} == {"Benjamin Cowen", "Pierre"}


def test_an_unknown_asset_yields_an_empty_but_valid_result():
    r = retrieve(stances=CORPUS, registry=REGISTRY, asset="DOGE", search_fn=_search_fn([]))
    assert r.folded == []
    assert r.split.counts["bullish"] == 0
    assert r.evidence == []


def test_retrieve_works_without_a_vector_leg_at_all():
    """The structured leg must stand alone — the index may not be built yet."""
    r = retrieve(stances=CORPUS, registry=REGISTRY, asset="ETH", search_fn=None)
    assert len(r.folded) == 3
    assert r.evidence == []


def test_retrieve_is_pure_and_does_not_mutate_the_input_corpus():
    before = [s.model_copy(deep=True) for s in CORPUS]
    retrieve(stances=CORPUS, registry=REGISTRY, asset="ETH", search_fn=_search_fn([]))
    assert CORPUS == before
