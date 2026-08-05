"""Tests for brain.index_cli — never touch the real data/ directory or load a
real embedding model. All I/O seams (embedder, conn, transcripts_root,
stances_root) are injected.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from brain.index_cli import DEFAULT_BATCH, index_all, main
from brain.vector_store import connect, count, search
from core.canon import Registry


class _FakeEmbedder:
    """Deterministic, dependency-free fake — never loads fastembed."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim
        self.calls: list[int] = []  # batch sizes seen, for the batching test

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls.append(len(texts))
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        vectors = np.ones((len(texts), self._dim), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return (vectors / norms).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


def _write_transcript(root, platform, source_id, person, text, published_at="2026-01-01"):
    d = root / "transcripts" / platform
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{source_id}.txt").write_text(text, encoding="utf-8")
    (d / f"{source_id}.json").write_text(json.dumps({
        "platform": platform, "source_id": source_id, "person": person,
        "url": f"https://example.com/{source_id}", "published_at": published_at,
    }), encoding="utf-8")


def _write_stance_file(root, platform, source_id, assets):
    d = root / "stances" / platform
    d.mkdir(parents=True, exist_ok=True)
    stances = [
        {
            "id": f"{platform}/{source_id}#{i}",
            "schema_version": "1",
            "asset": asset,
            "lean": "bullish",
            "rationale": "r",
            "source": {
                "person": "Cowen", "platform": platform, "url": "u",
                "published_at": "2026-01-01", "transcript_ref": f"{platform}/{source_id}",
            },
            "extraction": {"model": "m", "extracted_at": "t"},
        }
        for i, asset in enumerate(assets)
    ]
    (d / f"{source_id}.json").write_text(json.dumps({
        "transcript_ref": f"{platform}/{source_id}", "schema_version": "1",
        "model": "m", "extracted_at": "t", "stances": stances,
    }), encoding="utf-8")


@pytest.fixture
def data_root(tmp_path):
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "stances").mkdir()
    return tmp_path


class TestIndexAllBasics:
    def test_indexes_transcript_and_writes_chunks(self, data_root):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world " * 50)
        conn = connect(data_root / "index.db")
        embedder = _FakeEmbedder()

        stats = index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=embedder,
        )

        assert stats["transcripts_indexed"] == 1
        assert stats["chunks_written"] > 0
        assert count(conn) == stats["chunks_written"]

    def test_indexes_multiple_transcripts(self, data_root):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        _write_transcript(data_root, "youtube", "vid2", "Cowen", "goodbye world")
        conn = connect(data_root / "index.db")

        stats = index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
        )

        assert stats["transcripts_indexed"] == 2

    def test_indexed_chunks_are_searchable(self, data_root):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        conn = connect(data_root / "index.db")
        embedder = _FakeEmbedder()

        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=embedder,
        )

        hits = search(conn, embedder.embed_query("hello world"), k=5)
        assert len(hits) > 0
        assert hits[0].person == "Cowen"


class TestAssetsFromStanceFile:
    def test_reads_assets_from_stance_file_when_present(self, data_root):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        _write_stance_file(data_root, "youtube", "vid1", ["BTC", "ETH"])
        conn = connect(data_root / "index.db")

        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
        )

        row = conn.execute("SELECT assets FROM chunks LIMIT 1").fetchone()
        assert row[0] == "BTC,ETH"

    def test_assets_are_canonicalized_before_being_stored(self, data_root):
        """The retrieval side canonicalizes both the query asset and the stance asset via
        `resolve_asset` (retrieve.py:157,162), but the index stored `s.asset` RAW — so a
        chunk tagged 'Gold' never matched a query canonicalized to 'GOLD'
        (vector_store.py:150 does a LIKE on the literal column). Measured on the real
        corpus: 13 collision groups incl. GOLD/Gold, SILVER/Silver, ALTCOINS/altcoins.
        This was invisible only because 98% of chunks had EMPTY assets and the pre-filter
        fails open on empty — as the column populates, those chunks become selectively
        unreachable instead."""
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        _write_stance_file(data_root, "youtube", "vid1", ["Gold", "btc"])
        conn = connect(data_root / "index.db")
        registry = Registry(assets={"gold": "GOLD", "btc": "BTC"})

        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(), registry=registry,
        )

        row = conn.execute("SELECT assets FROM chunks LIMIT 1").fetchone()
        assert row[0] == "GOLD,BTC"

    def test_canonicalization_dedupes_labels_that_collapse_to_one_asset(self, data_root):
        """'Gold' and 'GOLD' in the same transcript must not produce a duplicated column."""
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        _write_stance_file(data_root, "youtube", "vid1", ["Gold", "GOLD"])
        conn = connect(data_root / "index.db")

        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
            registry=Registry(assets={"gold": "GOLD"}),
        )

        row = conn.execute("SELECT assets FROM chunks LIMIT 1").fetchone()
        assert row[0] == "GOLD"

    def test_unresolvable_asset_is_kept_verbatim_not_dropped(self, data_root):
        """An unknown label must still be indexed — `resolve_asset` returns it unchanged
        and the chunk stays reachable. Dropping it would silently orphan evidence."""
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        _write_stance_file(data_root, "youtube", "vid1", ["WEIRDTHING"])
        conn = connect(data_root / "index.db")

        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(), registry=Registry(),
        )

        row = conn.execute("SELECT assets FROM chunks LIMIT 1").fetchone()
        assert row[0] == "WEIRDTHING"

    def test_missing_stance_file_yields_empty_assets_not_an_error(self, data_root):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        conn = connect(data_root / "index.db")

        stats = index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
        )

        assert stats["transcripts_indexed"] == 1
        row = conn.execute("SELECT assets FROM chunks LIMIT 1").fetchone()
        assert row[0] == ""


