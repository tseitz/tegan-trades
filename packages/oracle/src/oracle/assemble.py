"""Engine assembly — corpus rows plus the price cache become candidates.

The read-time join of the two halves the rest of the codebase deliberately keeps apart:
``core.setups`` knows nothing about how an asset gets priced, and the rest of ``oracle`` knows
nothing about theses or gates. They meet here, once per run — route every asset the corpus
mentions to a cached daily series, build one ``Context`` per asset (never per thesis: that split
exists precisely so a hundred theses on BTC don't recompute BTC's structure a hundred times),
then let ``core.setups`` do the actual judging.

**This is impure**, and that is the reason it is its own module: it routes assets, reads the
price cache, and reaches the network for venue marks and hourly bars. Everything downstream of
``build_candidates`` — formatting, the triage loop, the decision sidecar — is pure or nearly so
and lives in ``setups_cli``.

**Why it is not in the CLI.** It was, and eight probe scripts imported it from there — including
the private ``_load_daily``, which is what a leading underscore means when five callers ignore
it. A composition root that every measurement in the repo needs is not a CLI concern, and
keeping it in one kept ``setups_cli`` at 1,277 lines.

Impure inputs are **injected, not reached for**: ``funding_venue`` and ``marks_index`` are
parameters so a caller replaying a past date can supply nothing rather than today's answer. A
mark index fetched now cannot be allowed to contradict a route as it stood a year ago.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from core import trigger
from core.canon import Registry, resolve_asset
from core.identity import DIFFERS
from core.rank import parse_date
from core.setups import (
    ARRIVAL,
    ZONE_LEVEL_REASONS,
    ZONE_TIMEFRAMES,
    Candidate,
    NotASetup,
    build_context,
    collapse,
    cross_reference,
)

from oracle import (
    cache,
    carry,
    confirm,
    derived,
    instruments,
    trigger_feed,
    venue_map,
)
from oracle import (
    marks as marks_mod,
)
from oracle import queue as queue_mod
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

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = REPO_ROOT / "cfg"

# Sessions of our own trading band a venue's mark may sit inside without reading as a fault.
# Matches ``probe_venue_coverage.RANGE_SESSIONS`` for the same reason it exists there: a venue
# oracle can run a session behind, and on a volatile name that reads as a collision — `BE`
# marked 187.4 across three venues against a 166.8 close, its own previous session.
_CONFIRM_SESSIONS = 5


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


def is_inside_zone(candidate: Candidate) -> bool:
    """Has price actually reached the zone? ``ARRIVAL`` is where the near edge sits on the
    approach ramp, so at or above it price is in the zone rather than still travelling."""
    return candidate.approach >= ARRIVAL


def load_daily(resolved: Priceable, *, table: RoutingTable, series_cache: dict):
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
                load_daily(leg_route, table=table, series_cache=series_cache)
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
    marks_index: dict | None = None,
    series_cache: dict | None = None,
) -> tuple[tuple[Candidate, ...], BuildStats]:
    """Route every asset the corpus mentions, build one ``Context`` each, gate every row
    against its asset's context, and collapse the outcome stream into candidates.

    **The two injection points exist for replaying a past date**, and both default to the live
    behaviour so nothing changes for a caller that ignores them.

    ``marks_index`` — pass ``{}`` to skip the venue sweep. A mark fetched now says what an
    instrument costs *today*; letting it contradict a route as that route stood a year ago is
    not a check, it is an anachronism. Empty refuses nothing, which is already
    ``oracle.confirm``'s contract when no venue answers.

    ``series_cache`` — pre-seed ``{asset: PriceSeries}`` to supply bars instead of reading the
    cache from disk. ``load_daily`` already consults this dict first, so seeding it is a
    substitution rather than a second code path.
    """
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
    mark_index = (marks_mod.index_marks(marks_mod.fetch_all())
                  if marks_index is None else marks_index)

    series_cache = {} if series_cache is None else series_cache
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
        daily = load_daily(resolved, table=table, series_cache=series_cache)
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

    # Each person's single most recent thesis per asset, over the whole corpus — including
    # calls that never reach a gate below. ``collapse`` uses this to flag a supporter whose
    # counted view has since been reversed; the reversal itself almost never survives
    # ``cross_reference`` on its own (the opposite direction usually needs a zone the current
    # structure doesn't have), so without this pass it just goes quiet instead of being shown.
    latest_by_person: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        key = (row.asset, row.person)
        if row.published_at > latest_by_person.get(key, ("", ""))[1]:
            latest_by_person[key] = (row.direction, row.published_at)

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
    kept, duplicate_zones = queue_mod.one_per_asset(
        collapse(outcomes, aliases=aliases, latest_by_person=latest_by_person)
    )
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
