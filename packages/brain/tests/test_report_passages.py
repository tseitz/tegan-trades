"""`format_passages` — the concept-question answer shape.

A concept question ("what is a judas swing") is answered by the passages themselves,
not by a roster split. These tests pin the properties that make that output safe to
reason over: full attribution, no mid-sentence truncation, and an explicit statement
of what the score does and does not mean.
"""
from __future__ import annotations

from dataclasses import dataclass

from brain.report import format_passages


@dataclass(frozen=True)
class _Hit:
    person: str
    published_at: str | None
    text: str
    score: float
    transcript_ref: str = "ref-1"


def _hit(**kw):
    base = {"person": "TraderMayne", "published_at": "2026-04-16",
            "text": "The manipulation is the Judas Swing.", "score": 0.645}
    return _Hit(**{**base, **kw})


def test_no_hits_says_so_without_inventing_an_answer():
    out = format_passages([], query="what is a judas swing")
    assert "No passages" in out
    assert "judas swing" in out


def test_renders_attribution_for_every_passage():
    out = format_passages([_hit(), _hit(person="TTrades", published_at="2026-03-17")],
                          query="q")
    assert "TraderMayne" in out
    assert "TTrades" in out
    assert "2026-04-16" in out
    assert "2026-03-17" in out


def test_undated_passage_is_labelled_not_blank():
    """A blank date reads as "no date rendered"; it must read as "the corpus has none"."""
    out = format_passages([_hit(published_at=None)], query="q")
    assert "undated" in out


def test_passage_text_is_not_truncated():
    """Truncation is what makes a passage unquotable. A concept answer IS the passage,
    so cutting it mid-sentence defeats the entire tool."""
    long_text = "word " * 400
    out = format_passages([_hit(text=long_text)], query="q")
    assert out.count("word") == 400


def test_whitespace_is_collapsed():
    out = format_passages([_hit(text="a\n\n  b\tc")], query="q")
    assert "a b c" in out


def test_states_that_score_is_not_comparable_across_queries():
    """IMPROVEMENTS.md §8 was mis-read for months because absolute cosine scores look
    low. They are only meaningful relative to the corpus for the SAME query, and the
    consumer of this output is a model that will otherwise anchor on the number."""
    out = format_passages([_hit(score=0.645)], query="q")
    assert "not comparable across queries" in out.lower()


def test_transcript_ref_is_present_so_a_claim_can_be_traced():
    out = format_passages([_hit(transcript_ref="yt-abc123")], query="q")
    assert "yt-abc123" in out


def test_passages_are_numbered_for_citation():
    out = format_passages([_hit(), _hit(), _hit()], query="q")
    assert "[1]" in out and "[2]" in out and "[3]" in out