class TestLimit:
    def test_limit_caps_transcripts_processed(self, data_root):
        for i in range(5):
            _write_transcript(data_root, "youtube", f"vid{i}", "Cowen", "text " * 10)
        conn = connect(data_root / "index.db")

        stats = index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(), limit=2,
        )

        assert stats["transcripts_indexed"] == 2


class TestRebuild:
    def test_rebuild_clears_store_before_indexing(self, data_root):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        conn = connect(data_root / "index.db")
        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
        )
        before = count(conn)
        assert before > 0

        stats = index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(), rebuild=True,
        )

        # Rebuilt from scratch: same single transcript re-indexed, not doubled.
        assert count(conn) == stats["chunks_written"] == before

    def test_without_rebuild_reindexing_is_idempotent_not_additive(self, data_root):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        conn = connect(data_root / "index.db")
        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
        )
        first_count = count(conn)

        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
        )

        assert count(conn) == first_count


class TestBatching:
    def test_embeds_in_batches_of_given_size(self, data_root):
        # Long enough text to produce several chunks well past one batch of 2.
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "word " * 3000)
        conn = connect(data_root / "index.db")
        embedder = _FakeEmbedder()

        index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=embedder, batch_size=2,
        )

        assert len(embedder.calls) > 1
        assert all(c <= 2 for c in embedder.calls)

    def test_default_batch_size_constant(self):
        assert DEFAULT_BATCH == 64


class TestEmptyTranscript:
    def test_transcript_with_no_content_produces_no_chunks_but_still_counts(self, data_root):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "   \n  ")
        conn = connect(data_root / "index.db")

        stats = index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
        )

        assert stats["transcripts_indexed"] == 1
        assert stats["chunks_written"] == 0


class TestMissingTxtFile:
    def test_sidecar_without_txt_file_is_skipped_not_fatal(self, data_root):
        d = data_root / "transcripts" / "youtube"
        d.mkdir(parents=True)
        (d / "vid1.json").write_text(json.dumps({
            "platform": "youtube", "source_id": "vid1", "person": "Cowen",
            "url": "u", "published_at": "2026-01-01",
        }), encoding="utf-8")
        conn = connect(data_root / "index.db")

        stats = index_all(
            transcripts_root=data_root / "transcripts",
            stances_root=data_root / "stances",
            conn=conn, embedder=_FakeEmbedder(),
        )

        assert stats["chunks_skipped"] == 1
        assert stats["transcripts_indexed"] == 0


class TestCliMain:
    def test_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "brain-index" in out

    def test_main_runs_end_to_end_with_injected_seams(self, data_root, monkeypatch, capsys):
        _write_transcript(data_root, "youtube", "vid1", "Cowen", "hello world")
        conn = connect(data_root / "index.db")
        embedder = _FakeEmbedder()

        import brain.index_cli as index_cli_mod
        monkeypatch.setattr(index_cli_mod, "TRANSCRIPTS_ROOT", data_root / "transcripts")
        monkeypatch.setattr(
            index_cli_mod.store_mod, "DATA_ROOT", data_root / "stances"
        )
        monkeypatch.setattr(index_cli_mod.store, "connect", lambda path=None: conn)
        monkeypatch.setattr(index_cli_mod, "FastEmbedder", lambda **kw: embedder)

        code = main([])

        assert code == 0
        out = capsys.readouterr().out
        assert "transcripts indexed" in out
        assert count(conn) > 0
