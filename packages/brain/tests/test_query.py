from dataclasses import dataclass

import pytest
from brain import query_cli
from brain.report import format_view
from brain.retrieve import retrieve
from brain.synthesize import Synthesis, SynthesisFailed, synthesize
from core.canon import Registry
from core.stance import ExtractedStance, build_stance
from core.thesis import Source

REGISTRY = Registry(
    people={
        "benjamin cowen": "Benjamin Cowen",
        "pierre": "Pierre",
        "technical roundup (cryptocred + donalt)": "Technical Roundup (CryptoCred + DonAlt)",
    },
    members={"Technical Roundup (CryptoCred + DonAlt)": ["CryptoCred", "DonAlt"]},
    assets={"eth": "ETH", "ethereum": "ETH"},
    tickers={"ETH": {}},
)


def stance(person, asset, lean, published, *, watching=None, rationale="because reasons"):
    src = Source(person=person, platform="youtube", url=f"https://youtu.be/{published}",
                 published_at=published, transcript_ref=f"youtube/{person[:3]}{published}")
    payload = {"asset": asset, "lean": lean, "rationale": rationale,
               "conviction": "high", "horizon": "swing"}
    if watching:
        payload["watching"] = watching
    return build_stance(ExtractedStance.model_validate(payload), source=src,
                        model="m", extracted_at="2026-07-24T00:00:00+00:00")


CORPUS = [
    stance("Benjamin Cowen", "ETH", "bullish", "2026-01-01"),
    stance("Benjamin Cowen", "ETH", "bearish", "2026-03-01", watching="a weekly close under 2400"),
    stance("Pierre", "ETH", "bearish", "2026-02-01"),
    stance("Technical Roundup (CryptoCred + DonAlt)", "ETH", "bullish", "2026-01-15"),
]


def _view(corpus=CORPUS, **kw):
    return retrieve(stances=corpus, registry=REGISTRY, asset="ETH", search_fn=None, **kw)


# ── report ──────────────────────────────────────────────────────────────────

def test_report_leads_with_the_computed_split():
    out = format_view(_view())
    assert "bullish 1" in out and "bearish 2" in out


def test_report_labels_multi_author_feeds_inline():
    """Without this, "1 bullish" hides that the bullish voice is two people."""
    out = format_view(_view())
    assert "CryptoCred" in out and "DonAlt" in out


def test_report_shows_what_changed_with_the_old_and_new_lean():
    out = format_view(_view())
    assert "bullish" in out and "bearish" in out
    assert "Benjamin Cowen" in out
    assert "->" in out or "→" in out


def test_report_surfaces_dates_on_every_view():
    out = format_view(_view())
    assert "2026-03-01" in out and "2026-02-01" in out


def test_report_includes_watching_when_present():
    assert "a weekly close under 2400" in format_view(_view())


def test_report_on_an_empty_view_says_so_rather_than_printing_a_bare_zero():
    out = format_view(retrieve(stances=[], registry=REGISTRY, asset="ETH", search_fn=None))
    assert "no" in out.lower()
    assert "ETH" in out


def test_report_appends_synthesis_when_supplied():
    out = format_view(_view(), synthesis=Synthesis(answer="They are split.", citations=[]))
    assert "They are split." in out


def test_report_marks_restatement_counts():
    out = format_view(_view())
    assert "restated" in out.lower()


# ── synthesis ───────────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, payload=None, exc=None):
        self.messages = self
        self.payload = payload
        self.exc = exc
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        self.last = kwargs
        if self.exc:
            raise self.exc

        class _B:
            type = "tool_use"

            def __init__(self, d):
                self.input = d

        class _M:
            def __init__(self, d):
                self.content = [_B(d)]

        return _M(self.payload)


def test_synthesize_returns_answer_and_citations():
    client = _FakeClient({"answer": "Roster is split 1-2 on ETH.",
                          "citations": [{"person": "Pierre", "quote": "I'm out"}]})
    result = synthesize(_view(), question="where is my roster on ETH", client=client)
    assert result.answer.startswith("Roster is split")
    assert result.citations[0]["person"] == "Pierre"


