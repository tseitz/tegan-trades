"""CLI to build (or rebuild) the local embedding index over the transcript corpus.

Walks `data/transcripts/<platform>/<source_id>.txt`, chunks each transcript
(`brain.chunk`), embeds the chunks (`brain.embed`), and upserts them into the
SQLite vector store (`brain.vector_store`). For each transcript, its stance
file (`brain.stance_store`) — if one exists yet — supplies the coarse `assets`
pre-filter; a transcript without a stance file is indexed with no assets
rather than treated as an error, since the stance corpus is built separately
and can lag transcript ingestion.

I/O seams (`transcripts_root`, `stances_root`, `conn`, `embedder`) are
injectable, mirroring `brain.sweep.extract_all`, so tests can run the whole
path against a `tmp_path` DB with a fake embedder and never touch real data
or load a model.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from brain import stance_store as store_mod
from brain import vector_store as store
from brain.chunk import chunk_transcript
from brain.embed import DEFAULT_MODEL, Embedder, FastEmbedder

# Repo root: src/brain/index_cli.py -> src/brain -> src -> brain -> packages -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
TRANSCRIPTS_ROOT = _REPO_ROOT / "data" / "transcripts"

DEFAULT_BATCH = 64


def _read_assets(platform: str, source_id: str, stances_root: Path) -> list[str]:
    if not store_mod.exists(platform, source_id, stances_root):
        return []
    stances = store_mod.load_stances(platform, source_id, stances_root)
    # dict.fromkeys dedupes while preserving first-seen order; set() would not.
    return list(dict.fromkeys(s.asset for s in stances))


def index_all(
    *,
    transcripts_root: Path | None = None,
    stances_root: Path | None = None,
    conn=None,
    embedder: Embedder | None = None,
    rebuild: bool = False,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH,
) -> dict:
    """Walk transcripts, chunk + embed + upsert each one. Returns summary counts.

    `chunks_skipped` counts transcripts that could not be chunked at all (their
    sidecar has no matching `.txt` file) — nothing was written for them. A
    transcript that chunks successfully but yields zero chunks (e.g. blank
    body) is NOT a skip; it still counts toward `transcripts_indexed`.
    """
    transcripts_root = transcripts_root or TRANSCRIPTS_ROOT
    stances_root = stances_root or store_mod.DATA_ROOT
    conn = conn if conn is not None else store.connect()
    embedder = embedder or FastEmbedder()

    if rebuild:
        store.clear(conn)

    sidecar_paths = sorted(transcripts_root.glob("*/*.json"))
    if limit is not None:
        sidecar_paths = sidecar_paths[:limit]

    transcripts_indexed = 0
    chunks_written = 0
    chunks_skipped = 0

    for sidecar_path in sidecar_paths:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        platform, source_id = sidecar["platform"], sidecar["source_id"]
        person = sidecar.get("person", "unknown")
        published_at = sidecar.get("published_at")
        transcript_ref = f"{platform}/{source_id}"

        txt_path = sidecar_path.with_suffix(".txt")
        if not txt_path.exists():
            chunks_skipped += 1
            continue
        text = txt_path.read_text(encoding="utf-8")

        chunks = chunk_transcript(text, transcript_ref)
        if not chunks:
            transcripts_indexed += 1
            continue

        assets = _read_assets(platform, source_id, stances_root)

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = embedder.embed([c.text for c in batch])
            store.upsert_chunks(
                conn, batch, person=person, published_at=published_at,
                assets=assets, vectors=vectors,
            )
            chunks_written += len(batch)

        transcripts_indexed += 1

    return {
        "transcripts_indexed": transcripts_indexed,
        "chunks_written": chunks_written,
        "chunks_skipped": chunks_skipped,
    }


def _format_summary(stats: dict, elapsed: float) -> str:
    return (
        f"{stats['transcripts_indexed']} transcripts indexed, "
        f"{stats['chunks_written']} chunks written, "
        f"{stats['chunks_skipped']} chunks skipped, "
        f"{elapsed:.1f}s elapsed"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain-index",
        description="Build the local embedding index over the transcript corpus.")
    parser.add_argument("--limit", type=int, default=None,
                        help="index at most N transcripts")
    parser.add_argument("--rebuild", action="store_true",
                        help="clear the index before indexing")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                        help="embed this many chunks per batch")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="embedding model name")
    args = parser.parse_args(argv)

    start = time.monotonic()
    stats = index_all(
        embedder=FastEmbedder(model_name=args.model),
        rebuild=args.rebuild,
        limit=args.limit,
        batch_size=args.batch,
    )
    elapsed = time.monotonic() - start
    print(_format_summary(stats, elapsed))
    return 0
