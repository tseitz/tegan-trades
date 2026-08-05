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
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path

from core import trigger
from core.canon import Registry, load_registry, resolve_asset
from core.identity import DIFFERS
from core.rank import parse_date
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
from execution.venues import ALL_NETWORKS, ALPACA

from oracle import (
    cache,
    carry,
    confirm,
    corpus,
    derived,
    exclusions,
    execute,
    instruments,
    listings,
    trigger_feed,
    venue_map,
    venue_routing,
)
from oracle import (
    marks as marks_mod,
)

# Re-exported: choosing which candidates a sitting sees is its own concern now — see that
# module's docstring for why it stopped being ``qualified[:limit]`` — but both names remain
# part of this CLI's surface for callers and tests that reach for them here.
from oracle import queue as queue_mod
from oracle import route as route_mod

# Re-exported: the sidecar's storage and its vault mirror live in their own module, but both
# remain part of this CLI's surface for callers and tests that reach for them here.
from oracle.decisions import append_decision, load_decisions, sync_mirror
from oracle.exclusions import DEFAULT_EXCLUSIONS
from oracle.queue import QueuePosition, build_queue, filter_candidates
from oracle.resample import to_weekly
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
from oracle.setups_render import (
    format_candidate,
    format_routing,
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
#
#   APPROVED  taking it
#   LATER     the zone is fine, price isn't there yet. REVERSIBLE — see `is_stale_decision`.
#   REJECTED  this zone is buried. Carries a vocabulary-2 reason.
#   ARCHIVED  the asset is gated out. Carries a vocabulary-2 reason and writes exclusions.yaml.
#
# These four are the *storage* vocabulary and are unchanged. They are no longer what you pick
# at the prompt — see ``_CHOICES``, where the reason is chosen and the verdict derived from it.
APPROVED = "approved"
LATER = "later"
REJECTED = "rejected"
ARCHIVED = "archived"

# Legacy: earlier runs recorded "skipped", which was permanent. Honour that rather than
# resurfacing everything already passed on — the sidecar is append-only and never rewritten.
SKIPPED = "skipped"

_PERMANENT = frozenset({APPROVED, REJECTED, ARCHIVED, SKIPPED})

# ── reason vocabulary 1 (retired 2026-07-28; kept because the sidecar is append-only) ───────
#
# 29 rows carry these and cannot be rewritten. They are NOT offered at the prompt any more.
# Every string below is distinct from every vocabulary-2 string, so a mining pass can partition
# on ``reason`` alone; ``reason_vocab`` is written as well, belt and braces.
REASON_TRADE = "trade_quality"
REASON_VIEW = "view_wrong"
REASON_OTHER = "other"
ARCHIVE_ASSET = "asset"
ARCHIVE_SETUP = "setup"

# ── reason vocabulary 2 ─────────────────────────────────────────────────────────────────────
#
# **Derived from the notes, not designed a priori.** Hand-labelling all 29 vocabulary-1 rows
# against what their free text literally says (the table is in ``docs/IMPROVEMENTS.md`` §4)
# found the old five buckets cutting across the real categories rather than along them:
#
#   * ``other`` was not a residue bucket. All 9 rows had a nameable category — 3 stale,
#     2 not-my-market, 2 duplicate, 2 disagreement. It was the shape of the missing keys.
#   * ``trade_quality`` was 80% not about trade quality: of 10 rows, 4 stale, 4 level-too-far,
#     1 dead gate, 1 blank. That is the bucket §4 mines to calibrate the setups scorer.
#   * ``view_wrong`` was 1-for-3 — ``SPX`` was level-too-far, ``WLD`` was a duplicate.
#
# **Scope is derived from the reason, never asked separately.** The old prompt asked scope
# first (reject vs archive) and cause second, but the notes decide cause first and the cause
# implies the scope every time — so the first prompt asked a question that had no answer yet,
# and mis-routed in both directions. All 3 ``archive``+``setup`` rows read as asset-level and
# are therefore inert (``drop_decided`` keys on the zone, so they will resurface), while
# "Zero interest in PNUT" went in as ``reject``+``other`` and had to be hand-added to
# ``cfg/exclusions.yaml`` afterwards.
#
# Per-zone. These bury one candidate; the next zone on the same instrument asks again.
REASON_STALE = "stale"           # the call has aged out — 8 of 29
REASON_FAR = "far"               # entry or target too far from price to be real — 5 of 29
REASON_DUPE = "dupe"             # a better zone for this same thesis is already queued — 3
REASON_SETUP = "bad_setup"       # structure or R:R doesn't hold up on the chart — 1 of 29
REASON_DISAGREE = "view"         # I read this differently, right now — 3 of 29
#
# ``REASON_DISAGREE`` measures agreement, NOT accuracy, and vocabulary 1's docstring got this
# wrong: it claimed ``view_wrong`` "calibrates the roster's trust score". At decision time you
# cannot know who was right — that needs the outcome, which arrives weeks later from price.
# All three rows read as a present-tense reservation ("not sure I'm shorting oil at these
# prices with the Iran conflict going on"), not a verdict on the analyst. So a mining pass may
# read this as "Tegan disagreed with this person N times" and must not read it as "this person
# was wrong N times".
#
# Asset-level. These write ``cfg/exclusions.yaml``, which is what makes the "no" stick.
REASON_NOT_MY_MARKET = "not_my_market"   # I don't trade this instrument
REASON_UNKNOWN_ASSET = "unknown_asset"   # unfamiliar ticker, possibly the wrong symbol

# Stamped on every row that carries a reason, so a mining pass never has to infer which
# vocabulary was in force from the value alone. Same role as ``SCORE_VERSION``.
REASON_VOCAB = 2


@dataclass(frozen=True)
class _Choice:
    """What one keystroke means: a verdict, a reason, and whether it gates the asset."""
    decision: str
    reason: str | None = None
    excludes: bool = False


# The whole vocabulary, flat — one keystroke settles both what happens and why.
#
# Ordered as the prompt prints it: take it, defer it, bury the zone, gate the asset.
_CHOICES: dict[str, _Choice] = {
    "a": _Choice(APPROVED),
    "l": _Choice(LATER),
    "s": _Choice(REJECTED, REASON_STALE),
    "f": _Choice(REJECTED, REASON_FAR),
    "d": _Choice(REJECTED, REASON_DUPE),
    "b": _Choice(REJECTED, REASON_SETUP),
    "v": _Choice(REJECTED, REASON_DISAGREE),
    "n": _Choice(ARCHIVED, REASON_NOT_MY_MARKET, excludes=True),
    "?": _Choice(ARCHIVED, REASON_UNKNOWN_ASSET, excludes=True),
}

# Spelled-out forms, so typing the whole word is not read as "first letter, then a note".
# Only words whose first letter is itself a key need to be here; the rest cannot collide.
_WORDS = {"approve": "a", "later": "l", "stale": "s", "far": "f", "dupe": "d",
          "duplicate": "d", "bad": "b", "view": "v"}

# Muscle memory from vocabulary 1. Both keys are gone, and neither can be honoured: "reject"
# no longer says *which* of five things it was, and "archive" no longer says which scope.
# Recognised only so the fall-through can name the replacement instead of silently deferring.
_RETIRED = {"r": "s/f/d/b/v", "reject": "s/f/d/b/v", "x": "n or ?", "archive": "n or ?"}

_PROMPT = ("[a]pprove  [l]ater  [q]uit\n"
           "  pass  · [s]tale  [f]ar  [d]upe  [b]ad setup  [v]iew\n"
           "  never · [n]ot my market  [?]don't know it\n"
           "> ")

_NOTE_TITLE = "# Approved Setups"

# Sessions of our own trading band a venue's mark may sit inside without reading as a fault.
# Matches ``probe_venue_coverage.RANGE_SESSIONS`` for the same reason it exists there: a venue
# oracle can run a session behind, and on a volatile name that reads as a collision — `BE`
# marked 187.4 across three venues against a 166.8 close, its own previous session.
_CONFIRM_SESSIONS = 5


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
    # Zones dropped because a better one on the same ticker was already in the list. Counted
    # rather than silently filtered: a queue that quietly halves itself reads as "this is
    # everything", which is the failure `unpriceable` above exists to prevent.
    duplicate_zones: int = 0
    # Named, not just counted, exactly as the excluded-asset line is. The set is small by
    # construction and every member is a curation task for a human — "6 assets price the wrong
    # instrument" is a fact you cannot act on, and `WTI JPY PURR` is one you can.
    contradicted: tuple[str, ...] = ()
    # Venue marks available to cross-check guessed routes. Carried so that *zero* can be said
    # out loud: `marks.fetch_all` swallows a venue outage by design, so an unreachable network
    # silently turns the identity check into a pass-everything. A gate that stops gating and
    # says nothing is the failure this file's tallies exist to prevent.
    marks_read: int = 0
    # The H1 verdict per candidate key, or empty when the trigger pass was skipped. Carried on
    # the stats rather than on ``Candidate`` so that a candidate stays exactly what it was —
    # a zone plus the people behind it — and the timeframe below it stays a separate reading.
    triggers: dict = field(default_factory=dict)

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

    **This reads ``trade_symbol``, not ``symbol``** — the divergence from ``score_cli``, and
    deliberate. Everything downstream of here is a number a human approves and a broker
    receives: zone edges, stop, target, ATR. Those have to be quoted on the instrument the
    order reaches, so a Dow setup is drawn on DIA's own swings rather than on ^DJI's divided
    by a ratio that drifts with every distribution. Grading stays on ``symbol`` next door,
    which is what keeps this from regrading the corpus (cfg/venue_map.yaml's `pricing` note).
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
        series = cache.load(resolved.source, resolved.trade_symbol)

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
    triggers_on: bool = True,
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

    # An independent price per ticker, used only to contradict a guessed route. Fetched here
    # rather than read from the price cache on purpose: a source that shared our routing would
    # agree with us about `WTI` being W&T Offshore and confirm the collision instead of catching
    # it. Empty when no venue answers, which refuses nothing — see ``oracle.confirm``.
    mark_index = marks_mod.index_marks(marks_mod.fetch_all())

    series_cache: dict = {}
    contexts: dict[str, tuple] = {}
    refs: dict[str, object] = {}
    unpriceable: Counter = Counter()
    contradicted: list[str] = []
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
        # Checked here and not only at fetch time, because the six known-bad series are already
        # on disk: a gate that only stopped future fetches would leave every one of them still
        # pricing, grading and reaching the queue. `recent` is the band a venue running a
        # session ahead of us is allowed to sit in without reading as a fault.
        recent = daily.bars[-_CONFIRM_SESSIONS:]
        verdict = confirm.check(
            resolved, close=daily.bars[-1].close, marks=mark_index,
            low=min(b.low for b in recent), high=max(b.high for b in recent),
        )
        if verdict is not None and verdict.verdict == DIFFERS:
            unpriceable[confirm.CONTRADICTED] += 1
            contradicted.append(asset)
            continue
        weekly = to_weekly(daily)
        # Which timeframe supplies this asset's zones is measured, not assigned: an instrument
        # trading on both sides of 12:00 UTC gets H12, everything else keeps the daily.
        #
        # **Cache only, no network.** ~300 assets route, and a fetch each is ~300 round trips
        # before the first candidate prints. The straddle answer is stable per instrument, so a
        # stale read costs nothing, and the trigger pass below fetches fresh bars for the few
        # dozen assets that actually produced a candidate. ``fetch-prices`` warms the rest.
        hourly = trigger_feed.load_cached(resolved) if triggers_on else None
        refs[asset] = resolved
        rung, rung_series = trigger_feed.setup_rung(hourly)
        setup_bars = daily.bars if rung_series is None else rung_series.bars
        ctx = build_context(setup_bars, weekly.bars, as_of=as_of,
                            setup_timeframe=rung)
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

    # Two spellings of one instrument are one trade, and `collapse` cannot see that on its own
    # — the zone, stop and target are drawn on `trade_symbol`, so RUT and IWM come out
    # identical while the supporters of that single zone read as 2 and 2. Built from the same
    # routing table the contexts were, so nothing can be aliased that was not also priced.
    aliases = instruments.alias_map(assets, table, venues_for=venue_map.venues_for)

    # One row per ticker, before sampling rather than after. Sampling after would let the
    # draw pick the daily zone and discard the weekly the precedence rule says outranks it.
    kept, duplicate_zones = queue_mod.one_per_asset(collapse(outcomes, aliases=aliases))
    candidates = tuple(kept)          # collapse's return type; callers index and unpack it

    # The trigger pass runs over candidates rather than assets: it is a handful of rows against
    # a few hundred, and the hourly bars are already in hand from the loop above.
    #
    # ``is_inside_zone`` supplies step 1 rather than a second notion of "has price arrived".
    # The queue already answers that question and two answers to it would drift.
    triggers = {}
    for candidate in candidates:
        # Fresh bars here, unlike the rung decision above: an entry signal computed on
        # yesterday's hourly is worse than none, and this is a few dozen fetches rather than
        # a few hundred. ``load_or_fetch`` asks only for the tail it is missing.
        hourly = (trigger_feed.load_or_fetch(refs[candidate.asset])
                  if triggers_on and candidate.asset in refs else None)
        if hourly is None:
            continue
        triggers[candidate.key] = trigger.detect(
            hourly.bars, direction=candidate.direction,
            zone_tagged=is_inside_zone(candidate),
        )
    stats = BuildStats(
        assets_total=len(assets), assets_priced=len(contexts),
        unpriceable=unpriceable, assets_uncached=uncached, assets_no_context=no_context,
        rejections=rejections, candidate_count=len(candidates),
        duplicate_zones=duplicate_zones,
        contradicted=tuple(contradicted),
        marks_read=sum(len(v) for v in mark_index.values()),
        triggers=triggers,
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
        # Its own group, and the only one here that describes an asset which *was* priced. The
        # others never reached a number; this one reached the wrong number and was graded and
        # offered on it, so a reader who skims must not file it under "no route".
        ("wrong instrument", {confirm.CONTRADICTED}),
    )
    parts = []
    for label, reasons in groups:
        hit = {r: n for r, n in stats.unpriceable.items() if r in reasons}
        if not hit:
            continue
        # The contradicted group names its members instead of restating its own label as a
        # reason. Six tickers fit on the line and each one is a curation task; the count alone
        # tells a reader something is wrong and nothing about where to go.
        if reasons == {confirm.CONTRADICTED}:
            detail = ", ".join(stats.contradicted)
        else:
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
    # Said only when it is true, and then unmissably: with no marks the identity check refused
    # nothing, and a clean "wrong instrument" line would otherwise be indistinguishable from a
    # corpus that is actually clean.
    if not stats.marks_read:
        parts.append("NO VENUE MARKS — guessed routes went unchecked this run")
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
                    queue: QueuePosition | None = None,
                    trigger=None) -> dict:
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
        # What the trigger timeframe said, and the two levels it would have used instead of the
        # zone's — §51. ``probe_trigger_replay`` could reconstruct only 5 usable rows from 142
        # decisions, because H1 is held for 60 days and the sample cannot be grown backwards;
        # recording the verdict as it is taken is the only way the zone geometry and the trigger
        # geometry ever accumulate side by side on the same rows.
        #
        # None means *not evaluated* — ``--no-triggers``, an unroutable source, or nothing
        # cached — and is deliberately distinct from ``no_trigger``, which is the hourly
        # actively withholding. Same distinction ``check_depth`` draws between an unmeasured
        # market and a dead one, and the reason the carry fields are None rather than 0.0.
        #
        # Additive, so per §4(d) ``score_version`` does NOT move, and the existing 142 rows must
        # NOT be backfilled: a re-run computes today's trigger against today's bars, which is
        # not what the person was looking at.
        "trigger_state": getattr(trigger, "state", None),
        "trigger_entry": getattr(trigger, "entry", None),
        "trigger_stop": getattr(trigger, "stop", None),
        "inside_zone": is_inside_zone(candidate),
        "agreement": candidate.agreement,
        "newest_at": candidate.newest_at,
        "people": list(candidate.people),
        "thesis_ids": list(candidate.thesis_ids),
    }
    if reason is not None:
        record["reason"] = reason
        # Which vocabulary that string belongs to. Written only alongside a reason, so the 8
        # reason-less archives from 2026-07-27 stay exactly as unminable as they are — a
        # ``reason_vocab`` on a row with no reason would imply a meaning nobody recorded.
        record["reason_vocab"] = REASON_VOCAB
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

