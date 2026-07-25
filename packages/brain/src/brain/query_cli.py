"""`brain "where is my roster on ETH"` — the question this project exists to answer.

Wiring only. Every piece of judgment lives in a pure module (`question.parse_assets`,
`retrieve.retrieve`, `report.format_view`, `synthesize.synthesize`); this file resolves
I/O and injects it, so the whole path is testable without a model, a database, or the
real `data/` directory.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from brain.question import parse_assets
from brain.report import format_view
from brain.retrieve import retrieve
from brain.synthesize import DEFAULT_MODEL, SynthesisFailed, synthesize

_CFG_DIR = Path(__file__).resolve().parents[4] / "cfg"

# Distinguishes "caller passed nothing" from "caller explicitly passed None to mean
# no vector leg" — tests rely on the latter to stay off the disk and off the model.
_UNSET = object()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain",
        description='Ask the roster a question, e.g. brain "where is my roster on ETH".')
    parser.add_argument("question", help="plain-language question")
    parser.add_argument("--asset", default=None,
                        help="explicit asset, when the question parse guesses wrong")
    parser.add_argument("--person", default=None, help="restrict to one feed")
    parser.add_argument("--since", default=None, help="ISO date lower bound")
    parser.add_argument("-k", type=int, default=8, help="evidence passages to retrieve")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--no-llm", action="store_true",
                        help="structured answer only — no synthesis call, no tokens spent")
    return parser


def _default_search_fn(query: str, k: int):
    """Build the vector leg lazily. A missing index is a normal early state — the
    structured answer stands on its own, so degrade to None instead of failing."""
    try:
        from brain import vector_store
        from brain.embed import FastEmbedder

        db = vector_store.DB_PATH
        if not db.exists():
            return None
        conn = vector_store.connect(db)
        if vector_store.count(conn) == 0:
            return None
        vector = FastEmbedder().embed_query(query)

        def search_fn(*, k, person, since, assets):
            return vector_store.search(conn, vector, k=k, person=person,
                                       since=since, assets=assets)

        return search_fn
    except Exception as exc:  # noqa: BLE001 - evidence is optional; never block the answer
        # Degrade, but never silently. A corrupt index or a real bug in the vector
        # store would otherwise look identical to "no index built yet", and this
        # codebase has lost days to exactly that shape of failure.
        print(f"[brain] evidence unavailable ({exc!r}) — answering from stances only",
              file=sys.stderr)
        return None


def main(
    argv: list[str] | None = None,
    *,
    stances=None,
    registry=None,
    search_fn=_UNSET,
    client=None,
    out=print,
) -> int:
    args = _build_parser().parse_args(argv)

    if registry is None:
        from core.canon import load_registry
        registry = load_registry(_CFG_DIR)
    if stances is None:
        from brain.stance_store import load_all_stances
        stances = load_all_stances()

    if not stances:
        out("No stances in the corpus yet — run `brain-extract` first.")
        return 1

    asset = args.asset
    if asset is None:
        parsed = parse_assets(args.question, registry)
        if not parsed:
            out("Could not work out which asset you mean. Pass --asset explicitly, "
                'e.g. --asset ETH.')
            return 2
        asset = parsed[0]
        if len(parsed) > 1:
            out(f"(question named {', '.join(parsed)}; answering for {asset} — "
                f"use --asset to pick another)\n")

    if search_fn is _UNSET:
        # Deliberately NOT gated on --no-llm. Retrieval is local CPU and costs nothing;
        # only synthesis spends tokens. Coupling them would silently strip the quotes
        # from a free answer.
        search_fn = _default_search_fn(args.question, args.k)

    view = retrieve(stances=stances, registry=registry, asset=asset,
                    person=args.person, since=args.since, k=args.k, search_fn=search_fn)

    synthesis = None
    if not args.no_llm and view.folded:
        try:
            synthesis = synthesize(view, question=args.question, client=client,
                                   model=args.model)
        except SynthesisFailed as exc:
            # The deterministic answer is the valuable part and it is already computed.
            # A dead model must not cost the user the split, the views, or the changes.
            print(f"[brain] synthesis unavailable: {exc}", file=sys.stderr)
            out("(synthesis unavailable — structured answer below)\n")

    out(format_view(view, synthesis=synthesis, question=args.question,
                    today=date.today()))
    return 0
