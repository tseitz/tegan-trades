"""Expose the brain corpus as MCP tools, so a Claude Code session can query it directly.

**Why this exists, and why it spends nothing.** `brain`'s synthesis leg shells out to
`claude -p`, which boots a whole second Claude in a subprocess: measured at $0.28 and
110s per question — comparable to distilling an entire 15k-token transcript, for one
paragraph over ~7k tokens of input. That cost is the harness boot, not the answer, and
it is paid again on every turn.

An MCP tool has no such boot. Retrieval is local numpy over a 76MB SQLite file (~0.6s,
zero tokens), and the model already running in the session does the reading. So the
same question that costs $0.28 through `brain` costs nothing here beyond the passages
entering a context window that is already open — and follow-ups re-use them.

This file is wiring. Every formatting decision lives in `brain.report`, every retrieval
decision in `brain.retrieve` / `brain.vector_store`, and both are pure and tested
without a server, a socket, or a model.
"""
from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from brain.report import format_passages, format_view
from brain.retrieve import retrieve

_INSTRUCTIONS = """Query a curated corpus of trader/analyst transcripts (666 videos,
18,108 indexed chunks) belonging to the user.

Pick the tool by question shape:

- `brain_search` — methodology, terminology, "what is X", "how does X work", "what do
  they call X", or anything where you want what someone actually SAID. Covers the whole
  corpus.
- `brain_roster` — "where does my roster stand on <asset>", disagreement, who changed
  their mind. Needs an asset.

Coverage differs sharply between them and it decides which answers are trustworthy:
the vector index covers 666/666 transcripts, but structured stances cover far fewer, so
`brain_roster` returning nothing usually means extraction hasn't run for that asset —
NOT that the roster is silent on it. Say which of the two you're looking at.

Both tools are free and local. Neither calls a model. Answer from the returned text,
quote it with attribution, and if the corpus doesn't cover something, say so rather than
filling the gap from your own knowledge — the entire point is what THESE people think."""

mcp = FastMCP("brain", instructions=_INSTRUCTIONS)

# The ONNX embedding model is the only expensive thing to construct, and it is immutable
# once loaded. Everything else (stances, the index) is deliberately re-read per call:
# a nightly job rewrites the corpus at 06:15 and this server outlives it, so caching
# corpus state would serve yesterday's answers with no way to tell.
_embedder = None


def _embed(query: str):
    global _embedder
    if _embedder is None:
        from brain.embed import FastEmbedder
        _embedder = FastEmbedder()
    return _embedder.embed_query(query)


def _search(query: str, *, k: int, person: str | None, since: str | None,
            assets: list[str] | None = None):
    """Rank corpus chunks against `query`. Returns [] when the index isn't built yet."""
    from brain import vector_store

    if not vector_store.DB_PATH.exists():
        return []
    # Opened per call, not held: FastMCP dispatches sync tools onto worker threads, and
    # a SQLite connection is bound to the thread that created it. Reopening costs
    # microseconds against a query that reads the whole table anyway.
    conn = vector_store.connect(vector_store.DB_PATH)
    try:
        if vector_store.count(conn) == 0:
            return []
        return vector_store.search(conn, _embed(query), k=k, person=person,
                                   since=since, assets=assets)
    finally:
        conn.close()


@mcp.tool()
def brain_search(query: str, k: int = 8, person: str | None = None,
                 since: str | None = None) -> str:
    """Search the transcript corpus for passages, in the speakers' own words.

    Use for methodology and terminology questions — "what is a fair value gap", "what do
    they call the down move before the up move", "how do they define displacement". This
    is the tool for anything that is not about an asset's direction.

    Phrase `query` as the full natural-language question. It is embedded and matched
    semantically, so keyword-style input ("FVG displacement") retrieves worse than the
    sentence you actually mean.

    Args:
        query: the question, in full natural language.
        k: passages to return. 8 is a good default; raise to 15 when surveying a topic.
        person: restrict to one feed, e.g. "TraderMayne". Useful because the corpus
            contains a 15-episode structured course from him, and course material
            answers methodology questions far better than daily stream commentary.
        since: ISO date lower bound, e.g. "2026-04-01".
    """
    hits = _search(query, k=k, person=person, since=since)
    return format_passages(hits, query=query)


@mcp.tool()
def brain_roster(asset: str, person: str | None = None, since: str | None = None,
                 k: int = 8) -> str:
    """Where the roster currently stands on one asset — split, per-feed views, changes.

    Counts are computed arithmetically from the corpus, never inferred: restate them as
    given rather than recounting from the listed views. A feed marked as multi-author is
    ONE feed with several speakers — never describe it as several independent voices
    agreeing.

    Args:
        asset: ticker or name, e.g. "ETH", "bitcoin dominance".
        person: restrict to one feed.
        since: ISO date lower bound.
        k: supporting passages to retrieve alongside the split.
    """
    from core.canon import load_registry

    from brain.query_cli import _CFG_DIR
    from brain.stance_store import load_all_stances

    registry = load_registry(_CFG_DIR)
    stances = load_all_stances()
    if not stances:
        return ("No stances extracted yet — run `brain-extract`. This is a coverage gap, "
                "not roster silence. `brain_search` still covers the whole corpus.")

    from core.canon import resolve_asset
    asset_c = resolve_asset(asset, registry)[0]

    view = retrieve(
        stances=stances, registry=registry, asset=asset, person=person, since=since,
        k=k, search_fn=lambda *, k, person, since, assets: _search(
            f"where does the roster stand on {asset}", k=k, person=person,
            since=since, assets=assets),
    )
    return format_view(view, question=f"where does the roster stand on {asset_c}")


def main() -> None:
    print("[brain-mcp] serving brain_search + brain_roster over stdio", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