def routing_plan(queue) -> tuple[venue_routing.Router, tuple[str, ...]]:
    """The router for this sitting, and the venues worth opening a session to. Free — no network.

    Two things that have to be decided in this order and are easy to get backwards. The desk is
    opened over the venues the *router* names, so the router comes first; but the router's
    ``can_short`` comes from a venue's own account, so it is filled in afterwards by whoever
    opened the desk. Hence a plain function returning both rather than one call doing all of it.
    """
    router = venue_routing.build({c.asset for c in queue.candidates})
    return router, venue_routing.candidate_venues(router, queue.candidates)

def triage(queue, *, decisions_path, vault_path, input_fn=input, out=print,
           as_of: date | None = None, color: bool | None = None,
           mirror_path=None, exclusions_path=None, desk=None, router=None,
           triggers=None) -> dict[str, int]:
    """Present each candidate in queue order; approve -> vault note (if given) + sidecar,
    all decisions -> sidecar. Quit stops immediately without consuming further input.

    ``queue`` is an ``oracle.queue.Queue`` rather than a bare list because every decision has
    to record what else was on screen when it was made. A plain sequence of candidates cannot
    answer that, and §4 is the account of what a file that cannot answer it is worth.

    ``as_of`` is only used to age each candidate for display, so it stays optional — a caller
    that doesn't supply one gets the date without the day count rather than an exception.
    ``color`` defaults to autodetection, and is an explicit argument so tests pin it off.

    **One prompt, not two.** ``_read_choice`` settles the verdict and the reason together —
    see ``_CHOICES`` for why splitting them mis-routed decisions in both directions. The only
    branching left here is what a verdict *causes*: a vault note, an exclusion, an order.

    ``exclusions_path`` is where an asset-level archive writes its standing rule. Optional, and
    its absence downgrades rather than fails: the archive is still recorded, it just doesn't
    stick across zones. Every write here is subordinate to the sidecar for the same reason the
    vault mirror is — losing a config line is recoverable, losing the judgement is not.

    ``desk`` is an ``execution.desk.Desk`` when ``--execute`` was passed, and None otherwise —
    which is the default and the entire behaviour of this loop before it existed. An order is
    offered **after** the approval has been written, never before: an order is a consequence of
    a decision, so the decision must already be durable when it is proposed. Declining the
    order leaves the approval standing.

    ``router`` is built here when the caller does not supply one. ``main`` does supply one,
    because the desk has to be opened over the venues the router names and that happens before
    the first prompt — and once built there is no reason to re-read the funding log and every
    cohort series a second time.

    **Venue routing is shown with or without a desk**, because which venue is cheapest is a
    fact about the candidate rather than about the run — and it is one of the numbers the
    judgement should be made *on*, so deferring it to placement would show it after the scarce
    input was already spent. Only the account-dependent half needs a desk: ``can_short`` is
    unknown without one, which ``core.routing`` reports as a distinct refusal from a measured
    "cannot short" rather than guessing (Alpaca has been seen disagreeing with itself on exactly
    that pair of fields).

    **The venue in the headline is the venue the order will go to**, which is the whole payoff:
    it names the instrument that would actually be traded, so a candidate routed to Alpaca shows
    its Alpaca ticker and not the perp's. Before routing existed this was ``Config.venue`` for
    every row, which was correct only because every row went there.
    """
    counts = {APPROVED: 0, LATER: 0, REJECTED: 0, ARCHIVED: 0}
    decided_at = datetime.now(UTC).isoformat(timespec="seconds")
    color = supports_color() if color is None else color
    total = len(queue.rows)
    pairing = thesis_pairing(queue.candidates)
    # Built once for the whole sitting: the funding log and every cohort series get read here
    # rather than per candidate. The pooled gap rate is still recomputed per candidate, because
    # it depends on that candidate's own stop — see ``core.gaps.pooled_excess``.
    if router is None:
        router = venue_routing.build(
            {c.asset for c in queue.candidates},
            can_short=desk.can_short(ALPACA) if desk is not None else None,
        )
    for i, row in enumerate(queue.rows, start=1):
        c = row.candidate
        zones, index = pairing[i - 1]
        routed = router.decide(c)
        # The routed venue, or None when nothing can take this candidate. Only resolved with a
        # desk: without --execute there is no run to name an instrument for, and inventing one
        # would print a ticker nothing could trade.
        venue = routed.winner.venue if desk is not None and routed.winner else None
        listing = venue_map.listing(c.asset, venue) if venue else None
        out(format_candidate(c, rank=i, total=total, as_of=as_of, color=color,
                             venue=venue,
                             venue_symbol=getattr(listing, "symbol", None),
                             zones_in_thesis=zones, zone_index=index,
                             trigger=(triggers or {}).get(c.key)))
        # A fifth summary line rather than a headline field: it is four facts (venue, cost,
        # margin, what is unpriced) and the headline is already carrying five.
        out(format_routing(routed, hold_days=CARRY_HOLD_DAYS, color=color))
        ans = input_fn(_PROMPT).strip()
        if ans.lower() in ("q", "quit"):
            break

        choice, note = _read_choice(ans, input_fn, out=out)
        decision, reason = choice.decision, choice.reason
        if decision == APPROVED and vault_path is not None:
            append_note(vault_path, render_note(c, decided_on=decided_at[:10]))
        if choice.excludes:
            _record_exclusion(c.asset, note, path=exclusions_path, out=out,
                              suspect_symbol=reason == REASON_UNKNOWN_ASSET)

        append_decision(
            decisions_path,
            decision_record(c, decision, decided_at=decided_at, reason=reason, note=note,
                            queue=queue.position(i), trigger=(triggers or {}).get(c.key)),
            mirror=mirror_path, warn=out)
        counts[decision] += 1

        # Strictly after the decision is on disk. An order is a consequence of an approval,
        # so the approval must survive independently of whether the order does — and a venue
        # timeout must never be able to lose the judgement that preceded it.
        if desk is not None and decision == APPROVED:
            execute.offer_routed(desk, c, venue, input_fn=input_fn, out=out)
    return counts


