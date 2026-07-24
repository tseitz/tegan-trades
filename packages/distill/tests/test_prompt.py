from core.thesis import Source
from distill.prompt import build_prompt

SOURCE = Source(person="Benjamin Cowen", platform="youtube", url="u",
                published_at="2025-02-28", transcript_ref="youtube/abc")


def test_prompt_carries_context_and_rules():
    system, user = build_prompt("ETH looks strong above 2400.", SOURCE)
    # trade vs macro_lean distinction is explained
    assert "trade" in system and "macro_lean" in system
    # do-not-invent guardrail
    assert "empty" in system.lower()
    # per-transcript context threaded in
    assert "Benjamin Cowen" in user
    assert "2025-02-28" in user
    # the transcript body is included
    assert "ETH looks strong above 2400." in user


def test_prompt_explains_asset_heard_provenance():
    system, _ = build_prompt("x", SOURCE)
    # names the provenance field and the ticker-format expectation
    assert "asset_heard" in system
    assert "ticker" in system.lower()


def test_prompt_is_deterministic():
    assert build_prompt("x", SOURCE) == build_prompt("x", SOURCE)
