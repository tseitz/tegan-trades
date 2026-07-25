"""``setups`` — cross-reference the roster against price structure and triage the candidates.

This is the read-time join of the two halves the rest of the codebase deliberately keeps
apart: ``core.setups`` knows nothing about how an asset gets priced, and ``oracle`` knows
nothing about theses or gates. Here they meet, once, per run — route every asset the corpus
mentions to a cached daily series, build one ``Context`` per asset (never per thesis: that
split exists precisely so a hundred theses on BTC don't recompute BTC's structure a hundred
times), then let ``core.setups`` do the actual judging.

**Rejections stay a first-class output, not debug noise.** Mirroring ``core.setups``'s own
reasoning: a corpus that produces zero candidates and a corpus whose gates are simply too
tight look identical unless the ``NotASetup`` reasons are tallied and shown. The same
applies one level up, to routing — an asset silently skipped for want of a price source is
indistinguishable from an asset that was never considered, so that count is reported too.

**Decisions are keyed on ``Candidate.key``, not on a row or run index.** A key is
content-addressed on the zone's date and prices, so a decision survives a price backfill
that would otherwise renumber everything built on bar position — the same failure mode
``core.thesis`` avoids with content-addressed thesis ids, and ``core.setups.Candidate.key``
documents directly. Approving a candidate is a durable, replayable fact; the sidecar is
append-only so a decision, once made, is never silently overwritten.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from core.canon import Registry, load_registry, resolve_asset
from core.setups import (
    TIER_LARGE,
    TIER_MAJOR,
    TIER_NONCRYPTO,
    TIER_SMALL,
    TIER_UNRANKED,
    Candidate,
    NotASetup,
    build_context,
    collapse,
    cross_reference,
)
from core.rank import parse_date

from oracle import cache, corpus, listings
from oracle.resample import to_weekly
from oracle.route import OracleRef, RoutingTable, load_routing_table, route

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = REPO_ROOT / "cfg"
DEFAULT_DECISIONS = REPO_ROOT / "data" / "setups" / "decisions.jsonl"

TIER_CHOICES = (TIER_MAJOR, TIER_LARGE, TIER_SMALL, TIER_UNRANKED, TIER_NONCRYPTO)

# Decision vocabulary. Deliberately distinct spelling from distill.triage_cli's
# "promoted"/"skipped"/"archived" — a setup is "approved" for execution, not "promoted"
# into a note; the two sidecars are unrelated and must never be confused on disk.
APPROVED = "approved"
SKIPPED = "skipped"
ARCHIVED = "archived"

_NOTE_TITLE = "# Approved Setups"


# ── engine assembly (impure: routes assets, reads the price cache) ─────────────────────────

@dataclass(frozen=True)
class BuildStats:
    """What happened while turning the corpus into candidates — the audit trail.

    Every count here answers "did the gates produce nothing, or was there nothing to gate":
    ``assets_unpriced`` covers assets with no route to a price source at all,
    ``assets_no_context`` covers assets that route fine but have no cached bars at-or-before
    ``as_of``, and ``rejections`` covers theses that made it to ``cross_reference`` and were
    refused there. Three different failure points, three different counters — collapsing
    them into one would make the eventual "zero candidates" unanswerable.
    """
    assets_total: int
    assets_priced: int
    assets_unpriced: int
    assets_no_context: int
    rejections: Counter
    candidate_count: int


def _load_daily(asset: str, table: RoutingTable, *, series_cache: dict):
    """Resolve an asset to a cached daily ``PriceSeries``, or None when it can't be priced.

    Mirrors ``score_cli._load_series`` — same routing table, same cache, same refusal to
    invent a second path from asset to price.
    """
    if asset in series_cache:
        return series_cache[asset]
    resolved = route(asset, table)
    series = None
    if isinstance(resolved, OracleRef):
        series = cache.load(resolved.source, resolved.symbol)
    series_cache[asset] = series
    return series


def build_candidates(
    rows,
    registry: Registry,
    *,
    as_of: date,
    listings_map,
    config_dir: Path = CONFIG_DIR,
) -> tuple[tuple[Candidate, ...], BuildStats]:
    """Route every asset the corpus mentions, build one ``Context`` each, gate every row
    against its asset's context, and collapse the outcome stream into candidates."""
    table = load_routing_table(
        config_dir, [(r.asset, r.domain) for r in rows], listings=listings_map
    )
    assets = sorted({r.asset for r in rows})

    series_cache: dict = {}
    contexts: dict[str, tuple] = {}
    unpriced = 0
    no_context = 0
    for asset in assets:
        daily = _load_daily(asset, table, series_cache=series_cache)
        if daily is None:
            unpriced += 1
            continue
        weekly = to_weekly(daily)
        ctx = build_context(daily.bars, weekly.bars, as_of=as_of)
        if ctx is None:
            no_context += 1
            continue
        contexts[asset] = (daily, ctx)

    rank_cache: dict[str, int | None] = {}
    outcomes = []
    for row in rows:
        entry = contexts.get(row.asset)
        if entry is None:
            continue
        daily, ctx = entry
        if row.asset not in rank_cache:
            _, _, rank = resolve_asset(row.asset, registry)
            rank_cache[row.asset] = rank
        published = parse_date(row.published_at)
        published_close = daily.close_on(published) if published is not None else None
        # agreement_count is irrelevant here: collapse() recomputes each candidate's score
        # from its own regrouped agreement count, so whatever a single Setup carried never
        # survives to the collapsed Candidate.
        outcomes.append(cross_reference(
            row, ctx, published_close=published_close,
            asset_rank=rank_cache[row.asset], agreement_count=0,
        ))

    candidates = collapse(outcomes)
    rejections = Counter(o.reason for o in outcomes if isinstance(o, NotASetup))
    stats = BuildStats(
        assets_total=len(assets), assets_priced=len(contexts),
        assets_unpriced=unpriced, assets_no_context=no_context,
        rejections=rejections, candidate_count=len(candidates),
    )
    return candidates, stats