def format_counts(counts: dict[str, int]) -> str:
    """The end-of-session summary, derived from the counts rather than hand-listing verdicts.

    Hand-listing is exactly what broke: the line named `skipped` explicitly, the vocabulary
    changed under it, and a ``# pragma: no cover`` meant nothing caught the drift until it
    raised ``KeyError`` at the end of a real triage session. Deriving it means adding a verdict
    can never leave the summary behind.
    """
    return " · ".join(f"{verdict} {count}" for verdict, count in counts.items())


def _read_choice(ans: str, input_fn, *, out) -> tuple[_Choice, str | None]:
    """Turn what was typed into a verdict, a reason and a note. One prompt, not two.

    **The scope question is gone.** Vocabulary 1 asked reject-or-archive first and the cause
    second; the cause determines the scope in every one of the 29 recorded notes, so the first
    question was being asked before it had an answer. Here the reason *is* the answer and the
    scope falls out of ``_CHOICES``.

    Three input shapes, all preserved from the prompts this replaces:

    * a bare keystroke → a second prompt for the note;
    * a spelled-out word in ``_WORDS`` → the same, and not misread as key-plus-note;
    * anything else longer than one character → first letter is the key, **the whole string is
      the note**. Typing a sentence works and nothing typed is ever discarded.

    **Unrecognised defers.** ``LATER`` is the only reversible verdict, so a stray keypress —
    or vocabulary-1 muscle memory — costs you one re-prompt next session rather than burying a
    candidate for good. That is a deliberate change of default: with the destructive keys now
    sharing one prompt with everything else, falling through to any permanent verdict would be
    the same trap the old ``_ask_archive_kind`` avoided by refusing to guess "asset".
    """
    lowered = ans.lower()
    if not ans:
        return _CHOICES["l"], None
    if lowered in _RETIRED:
        out(f"  ! '{ans}' was retired on 2026-07-28 — use {_RETIRED[lowered]}."
            f" Deferred to later, so nothing is lost.")
        return _CHOICES["l"], None

    key = _WORDS.get(lowered, lowered[:1])
    choice = _CHOICES.get(key)
    if choice is None:
        out(f"  ! '{ans}' is not one of the keys — deferred to later, so nothing is lost.")
        return _CHOICES["l"], None

    # A key followed by a space drops the key from the note; anything else keeps the string
    # whole. "s  very stale" is a key and a note, but "view is wrong" is one sentence whose
    # first letter happens to be a key — and vocabulary 1 stored the latter verbatim, which is
    # the behaviour worth keeping. The distinction matters most for ``n`` and ``?``, where the
    # note becomes a line in a committed config file and "?never heard of this" would be spoor.
    inline = None
    if len(ans) > 1 and lowered not in _WORDS:
        inline = ans[1:].strip() if ans[1:2].isspace() else ans

    # Approve and later never consume a second answer: a prompt they don't take would shift
    # every later keystroke onto the wrong candidate. An inline note still survives.
    if choice.decision in (APPROVED, LATER):
        return choice, inline
    if inline is not None:
        return choice, inline

    prompt = ("  reason (required, becomes the exclusion): " if choice.excludes
              else "  note (enter to skip): ")
    return choice, input_fn(prompt).strip() or None


