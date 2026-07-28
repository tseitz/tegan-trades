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
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

from core.canon import Registry, load_registry, resolve_asset
from core.setups import (
    ARRIVAL,
    CARRY_HOLD_DAYS,
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
from execution.broker import NETWORKS

from oracle import cache, carry, corpus, derived, exclusions, execute, listings
# Re-exported: the sidecar's storage and its vault mirror live in their own module, but both
# remain part of this CLI's surface for callers and tests that reach for them here.
from oracle.decisions import append_decision, load_decisions, sync_mirror  # noqa: F401
from oracle.exclusions import DEFAULT_EXCLUSIONS
# Re-exported: choosing which candidates a sitting sees is its own concern now — see that
# module's docstring for why it stopped being ``qualified[:limit]`` — but both names remain
# part of this CLI's surface for callers and tests that reach for them here.
from oracle import queue as queue_mod
from oracle.queue import QueuePosition, build_queue, filter_candidates  # noqa: F401
from oracle.resample import to_weekly
from oracle import route as route_mod
from oracle.route import (
    DerivedRef,
    OracleRef,
    Priceable,
    RoutingTable,
    Unpriceable,
    load_routing_table,
    route,
)
# Re-exported: the queue's layout lives in its own module, but ``format_candidate`` remains
# part of this CLI's surface for callers and tests that reach for it here.
from oracle.setups_render import (  # noqa: F401
    format_candidate,
    format_views,
    supports_color,
    thesis_pairing,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = REPO_ROOT / "cfg"
DEFAULT_DECISIONS = REPO_ROOT / "data" / "setups" / "decisions.jsonl"

# The running approvals note. Derived from ``Path.home()`` rather than hardcoded to
# /Users/tseitz (which is what ``distill.triage_cli.DEFAULT_VAULT_NOTE`` does) so the
# default is portable. The filename matches the note that already exists, whose title
# is the ``_NOTE_TITLE`` below — this is the file that was being passed by hand.
DEFAULT_VAULT_NOTE = Path.home() / "vault" / "Trading" / "Trade Logs" / "Setups.md"

# The sidecar's second copy. Sits beside the approvals note because it is the same kind of
# thing — hand-entered judgement, which ``architecture.md`` puts on the vault side of the
# repo/vault boundary — and because ``data/`` is gitignored, so the sidecar is otherwise
# unbacked. See ``oracle.decisions`` and ``docs/IMPROVEMENTS.md`` §4b.
DEFAULT_MIRROR = DEFAULT_VAULT_NOTE.parent / "decisions.jsonl"

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
#   ARCHIVED  suppress permanently. Asked WHICH KIND — see ARCHIVE_ASSET/ARCHIVE_SETUP.
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

# Which kind of archive. Recorded in the same ``reason`` field as the rejection vocabulary —
# distinct values, and ``decision`` already namespaces them, so a mining pass partitions on the
# pair rather than needing a sixth column.
#
# **Why archive is asked at all**, when it was documented for months as "explicitly NOT a
# judgment": one key was doing two jobs. In the 2026-07-27 session 8 of 25 decisions were
# archives, none carried a reason, and they were confirmed afterwards to be a *mix* of "I don't
# trade this asset" and "this setup is stale". That made all 8 unminable — and §4 mines exactly
# this file — while leaving the asset-level ones ineffective anyway, because ``drop_decided``
# keys on the zone. One keystroke separates them at the only moment the meaning is known.
#
# ASSET also writes ``cfg/exclusions.yaml``, which is what makes an asset-level "no" stick.
# SETUP is the conservative default for anything unrecognised: guessing ASSET from a stray
# keypress would delete a whole market from the queue permanently.
ARCHIVE_ASSET = "asset"
ARCHIVE_SETUP = "setup"

_NOTE_TITLE = "# Approved Setups"


# ── engine assembly (impure: routes assets, reads the price cache) ─────────────────────────

@dataclass(frozen=True)
class BuildStats:
    """What happened while turning the corpus into candidates — the audit trail.

    Every count here answers "did the gates produce nothing, or was there nothing to gate":
    ``unpriceable`` covers assets routing refused, ``assets_uncached`` covers assets that route
    fine but have nothing in the price cache, ``assets_no_context`` covers assets with bars but
    none at-or-before ``as_of``, and ``rejections`` covers theses that reached
    ``cross_reference`` and were refused there. Four different failure points, four different
    counters — collapsing them into one would make the eventual "zero candidates" unanswerable.

    ``unpriceable`` is a Counter rather than an int for the same reason ``rejections`` is. It
    was an int, and that int was the single most misleading number the queue printed: "183 with
    no price source" reads as 183 missed opportunities when 27 of those assets are not
    instruments at all and 25 more route perfectly well and simply have not been fetched. See
    ``route.NOT_AN_ASSET`` and the grouping beside it.
    """
    assets_total: int
    assets_priced: int
    unpriceable: Counter          # route refused, by reason
    assets_uncached: int          # routed fine, nothing in the price cache
    assets_no_context: int
    rejections: Counter
    candidate_count: int

    @property
    def assets_unpriced(self) -> int:
        """Everything that never reached a ``Context``, however it failed. The headline."""
        return sum(self.unpriceable.values()) + self.assets_uncached


def _load_daily(resolved: Priceable, *, table: RoutingTable, series_cache: dict):
    """Load a routed reference's daily ``PriceSeries``, or None when it cannot be assembled.

    Mirrors ``score_cli._load_series`` — same cache, same refusal to invent a second path from
    asset to price. Routing moved out to the caller so the *reason* a route failed survives:
    returning a bare None conflated "this is not an instrument" with "nobody has fetched it",
    which are opposite problems with opposite fixes.

    A ``DerivedRef`` is assembled from its two legs, which are routed through the same table
    and therefore obey the same curation. **Each leg must resolve to a fetched series** — a leg
    that is itself derived returns None rather than recursing, because nothing in the corpus
    needs a ratio of ratios and refusing is cheaper than reasoning about cycles.
    """
    if resolved.asset in series_cache:
        return series_cache[resolved.asset]

    series = None
    if isinstance(resolved, DerivedRef):
        legs = []
        for leg in (resolved.numerator, resolved.denominator):
            leg_route = route(leg, table)
            legs.append(
                _load_daily(leg_route, table=table, series_cache=series_cache)
                if isinstance(leg_route, OracleRef) else None
            )
        if all(leg is not None for leg in legs):
            series = derived.ratio(legs[0], legs[1], symbol=resolved.asset)
    else:
        series = cache.load(resolved.source, resolved.symbol)

    series_cache[resolved.asset] = series
    return series


def build_candidates(
    rows,
    registry: Registry,
    *,
    as_of: date,
    listings_map,
    config_dir: Path = CONFIG_DIR,
    funding_venue: str | None = carry.DEFAULT_VENUE,
) -> tuple[tuple[Candidate, ...], BuildStats]:
    """Route every asset the corpus mentions, build one ``Context`` each, gate every row
    against its asset's context, and collapse the outcome stream into candidates."""
    table = load_routing_table(
        config_dir, [(r.asset, r.domain) for r in rows], listings=listings_map
    )
    assets = sorted({r.asset for r in rows})

    # What holding each asset costs, on the venue it would actually trade on. Empty when the
    # funding log has not been seeded — every carry field then stays None and the engine
    # behaves exactly as it did before, which is the point of shipping this display-only.
    outlooks = (
        carry.outlooks_for(assets, venue=funding_venue) if funding_venue else {}
    )

    series_cache: dict = {}
    contexts: dict[str, tuple] = {}
    unpriceable: Counter = Counter()
    uncached = 0
    no_context = 0
    for asset in assets:
        resolved = route(asset, table)
        # Routing's own verdict, kept rather than flattened to None. ``Unpriceable`` already
        # carries a reason and always did; the tally simply threw it away.
        if isinstance(resolved, Unpriceable):
            unpriceable[resolved.reason] += 1
            continue
        daily = _load_daily(resolved, table=table, series_cache=series_cache)
        if daily is None:
            uncached += 1
            continue
        weekly = to_weekly(daily)
        ctx = build_context(daily.bars, weekly.bars, as_of=as_of)
        if ctx is None:
            no_context += 1
            continue
        # Attached after the fact rather than threaded through ``build_context``, which
        # computes structure from bars and has no business knowing about venues.
        outlook = outlooks.get(asset)
        if outlook is not None:
            ctx = replace(ctx, funding=outlook)
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
        unpriceable=unpriceable, assets_uncached=uncached, assets_no_context=no_context,
        rejections=rejections, candidate_count=len(candidates),
    )
    return candidates, stats


# ── pure: decision records, formatting ──────────────────────────────────────────────────────

def format_unpriced(stats: BuildStats) -> str:
    """The unpriced count, split into the groups that have different answers.

    The single number this replaces read as "N missed opportunities" and was mostly nothing of
    the kind. Grouping is the whole content: ``not an instrument`` needs no fix and never will,
    ``computable`` is the actual backlog, and ``no route`` is a long tail of one-row labels the
    LLM lifted from transcripts. Counts are per *asset*, matching the line above.

    Sorted within each group by size, and the group is named before the reasons, because the
    failure mode being fixed is a reader summing numbers that do not belong together.
    """
    groups = (
        ("computable", route_mod.COMPUTABLE),
        ("no route", route_mod.NO_ROUTE),
        ("not an instrument", route_mod.NOT_AN_ASSET),
    )
    parts = []
    for label, reasons in groups:
        hit = {r: n for r, n in stats.unpriceable.items() if r in reasons}
        if hit:
            detail = ", ".join(f"{r} {n}" for r, n in sorted(hit.items(), key=lambda kv: -kv[1]))
            parts.append(f"{label} {sum(hit.values())} ({detail})")
    # Anything the groups above don't claim. Printed rather than dropped: a reason added to
    # oracle_map.yaml and not to route.py would otherwise vanish from this line silently, which
    # is exactly how ``event`` and ``derived_ratio`` stayed unnameable in the first place.
    known = set().union(*(rs for _, rs in groups))
    rest = {r: n for r, n in stats.unpriceable.items() if r not in known}
    if rest:
        parts.append("ungrouped " + ", ".join(f"{r} {n}" for r, n in sorted(rest.items())))
    if stats.assets_uncached:
        parts.append(f"routed but never fetched {stats.assets_uncached}")
    return "unpriced: " + " · ".join(parts) if parts else "unpriced: none"


def is_inside_zone(candidate: Candidate) -> bool:
    """Has price actually reached the zone? ``ARRIVAL`` is where the near edge sits on the
    approach ramp, so at or above it price is in the zone rather than still travelling."""
    return candidate.approach >= ARRIVAL


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
                    reason: str | None = None, note: str | None = None,
                    queue: QueuePosition | None = None) -> dict:
    """A JSON-serializable decision, keyed on the candidate's content-addressed zone.

    ``inside_zone`` and ``agreement`` are the state a later run compares against to decide
    whether a deferral has become actionable, so they are recorded even for decisions that are
    permanent — the file is the audit trail, not just a suppression list.

    ``queue`` is what the candidate was judged *against* — see ``oracle.queue`` for why a
    verdict is meaningless without it. Optional because it is genuinely absent on the 77 rows
    written before it existed, and those must stay absent: a re-run would yield today's queue,
    not the one that was on screen, which is the trap §4's do-not-backfill rule spells out.
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
        # Both are inputs the correlation needs and neither was recoverable after the fact.
        # ``reward_risk`` is a weighted term in ``_score`` *and* the headline number in the
        # queue, so its weight was the one thing decisions could not be mined against;
        # ``price`` fixes the decision to a market state, without which "was this judged in
        # the zone or halfway to the target" is unanswerable once prices move on.
        "reward_risk": candidate.reward_risk,
        # The scored ratio, distinct from the one above since `SCORE_VERSION` 6 — §19(d). Both
        # are recorded for the same reason §21 records both nominal and carry-adjusted R:R:
        # which of them actually predicts an approval is a measurement nobody has made, and it
        # stays unanswerable unless every decision carries both.
        "reward_risk_from_price": candidate.reward_risk_from_price,
        "price": candidate.price,
        "score": candidate.score,
        # Which scale ``score`` is on. Weights change as the ranker is tuned, and the sidecar
        # is the only record of what a candidate was worth at the moment it was judged — so
        # correlating decisions against scores across a re-weighting compares two different
        # scales unless the generation travels with the number.
        "score_version": SCORE_VERSION,
        # One number where v2 recorded ``proximity`` and silently omitted ``depth``. That gap
        # made the mining pass structurally unable to see 0.15 of the score: for the 14 v2
        # rows the combined depth-and-RR contribution can only be recovered as a single
        # residual (measured 0.243 approved vs 0.246 rejected — no signal either way, but not
        # separable into which term carried it). ``approach`` is the whole ramp, so v3 rows
        # leave nothing unrecorded.
        "approach": candidate.approach,
        "freshness": candidate.freshness,
        "trend_alignment": candidate.trend_alignment,
        # The two raw trend states behind ``trend_alignment``, which is only ever 1.0 or 0.0 and
        # so cannot say *which* states produced it. Recorded for §27: the daily leg was a gate
        # until 2026-07-28, so every row judged before then had a daily trend that agreed or was
        # ranging and the sidecar holds **zero** negatives for it. Whether daily alignment
        # belongs in ``_score`` at all (§27's option 2) is unanswerable without them — the same
        # record-both-then-measure pattern §21 used for carry and §19(d) for the two R:R
        # numbers. Additive, so per §4(d) no ``score_version`` bump, and the existing rows must
        # NOT be backfilled: a re-run yields today's trend state, not the decision-time one.
        "weekly_trend": candidate.weekly_trend,
        "daily_trend": candidate.daily_trend,
        # Carry, recorded so §21's question — does carry-adjusted R:R predict the decision
        # better than nominal R:R does — becomes minable from the next session onward. Both
        # are None for an asset with no funding observations, which is itself a fact worth
        # recording: it says the decision was made without carry information, not that carry
        # was zero. Additive fields, so per §4(d) no ``score_version`` bump — and the 54
        # existing rows must NOT be backfilled, for the same reason §4(a) refuses it.
        "funding_annual": candidate.funding_annual,
        "carry_reward_risk": candidate.carry_reward_risk,
        "inside_zone": is_inside_zone(candidate),
        "agreement": candidate.agreement,
        "newest_at": candidate.newest_at,
        "people": list(candidate.people),
        "thesis_ids": list(candidate.thesis_ids),
    }
    if reason is not None:
        record["reason"] = reason
    # Omitted rather than written blank: a mining pass has to tell "no note given" apart from
    # a note that exists and says nothing, and an empty string reads as the latter.
    if note:
        record["reason_note"] = note
    if queue is not None:
        # What else was on screen. ``queue_mode`` is the load-bearing one — it says whether
        # this sitting may be pooled with another at all — and ``queue_band`` narrows that to
        # the half that is genuinely a sample, since the head is still a score-ordered slice.
        # ``queue_score_min``/``_max`` describe the rows that were *offered*, which is what a
        # sitting abandoned halfway cannot otherwise report.
        #
        # Additive, so ``score_version`` deliberately does NOT move: ``core.setups._score`` is
        # untouched here, and bumping it would re-partition the sidecar and strand the v5
        # cohort this is trying to grow. Same precedent as §21's carry fields.
        record.update({
            "queue_mode": queue.mode,
            "queue_band": queue.band,
            "queue_rank": queue.rank,
            "queue_size": queue.size,
            "queue_score_min": queue.score_min,
            "queue_score_max": queue.score_max,
            "queue_population": queue.population,
        })
    return record


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
        f"- **Reward:risk:** {c.reward_risk:.2f}"
        + (
            ""
            if c.carry_reward_risk is None
            else f" (carry-adjusted {c.carry_reward_risk:.2f}"
                 f" at {c.funding_annual:.1%}/yr funding over {CARRY_HOLD_DAYS}d)"
        ),
        f"- **Trend:** weekly {c.weekly_trend} · daily {c.daily_trend} · zone {c.zone}",
        f"- **Supporting:** {format_views(c)} (agreement {c.agreement})",
        f"- **Theses:** {', '.join(c.thesis_ids)}",
    ]
    return "\n".join(lines)


# ── decisions sidecar (JSONL, append-only) ──────────────────────────────────────────────────

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

def triage(queue, *, decisions_path, vault_path, input_fn=input, out=print,
           as_of: date | None = None, color: bool | None = None,
           mirror_path=None, exclusions_path=None, session=None) -> dict[str, int]:
    """Present each candidate in queue order; approve -> vault note (if given) + sidecar,
    all decisions -> sidecar. Quit stops immediately without consuming further input.

    ``queue`` is an ``oracle.queue.Queue`` rather than a bare list because every decision has
    to record what else was on screen when it was made. A plain sequence of candidates cannot
    answer that, and §4 is the account of what a file that cannot answer it is worth.

    ``as_of`` is only used to age each candidate for display, so it stays optional — a caller
    that doesn't supply one gets the date without the day count rather than an exception.
    ``color`` defaults to autodetection, and is an explicit argument so tests pin it off.

    ``exclusions_path`` is where an asset-level archive writes its standing rule. Optional, and
    its absence downgrades rather than fails: the archive is still recorded, it just doesn't
    stick across zones. Every write here is subordinate to the sidecar for the same reason the
    vault mirror is — losing a config line is recoverable, losing the judgement is not.

    ``session`` is an ``execution.session.Session`` when ``--execute`` was passed, and None
    otherwise — which is the default and the entire behaviour of this loop before it existed.
    It is offered **after** the approval has been written, never before: an order is a
    consequence of a decision, so the decision must already be durable when it is proposed.
    Declining the order leaves the approval standing.
    """
    counts = {APPROVED: 0, LATER: 0, REJECTED: 0, ARCHIVED: 0}
    decided_at = datetime.now(UTC).isoformat(timespec="seconds")
    color = supports_color() if color is None else color
    total = len(queue.rows)
    pairing = thesis_pairing(queue.candidates)
    for i, row in enumerate(queue.rows, start=1):
        c = row.candidate
        zones, index = pairing[i - 1]
        out(format_candidate(c, rank=i, total=total, as_of=as_of, color=color,
                             zones_in_thesis=zones, zone_index=index))
        ans = input_fn("[a]pprove / [l]ater / [r]eject / [x]archive / [q]uit: ").strip().lower()
        if ans in ("q", "quit"):
            break

        reason, note = None, None
        if ans in ("a", "approve"):
            decision = APPROVED
            if vault_path is not None:
                append_note(vault_path, render_note(c, decided_on=decided_at[:10]))
        elif ans in ("r", "reject"):
            decision = REJECTED
            reason, note = _ask_reason(input_fn)
        elif ans in ("x", "archive"):
            decision = ARCHIVED
            reason, note = _ask_archive_kind(input_fn)
            if reason == ARCHIVE_ASSET:
                _record_exclusion(c.asset, note, path=exclusions_path, out=out)
        else:
            # Blank falls through to LATER on purpose: it is the only reversible answer, so a
            # stray keypress defers rather than permanently burying a setup.
            decision = LATER

        append_decision(
            decisions_path,
            decision_record(c, decision, decided_at=decided_at, reason=reason, note=note,
                            queue=queue.position(i)),
            mirror=mirror_path, warn=out)
        counts[decision] += 1

        # Strictly after the decision is on disk. An order is a consequence of an approval,
        # so the approval must survive independently of whether the order does — and a venue
        # timeout must never be able to lose the judgement that preceded it.
        if session is not None and decision == APPROVED:
            execute.offer(session, c, input_fn=input_fn, out=out)
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


def _ask_reason(input_fn) -> tuple[str, str | None]:
    """Why the reject — a keystroke, plus a free-text note.

    The keystroke is worth its own key because the answers feed different things: bad trade
    quality calibrates the setups scorer, a wrong view calibrates the roster's trust score.
    Collapsing them would discard the distinction that matters most to what the roster is for.

    But the enum only says *which* loop to calibrate, never *what to change* — three buckets
    cannot distinguish "reward:risk too thin at this tier" from "the zone is four months
    stale". That is the whole content of a tuning pass, so the note is recorded verbatim
    alongside it and left unparsed.

    Anything longer than the one keystroke is taken as the note itself, so typing a whole
    sentence at this prompt works and nothing typed is ever discarded — previously only the
    first letter survived. A bare keystroke falls through to a second prompt instead.
    """
    ans = input_fn("  why? [t]rade quality / [v]iew wrong / [o]ther: ").strip()
    reason = _REASONS.get(ans[:1].lower(), REASON_OTHER)
    if len(ans) > 1:
        return reason, ans
    note = input_fn("  note (enter to skip): ").strip()
    return reason, note or None


_ARCHIVE_KINDS = {"a": ARCHIVE_ASSET, "s": ARCHIVE_SETUP}


def _ask_archive_kind(input_fn) -> tuple[str, str | None]:
    """Which of the two archives this is, plus the note that becomes the exclusion's reason.

    Same shape as ``_ask_reason`` deliberately — one keystroke, and anything longer is taken as
    the note — so the two prompts behave identically under the fingers and nothing typed is
    discarded.

    **Unrecognised falls to ``ARCHIVE_SETUP``, not to a third "unknown" bucket.** The asymmetry
    is the point: a setup archive is inert, while an asset archive writes a standing rule that
    removes a market from the queue for good. Defaulting the ambiguous case to the destructive
    one is exactly how a stray keypress becomes folklore. Note this is the opposite default from
    ``_ask_reason``, which falls to ``REASON_OTHER`` — there, every branch is equally inert.
    """
    ans = input_fn("  archive: [a]sset (never show this asset) / [s]etup (just this zone): ").strip()
    kind = _ARCHIVE_KINDS.get(ans[:1].lower(), ARCHIVE_SETUP)
    if len(ans) > 1:
        return kind, ans
    prompt = ("  reason (required, becomes the exclusion): " if kind == ARCHIVE_ASSET
              else "  note (enter to skip): ")
    return kind, input_fn(prompt).strip() or None


def _record_exclusion(asset: str, reason: str | None, *, path, out) -> None:
    """Make an asset-level archive stick, or say clearly why it didn't.

    Subordinate to the sidecar in every branch — this runs *after* nothing and *before* nothing
    that matters, and it never raises. The decision is recorded by the caller either way, which
    is the same rule ``oracle.decisions`` applies to the vault mirror: a convenience that fails
    must not take a judgement down with it.

    Silence is the one unacceptable outcome, so all four cases print. A gate everyone believes
    is running and isn't is worse than no gate — §6h's whole failure class.
    """
    if path is None:
        out(f"  ! archived {asset} for the asset, but no exclusions file is configured"
            f" — it will resurface on the next zone")
        return
    if not reason:
        out(f"  ! no reason given, so {asset} was NOT added to {Path(path).name}"
            f" — the decision is recorded, but the asset will resurface on the next zone")
        return
    try:
        added = exclusions.append(path, asset, reason)
    except (OSError, ValueError) as exc:
        out(f"  ! could not write {Path(path).name}: {exc}"
            f" — the decision is recorded; add {asset} by hand to make it stick")
        return
    if added:
        out(f"  + {Path(path).name}: {asset.upper()} — {reason}")
        out("    review before committing")
    else:
        out(f"  = {asset.upper()} is already excluded in {Path(path).name}; left as it is")


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
    # The opt-out, not the default. A score-ordered cap is what made every sitting judge a
    # narrower slice than the last, so `top` is kept only for the run where you want tonight's
    # best trades and nothing else — and the sidecar records which one ran, because the two
    # produce decisions that cannot be pooled with each other.
    parser.add_argument("--sample", choices=(queue_mod.STRATIFIED, queue_mod.TOP),
                        default=queue_mod.STRATIFIED,
                        help=f"how to fill the queue when more candidates qualify than "
                             f"--limit (default: %(default)s — the top {queue_mod.HEAD_SIZE} "
                             f"by score plus one draw per stratum across the rest; 'top' is "
                             f"the first --limit rows in queue order, which is weekly before "
                             f"daily and NOT best-score-first)")
    parser.add_argument("--min-score", type=float, default=None, help="drop candidates below this score")
    parser.add_argument("--tier", action="append", dest="tiers", choices=TIER_CHOICES,
                        help="restrict to this tier; repeatable")
    parser.add_argument("--vault-note", type=Path, default=DEFAULT_VAULT_NOTE,
                        help=f"running approvals note to append to (default: {DEFAULT_VAULT_NOTE})")
    parser.add_argument("--no-vault-note", action="store_true",
                        help="record approvals to the decisions sidecar only, skipping the vault")
    # Every other path this CLI touches is overridable; the primary sidecar was the one
    # exception, which made a rehearsal run impossible — a decided queue is permanently
    # empty, so there was no way to exercise the approve path without spending a real
    # judgement. Pointing this elsewhere forces the vault mirror OFF (see main).
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS,
                        help="decisions sidecar to read and append to "
                             "(default: %(default)s). Anything else is a scratch run: the "
                             "vault mirror is disabled and every candidate looks undecided.")
    parser.add_argument("--decisions-mirror", type=Path, default=DEFAULT_MIRROR,
                        help=f"second copy of the decisions sidecar (default: {DEFAULT_MIRROR})")
    parser.add_argument("--no-mirror", action="store_true",
                        help="skip the vault mirror; the sidecar under data/ is then the only copy")
    parser.add_argument("--exclusions", type=Path, default=CONFIG_DIR / DEFAULT_EXCLUSIONS,
                        help="assets to keep out of the queue entirely (cfg/exclusions.yaml)")
    parser.add_argument("--funding-venue", default=carry.DEFAULT_VENUE,
                        help="venue whose funding prices the carry (default: %(default)s)")
    parser.add_argument("--no-funding", action="store_true",
                        help="ignore the funding log; carry is not computed or displayed")
    parser.add_argument("--list", action="store_true",
                        help="print the queue and exit, no prompting")
    # Execution is opt-in per run and has no config-file switch that could turn it on.
    # A flag you must type every time is the difference between "I meant to trade tonight"
    # and "I forgot this was still enabled from last week".
    parser.add_argument("--execute", action="store_true",
                        help="after approving, offer to place the trade on the configured "
                             "venue (default: off — approvals are recorded only)")
    parser.add_argument("--network", choices=sorted(NETWORKS), default=None,
                        help="override cfg/execution.yaml's network; mainnet additionally "
                             "requires a typed confirmation")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    as_of = args.as_of or datetime.now(UTC).date()

    # A scratch sidecar is a rehearsal, and the vault mirror belongs to the real one. Syncing
    # them would compare a deliberately-empty file against the true history and report a
    # divergence — or, worse on a fresh scratch file, restore 77 real decisions into it.
    decisions_path = args.decisions
    scratch = decisions_path != DEFAULT_DECISIONS
    if scratch:
        print(f"  SCRATCH RUN — decisions go to {decisions_path}, not the real sidecar")
        print("  the vault mirror is off; nothing here reaches your durable decision log")

    # Reconciled up front, before the queue is built: a restore has to land before
    # ``load_decisions`` reads the sidecar, or a session run against a lost ``data/`` would
    # re-ask every question the mirror already holds the answers to.
    mirror_path = None if (args.no_mirror or scratch) else args.decisions_mirror
    if mirror_path is not None:
        sync = sync_mirror(decisions_path, mirror_path)
        if sync.message:
            print(f"  {sync.message}")
        if sync.diverged:
            mirror_path = None

    registry = load_registry(CONFIG_DIR)
    rows = list(corpus.iter_rows(registry))
    listings_map = listings.load_or_fetch(cache.DATA_ROOT / "_listings.json")

    candidates, stats = build_candidates(
        rows, registry, as_of=as_of, listings_map=listings_map,
        funding_venue=None if args.no_funding else args.funding_venue,
    )

    print(f"{stats.assets_total} assets -> {stats.assets_priced} priced, "
          f"{stats.assets_unpriced} unpriced, "
          f"{stats.assets_no_context} priced but no bars at/before {as_of.isoformat()}")
    print("  " + format_unpriced(stats))
    print(f"{stats.candidate_count} candidates")
    if stats.rejections:
        print("  rejected: " + ", ".join(f"{k}={v}" for k, v in stats.rejections.most_common()))

    # Before the decision filter, because an excluded asset is not a judgment being remembered
    # — it is a question that should never have been asked. Reported either way: a filter
    # nobody can see is indistinguishable from a corpus that went quiet.
    excluded = exclusions.load(args.exclusions)
    unmatched = exclusions.unmatched_symbols(excluded, {r.asset for r in rows})
    if unmatched:
        print(f"  warning: no thesis mentions {', '.join(unmatched)} — check {args.exclusions} "
              f"for a typo; these suppress nothing", file=sys.stderr)
    candidates, dropped = exclusions.partition(candidates, excluded)
    if dropped:
        assets = sorted({c.asset for c in dropped})
        print(f"  {len(dropped)} on excluded assets — hidden ({', '.join(assets)})")

    decided = load_decisions(decisions_path)
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
    queue = build_queue(qualified, limit=None if args.limit == 0 else args.limit,
                        stratified=args.sample == queue_mod.STRATIFIED,
                        rng=queue_mod.rng_for(as_of))
    held_back = len(qualified) - len(queue)

    if not queue.rows:
        print("Nothing to review.")
        return 0

    # Named explicitly rather than left as "showing the top N", which is what it used to say
    # and is now only true under --sample top. Which scheme ran decides whether the sitting's
    # decisions can be pooled with any other, so it is not a detail to leave off screen.
    if held_back and queue.mode == queue_mod.STRATIFIED:
        print(f"  showing {len(queue)} of {len(qualified)} — the top {queue_mod.HEAD_SIZE} by "
              f"score, plus one drawn from each of {len(queue) - queue_mod.HEAD_SIZE} strata "
              f"below them, so the sitting spans "
              f"{queue.score_min:.3f}-{queue.score_max:.3f} (--sample top for the first "
              f"{len(queue)} in queue order, --limit 0 for all)")
    elif held_back:
        # NOT "the top N by score", which is what this said for as long as the cap existed and
        # was measurably false: ``collapse`` orders weekly before daily, so with 28 weekly rows
        # against a cap of 25 the queue was 25 weekly and 0 daily, and the highest-scoring
        # candidate in the whole population (TSLA, 0.90, position 29) was never shown at all.
        print(f"  showing the first {len(queue)} in queue order, weekly before daily — "
              f"{held_back} more qualify, and they are not all lower-scoring "
              f"(--limit 0 for all)")

    if args.list:
        color = supports_color()
        pairing = thesis_pairing(queue.candidates)
        for i, row in enumerate(queue.rows, start=1):
            zones, index = pairing[i - 1]
            print(format_candidate(row.candidate, rank=i, total=len(queue), as_of=as_of,
                                   color=color, zones_in_thesis=zones, zone_index=index))
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

    if mirror_path is not None:
        print(f"  decisions mirrored to {mirror_path}")

    # Opened before the first candidate is shown, for the same reason the vault note is
    # resolved there: a missing key or an unreachable venue must fail while the cost is zero,
    # not after a session's worth of judgement has been entered.
    session = None
    if args.execute:
        try:
            session = execute.open_session(network=args.network)
        except Exception as exc:  # noqa: BLE001 - surfaced as a message, not a traceback
            print(f"error: could not start execution — {exc}", file=sys.stderr)
            return 1
        if session is None:
            return 1
    elif args.network is not None:
        print("  note: --network has no effect without --execute", file=sys.stderr)

    counts = triage(  # pragma: no cover - interactive
        queue, decisions_path=decisions_path, vault_path=vault_note, as_of=as_of,
        mirror_path=mirror_path, exclusions_path=args.exclusions, session=session)
    print("\n" + format_counts(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
