import pytest
from pydantic import ValidationError

from core.thesis import ThesisExtraction, TradeThesis, MacroLeanThesis

TRADE = {
    "thesis_type": "trade", "domain": "crypto", "asset": "ETH",
    "direction": "long", "timeframe": "swing", "conviction": "high",
    "summary": "Long ETH off 2400 support", "invalidation": "close below 2200",
    "key_levels": [2400, 3000], "confidence": 0.8,
}
LEAN = {
    "thesis_type": "macro_lean", "domain": "crypto", "asset": "BTC",
    "direction": "long", "timeframe": "macro", "conviction": "med",
    "summary": "Bullish BTC into year-end", "confidence": 0.6,
}


def test_trade_and_lean_parse_via_discriminator():
    ex = ThesisExtraction.model_validate({"theses": [TRADE, LEAN]})
    assert isinstance(ex.theses[0], TradeThesis)
    assert isinstance(ex.theses[1], MacroLeanThesis)
    assert ex.theses[0].key_levels == [2400, 3000]
    assert ex.theses[1].invalidation is None


def test_trade_requires_invalidation():
    bad = {**TRADE}
    del bad["invalidation"]
    with pytest.raises(ValidationError):
        ThesisExtraction.model_validate({"theses": [bad]})


def test_trade_requires_nonempty_key_levels():
    with pytest.raises(ValidationError):
        ThesisExtraction.model_validate({"theses": [{**TRADE, "key_levels": []}]})


def test_lean_without_levels_is_valid():
    ex = ThesisExtraction.model_validate({"theses": [LEAN]})
    assert ex.theses[0].key_levels == []


def test_empty_extraction_is_valid():
    assert ThesisExtraction.model_validate({"theses": []}).theses == []


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        ThesisExtraction.model_validate({"theses": [{**LEAN, "confidence": 1.5}]})
