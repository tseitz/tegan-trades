"""Tests for brain.vector_store — the brute-force-cosine-over-SQLite index.

Every test uses an in-memory or tmp_path DB; none touch the real
`data/brain/index.db`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from brain.chunk import Chunk
from brain.vector_store import DB_PATH, SearchHit, clear, connect, count, search, upsert_chunks


def _chunk(ref: str, idx: int, text: str) -> Chunk:
    return Chunk(id=f"{ref}#c{idx}", transcript_ref=ref, index=idx,
                 start=idx * 10, end=idx * 10 + len(text), text=text)


def _unit_vector(seed: float, dim: int = 4) -> np.ndarray:
    v = np.array([seed, 1.0, 0.0, 0.0], dtype=np.float32)[:dim]
    return (v / np.linalg.norm(v)).astype(np.float32)


class TestDbPathResolution:
    def test_db_path_points_at_data_brain_index_db(self):
        assert DB_PATH.name == "index.db"
        assert DB_PATH.parent.name == "brain"
        assert DB_PATH == Path(__file__).resolve().parents[3] / "data" / "brain" / "index.db"


class TestConnectCreatesSchema:
    def test_connect_creates_parent_dir_and_table(self, tmp_path):
        db_path = tmp_path / "nested" / "index.db"
        conn = connect(db_path)
        assert db_path.exists()
        assert count(conn) == 0

    def test_connect_is_idempotent(self, tmp_path):
        db_path = tmp_path / "index.db"
        connect(db_path).close()
        conn2 = connect(db_path)  # should not raise on existing schema
        assert count(conn2) == 0


class TestUpsertAndCount:
    def test_upsert_inserts_rows_and_count_reflects_them(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = [_chunk("youtube/abc", 0, "hello"), _chunk("youtube/abc", 1, "world")]
        vectors = np.stack([_unit_vector(1.0), _unit_vector(2.0)])

        n = upsert_chunks(conn, chunks, person="Cowen", published_at="2026-01-01",
                          assets=["BTC"], vectors=vectors)

        assert n == 2
        assert count(conn) == 2

    def test_upsert_with_duplicate_content_addressed_id_does_not_raise(self, tmp_path):
        """Two byte-identical chunks in one transcript legitimately share an id —
        re-indexing must be idempotent, not an integrity error."""
        conn = connect(tmp_path / "index.db")
        same_id_chunks = [
            Chunk(id="youtube/abc#dup", transcript_ref="youtube/abc", index=0,
                  start=0, end=5, text="hello"),
            Chunk(id="youtube/abc#dup", transcript_ref="youtube/abc", index=1,
                  start=5, end=10, text="hello"),
        ]
        vectors = np.stack([_unit_vector(1.0), _unit_vector(1.0)])

        upsert_chunks(conn, same_id_chunks, person="Cowen", published_at="2026-01-01",
                     assets=[], vectors=vectors)

        assert count(conn) == 1  # second insert replaced the first, no crash

    def test_reindexing_same_chunks_is_idempotent(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = [_chunk("youtube/abc", 0, "hello")]
        vectors = np.stack([_unit_vector(1.0)])

        upsert_chunks(conn, chunks, person="Cowen", published_at="2026-01-01",
                      assets=["BTC"], vectors=vectors)
        upsert_chunks(conn, chunks, person="Cowen", published_at="2026-01-01",
                      assets=["BTC"], vectors=vectors)

        assert count(conn) == 1


class TestClear:
    def test_clear_empties_the_store(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = [_chunk("youtube/abc", 0, "hello")]
        upsert_chunks(conn, chunks, person="Cowen", published_at="2026-01-01",
                      assets=[], vectors=np.stack([_unit_vector(1.0)]))
        assert count(conn) == 1

        clear(conn)
        assert count(conn) == 0


class TestSearchEmptyStore:
    def test_search_on_empty_store_returns_empty_list(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        hits = search(conn, _unit_vector(1.0), k=5)
        assert hits == []


class TestSearchRanking:
    def test_search_returns_hits_sorted_by_score_descending(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = [_chunk("youtube/a", 0, "near"), _chunk("youtube/b", 0, "far")]
        near = _unit_vector(1.0)
        far = _unit_vector(-5.0)
        upsert_chunks(conn, [chunks[0]], person="Cowen", published_at="2026-01-01",
                      assets=[], vectors=np.stack([near]))
        upsert_chunks(conn, [chunks[1]], person="Cowen", published_at="2026-01-01",
                      assets=[], vectors=np.stack([far]))

        hits = search(conn, near, k=2)

        assert len(hits) == 2
        assert hits[0].chunk_id == chunks[0].id
        assert hits[0].score >= hits[1].score
        assert isinstance(hits[0], SearchHit)

    def test_search_respects_k(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = [_chunk("youtube/a", i, f"chunk{i}") for i in range(5)]
        vectors = np.stack([_unit_vector(float(i)) for i in range(5)])
        upsert_chunks(conn, chunks, person="Cowen", published_at="2026-01-01",
                      assets=[], vectors=vectors)

        hits = search(conn, _unit_vector(0.0), k=2)

        assert len(hits) == 2

    def test_search_score_is_plain_dot_product_of_normalized_vectors(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunk = _chunk("youtube/a", 0, "hello")
        v = _unit_vector(3.0)
        upsert_chunks(conn, [chunk], person="Cowen", published_at="2026-01-01",
                      assets=[], vectors=np.stack([v]))

        hits = search(conn, v, k=1)

        assert hits[0].score == pytest.approx(float(v @ v), abs=1e-6)
        assert hits[0].score == pytest.approx(1.0, abs=1e-6)


class TestSearchFilters:
    def _seed(self, conn):
        chunks = [
            _chunk("youtube/a", 0, "alice btc"),
            _chunk("youtube/b", 0, "bob eth"),
            _chunk("youtube/c", 0, "alice old"),
        ]
        vectors = np.stack([_unit_vector(1.0), _unit_vector(2.0), _unit_vector(3.0)])
        upsert_chunks(conn, [chunks[0]], person="Alice", published_at="2026-06-01",
                      assets=["BTC"], vectors=vectors[0:1])
        upsert_chunks(conn, [chunks[1]], person="Bob", published_at="2026-06-01",
                      assets=["ETH"], vectors=vectors[1:2])
        upsert_chunks(conn, [chunks[2]], person="Alice", published_at="2020-01-01",
                      assets=["BTCDOM"], vectors=vectors[2:3])
        return chunks

    def test_filter_by_person(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = self._seed(conn)

        hits = search(conn, _unit_vector(1.0), k=10, person="Alice")

        assert {h.chunk_id for h in hits} == {chunks[0].id, chunks[2].id}

    def test_filter_by_since_is_lexical_iso_comparison(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = self._seed(conn)

        hits = search(conn, _unit_vector(1.0), k=10, since="2026-01-01")

        ids = {h.chunk_id for h in hits}
        assert chunks[0].id in ids
        assert chunks[1].id in ids
        assert chunks[2].id not in ids  # published before `since`

    def test_filter_by_assets_matches_any_requested_asset(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = self._seed(conn)

        hits = search(conn, _unit_vector(1.0), k=10, assets=["ETH"])

        assert {h.chunk_id for h in hits} == {chunks[1].id}

    def test_filter_by_assets_does_not_false_positive_on_prefix(self, tmp_path):
        """A row tagged BTCDOM must not match a query for BTC — the delimiter-
        wrapped substring match is what prevents that false positive."""
        conn = connect(tmp_path / "index.db")
        chunks = self._seed(conn)

        hits = search(conn, _unit_vector(1.0), k=10, assets=["BTC"])

        ids = {h.chunk_id for h in hits}
        assert chunks[0].id in ids       # tagged exactly BTC
        assert chunks[2].id not in ids   # tagged BTCDOM — must not match

    def test_filters_compose_with_and(self, tmp_path):
        conn = connect(tmp_path / "index.db")
        chunks = self._seed(conn)

        hits = search(conn, _unit_vector(1.0), k=10, person="Alice", assets=["BTC"])

        assert {h.chunk_id for h in hits} == {chunks[0].id}
