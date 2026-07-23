from core.thesis import (
    MacroLeanThesis, TradeThesis, Source, Thesis, build_thesis, SCHEMA_VERSION,
)

SOURCE = Source(
    person="Benjamin Cowen", platform="youtube",
    url="https://www.youtube.com/watch?v=abc12345678",
    published_at="2025-02-28", transcript_ref="youtube/abc12345678",
)


def test_build_thesis_enriches_trade():
    extracted = TradeThesis(
        domain="crypto", asset="ETH", direction="long", timeframe="swing",
        conviction="high", summary="Long ETH", invalidation="below 2200",
        key_levels=[2400.0], confidence=0.8,
    )
    t = build_thesis(extracted, source=SOURCE, model="claude-sonnet-5",
                     extracted_at="2026-07-23T00:00:00+00:00", index=0)
    assert isinstance(t, Thesis)
    assert t.id == "youtube/abc12345678#0"
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
                     extracted_at="2026-07-23T00:00:00+00:00", index=3)
    assert t.id == "youtube/abc12345678#3"
    assert t.thesis_type == "macro_lean"
    assert t.invalidation is None
    assert t.key_levels == []


def test_thesis_round_trips_through_json():
    extracted = MacroLeanThesis(
        domain="macro", asset="DXY", direction="short", timeframe="position",
        conviction="low", summary="DXY topping", confidence=0.4,
    )
    t = build_thesis(extracted, source=SOURCE, model="m",
                     extracted_at="2026-07-23T00:00:00+00:00", index=1)
    restored = Thesis.model_validate_json(t.model_dump_json())
    assert restored == t
