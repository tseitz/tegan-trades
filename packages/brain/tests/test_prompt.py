from brain.prompt import build_prompt
from core.thesis import Source

SOURCE = Source(
    person="Benjamin Cowen",
    platform="youtube",
    url="https://youtu.be/abc123",
    published_at="2026-07-01",
    transcript_ref="youtube/abc123",
)


def _system() -> str:
    return build_prompt("some transcript", SOURCE)[0].lower()


def test_targets_narrative_stance_not_discrete_calls():
    s = _system()
    assert "stance" in s
    assert "lean" in s


def test_explicitly_steers_away_from_levels_and_entries():
    """The opposite of distill/prompt.py. Without this the model drifts back into
    extracting trade setups, which is the Calls tier's job and already done."""
    s = _system()
    assert "level" in s
    assert "entry" in s or "entries" in s


def test_asks_for_what_would_change_their_mind():
    """`watching` is the highest-value field in the schema — the machine-readable
    form of "these people adjust as data comes in"."""
    assert "change" in _system() and "mind" in _system()


def test_instructs_empty_over_invention():
    s = _system()
    assert "empty" in s
    assert "invent" in s or "fabricat" in s


def test_names_the_valid_lean_values():
    s = _system()
    for lean in ("bullish", "bearish", "neutral", "uncertain"):
        assert lean in s


def test_distinguishes_neutral_from_uncertain():
    """Collapsing them would fabricate a sideways view the speaker never held."""
    s = _system()
    assert "uncertain" in s and "neutral" in s
    # both must appear in the same explanatory sentence region, not just as bare enum values
    assert s.count("uncertain") >= 2


def test_warns_about_misheard_tickers_from_auto_captions():
    s = _system()
    assert "asset_heard" in s


def test_instructs_omission_rather_than_null_for_unknown_optionals():
    """The wire schema allows null, but omission is unambiguously safe under the
    strict --json-schema subset. Ask for omission."""
    s = _system()
    assert "omit" in s


def test_user_message_carries_the_source_context_and_transcript():
    _, user = build_prompt("the transcript body", SOURCE)
    assert "Benjamin Cowen" in user
    assert "2026-07-01" in user
    assert "https://youtu.be/abc123" in user
    assert "the transcript body" in user
