"""distill-triage — rank the thesis firehose on intrinsic signals and interactively
promote the keepers into a durable vault note (Phase 3b).

Promotion is a *snapshot into the vault*, never a status flip in the firehose: the vault
note's existence is the durable record, so a future ``--force`` re-distill (which rewrites
data/theses with new ids) can't erase a promotion. A JSONL decisions sidecar under data/
keeps the queue from re-surfacing anything already skipped/archived/promoted; it's tied to
firehose ids and is expected to go stale on re-distill (the vault notes persist regardless).

Deterministic, no LLM. Mirrors canon_cli's input_fn/out injection for unit-testable I/O."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from core.canon import ResolvedThesis, load_registry, resolve
from core.rank import build_agreement_index, corpus_span, score
from core.thesis import Thesis

from distill import store as store_mod

_REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = _REPO_ROOT / "cfg"
DEFAULT_DECISIONS = _REPO_ROOT / "data" / "triage" / "decisions.jsonl"
DEFAULT_VAULT_NOTE = Path(
    "/Users/tseitz/vault/Claude/Projects/tegan-trades/Promoted Theses.md"
)


@dataclass(frozen=True)
class RankedThesis:
    thesis: Thesis
    resolved: ResolvedThesis
    score: float
    source_path: Path


# ── ranking the corpus ──────────────────────────────────────────────────────

def rank_corpus(theses_root, registry, *, decided: set[str] | None = None) -> list[RankedThesis]:
    """Walk data/theses/*/*.json, resolve + score every thesis, return sorted desc.
    ``decided`` ids are dropped from the output but still count toward agreement and the
    corpus date span (so triaging one thesis doesn't distort the ranking of the rest)."""
    decided = decided or set()
    loaded: list[tuple[Thesis, ResolvedThesis, Path]] = []
    for path in sorted(Path(theses_root).glob("*/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for raw in doc.get("theses", []):
            thesis = Thesis.model_validate(raw)
            loaded.append((thesis, resolve(thesis, registry), path))

    if not loaded:
        return []

    index = build_agreement_index(
        (res.asset_canonical, t.direction, res.person_canonical) for t, res, _ in loaded
    )
    span = corpus_span(t.source.published_at for t, _, _ in loaded)
    newest, oldest = span if span else (None, None)

    ranked = [
        RankedThesis(
            thesis=t,
            resolved=res,
            score=score(t, res, index, newest=newest, oldest=oldest),
            source_path=path,
        )
        for t, res, path in loaded
        if t.id not in decided
    ]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


# ── decisions sidecar (JSONL, last-wins) ────────────────────────────────────

def load_decisions(path) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        return {}
    decisions: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        decisions[rec["id"]] = rec["decision"]
    return decisions


def record_decision(path, thesis_id: str, decision: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"id": thesis_id, "decision": decision}) + "\n")


# ── vault note rendering ────────────────────────────────────────────────────

_NOTE_TITLE = "# Promoted Theses"


def render_note(ranked: RankedThesis) -> str:
    """One markdown section for a promoted thesis. Uses canonical person/asset labels."""
    t, res = ranked.thesis, ranked.resolved
    lines = [
        (f"## {t.source.published_at} · {res.person_canonical} · "
         f"{res.asset_canonical} {t.direction} ({t.timeframe}) · score {ranked.score:.2f}"),
        "",
        t.summary,
        "",
        f"- **Conviction:** {t.conviction} · **Confidence:** {t.extraction.confidence:.2f}",
    ]
    if t.invalidation:
        lines.append(f"- **Invalidation:** {t.invalidation}")
    if t.key_levels:
        lines.append("- **Key levels:** " + ", ".join(str(x) for x in t.key_levels))
    if t.catalyst:
        lines.append(f"- **Catalyst:** {t.catalyst}")
    if t.asset_heard:
        lines.append(f"- **Heard as:** {t.asset_heard}")
    for q in t.quotes:
        lines += ["", f"> {q.text}"]
    lines += ["", f"[source]({t.source.url})"]
    return "\n".join(lines)


def append_note(vault_path, section: str) -> None:
    """Append a section to the running promotions note, creating it (with title) if absent."""
    path = Path(vault_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = section.strip()
    if path.exists():
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n\n" + body + "\n",
                        encoding="utf-8")
    else:
        path.write_text(f"{_NOTE_TITLE}\n\n{body}\n", encoding="utf-8")


# ── interactive triage loop ─────────────────────────────────────────────────

def _prompt_header(r: RankedThesis) -> str:
    t, res = r.thesis, r.resolved
    return (f"\n[score {r.score:.2f}] {res.person_canonical} · "
            f"{res.asset_canonical} {t.direction} ({t.timeframe})\n{t.summary}")


def triage(ranked, *, decisions_path, vault_path, input_fn=input, out=print) -> dict[str, int]:
    """Present each ranked thesis; approve -> write vault note, all decisions -> sidecar.
    Quit stops immediately without consuming further input."""
    counts = {"approved": 0, "skipped": 0, "archived": 0}
    for r in ranked:
        out(_prompt_header(r))
        ans = input_fn("[a]pprove / [s]kip / [x]archive / [q]uit: ").strip().lower()
        if ans in ("q", "quit"):
            break
        if ans in ("a", "approve"):
            append_note(vault_path, render_note(r))
            record_decision(decisions_path, r.thesis.id, "promoted")
            counts["approved"] += 1
        elif ans in ("x", "archive"):
            record_decision(decisions_path, r.thesis.id, "archived")
            counts["archived"] += 1
        else:  # blank or 's' -> skip ("seen, pass"); won't re-surface
            record_decision(decisions_path, r.thesis.id, "skipped")
            counts["skipped"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="distill-triage",
        description="Rank the thesis firehose and promote keepers into a vault note.")
    parser.add_argument("--top", type=int, default=20, help="how many top-ranked to review")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT_NOTE,
                        help="running promotions note to append to")
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS,
                        help="triage-decisions sidecar (JSONL)")
    args = parser.parse_args(argv)

    registry = load_registry(CONFIG_DIR)
    decided = set(load_decisions(args.decisions))
    ranked = rank_corpus(store_mod.DATA_ROOT, registry, decided=decided)[: args.top]
    if not ranked:  # pragma: no cover - trivial
        print("Queue empty — nothing left to triage.")
        return 0
    counts = triage(ranked, decisions_path=args.decisions, vault_path=args.vault)  # pragma: no cover - interactive
    print(f"\npromoted {counts['approved']} · skipped {counts['skipped']} · "  # pragma: no cover
          f"archived {counts['archived']} -> {args.vault}")
    return 0