def test_synthesize_hands_the_model_the_precomputed_counts():
    """The counts are arithmetic over the corpus. Letting the model derive them is how
    a synthesis silently reports the wrong split."""
    client = _FakeClient({"answer": "ok"})
    synthesize(_view(), question="q", client=client)
    sent = client.last["system"] + client.last["messages"][0]["content"]
    assert "bullish 1" in sent and "bearish 2" in sent
    assert "do not" in sent.lower() and "count" in sent.lower()


def test_synthesize_tells_the_model_about_multi_author_feeds():
    client = _FakeClient({"answer": "ok"})
    synthesize(_view(), question="q", client=client)
    sent = client.last["messages"][0]["content"]
    assert "CryptoCred" in sent


def test_synthesize_raises_a_typed_error_when_the_call_fails():
    with pytest.raises(SynthesisFailed):
        synthesize(_view(), question="q", client=_FakeClient(exc=RuntimeError("down")),
                   retries=2)


def test_synthesize_retries_before_giving_up():
    client = _FakeClient(exc=RuntimeError("down"))
    with pytest.raises(SynthesisFailed):
        synthesize(_view(), question="q", client=client, retries=3)
    assert client.calls == 3


# ── cli ─────────────────────────────────────────────────────────────────────

def _run(argv, **kw):
    """`client=None` means "build the real subscription client" (the repo-wide idiom),
    so every test here must either pass a fake client or `--no-llm`. Getting this wrong
    makes the suite fire real `claude -p` calls — it did, once, at 19s per test."""
    lines = []
    kw.setdefault("search_fn", None)
    code = query_cli.main(argv, stances=CORPUS, registry=REGISTRY,
                          out=lines.append, **kw)
    return code, "\n".join(lines)


def test_cli_resolves_the_asset_from_the_question():
    code, out = _run(["where is my roster on ethereum", "--no-llm"])
    assert code == 0
    assert "ETH" in out


def test_cli_no_llm_skips_synthesis_entirely():
    client = _FakeClient({"answer": "should not be called"})
    code, out = _run(["where is my roster on ETH", "--no-llm"], client=client)
    assert client.calls == 0
    assert "should not be called" not in out


def test_cli_explicit_asset_overrides_the_parsed_one():
    code, out = _run(["some vague question", "--asset", "ETH", "--no-llm"])
    assert "ETH" in out


def test_cli_reports_when_no_asset_could_be_resolved():
    code, out = _run(["what changed this week", "--no-llm"])
    assert code != 0 or "asset" in out.lower()


def test_cli_still_prints_the_structured_answer_when_synthesis_fails():
    """A dead LLM must not cost the user the deterministic part of the answer."""
    client = _FakeClient(exc=RuntimeError("down"))
    code, out = _run(["roster on ETH"], client=client)
    assert code == 0
    assert "bearish 2" in out
    assert "synthes" in out.lower()


def test_cli_passes_person_and_since_filters_through():
    code, out = _run(["roster on ETH", "--person", "Pierre", "--no-llm"])
    assert "Pierre" in out
    assert "Benjamin Cowen" not in out


def test_cli_never_constructs_a_real_client_when_no_llm_is_set():
    """Guards the footgun above: --no-llm must spend nothing, even with client=None."""
    import brain.synthesize as syn

    called = []
    original = syn.synthesize
    syn.synthesize = lambda *a, **k: called.append(1)
    try:
        _run(["roster on ETH", "--no-llm"])
    finally:
        syn.synthesize = original
    assert called == []


# ── staleness + split presentation (found by running it on the real corpus) ──

from datetime import date  # noqa: E402


def test_a_view_older_than_its_horizon_allows_is_marked_stale():
    """Observed live: TraderMayne's most recent BTC statement was 14 months old and
    printed identically to one from last week."""
    corpus = [stance("Pierre", "ETH", "bullish", "2025-01-01")]  # swing, very old
    out = format_view(retrieve(stances=corpus, registry=REGISTRY, asset="ETH",
                               search_fn=None), today=date(2026, 7, 24))
    assert "STALE" in out


