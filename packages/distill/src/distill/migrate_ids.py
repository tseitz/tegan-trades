"""distill-migrate-ids — re-key stored theses from positional ids to content-addressed ones,
and carry the triage decisions sidecar across with them.

One-time migration for corpora distilled before ``core.thesis.thesis_id`` existed. Ids used
to be ``<transcript_ref>#<index>``, which meant a ``--force`` re-distill left every decision
pointing at whatever call now occupies that slot. Deterministic, no LLM, and idempotent:
ids are derived purely from stored content, so re-running is a no-op.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from core.thesis import thesis_id

from distill import store as store_mod

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DECISIONS = _REPO_ROOT / "data" / "triage" / "decisions.jsonl"


@dataclass(frozen=True)
class MigrationReport:
    documents: int = 0
    theses: int = 0
    decisions_remapped: int = 0
    decisions_dropped: int = 0
    dropped_ids: list[str] = field(default_factory=list)


def _id_for(raw: dict) -> str:
    return thesis_id(
        raw["source"]["transcript_ref"],
        thesis_type=raw["thesis_type"],
        asset=raw["asset"],
        direction=raw["direction"],
        timeframe=raw["timeframe"],
        summary=raw["summary"],
    )


def remap_document(doc: dict) -> tuple[dict, dict[str, str]]:
    """Return (rewritten doc, old_id -> new_id). Non-mutating."""
    mapping: dict[str, str] = {}
    theses = []
    for raw in doc.get("theses", []):
        new_id = _id_for(raw)
        mapping[raw["id"]] = new_id
        theses.append({**raw, "id": new_id})
    return {**doc, "theses": theses}, mapping


def _remap_decisions(path: Path, mapping: dict[str, str]) -> tuple[int, list[str]]:
    """Rewrite the sidecar under the new ids. Ids already migrated map to themselves;
    ids with no surviving thesis are dropped rather than left dangling."""
    if not path.exists():
        return 0, []
    live = set(mapping.values())
    kept: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        new_id = mapping.get(rec["id"], rec["id"] if rec["id"] in live else None)
        if new_id is None:
            dropped.append(rec["id"])
            continue
        kept.append(json.dumps({"id": new_id, "decision": rec["decision"]}))
        seen.add(new_id)
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return len(kept), dropped


def migrate(theses_root, decisions_path, *, dry_run: bool = False) -> MigrationReport:
    mapping: dict[str, str] = {}
    documents = theses = 0
    for path in sorted(Path(theses_root).glob("*/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        new_doc, doc_map = remap_document(doc)
        if not doc_map:
            continue
        mapping.update(doc_map)
        documents += 1
        theses += len(doc_map)
        if not dry_run:
            path.write_text(json.dumps(new_doc, indent=2), encoding="utf-8")

    if dry_run:
        return MigrationReport(documents=documents, theses=theses)
    remapped, dropped = _remap_decisions(Path(decisions_path), mapping)
    return MigrationReport(documents=documents, theses=theses, decisions_remapped=remapped,
                           decisions_dropped=len(dropped), dropped_ids=dropped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="distill-migrate-ids",
        description="Re-key stored theses to content-addressed ids and remap triage decisions.")
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)

    report = migrate(store_mod.DATA_ROOT, args.decisions, dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{report.theses} theses across {report.documents} documents re-keyed")
    if not args.dry_run:
        print(f"{prefix}decisions: {report.decisions_remapped} remapped, "
              f"{report.decisions_dropped} dropped")
        for stale in report.dropped_ids:
            print(f"  dropped (no surviving thesis): {stale}")
    return 0
