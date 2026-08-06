"""MCP surface wiring.

The tools themselves are thin — `brain.report` and `brain.retrieve` carry the judgment
and are tested directly. What these tests pin is the wiring that a type checker can't:
that both tools are registered, that their descriptions reach the model, and that a
missing index degrades to an honest empty answer instead of raising at the server.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest
from brain import mcp_server


def _tools():
    return {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}


def test_both_tools_are_registered():
    assert set(_tools()) == {"brain_search", "brain_roster"}


def test_tool_descriptions_are_non_empty():
    """The description IS the routing signal — a model with no docstring picks the wrong
    tool, and the two here differ in corpus coverage, not just in shape."""
    for name, tool in _tools().items():
        assert tool.description and len(tool.description) > 80, name


def test_search_advertises_the_query_should_be_natural_language():
    """Keyword-style input retrieves measurably worse against a sentence embedder, and
    a model will default to keywords unless told otherwise."""
    assert "natural language" in _tools()["brain_search"].description.lower()


def test_instructions_state_the_coverage_asymmetry():
    """Fully indexed vs. far fewer extracted is the single fact that decides whether an
    empty roster answer means "nobody said anything" or "extraction hasn't run".

    Asserts the asymmetry is *stated*, not that any particular number appears — the older
    version pinned the literal "666" and so passed happily while that number went stale.
    """
    text = mcp_server.build_instructions()
    assert "brain_roster" in text
    assert "structured stances cover" in text
    assert "extraction hasn't run" in text


def test_missing_index_returns_no_hits_rather_than_raising(monkeypatch, tmp_path):
    """A missing index is a normal early state. The server must stay up and say so."""
    from brain import vector_store

    monkeypatch.setattr(vector_store, "DB_PATH", tmp_path / "absent.db")
    assert mcp_server._search("anything", k=5, person=None, since=None) == []


def test_search_tool_reports_an_empty_index_honestly(monkeypatch, tmp_path):
    from brain import vector_store

    monkeypatch.setattr(vector_store, "DB_PATH", tmp_path / "absent.db")
    out = mcp_server.brain_search("what is a judas swing")
    assert "No passages" in out
    assert "do not fill the gap from outside knowledge" in out


def test_empty_index_does_not_construct_the_embedder(monkeypatch, tmp_path):
    """Loading the ONNX model to search a database that isn't there wastes seconds on
    every call in a fresh checkout."""
    from brain import vector_store

    monkeypatch.setattr(vector_store, "DB_PATH", tmp_path / "absent.db")
    monkeypatch.setattr(mcp_server, "_embed",
                        lambda _q: pytest.fail("embedder built for a missing index"))
    assert mcp_server._search("anything", k=5, person=None, since=None) == []


def test_roster_without_stances_distinguishes_gap_from_silence(monkeypatch):
    monkeypatch.setattr("brain.stance_store.load_all_stances", list)
    out = mcp_server.brain_roster("ETH")
    assert "coverage gap" in out
    assert "brain_search" in out


class TestInstructionsAreMeasuredNotHardcoded:
    """The advertised coverage was a literal string ("666 videos, 18,108 indexed chunks")
    and went stale as the corpus grew to 918 — the server kept claiming full coverage while
    the index was 197 transcripts behind. These pin it to what is actually on disk."""

    def test_counts_come_from_the_index_not_a_constant(self, monkeypatch):
        import brain.mcp_server as mod
        monkeypatch.setattr(mod, "corpus_counts", lambda: (918, 22643, 455))

        text = mod.build_instructions()

        assert "918 videos" in text
        assert "22,643 indexed chunks" in text
        assert "918/918 transcripts" in text
        assert "455" in text

    def test_an_unreadable_index_does_not_stop_the_server_starting(self, monkeypatch):
        """An unbuilt or corrupt index must degrade to zeros, not raise at import time —
        the server has to come up in order to report that it has nothing."""
        from brain import vector_store

        def _boom(*a, **kw):
            raise sqlite3.DatabaseError("file is not a database")

        monkeypatch.setattr(vector_store, "connect", _boom)

        import brain.mcp_server as mod
        transcripts, chunks, _ = mod.corpus_counts()

        assert (transcripts, chunks) == (0, 0)
