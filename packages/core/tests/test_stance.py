import pytest
from pydantic import ValidationError

from core.canon import Registry, resolve
from core.stance import (
    ExtractedStance,
    Stance,
    build_stance,
    parse_stances,
    stance_id,
)
from core.thesis import Source

SOURCE = Source(
    person="Benjamin Cowen",
    platform="youtube",
    url="https://youtu.be/abc123",
    published_at="2026-07-01",
    transcript_ref="youtube/abc123",
)


def _minimal(**over) -> dict:
    return {"asset": "ETH", "lean": "bullish", "rationale": "supply is drying up", **over}


# ── schema: permissive by design ────────────────────────────────────────────

def test_only_asset_lean_rationale_are_required():
    s = ExtractedStance.model_validate(_minimal())
    assert (s.asset, s.lean, s.rationale) == ("ETH", "bullish", "supply is drying up")
    # Everything else is genuinely optional — narrative often doesn't say.
    assert s.conviction is None
    assert s.horizon is None
    assert s.watching is None
    assert s.asset_heard is None


@pytest.mark.parametrize("missing", ["asset", "lean", "rationale"])
def test_missing_required_field_is_a_validation_error(missing):
    payload = _minimal()
    del payload[missing]
    with pytest.raises(ValidationError):
        ExtractedStance.model_validate(payload)


def test_lean_is_constrained_to_the_four_values():
    for lean in ("bullish", "bearish", "neutral", "uncertain"):
        assert ExtractedStance.model_validate(_minimal(lean=lean)).lean == lean
    with pytest.raises(ValidationError):
        ExtractedStance.model_validate(_minimal(lean="moon"))


def test_horizon_accepts_timeframes_and_none():
    for h in ("scalp", "swing", "position", "macro"):
        assert ExtractedStance.model_validate(_minimal(horizon=h)).horizon == h
    assert ExtractedStance.model_validate(_minimal(horizon=None)).horizon is None
    with pytest.raises(ValidationError):
        ExtractedStance.model_validate(_minimal(horizon="forever"))


def test_watching_carries_what_would_change_their_mind():
    s = ExtractedStance.model_validate(_minimal(watching="a weekly close under 2400"))
    assert s.watching == "a weekly close under 2400"


# ── per-item validation: one bad stance must never take its siblings ────────

def test_a_malformed_stance_is_dropped_without_losing_its_siblings():
    payload = {"stances": [
        _minimal(asset="ETH"),
        {"asset": "BTC", "lean": "moon", "rationale": "nonsense lean"},  # bad
        _minimal(asset="SOL", lean="bearish"),
    ]}
    parsed = parse_stances(payload)
    assert [s.asset for s in parsed.stances] == ["ETH", "SOL"]
    assert len(parsed.dropped) == 1


def test_dropped_stances_carry_the_raw_item_and_a_reason_so_they_can_be_logged():
    bad = {"asset": "BTC", "lean": "moon", "rationale": "nonsense lean"}
    parsed = parse_stances({"stances": [bad]})
    assert parsed.stances == []
    assert parsed.dropped[0].raw == bad
    assert parsed.dropped[0].error  # non-empty explanation
    assert "lean" in parsed.dropped[0].error


def test_every_item_malformed_yields_empty_rather_than_raising():
    parsed = parse_stances({"stances": [{"asset": "BTC"}, {"nope": 1}]})
    assert parsed.stances == []
    assert len(parsed.dropped) == 2


def test_non_dict_items_are_dropped_not_crashed_on():
    parsed = parse_stances({"stances": ["a bare string", None, _minimal()]})
    assert [s.asset for s in parsed.stances] == ["ETH"]
    assert len(parsed.dropped) == 2


@pytest.mark.parametrize("payload", [{}, {"stances": []}, {"stances": None}, None])
def test_absent_or_empty_payload_degrades_to_empty(payload):
    parsed = parse_stances(payload)
    assert parsed.stances == []
    assert parsed.dropped == []


def test_a_non_list_stances_value_degrades_rather_than_raising():
    parsed = parse_stances({"stances": "not a list"})
    assert parsed.stances == []
    assert parsed.dropped == []


# ── content-addressed ids ───────────────────────────────────────────────────

def _sid(**over) -> str:
    kw = {"asset": "ETH", "lean": "bullish", "rationale": "supply is drying up", **over}
    return stance_id("youtube/abc123", **kw)


def test_stance_id_is_prefixed_with_the_transcript_ref():
    assert _sid().startswith("youtube/abc123#")


def test_identical_content_yields_an_identical_id():
    assert _sid() == _sid()


@pytest.mark.parametrize("field", ["asset", "lean", "rationale"])
def test_changing_any_content_field_changes_the_id(field):
    assert _sid() != _sid(**{field: "something else"})


def test_a_different_transcript_yields_a_different_id():
    assert _sid() != stance_id(
        "youtube/zzz999", asset="ETH", lean="bullish", rationale="supply is drying up",
    )


def test_id_normalizes_whitespace_and_case_so_reformatting_does_not_churn_it():
    assert _sid() == _sid(rationale="  Supply   IS Drying\n Up ")


# ── build_stance ────────────────────────────────────────────────────────────

def _built(**over) -> Stance:
    return build_stance(
        ExtractedStance.model_validate(_minimal(**over)),
        source=SOURCE,
        model="claude-sonnet-5",
        extracted_at="2026-07-24T12:00:00+00:00",
    )


def test_build_stance_stamps_id_source_and_provenance():
    s = _built()
    assert s.id == _sid()
    assert s.source == SOURCE
    assert s.extraction.model == "claude-sonnet-5"
    assert s.extraction.extracted_at == "2026-07-24T12:00:00+00:00"
    assert s.schema_version


def test_build_stance_carries_the_optional_fields_through():
    s = _built(conviction="high", horizon="position", watching="a weekly close under 2400",
               asset_heard="eath")
    assert (s.conviction, s.horizon) == ("high", "position")
    assert s.watching == "a weekly close under 2400"
    assert s.asset_heard == "eath"


# ── the canon contract: Stance must work in the existing lens unchanged ─────

def test_canon_resolve_works_on_a_stance_without_modification():
    registry = Registry(
        people={"benjamin cowen": "Benjamin Cowen"},
        assets={"eth": "ETH"},
        tickers={"ETH": {"name": "Ethereum", "market_cap_rank": 2}},
    )
    r = resolve(_built(), registry)
    assert r.person_canonical == "Benjamin Cowen"
    assert r.asset_canonical == "ETH"
    assert r.asset_resolved is True