def _record_exclusion(asset: str, reason: str | None, *, path, out,
                      suspect_symbol: bool = False) -> None:
    """Make an asset-level archive stick, or say clearly why it didn't.

    Subordinate to the sidecar in every branch — this runs *after* nothing and *before* nothing
    that matters, and it never raises. The decision is recorded by the caller either way, which
    is the same rule ``oracle.decisions`` applies to the vault mirror: a convenience that fails
    must not take a judgement down with it.

    Silence is the one unacceptable outcome, so all four cases print. A gate everyone believes
    is running and isn't is worse than no gate — §6h's whole failure class.

    ``suspect_symbol`` is set by the ``?`` key and prints an extra line naming the symbol for
    canon review. "I don't recognise this ticker" is usually a *data* complaint rather than a
    preference, and the sidecar is where those went to die: ``UROY`` recorded "Not sure what
    this asset is. I see URC?" and ``DASH`` asked whether Doordash could be told from the
    crypto — two live routing bugs, both filed as decisions nobody would ever grep. Note this
    is the mirror image of ``exclusions.unmatched_symbols``, which warns when an excluded
    symbol matches nothing in the corpus; this warns when the symbol matched but you couldn't
    identify what it matched.
    """
    if suspect_symbol:
        out(f"  ? {asset.upper()} flagged for canon review — check cfg/assets.yaml and"
            f" cfg/tickers.json for a collision or a wrong mapping before committing")
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
    parser.add_argument("--no-triggers", action="store_true",
                        help="skip the H1 trigger; zones stay on the daily and no entry "
                             "confirmation is shown or waited for")
    parser.add_argument("--list", action="store_true",
                        help="print the queue and exit, no prompting")
    # Execution is opt-in per run and has no config-file switch that could turn it on.
    # A flag you must type every time is the difference between "I meant to trade tonight"
    # and "I forgot this was still enabled from last week".
    parser.add_argument("--execute", action="store_true",
                        help="after approving, offer to place the trade on the configured "
                             "venue (default: off — approvals are recorded only)")
    # Choices span every venue, and the venue/network pairing is checked by
    # ``Config.validate`` rather than by argparse — which cannot know which venue the config
    # names. Taking these from Hyperliquid's table alone made `--network paper` an argparse
    # error and `--network live` unreachable, so the typed confirmation guarded a path that
    # could not be walked.
    parser.add_argument("--network", choices=sorted(ALL_NETWORKS), default=None,
                        help="override cfg/execution.yaml's network; must be one the "
                             "configured venue has, and a real-money one (mainnet, live) "
                             "additionally requires a typed confirmation")
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
        triggers_on=not args.no_triggers,
    )

    print(f"{stats.assets_total} assets -> {stats.assets_priced} priced, "
          f"{stats.assets_unpriced} unpriced, "
          f"{stats.assets_no_context} priced but no bars at/before {as_of.isoformat()}")
    print("  " + format_unpriced(stats))
    print(f"{stats.candidate_count} candidates")
    # Named, not just netted out of the total. Dropping a zone is a real decision about what
    # you are allowed to consider, and a queue that shrinks without saying so reads as the
    # whole population — the same failure the `unpriceable` breakdown exists to prevent.
    if stats.duplicate_zones:
        print(f"  {stats.duplicate_zones} second zone(s) held back — one row per ticker, "
              f"weekly outranks daily")
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
                                   color=color, zones_in_thesis=zones, zone_index=index,
                                   trigger=stats.triggers.get(row.candidate.key)))
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

    # Built before the desk, because which venues are worth connecting to is the router's
    # answer and not a constant — see ``venue_routing.candidate_venues``.
    router, wanted = routing_plan(queue)

    # Opened before the first candidate is shown, for the same reason the vault note is
    # resolved there: a missing key or an unreachable venue must fail while the cost is zero,
    # not after a session's worth of judgement has been entered.
    desk = None
    if args.execute:
        try:
            desk = execute.open_desk(wanted=wanted, network=args.network)
        except Exception as exc:  # noqa: BLE001 - surfaced as a message, not a traceback
            print(f"error: could not start execution — {exc}", file=sys.stderr)
            return 1
        if desk is None:
            return 1
        # Asked of Alpaca specifically, and only now that there is an account to ask. This is
        # the one routing input a run cannot know offline, and it gates every equity short.
        router = replace(router, can_short=desk.can_short(ALPACA))
    elif args.network is not None:
        print("  note: --network has no effect without --execute", file=sys.stderr)

    counts = triage(  # pragma: no cover - interactive
        queue, decisions_path=decisions_path, vault_path=vault_note, as_of=as_of,
        mirror_path=mirror_path, exclusions_path=args.exclusions, desk=desk, router=router,
        triggers=stats.triggers)
    print("\n" + format_counts(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