def test_a_recent_view_is_not_marked_stale():
    corpus = [stance("Pierre", "ETH", "bullish", "2026-07-20")]
    out = format_view(retrieve(stances=corpus, registry=REGISTRY, asset="ETH",
                               search_fn=None), today=date(2026, 7, 24))
    assert "STALE" not in out


def test_staleness_thresholds_are_per_horizon():
    """A 4-month-old swing call is stale; a 4-month-old macro call is not."""
    from brain.report import STALE_AFTER_DAYS
    assert STALE_AFTER_DAYS["swing"] < STALE_AFTER_DAYS["macro"]


def test_no_staleness_is_shown_when_today_is_not_supplied():
    out = format_view(_view())
    assert "STALE" not in out


def test_split_exceeding_the_feed_count_is_explained_not_left_looking_broken():
    corpus = [
        stance("Pierre", "ETH", "bullish", "2026-03-01"),
        stance("Pierre", "ETH", "bearish", "2026-03-01"),
    ]
    corpus[1] = corpus[1].model_copy(update={"horizon": "macro"})
    out = format_view(retrieve(stances=corpus, registry=REGISTRY, asset="ETH",
                               search_fn=None))
    assert "counted under each" in out


def test_evidence_is_still_retrieved_with_no_llm():
    """Retrieval is local and free; only synthesis spends tokens. Coupling them would
    silently strip quotes from a free answer."""
    @dataclass(frozen=True)
    class H:
        transcript_ref: str
        person: str
        published_at: str
        text: str
        score: float

    hits = [H("youtube/x", "Pierre", "2026-03-01", "a real quote", 0.9)]
    lines = []
    query_cli.main(["roster on ETH", "--no-llm"], stances=CORPUS, registry=REGISTRY,
                   out=lines.append, search_fn=lambda **kw: list(hits))
    assert "a real quote" in "\n".join(lines)


# ── regression: leaked tool-call markup in the answer (seen on the first real run) ──

_LEAKED = (
    "The roster is split 6-2-3.\n"
    "</answer>\n"
    '<parameter name="citations">[{"person": "TTrades", '
    '"quote": "We are in a consolidation.", "published_at": "2026-03-31"}]'
)


def test_leaked_tool_markup_is_stripped_from_the_answer():
    client = _FakeClient({"answer": _LEAKED})
    result = synthesize(_view(), question="q", client=client)
    assert result.answer == "The roster is split 6-2-3."
    assert "</answer>" not in result.answer
    assert "<parameter" not in result.answer


def test_citations_are_recovered_from_the_leaked_block():
    client = _FakeClient({"answer": _LEAKED})
    result = synthesize(_view(), question="q", client=client)
    assert result.citations[0]["person"] == "TTrades"
    assert result.citations[0]["quote"] == "We are in a consolidation."


def test_a_clean_answer_is_left_untouched():
    client = _FakeClient({"answer": "Plain answer.",
                          "citations": [{"person": "P", "quote": "q"}]})
    result = synthesize(_view(), question="q", client=client)
    assert result.answer == "Plain answer."
    assert len(result.citations) == 1


def test_citations_without_a_quote_are_dropped():
    client = _FakeClient({"answer": "a", "citations": [{"person": "P"}, "junk",
                                                       {"person": "Q", "quote": "real"}]})
    result = synthesize(_view(), question="q", client=client)
    assert [c["person"] for c in result.citations] == ["Q"]


def test_an_answer_that_is_only_leaked_markup_is_treated_as_a_failure():
    client = _FakeClient({"answer": '</answer><parameter name="citations">[]'})
    with pytest.raises(SynthesisFailed):
        synthesize(_view(), question="q", client=client, retries=1)


def test_a_corrupt_stance_file_is_skipped_loudly_not_silently(tmp_path, capsys):
    """A silently-dropped document shrinks a consensus count with no trace."""
    from brain.stance_store import load_all_stances

    d = tmp_path / "youtube"
    d.mkdir(parents=True)
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_all_stances(tmp_path) == []
    assert "bad.json" in capsys.readouterr().err
