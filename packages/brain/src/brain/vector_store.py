"""SQLite-backed vector store for transcript chunk embeddings.

No vector database. 666 transcripts is roughly 15k chunks x 384 dims ≈ 23MB;
brute-force cosine similarity in numpy over that many rows is sub-millisecond,
so SQLite (for storage + cheap person/date/asset pre-filtering) plus a numpy
matrix-multiply (for ranking) is simpler than adding a dependency like
`sqlite-vec` — that would only start to earn its keep past ~100k chunks,
which this corpus is nowhere near.

Vectors are stored as raw float32 BLOBs and are assumed to already be
L2-normalized by the embedder (see `brain.embed`) — `search` relies on that
to compute cosine similarity as a plain dot product, with no renormalization.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from brain.chunk import Chunk

# Repo root: src/brain/vector_store.py -> src/brain -> src -> brain -> packages -> <root>
DB_PATH = Path(__file__).resolve().parents[4] / "data" / "brain" / "index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
  id             TEXT PRIMARY KEY,
  transcript_ref TEXT NOT NULL,
  person         TEXT NOT NULL,
  published_at   TEXT,
  assets         TEXT,
  idx            INTEGER NOT NULL,
  start          INTEGER NOT NULL,
  end            INTEGER NOT NULL,
  text           TEXT NOT NULL,
  vector         BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_transcript_ref ON chunks(transcript_ref);
CREATE INDEX IF NOT EXISTS idx_chunks_person ON chunks(person);
CREATE INDEX IF NOT EXISTS idx_chunks_published_at ON chunks(published_at);

CREATE TABLE IF NOT EXISTS indexed (
  transcript_ref TEXT PRIMARY KEY,
  fingerprint    REAL NOT NULL,
  indexed_at     TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    transcript_ref: str
    person: str
    published_at: str | None
    text: str
    score: float


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the index DB, ensuring parent dirs and schema exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _assets_column(assets: list[str] | None) -> str:
    """Comma-join asset tickers for storage.

    IMPORTANT: a chunk is a window of narrative text, not a claim about any one
    asset — a chunk may mention several assets stanced in its transcript, or
    none at all. This column holds every asset stanced *anywhere in that
    transcript* (see `index_cli`), denormalized onto each of its chunks purely
    as a coarse pre-filter for `search`. It must never be read as "this chunk
    is about asset X" — only as "this chunk's transcript also touches asset X
    somewhere, so don't filter it out before ranking."
    """
    return ",".join(assets) if assets else ""


def upsert_chunks(
    conn: sqlite3.Connection,
    chunks: list[Chunk],
    *,
    person: str,
    published_at: str | None,
    assets: list[str] | None,
    vectors: np.ndarray,
) -> int:
    """Insert or replace chunk rows (with their vectors) into the store.

    Chunk ids are content-addressed (see `brain.chunk`), so two byte-identical
    chunks within one transcript legitimately share an id. `INSERT OR REPLACE`
    makes both re-indexing and same-run duplicates idempotent rather than
    raising an integrity error.
    """
    assets_col = _assets_column(assets)
    rows = [
        (
            c.id, c.transcript_ref, person, published_at, assets_col,
            c.index, c.start, c.end, c.text,
            np.asarray(vectors[i], dtype=np.float32).tobytes(),
        )
        for i, c in enumerate(chunks)
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO chunks
           (id, transcript_ref, person, published_at, assets, idx, start, end, text, vector)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def clear(conn: sqlite3.Connection) -> None:
    """Empty the store — BOTH the chunks and the ledger that says what has been indexed.

    These must be cleared together. Dropping the chunk rows while leaving the ledger
    behind would make every transcript look already-indexed on the next run, so a
    `--rebuild` would skip the entire corpus and leave an empty index reporting success.
    """
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM indexed")
    conn.commit()


def delete_transcript(conn: sqlite3.Connection, transcript_ref: str) -> int:
    """Drop every chunk belonging to one transcript. Returns the number removed.

    Needed before re-indexing a transcript whose text changed. Chunk ids are
    content-addressed (`brain.chunk`), so new text yields new ids and `upsert_chunks`
    writes alongside the old rows rather than replacing them — leaving orphaned passages
    that no transcript contains any more but that `search` will still happily return.
    """
    cursor = conn.execute("DELETE FROM chunks WHERE transcript_ref = ?", (transcript_ref,))
    conn.commit()
    return cursor.rowcount


def fingerprints(conn: sqlite3.Connection) -> dict[str, float]:
    """The whole ledger as `{transcript_ref: fingerprint}`, read in one query.

    Returned wholesale rather than probed per transcript: the corpus is ~900 rows and the
    caller checks every one of them, so a single SELECT beats 900 round trips.
    """
    return dict(conn.execute("SELECT transcript_ref, fingerprint FROM indexed").fetchall())


def record_indexed(
    conn: sqlite3.Connection, transcript_ref: str, fingerprint: float, indexed_at: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO indexed (transcript_ref, fingerprint, indexed_at) "
        "VALUES (?, ?, ?)",
        (transcript_ref, fingerprint, indexed_at),
    )
    conn.commit()


def search(
    conn: sqlite3.Connection,
    query_vector: np.ndarray,
    *,
    k: int = 10,
    person: str | None = None,
    since: str | None = None,
    assets: list[str] | None = None,
) -> list[SearchHit]:
    """Rank chunks by cosine similarity to `query_vector`, after composing any
    given filters with AND. Filters narrow the SQL row set *before* ranking;
    the actual similarity math happens once in numpy over whatever remains.
    """
    where: list[str] = []
    params: list[object] = []

    if person is not None:
        where.append("person = ?")
        params.append(person)
    if since is not None:
        # ISO-8601 date strings compare correctly as plain strings — the same
        # convention the rest of this repo uses for date filtering.
        where.append("published_at >= ?")
        params.append(since)
    if assets:
        # Wrap the stored comma-list in delimiters before matching, so a query
        # for "BTC" can't false-positive against a row tagged only "BTCDOM".
        clauses = [" OR ".join("(',' || assets || ',') LIKE ?" for _ in assets)]
        # An EMPTY assets column means "this transcript has no stance file yet", NOT
        # "this transcript is about nothing". Indexing is free and stance extraction
        # costs tokens, so extraction lagging indexing is the normal state — dropping
        # unknowns here would make most of the corpus invisible to every asset-filtered
        # search, silently. This column is a coarse pre-filter, never a claim about the
        # chunk, so unknown resolves to keep-and-let-cosine-rank.
        clauses.append("assets IS NULL OR assets = ''")
        where.append(f"({' OR '.join(clauses)})")
        params.extend(f"%,{asset},%" for asset in assets)

    sql = "SELECT id, transcript_ref, person, published_at, text, vector FROM chunks"
    if where:
        sql += " WHERE " + " AND ".join(where)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []

    # strict: every row has the six columns the SELECT names, so a ragged transpose is
    # impossible unless the schema and this query have drifted apart — which is worth raising on.
    ids, refs, persons, published_ats, texts, blobs = zip(*rows, strict=True)
    matrix = np.stack([np.frombuffer(blob, dtype=np.float32) for blob in blobs])
    # Every stored vector (and the query vector) is already L2-normalized by
    # brain.embed, so this dot product IS cosine similarity — no renormalizing.
    scores = matrix @ np.asarray(query_vector, dtype=np.float32)

    top_k = np.argsort(-scores)[:k]
    return [
        SearchHit(
            chunk_id=ids[i],
            transcript_ref=refs[i],
            person=persons[i],
            published_at=published_ats[i],
            text=texts[i],
            score=float(scores[i]),
        )
        for i in top_k
    ]
