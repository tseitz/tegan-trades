from core.thesis import (
    MacroLeanThesis, TradeThesis, Source, Thesis, build_thesis, thesis_id, SCHEMA_VERSION,
)

SOURCE = Source(
    person="Benjamin Cowen", platform="youtube",
    url="https://www.youtube.com/watch?v=abc12345678",
    published_at="2025-02-28", transcript_ref="youtube/abc12345678",
)


def _extracted(*, asset="BTC", direction="long", timeframe="position",
               summary="accumulate spot", conviction="high", confidence=0.9):
    return MacroLeanThesis(domain="crypto", asset=asset, direction=direction,
                           timeframe=timeframe, conviction=conviction,
                           summary=summary, confidence=confidence)


def test_thesis_id_is_content_addressed_not_positional():
    """Positional ids (`ref#0`) silently re-point at a different call after a --force
    re-distill, so triage decisions keyed on them suppress the wrong theses."""
    a = _extracted(asset="BTC", direction="long", summary="buy the dip below 60k")
    b = _extracted(asset="BTC", direction="short", summary="expects a new low under 60k")

    first = build_thesis(a, source=SOURCE, model="m", extracted_at="2026-01-01T00:00:00Z")
    # Same content, different position in the batch -> same id.
    again = build_thesis(a, source=SOURCE, model="m", extracted_at="2026-06-06T00:00:00Z")
    other = build_thesis(b, source=SOURCE, model="m", extracted_at="2026-01-01T00:00:00Z")

    assert first.id == again.id
    assert first.id != other.id
    assert first.id.startswith(f"{SOURCE.transcript_ref}#")


def test_thesis_id_ignores_whitespace_and_case_churn():
    """LLM rewording will still change the id (fails safe: the thesis re-surfaces for
    triage). Trivial reformatting shouldn't."""
    a = _extracted(summary="Buy the dip  below 60k")
    b = _extracted(summary="buy the dip below 60k")
    assert (build_thesis(a, source=SOURCE, model="m", extracted_at="x").id
            == build_thesis(b, source=SOURCE, model="m", extracted_at="x").id)


def test_thesis_id_changes_when_the_call_changes():
    base = dict(asset="BTC", direction="long", timeframe="position", summary="accumulate")
    ident = build_thesis(_extracted(**base), source=SOURCE, model="m", extracted_at="x").id
    for field, value in [("asset", "ETH"), ("direction", "short"),
                         ("timeframe", "swing"), ("summary", "sell")]:
        changed = build_thesis(_extracted(**{**base, field: value}),
                               source=SOURCE, model="m", extracted_at="x").id
        assert changed != ident, f"{field} must participate in the id"


def test_build_thesis_enriches_trade():
    extracted = TradeThesis(
        domain="crypto", asset="ETH", direction="long", timeframe="swing",
        conviction="high", summary="Long ETH", invalidation="below 2200",
        key_levels=[2400.0], confidence=0.8,
    )
    t = build_thesis(extracted, source=SOURCE, model="claude-sonnet-5",
                     extracted_at="2026-07-23T00:00:00+00:00")
    assert isinstance(t, Thesis)
    assert t.id == thesis_id("youtube/abc12345678", thesis_type="trade", asset="ETH",
                             direction="long", timeframe="swing", summary="Long ETH")
    assert t.schema_version == SCHEMA_VERSION
    assert t.thesis_type == "trade"
    assert t.invalidation == "below 2200"
    assert t.key_levels == [2400.0]
    assert t.source == SOURCE
    assert t.extraction.model == "claude-sonnet-5"
    assert t.extraction.confidence == 0.8
    assert t.extraction.extracted_at == "2026-07-23T00:00:00+00:00"
    assert t.status == "raw"
    assert t.ext == {}


def test_build_thesis_enriches_lean():
    extracted = MacroLeanThesis(
        domain="crypto", asset="BTC", direction="long", timeframe="macro",
        conviction="med", summary="Bullish BTC", confidence=0.6,
    )
    t = build_thesis(extracted, source=SOURCE, model="claude-sonnet-5",
                     extracted_at="2026-07-23T00:00:00+00:00")
    assert t.id.startswith("youtube/abc12345678#")
    assert t.thesis_type == "macro_lean"
    assert t.invalidation is None
    assert t.key_levels == []


def test_asset_heard_defaults_none():
    extracted = MacroLeanThesis(
        domain="crypto", asset="ADA", direction="long", timeframe="swing",
        conviction="med", summary="Cardano bid", confidence=0.5,
    )
    t = build_thesis(extracted, source=SOURCE, model="m",
                     extracted_at="2026-07-23T00:00:00+00:00")
    assert t.asset_heard is None


def test_asset_heard_carries_through_and_round_trips():
    # Auto-caption misheard "Cardano" as "Cards"; model kept the provenance.
    heard = TradeThesis(
        domain="crypto", asset="ADA", asset_heard="Cards", direction="long",
        timeframe="swing", conviction="high", summary="Cardano long",
        invalidation="below 0.30", key_levels=[0.45], confidence=0.7,
    )
    t = build_thesis(heard, source=SOURCE, model="m",
                     extracted_at="2026-07-23T00:00:00+00:00")
    assert t.asset_heard == "Cards"
    restored = Thesis.model_validate_json(t.model_dump_json())
    assert restored == t
    assert restored.asset_heard == "Cards"


def test_thesis_round_trips_through_json():
    extracted = MacroLeanThesis(
        domain="macro", asset="DXY", direction="short", timeframe="position",
        conviction="low", summary="DXY topping", confidence=0.4,
    )
    t = build_thesis(extracted, source=SOURCE, model="m",
                     extracted_at="2026-07-23T00:00:00+00:00")
    restored = Thesis.model_validate_json(t.model_dump_json())
    assert restored == t