# ── pure: filtering, decision records, formatting ───────────────────────────────────────────

def filter_candidates(
    candidates,
    *,
    min_score: float | None = None,
    tiers: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    """Filter (and cap) a candidate stream. ``candidates`` is assumed already best-score-first,
    as ``collapse`` returns it, so ``limit`` after filtering is "the top N that qualify"."""
    out = list(candidates)
    if min_score is not None:
        out = [c for c in out if c.score >= min_score]
    if tiers:
        wanted = set(tiers)
        out = [c for c in out if c.tier in wanted]
    if limit is not None:
        out = out[:limit]
    return out


def drop_decided(candidates, decided: set[str]) -> list[Candidate]:
    """Candidates whose ``key`` is not already in the decisions sidecar."""
    return [c for c in candidates if c.key not in decided]


def decision_record(candidate: Candidate, decision: str, *, decided_at: str) -> dict:
    """A JSON-serializable decision, keyed on the candidate's content-addressed zone."""
    return {
        "candidate_key": candidate.key,
        "decision": decision,
        "decided_at": decided_at,
        "asset": candidate.asset,
        "direction": candidate.direction,
        "entry": candidate.entry,
        "stop": candidate.stop,
        "target": candidate.target,
        "target_source": candidate.target_source,
        "thesis_ids": list(candidate.thesis_ids),
    }


def format_candidate(candidate: Candidate, *, rank: int | None = None) -> str:
    """Render one candidate for the interactive prompt. Stop and invalidation are shown as
    two distinct labelled values on purpose — they answer different questions ("where is
    this trade wrong" vs "where does the zone itself die") and blurring them into one number
    would hide that. ``target_source`` is always shown so an inferred target never reads as
    a clean, stated one.

    Every candidate leads with **when it was last called**, and each supporting person carries
    their own date. A bare agreement count hides that one of four people last spoke months ago,
    and a queue without dates was already fixed once in ``triage_cli`` for exactly that reason.
    """
    c = candidate
    header = f"[{rank}] " if rank is not None else ""
    span = "" if c.newest_at == c.oldest_at else f", oldest {c.oldest_at}"
    lines = [
        f"\n{header}{c.asset} {c.direction.upper()} · tier {c.tier} · score {c.score:.2f}",
        f"  last called {c.newest_at}{span}",
        f"  entry zone {c.entry_bottom:g}–{c.entry_top:g}  ·  entry {c.entry:g}",
        f"  stop {c.stop:g}  ·  invalidation {c.invalidation:g}",
        f"  target {c.target:g}  [{c.target_source}]",
        f"  reward:risk {c.reward_risk:.2f}  ·  proximity {c.proximity:.2f}  ·  depth {c.depth:.2f}",
        f"  weekly {c.weekly_trend} · daily {c.daily_trend} · zone {c.zone}",
        f"  people: {format_views(c)}  ·  agreement {c.agreement}",
    ]
    return "\n".join(lines)


def format_views(candidate: Candidate) -> str:
    """``Person (date)`` per supporter, newest first — so a stale voice is visible as one."""
    return ", ".join(f"{v.person} ({v.published_at})" for v in candidate.views)


def render_note(candidate: Candidate) -> str:
    """One markdown section for an approved candidate, same shape as ``triage_cli.render_note``."""
    c = candidate
    lines = [
        f"## {c.asset} {c.direction} · tier {c.tier} · score {c.score:.2f}",
        "",
        f"- **Last called:** {c.newest_at}"
        + ("" if c.newest_at == c.oldest_at else f" (oldest {c.oldest_at})"),
        f"- **Entry zone:** {c.entry_bottom:g}–{c.entry_top:g} (entry {c.entry:g})",
        f"- **Stop:** {c.stop:g}",
        f"- **Invalidation:** {c.invalidation:g}",
        f"- **Target:** {c.target:g} ({c.target_source})",
        f"- **Reward:risk:** {c.reward_risk:.2f}",
        f"- **Trend:** weekly {c.weekly_trend} · daily {c.daily_trend} · zone {c.zone}",
        f"- **Supporting:** {format_views(c)} (agreement {c.agreement})",
        f"- **Theses:** {', '.join(c.thesis_ids)}",
    ]
    return "\n".join(lines)


# ── decisions sidecar (JSONL, append-only) ──────────────────────────────────────────────────

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
        decisions[rec["candidate_key"]] = rec["decision"]
    return decisions


def append_decision(path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def append_note(vault_path, section: str) -> None:
    """Append a section to the running approvals note, creating it (with title) if absent."""
    path = Path(vault_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = section.strip()
    if path.exists():
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n\n" + body + "\n",
                        encoding="utf-8")
    else:
        path.write_text(f"{_NOTE_TITLE}\n\n{body}\n", encoding="utf-8")


# ── interactive triage loop ──────────────────────────────────────────────────────────────────

def triage(candidates, *, decisions_path, vault_path, input_fn=input, out=print) -> dict[str, int]:
    """Present each candidate, highest score first; approve -> vault note (if given) + sidecar,
    all decisions -> sidecar. Quit stops immediately without consuming further input."""
    counts = {APPROVED: 0, SKIPPED: 0, ARCHIVED: 0}
    decided_at = datetime.now(UTC).isoformat(timespec="seconds")
    for i, c in enumerate(candidates, start=1):
        out(format_candidate(c, rank=i))
        ans = input_fn("[a]pprove / [s]kip / [x]archive / [q]uit: ").strip().lower()
        if ans in ("q", "quit"):
            break
        if ans in ("a", "approve"):
            if vault_path is not None:
                append_note(vault_path, render_note(c))
            append_decision(decisions_path, decision_record(c, APPROVED, decided_at=decided_at))
            counts[APPROVED] += 1
        elif ans in ("x", "archive"):
            append_decision(decisions_path, decision_record(c, ARCHIVED, decided_at=decided_at))
            counts[ARCHIVED] += 1
        else:  # blank or 's' -> skip ("seen, pass"); won't re-surface
            append_decision(decisions_path, decision_record(c, SKIPPED, decided_at=decided_at))
            counts[SKIPPED] += 1
    return counts


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="setups",
        description="Cross-reference the roster against price structure and triage the candidates.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="as-of date, ISO format (default: today)")
    parser.add_argument("--limit", type=int, default=None, help="cap how many candidates to review")
    parser.add_argument("--min-score", type=float, default=None, help="drop candidates below this score")
    parser.add_argument("--tier", action="append", dest="tiers", choices=TIER_CHOICES,
                        help="restrict to this tier; repeatable")
    parser.add_argument("--vault-note", type=Path, default=None,
                        help="running approvals note to append to (omit to skip the vault write)")
    parser.add_argument("--list", action="store_true",
                        help="print the queue and exit, no prompting")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    as_of = args.as_of or datetime.now(UTC).date()

    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")

    candidates, stats = build_candidates(rows, registry, as_of=as_of, listings_map=listings_map)

    print(f"{stats.assets_total} assets -> {stats.assets_priced} priced, "
          f"{stats.assets_unpriced} with no price source, "
          f"{stats.assets_no_context} priced but no bars at/before {as_of.isoformat()}")
    print(f"{stats.candidate_count} candidates")
    if stats.rejections:
        print("  rejected: " + ", ".join(f"{k}={v}" for k, v in stats.rejections.most_common()))

    decided = set(load_decisions(DEFAULT_DECISIONS))
    undecided = drop_decided(candidates, decided)
    already_decided = len(candidates) - len(undecided)
    if already_decided:
        print(f"  {already_decided} already decided — skipped")

    tiers = tuple(args.tiers) if args.tiers else None
    queue = filter_candidates(undecided, min_score=args.min_score, tiers=tiers, limit=args.limit)

    if not queue:
        print("Nothing to review.")
        return 0

    if args.list:
        for i, c in enumerate(queue, start=1):
            print(format_candidate(c, rank=i))
        return 0

    if args.vault_note is None:
        print("  no --vault-note given; approvals will not be written to the vault")

    counts = triage(  # pragma: no cover - interactive
        queue, decisions_path=DEFAULT_DECISIONS, vault_path=args.vault_note)
    print(f"\napproved {counts[APPROVED]} · skipped {counts[SKIPPED]} · "  # pragma: no cover
          f"archived {counts[ARCHIVED]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
