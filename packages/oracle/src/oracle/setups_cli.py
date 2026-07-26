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
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from core.canon import Registry, load_registry, resolve_asset
from core.setups import (
    SCORE_VERSION,
    TIER_LARGE,
    TIER_MAJOR,
    TIER_NONCRYPTO,
    TIER_SMALL,
    TIER_UNRANKED,
    ZONE_LEVEL_REASONS,
    ZONE_TIMEFRAMES,
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

# The running approvals note. Derived from ``Path.home()`` rather than hardcoded to
# /Users/tseitz (which is what ``distill.triage_cli.DEFAULT_VAULT_NOTE`` does) so the
# default is portable. The filename matches the note that already exists, whose title
# is the ``_NOTE_TITLE`` below — this is the file that was being passed by hand.
DEFAULT_VAULT_NOTE = Path.home() / "vault" / "Trading" / "Trade Logs" / "Setups.md"

TIER_CHOICES = (TIER_MAJOR, TIER_LARGE, TIER_SMALL, TIER_UNRANKED, TIER_NONCRYPTO)

# How many candidates a default run puts in front of you.
#
# There is a cap at all because age stopped being a gate: every dated thesis now reaches the
# later gates, so the queue is bounded by *attention* rather than by a hidden constant
# deciding on your behalf what was too old to look at. The queue is score-ordered, so the cap
# always keeps the best — and the count of what it held back is printed, because a silently
# truncated list reads as "this is everything". TUNE: it should be a sitting's worth.
DEFAULT_LIMIT = 25

# Decision vocabulary. Deliberately distinct spelling from distill.triage_cli's
# "promoted"/"skipped"/"archived" — a setup is "approved" for execution, not "promoted"
# into a note; the two sidecars are unrelated and must never be confused on disk.
# Decisions. Four of them, because there are four genuinely different things a person means
# when they don't take a setup, and the original three all behaved identically — which made the
# labels promise a distinction the code didn't deliver.
#
#   APPROVED  taking it
#   LATER     the zone is fine, price isn't there yet. REVERSIBLE — see `is_stale_decision`.
#   REJECTED  judged and declined. Carries a reason, because "bad trade" calibrates the setups
#             scorer while "their view is wrong" calibrates the roster trust score, and those
#             are feedback to different consumers.
#   ARCHIVED  suppress permanently, explicitly NOT a judgment ("I don't trade this").
APPROVED = "approved"
LATER = "later"
REJECTED = "rejected"
ARCHIVED = "archived"

# Legacy: earlier runs recorded "skipped", which was permanent. Honour that rather than
# resurfacing everything already passed on — the sidecar is append-only and never rewritten.
SKIPPED = "skipped"

_PERMANENT = frozenset({APPROVED, REJECTED, ARCHIVED, SKIPPED})

# Why a candidate was rejected.
REASON_TRADE = "trade_quality"
REASON_VIEW = "view_wrong"
REASON_OTHER = "other"

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
    rejections: Counter = Counter()
    # Thesis-level refusals are identical across timeframes — a thesis whose weekly trend
    # disagrees disagrees once, however many zone timeframes it is tried against. Counting them
    # per pass would double every such tally and make the queue's headline diagnostic lie. Zone
    # -level ones are counted per pass, because "no live weekly zone" and "no live daily zone"
    # are genuinely two separate facts about two separate zones. See ZONE_LEVEL_REASONS.
    counted_once: set[tuple[str, str]] = set()
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
        for zone_timeframe in ZONE_TIMEFRAMES:
            # agreement_count is irrelevant here: collapse() recomputes each candidate's score
            # from its own regrouped agreement count, so whatever a single Setup carried never
            # survives to the collapsed Candidate.
            outcome = cross_reference(
                row, ctx, published_close=published_close,
                zone_timeframe=zone_timeframe,
                asset_rank=rank_cache[row.asset], agreement_count=0,
            )
            outcomes.append(outcome)
            if not isinstance(outcome, NotASetup):
                continue
            if outcome.reason in ZONE_LEVEL_REASONS:
                rejections[outcome.reason] += 1
            elif (outcome.thesis_id, outcome.reason) not in counted_once:
                counted_once.add((outcome.thesis_id, outcome.reason))
                rejections[outcome.reason] += 1

    candidates = collapse(outcomes)
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


def is_inside_zone(candidate: Candidate) -> bool:
    """Has price actually reached the zone? ``proximity`` saturates at 1.0 on arrival."""
    return candidate.proximity >= 1.0


def resurfaces(candidate: Candidate, record: dict | None) -> bool:
    """Whether a previously-deferred candidate is worth showing again.

    Only ``LATER`` is reversible, and only on the two things that genuinely change the
    situation: **price has entered the zone**, or **another person now backs it**. Everything
    else stays buried.

    This exists because deferring used to be permanent, inherited from ``triage_cli`` where it
    made sense — a skipped *thesis* is a judgment about the thesis. A deferred *zone* is almost
    always a judgment about timing: on the first live run **every candidate had depth 0.00**,
    meaning price had reached none of them. Burying those forever would discard the setup at
    exactly the moment it became actionable.
    """
    if record is None:
        return True
    if record.get("decision") != LATER:
        return False
    was_inside = bool(record.get("inside_zone"))
    if is_inside_zone(candidate) and not was_inside:
        return True
    return candidate.agreement > int(record.get("agreement") or 0)


def drop_decided(candidates, decided: dict[str, dict]) -> list[Candidate]:
    """Candidates not already settled — deferrals return once the situation has moved on."""
    return [c for c in candidates if resurfaces(c, decided.get(c.key))]


def decision_record(candidate: Candidate, decision: str, *, decided_at: str,
                    reason: str | None = None) -> dict:
    """A JSON-serializable decision, keyed on the candidate's content-addressed zone.

    ``inside_zone`` and ``agreement`` are the state a later run compares against to decide
    whether a deferral has become actionable, so they are recorded even for decisions that are
    permanent — the file is the audit trail, not just a suppression list.
    """
    record = {
        "candidate_key": candidate.key,
        "decision": decision,
        "decided_at": decided_at,
        "asset": candidate.asset,
        "direction": candidate.direction,
        "entry": candidate.entry,
        # Which series the zone came from. Recorded because the whole reason weekly zones exist
        # is the open question "do weekly setups actually beat daily ones", and that stays
        # answerable only if every decision says which kind it was judging.
        "zone_timeframe": candidate.zone_timeframe,
        "stop": candidate.stop,
        "target": candidate.target,
        "target_source": candidate.target_source,
        "score": candidate.score,
        # Which scale ``score`` is on. Weights change as the ranker is tuned, and the sidecar
        # is the only record of what a candidate was worth at the moment it was judged — so
        # correlating decisions against scores across a re-weighting compares two different
        # scales unless the generation travels with the number.
        "score_version": SCORE_VERSION,
        "proximity": candidate.proximity,
        "freshness": candidate.freshness,
        "trend_alignment": candidate.trend_alignment,
        "inside_zone": is_inside_zone(candidate),
        "agreement": candidate.agreement,
        "newest_at": candidate.newest_at,
        "people": list(candidate.people),
        "thesis_ids": list(candidate.thesis_ids),
    }
    if reason is not None:
        record["reason"] = reason
    return record


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
    # Age and macro alignment stopped being gates, so the queue has to show them. A soft gate
    # that isn't displayed is strictly worse than a hard one: the candidate arrives looking
    # like every other, and the judgement it was softened to enable can't actually be made.
    unaligned = "  ·  no macro alignment" if not c.trend_alignment else ""
    lines = [
        # The zone timeframe leads the heading because the same asset can now appear twice —
        # once per timeframe — and the two differ in exactly the numbers a glance skips over.
        # Unlabelled, a weekly and a daily GOOGL long read as a duplicate rather than as two
        # setups with different risk.
        f"\n{header}{c.asset} {c.direction.upper()} · {c.zone_timeframe} zone"
        f" · tier {c.tier} · score {c.score:.2f}",
        f"  last called {c.newest_at}{span}  ·  freshness {c.freshness:.2f}",
        f"  entry zone {c.entry_bottom:g}–{c.entry_top:g}  ·  entry {c.entry:g}",
        f"  stop {c.stop:g}  ·  invalidation {c.invalidation:g}",
        f"  target {c.target:g}  [{c.target_source}]",
        f"  reward:risk {c.reward_risk:.2f}  ·  proximity {c.proximity:.2f}  ·  depth {c.depth:.2f}",
        f"  weekly {c.weekly_trend} · daily {c.daily_trend} · zone {c.zone}{unaligned}",
        f"  people: {format_views(c)}  ·  agreement {c.agreement}",
    ]
    return "\n".join(lines)


def format_views(candidate: Candidate) -> str:
    """``Person (date)`` per supporter, newest first — so a stale voice is visible as one."""
    return ", ".join(f"{v.person} ({v.published_at})" for v in candidate.views)


def render_note(candidate: Candidate, *, decided_on: str) -> str:
    """One markdown section for an approved candidate, same shape as ``triage_cli.render_note``.

    ``decided_on`` (a YYYY-MM-DD string) leads the heading because the note is append-only and
    the rest of the heading is not unique: approving the same asset again weeks later at a
    different entry would otherwise render a byte-identical ``## ZEC long · tier large`` section,
    leaving no way to tell the two apart or read the file chronologically. This mirrors the
    dated headings already used in ``Promoted Theses.md``.
    """
    c = candidate
    lines = [
        f"## {decided_on} · {c.asset} {c.direction} · {c.zone_timeframe} zone"
        f" · tier {c.tier} · score {c.score:.2f}",
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

def load_decisions(path) -> dict[str, dict]:
    """Latest decision record per candidate key.

    The whole record is returned, not just the verdict, because deciding whether a deferral has
    become actionable needs the state captured at the time. Later lines win, so re-deciding a
    zone supersedes the earlier answer without rewriting history.
    """
    path = Path(path)
    if not path.exists():
        return {}
    decisions: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        decisions[rec["candidate_key"]] = rec
    return decisions


def append_decision(path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


class VaultNoteUnavailable(Exception):
    """The vault note's directory does not exist, so the approvals note cannot be written."""


def resolve_vault_note(path, *, disabled: bool):
    """Return the note path to write to, or None when the vault write is switched off.

    Raises ``VaultNoteUnavailable`` when the parent directory is missing rather than creating
    it. ``append_note`` calls ``mkdir(parents=True)``, so defaulting the path would otherwise
    scatter an empty, fake vault tree onto any machine where the real vault isn't mounted —
    and the approvals would look filed while living somewhere nobody reads.

    Callers must invoke this *before* the triage loop starts. Failing after a decision has
    been entered would throw away the session's judgement, which is the scarce input here.
    """
    if disabled:
        return None
    path = Path(path)
    if not path.parent.is_dir():
        raise VaultNoteUnavailable(
            f"vault note path {path} is not reachable ({path.parent} does not exist). "
            f"Pass --vault-note <path> to point somewhere else, or --no-vault-note to "
            f"record approvals to the decisions sidecar only."
        )
    return path


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
    counts = {APPROVED: 0, LATER: 0, REJECTED: 0, ARCHIVED: 0}
    decided_at = datetime.now(UTC).isoformat(timespec="seconds")
    for i, c in enumerate(candidates, start=1):
        out(format_candidate(c, rank=i))
        ans = input_fn("[a]pprove / [l]ater / [r]eject / [x]archive / [q]uit: ").strip().lower()
        if ans in ("q", "quit"):
            break

        reason = None
        if ans in ("a", "approve"):
            decision = APPROVED
            if vault_path is not None:
                append_note(vault_path, render_note(c, decided_on=decided_at[:10]))
        elif ans in ("r", "reject"):
            decision = REJECTED
            reason = _ask_reason(input_fn)
        elif ans in ("x", "archive"):
            decision = ARCHIVED
        else:
            # Blank falls through to LATER on purpose: it is the only reversible answer, so a
            # stray keypress defers rather than permanently burying a setup.
            decision = LATER

        append_decision(decisions_path,
                        decision_record(c, decision, decided_at=decided_at, reason=reason))
        counts[decision] += 1
    return counts


def format_counts(counts: dict[str, int]) -> str:
    """The end-of-session summary, derived from the counts rather than hand-listing verdicts.

    Hand-listing is exactly what broke: the line named `skipped` explicitly, the vocabulary
    changed under it, and a ``# pragma: no cover`` meant nothing caught the drift until it
    raised ``KeyError`` at the end of a real triage session. Deriving it means adding a verdict
    can never leave the summary behind.
    """
    return " · ".join(f"{verdict} {count}" for verdict, count in counts.items())


_REASONS = {"t": REASON_TRADE, "v": REASON_VIEW, "o": REASON_OTHER}


def _ask_reason(input_fn) -> str:
    """Why the reject — one keystroke.

    Worth the extra key because the two answers feed different things: bad trade quality
    calibrates the setups scorer, a wrong view calibrates the roster's trust score. Collapsing
    them would discard the distinction that matters most to what the roster is actually for.
    """
    ans = input_fn("  why? [t]rade quality / [v]iew wrong / [o]ther: ").strip().lower()
    return _REASONS.get(ans[:1], REASON_OTHER)


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="setups",
        description="Cross-reference the roster against price structure and triage the candidates.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="as-of date, ISO format (default: today)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"cap how many candidates to review (default: {DEFAULT_LIMIT}; "
                             f"0 for no cap)")
    parser.add_argument("--min-score", type=float, default=None, help="drop candidates below this score")
    parser.add_argument("--tier", action="append", dest="tiers", choices=TIER_CHOICES,
                        help="restrict to this tier; repeatable")
    parser.add_argument("--vault-note", type=Path, default=DEFAULT_VAULT_NOTE,
                        help=f"running approvals note to append to (default: {DEFAULT_VAULT_NOTE})")
    parser.add_argument("--no-vault-note", action="store_true",
                        help="record approvals to the decisions sidecar only, skipping the vault")
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

    decided = load_decisions(DEFAULT_DECISIONS)
    undecided = drop_decided(candidates, decided)
    already_decided = len(candidates) - len(undecided)
    if already_decided:
        print(f"  {already_decided} already decided — hidden")
    returning = sum(
        1 for c in undecided
        if (rec := decided.get(c.key)) is not None and rec.get("decision") == LATER
    )
    if returning:
        print(f"  {returning} deferred earlier, back because price arrived or support grew")

    tiers = tuple(args.tiers) if args.tiers else None
    qualified = filter_candidates(undecided, min_score=args.min_score, tiers=tiers)
    queue = filter_candidates(qualified, limit=None if args.limit == 0 else args.limit)
    held_back = len(qualified) - len(queue)

    if not queue:
        print("Nothing to review.")
        return 0

    if held_back:
        print(f"  showing the top {len(queue)} by score — {held_back} more qualify "
              f"(--limit 0 for all)")

    if args.list:
        for i, c in enumerate(queue, start=1):
            print(format_candidate(c, rank=i))
        return 0

    # Resolved before the first prompt: a vault that turns out to be unreachable must fail
    # while the cost is zero, not after a session's worth of decisions has been entered.
    try:
        vault_note = resolve_vault_note(args.vault_note, disabled=args.no_vault_note)
    except VaultNoteUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if vault_note is None:
        print("  --no-vault-note; approvals recorded to the decisions sidecar only")
    else:
        print(f"  approvals append to {vault_note}")

    counts = triage(  # pragma: no cover - interactive
        queue, decisions_path=DEFAULT_DECISIONS, vault_path=vault_note)
    print("\n" + format_counts(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
