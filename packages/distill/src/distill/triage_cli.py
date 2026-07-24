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
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from core.canon import ResolvedThesis, load_registry, resolve
from core.rank import build_agreement_index, corpus_span, parse_date, score
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
    restated: int = 0                    # older near-identical statements folded into this one
    restated_since: str | None = None    # published_at of the oldest one folded in


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


# ── collapsing repeat statements of the same call ───────────────────────────

DEFAULT_WINDOW_DAYS = 30


def _restatement_key(r: RankedThesis) -> tuple[str, str, str, str]:
    return (r.resolved.person_canonical, r.resolved.asset_canonical,
            r.thesis.direction, r.thesis.timeframe)


def collapse_restatements(
    ranked: list[RankedThesis],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    latest_only: bool = True,
) -> list[RankedThesis]:
    """Fold repeat statements of the same call, keyed on (person, asset, direction, timeframe).

    These people re-state the same position weekly, so an unfiltered queue spends most of its
    slots on one person restating one view. ``timeframe`` is part of the key on purpose: a
    long-term ``position`` call and a short-term ``swing`` call on the same asset are separate
    theses, and these people routinely hold both at once.

    ``latest_only`` (default) keeps just the current statement per key — what do they think
    *now*. ``restated``/``restated_since`` then cover the entire history, so a queue entry
    never hides how much was folded away.

    ``latest_only=False`` keeps one entry per ``window_days`` cluster instead, for reviewing
    how a view evolved. Clusters anchor on their newest member rather than chaining
    transitively, so a steady weekly drip can't merge into one blob spanning the corpus and
    an old call can't end up absorbing the current one. ``window_days`` is ignored when
    ``latest_only`` is set — there's nothing left to cluster.

    The survivor is always the *most recent*, never the highest-scoring. Undated theses are
    never folded: without a date there's no way to say which statement is current. Score
    ordering is preserved.
    """
    grouped: dict[tuple, list[RankedThesis]] = defaultdict(list)
    keep: dict[str, tuple[int, str | None]] = {}

    for r in sorted(ranked, key=lambda r: r.thesis.source.published_at or "", reverse=True):
        if parse_date(r.thesis.source.published_at) is None:
            keep[r.thesis.id] = (0, None)  # undated — always its own position
        else:
            grouped[_restatement_key(r)].append(r)  # stays sorted newest-first

    for members in grouped.values():
        for cluster in ([members] if latest_only else _cluster_by_window(members, window_days)):
            survivor = cluster[0]  # newest-first, so [0] is the current statement
            oldest = cluster[-1].thesis.source.published_at if len(cluster) > 1 else None
            keep[survivor.thesis.id] = (len(cluster) - 1, oldest)

    return [
        replace(r, restated=keep[r.thesis.id][0], restated_since=keep[r.thesis.id][1])
        for r in ranked
        if r.thesis.id in keep
    ]


def _cluster_by_window(members: list[RankedThesis], window_days: int) -> list[list[RankedThesis]]:
    """Split a newest-first run of same-key theses into within-``window_days`` clusters."""
    clusters: list[list[RankedThesis]] = []
    for r in members:
        anchor = parse_date(clusters[-1][0].thesis.source.published_at) if clusters else None
        current = parse_date(r.thesis.source.published_at)
        if anchor is not None and current is not None and (anchor - current).days <= window_days:
            clusters[-1].append(r)
        else:
            clusters.append([r])
    return clusters


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
    if ranked.restated:
        lines.append(f"- **Restated:** {ranked.restated} similar call(s) since {ranked.restated_since}")
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
    when = t.source.published_at or "undated"
    restated = f" · +{r.restated} similar since {r.restated_since}" if r.restated else ""
    return (f"\n[score {r.score:.2f}] {when} · {res.person_canonical} · "
            f"{res.asset_canonical} {t.direction} ({t.timeframe}){restated}\n{t.summary}")


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
    parser.add_argument("--history", action="store_true",
                        help="show how a view evolved: one entry per --window-days cluster "
                             "instead of only the current statement")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                        help="cluster size for --history (ignored otherwise)")
    parser.add_argument("--no-dedup", action="store_true",
                        help="show every restatement, unfolded")
    args = parser.parse_args(argv)

    registry = load_registry(CONFIG_DIR)
    decided = set(load_decisions(args.decisions))
    ranked = rank_corpus(store_mod.DATA_ROOT, registry, decided=decided)
    if not args.no_dedup:  # fold before slicing, or --top just re-fills with restatements
        before = len(ranked)
        ranked = collapse_restatements(
            ranked, window_days=args.window_days, latest_only=not args.history)
        mode = f"{args.window_days}d clusters" if args.history else "current view per position"
        print(f"{before} theses -> {len(ranked)} distinct positions ({mode})")
    ranked = ranked[: args.top]
    if not ranked:  # pragma: no cover - trivial
        print("Queue empty — nothing left to triage.")
        return 0
    counts = triage(ranked, decisions_path=args.decisions, vault_path=args.vault)  # pragma: no cover - interactive
    print(f"\npromoted {counts['approved']} · skipped {counts['skipped']} · "  # pragma: no cover
          f"archived {counts['archived']} -> {args.vault}")
    return 0
