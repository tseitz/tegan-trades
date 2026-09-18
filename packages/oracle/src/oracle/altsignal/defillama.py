"""DefiLlama — chain- and protocol-level usage and liquidity, as a confirmation signal for a
crypto holding and (via ``fetch_protocols``) the source for one side of a protocol comparison.

Chain-level: three endpoints, all free and unauthenticated (verified live 2026-09-03):

    /v2/historicalChainTvl/{chain}   TVL history for one chain — take the latest point
    /stablecoincharts/{chain}        stablecoin supply on one chain over time — "dry powder"
    /overview/dexs/{chain}           DEX trading volume for one chain — proves real usage,
                                      not just parked capital

Two gaps, both confirmed absent rather than missed, **and confirmed to hold at protocol
granularity too** (`docs/research/protocol-comparison-metrics.md` §2). **Active users** has no
endpoint at all, free or paid. **Perps volume** does have one and it answers HTTP 402 — but
`/overview/open-interest` is free, so "derivatives data" is not paywalled as a category.

Protocol-level: `fetch_protocols` reads four more endpoints, all free and unauthenticated
(verified live 2026-09-14/16):

    /summary/fees/{slug}?dataType=…          30d fees / revenue for one protocol
    /tvl/{slug}                              TVL, as a bare JSON number
    /summary/open-interest/{slug}            open interest + the ``category`` field
    https://defillama-datasets.llama.fi/emissions/{slug}   forward unlock schedule (§5 below)

**A wrong slug answers HTTP 200 with an EMPTY BODY**, which parses as "no data" rather than
"wrong name" — confirmed live on `/tvl/{aster-perps,hyperliquid-perps}`. Never guess a slug;
confirm one by hitting the endpoint and checking the body is non-empty.

**`dataType=dailyRevenue` does not exist for every protocol.** `aster-perps` serves
`dailyFees` and 400s on `dailyRevenue`. That must surface as *no reading*, not a fees number
mislabelled as revenue — a fallback written into the value would overstate the share that
actually reaches a token holder.

**The forward unlock schedule is a scrape, not an API** — `/api/emissions` and friends are all
402; the dataset above is what the DefiLlama *website* reads, is undocumented and unversioned,
and can be withdrawn without notice. Two traps in it, both measured against this repo's own
data and both worth stating beside the parser that avoids them:

- `componentData.sections[*].emission30d` is **not** "tokens unlocked in the last 30 days" —
  it reports 37,018,525 for HYPE's Core Contributors against a real 433,419 move over the same
  window. Compute the 30-day delta from `documentedData` yourself; do not read this field.
- `documentedData` extends **into the future** — Lighter's series runs to 2029-12-29. Take the
  last point *at or before* the reference time, never the series' last point.

Venue-level (#73): `fetch_venues` reads three more endpoints, sizes and shapes measured live
2026-09-17:

    https://api.llama.fi/protocols       audits, forkedFromIds, hallmarks — 8.85 MB, ~8,280
                                          protocols, one download per run
    https://yields.llama.fi/pools        apy, apyReward, sigma, count, outlier, stablecoin —
                                          11.37 MB, ~16,550 pools, one download per run
    https://api.llama.fi/protocol/{slug} the honest age: first non-zero point in `tvl[]` —
                                          29.4 MB for aave-v3, 2.04 MB for lido, so this one is
                                          fetched once per slug, EVER, never re-polled

`tvl[]` points are `{"date": <unix>, "totalLiquidityUSD": <float>}` — neither name is guessable
from the field names the other three endpoints use. `listedAt` is **not** used even as a
fallback for the age: it is DefiLlama's listing date, not a deployment date, and Lido carries
none at all.

`audits` is present on 99.8% of protocols; reading its 35.5%-nonzero share as *coverage* would
overstate how gappy the audit check is. The decision rule is `audits != "0"`. `audit_links`
(34.6%) is not read — it is a URL list that disagrees with `audits` in both directions, and the
gate needs one rule, not a union of two. `forkedFromIds` (lineage) and `parentProtocol`
(DefiLlama's own family grouping, e.g. `aave-v3 -> "parent#aave"`) are two different relations
under one English word — only `forkedFromIds` is lineage.

**`venue_first_tvl` is its own write-once store kind, never folded into `venue_safety_facts`.**
`venue_safety_facts` is rewritten every night from the bulk `/protocols` payload, which carries
no age. `altsignal_store`'s reader takes only the latest row per key, so if the age lived in
that same nightly dict, the second night's write — with no history fetch attached — would
silently erase the date, and the Safety gate would start failing every venue from night two
onward with no error anywhere. Keeping it separate, keyed on protocol slug, and written once on
a successful fetch (never on failure, and never overwritten) is what a "date a protocol first
had TVL" fact actually needs: it cannot move.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.altsignal import AltSignalReading

from oracle import http

TVL_BASE = "https://api.llama.fi/v2/historicalChainTvl"
STABLECOIN_BASE = "https://stablecoins.llama.fi/stablecoincharts"
DEX_BASE = "https://api.llama.fi/overview/dexs"
FEES_BASE = "https://api.llama.fi/summary/fees"
PROTOCOL_TVL_BASE = "https://api.llama.fi/tvl"
OPEN_INTEREST_BASE = "https://api.llama.fi/summary/open-interest"
EMISSIONS_BASE = "https://defillama-datasets.llama.fi/emissions"
PROTOCOLS_BASE = "https://api.llama.fi/protocols"
POOLS_BASE = "https://yields.llama.fi/pools"
PROTOCOL_HISTORY_BASE = "https://api.llama.fi/protocol"

# Days-back/forward windows the unlock schedule reports over. TUNE.
UNLOCK_LOOKBACK_DAYS = 30
UNLOCK_LOOKAHEAD_DAYS = 90

SOURCE = "defillama"


def parse_chain_tvl(payload) -> float | None:
    """The latest TVL point. Empty history (a chain DefiLlama doesn't track) is ``None``."""
    if not payload:
        return None
    return payload[-1].get("tvl")


def parse_stablecoin_supply(payload) -> float | None:
    """The latest USD-denominated stablecoin supply on the chain — a leading liquidity signal
    that often moves before TVL does."""
    if not payload:
        return None
    latest = payload[-1]
    return (latest.get("totalCirculatingUSD") or {}).get("peggedUSD")


def parse_dex_volume(payload) -> float | None:
    """24h DEX trading volume — confirms TVL growth is real usage, not parked capital."""
    if not payload:
        return None
    return payload.get("total24h")


def fetch(
    chains: list[str], *, get_json=http.get_json, observed_at: datetime | None = None
) -> list[AltSignalReading]:
    """One reading per metric per chain. A chain missing one metric still yields the others —
    a partial sweep is worth more than none, same reasoning as the funding source adapters."""
    at = observed_at or datetime.now(UTC)
    readings: list[AltSignalReading] = []

    for chain in chains:
        tvl = parse_chain_tvl(get_json(f"{TVL_BASE}/{chain}"))
        if tvl is not None:
            readings.append(
                AltSignalReading(source=SOURCE, kind="chain_tvl", key=chain, value=tvl, observed_at=at)
            )

        supply = parse_stablecoin_supply(get_json(f"{STABLECOIN_BASE}/{chain}"))
        if supply is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="stablecoin_supply", key=chain, value=supply, observed_at=at
                )
            )

        volume = parse_dex_volume(get_json(f"{DEX_BASE}/{chain}"))
        if volume is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="dex_volume", key=chain, value=volume, observed_at=at
                )
            )

    return readings


# ------------------------------------------------------------------------- protocol-level


def _llama_total(slug: str, data_type: str, *, get_json) -> float | None:
    """30-day total for one fee dimension, or ``None`` when this protocol has no adapter for
    it — ``aster-perps`` 400s on ``dailyRevenue`` while serving ``dailyFees``. Ported from
    `scripts/probe_perp_venue_fundamentals.py`'s `_llama_total`: only ``total30d`` is read, so
    both chart params drop — measured 14x smaller on the wire for six floats."""
    try:
        payload = get_json(
            f"{FEES_BASE}/{slug}",
            {
                "dataType": data_type,
                "excludeTotalDataChart": "true",
                "excludeTotalDataChartBreakdown": "true",
            },
        )
    except http.FetchError:
        return None
    return payload.get("total30d") if isinstance(payload, dict) else None


def parse_protocol_tvl(payload) -> float | None:
    """``/tvl/{slug}`` answers a bare JSON number, or an empty body for a wrong slug — which
    parses as ``""``, not a number, so this returns ``None`` rather than raising."""
    return payload if isinstance(payload, (int, float)) else None


def _protocol_tvl(slug: str, *, get_json) -> float | None:
    try:
        return parse_protocol_tvl(get_json(f"{PROTOCOL_TVL_BASE}/{slug}"))
    except http.FetchError:
        return None


def parse_open_interest(payload) -> tuple[float | None, str | None]:
    """``(open interest, category)`` from one ``/summary/open-interest/{slug}`` child object.

    ``total24h`` is the field name DefiLlama's dimension-adapter API uses for every "summary"
    endpoint, fees included — but open interest is a snapshot, not a 24h flow, and this field
    carries the latest snapshot in that shape. Confirmed live: `hyperliquid-perps` reads
    $6.80B here against the $6.67B `/tvl/hyperliquid` reads for the same day, the right order
    of magnitude for a snapshot, not a summed flow.
    """
    if not isinstance(payload, dict):
        return None, None
    return payload.get("total24h"), payload.get("category")


def _open_interest_and_category(slug: str, *, get_json) -> tuple[float | None, str | None]:
    try:
        return parse_open_interest(get_json(f"{OPEN_INTEREST_BASE}/{slug}"))
    except http.FetchError:
        return None, None


def parse_unlock_schedule(payload, *, now: datetime) -> dict | None:
    """The forward unlock schedule, summed across every allocation section.

    See the module docstring for the two measured traps this avoids: it reads
    ``documentedData``, never ``componentData``'s ``emission30d``, and every "as of" figure
    takes the last point *at or before* ``now`` rather than the series' last point, which can
    sit years in the future.

    Returns ``None`` when the dataset has no ``documentedData`` at all — an unlisted protocol,
    or one this undocumented dataset does not cover.
    """
    sections = ((payload or {}).get("documentedData") or {}).get("data") or []
    if not sections:
        return None

    now_ts = now.timestamp()
    lookback_ts = now_ts - UNLOCK_LOOKBACK_DAYS * 86400
    lookahead_ts = now_ts + UNLOCK_LOOKAHEAD_DAYS * 86400
    unlocked_now = 0.0
    unlocked_lookback = 0.0
    unlocked_lookahead = 0.0
    documented_end_ts = 0

    for section in sections:
        points = section.get("data") or []
        if not points:
            continue
        documented_end_ts = max(documented_end_ts, points[-1]["timestamp"])

        at_or_before_now = [p for p in points if p["timestamp"] <= now_ts]
        if at_or_before_now:
            unlocked_now += at_or_before_now[-1]["unlocked"]

        at_or_before_lookback = [p for p in points if p["timestamp"] <= lookback_ts]
        if at_or_before_lookback:
            unlocked_lookback += at_or_before_lookback[-1]["unlocked"]

        at_or_before_lookahead = [p for p in points if p["timestamp"] <= lookahead_ts]
        # Empty means every point in this section sits AFTER the lookahead window — a tranche
        # that has not started unlocking yet (Lighter's Team/Investors allocations, which sit
        # at zero until ~2026-12-29). Its contribution at the lookahead point is 0, not the
        # section's eventual total — the mirror of `at_or_before_lookback`'s default above.
        if at_or_before_lookahead:
            unlocked_lookahead += at_or_before_lookahead[-1]["unlocked"]

    tbd_amount = ((payload or {}).get("supplyMetrics") or {}).get("tbdAmount")
    return {
        "unlocked_today": unlocked_now,
        "unlocked_30d": unlocked_now - unlocked_lookback,
        "scheduled_90d": unlocked_lookahead - unlocked_now,
        "tbd_amount": tbd_amount,
        # ISO-8601 string, not a `date` — `altsignal_store.append` calls `json.dumps` on this
        # dict directly, and `date` is not JSON-native.
        "documented_end": datetime.fromtimestamp(documented_end_ts, tz=UTC).isoformat()
        if documented_end_ts
        else None,
    }


def _unlock_schedule(slug: str, *, get_json, now: datetime) -> dict | None:
    try:
        payload = get_json(f"{EMISSIONS_BASE}/{slug}")
    except http.FetchError:
        return None
    return parse_unlock_schedule(payload, now=now)


def fetch_protocols(
    entries, *, get_json=http.get_json, observed_at: datetime | None = None
) -> list[AltSignalReading]:
    """One reading per metric per configured protocol — the DefiLlama half of a comparison
    card. A missing metric yields no reading, same convention as `fetch()`, so one 400 on
    ``dailyRevenue`` costs only that reading, never the protocol's other five.

    ``key`` is always the **DefiLlama slug the reading came from**, source-native, matching
    `core.altsignal.AltSignalReading`'s convention — never the ticker. Fees, revenue and TVL
    key on `ProtocolEntry.llama_fees`/`llama_tvl`; open interest keys on each
    `llama_oi` slug individually rather than pre-summed, because one protocol can be split
    into several DefiLlama rows (Lighter is `lighter-perps` + `lighter-robinhood-perps`) and
    summing here would throw away which rows were actually seen — the sum happens in
    `compare`, which records the count. `protocol_category` keys on `llama_tvl` instead, taken
    from the first `llama_oi` slug that actually answered: category gates whether `compare`
    draws a TVL row at all, so it has to live in the same key space as `protocol_tvl` rather
    than the OI slug it happened to be read from — and a transient failure on `llama_oi[0]`
    must not drop the category just because a later slug would have served the same one.
    """
    at = observed_at or datetime.now(UTC)
    readings: list[AltSignalReading] = []

    for entry in entries:
        fees = _llama_total(entry.llama_fees, "dailyFees", get_json=get_json)
        if fees is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="protocol_fees_30d", key=entry.llama_fees,
                    value=fees, observed_at=at,
                )
            )

        revenue = _llama_total(entry.llama_fees, "dailyRevenue", get_json=get_json)
        if revenue is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="protocol_revenue_30d", key=entry.llama_fees,
                    value=revenue, observed_at=at,
                )
            )

        tvl = _protocol_tvl(entry.llama_tvl, get_json=get_json)
        if tvl is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="protocol_tvl", key=entry.llama_tvl,
                    value=tvl, observed_at=at,
                )
            )

        category = None
        for oi_slug in entry.llama_oi:
            oi, oi_category = _open_interest_and_category(oi_slug, get_json=get_json)
            if oi is not None:
                readings.append(
                    AltSignalReading(
                        source=SOURCE, kind="protocol_open_interest", key=oi_slug,
                        value=oi, observed_at=at,
                    )
                )
            # First slug that actually answered, not always `llama_oi[0]` — a transient
            # failure on the first slug must not silently drop `compare`'s TVL-category-
            # mismatch guard for the whole protocol when a later slug has the same category.
            if category is None:
                category = oi_category
        if category is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="protocol_category", key=entry.llama_tvl,
                    value=category, observed_at=at,
                )
            )

        unlocks = _unlock_schedule(entry.llama_fees, get_json=get_json, now=at)
        if unlocks is not None:
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="unlock_schedule", key=entry.llama_fees,
                    value=unlocks, observed_at=at,
                )
            )

    return readings


# ------------------------------------------------------------------------- venue Safety (#73)


def parse_venue_metadata(payload, *, slugs: list[str]) -> dict[str, dict]:
    """Gate-level facts for every requested slug **plus one per resolved parent**.

    ``id`` is a string (`"182"`), and `forkedFromIds` names parents by id, not slug — so this
    indexes the bulk payload by both `id` and `slug` in one pass and resolves every fork edge
    it can before returning. A parent slug picked up this way is walked too, so a two-generation
    fork chain still resolves; a parent this payload doesn't carry (a bad id, or the id
    genuinely absent) is silently dropped from `forked_from` rather than raising — `core.safety`
    reads that as `unresolved_parent`, which is the correct reading of "we could not confirm
    this lineage", not an error.

    ``audited`` is ``None`` only when the `audits` field itself is absent (0.2% of protocols) —
    every other protocol gets a real ``True``/``False`` from `audits != "0"`. ``incidents`` is
    a bare count of `hallmarks` entries; DefiLlama does not distinguish an incident from a
    routine milestone inside that list, so this counts the list rather than guessing which are
    good news.
    """
    by_id: dict[str, dict] = {}
    by_slug: dict[str, dict] = {}
    for entry in payload or ():
        protocol_id = entry.get("id")
        slug = entry.get("slug")
        if protocol_id is not None:
            by_id[str(protocol_id)] = entry
        if slug is not None:
            by_slug[slug] = entry

    out: dict[str, dict] = {}
    queue = list(slugs)
    queued = set(slugs)
    while queue:
        slug = queue.pop()
        entry = by_slug.get(slug)
        if entry is None:
            continue
        parent_slugs = [
            by_id[str(parent_id)]["slug"]
            for parent_id in (entry.get("forkedFromIds") or ())
            if str(parent_id) in by_id
        ]
        audits_raw = entry.get("audits")
        out[slug] = {
            "audited": None if audits_raw is None else audits_raw != "0",
            "forked_from": parent_slugs,
            "incidents": len(entry.get("hallmarks") or ()),
        }
        for parent_slug in parent_slugs:
            if parent_slug not in queued:
                queued.add(parent_slug)
                queue.append(parent_slug)
    return out


def parse_venue_pools(payload, *, pool_ids: list[str]) -> dict[str, dict]:
    """Score- and ranking-level facts for every requested pool uuid, found on the pool's own
    row — never presummed or renamed, so a caller reads exactly what DefiLlama reported."""
    wanted = set(pool_ids)
    out: dict[str, dict] = {}
    for entry in (payload or {}).get("data") or ():
        pool_id = entry.get("pool")
        if pool_id not in wanted:
            continue
        out[pool_id] = {
            "apy": entry.get("apy"),
            "apy_reward": entry.get("apyReward"),
            "sigma": entry.get("sigma"),
            "count": entry.get("count"),
            "outlier": entry.get("outlier"),
            "stablecoin": entry.get("stablecoin"),
        }
    return out


def parse_first_tvl_date(payload) -> str | None:
    """The honest deployment age: the first point in `tvl[]` whose `totalLiquidityUSD` is
    non-zero, as an ISO-8601 date string — never `date`, which `altsignal_store.append` cannot
    serialise (see its own docstring). `None` when the protocol has no such point at all."""
    for point in (payload or {}).get("tvl") or ():
        value = point.get("totalLiquidityUSD")
        if not value:
            continue
        timestamp = point.get("date")
        if timestamp is None:
            continue
        return datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()
    return None


def _first_tvl_date(slug: str, *, get_json) -> str | None:
    try:
        payload = get_json(f"{PROTOCOL_HISTORY_BASE}/{slug}")
    except http.FetchError:
        return None
    return parse_first_tvl_date(payload)


def fetch_venues(
    entries, *, get_json=http.get_json, observed_at: datetime | None = None,
    known_ages: frozenset[str] = frozenset(),
) -> list[AltSignalReading]:
    """Every Safety-gate reading for a `venues:` list — gate-level facts keyed on protocol
    slug, the once-ever age keyed the same way, and pool-level score/ranking facts keyed on
    pool uuid. See the module docstring for why the age is its own write-once store kind.

    ``known_ages`` is the set of protocol slugs `venue_first_tvl` already has a row for — the
    caller reads it from the store before calling this, and every slug in it skips the (heavy,
    once-only) history fetch. A slug's history fetch failing writes **no** `venue_first_tvl`
    row at all, never a null: recording one would skip that slug's history fetch forever and
    fail it permanently, with no error anywhere to say why.

    The three endpoints fail independently, the same convention `fetch_protocols` uses: a
    down `/pools` must not cost the gate-level facts from `/protocols`, or vice versa.
    """
    at = observed_at or datetime.now(UTC)
    readings: list[AltSignalReading] = []

    slugs = [entry.llama_protocol for entry in entries]
    try:
        protocols_payload = get_json(PROTOCOLS_BASE)
    except http.FetchError:
        protocols_payload = None

    if protocols_payload is not None:
        for slug, facts in parse_venue_metadata(protocols_payload, slugs=slugs).items():
            readings.append(
                AltSignalReading(
                    source=SOURCE, kind="venue_safety_facts", key=slug,
                    value=facts, observed_at=at,
                )
            )
            if slug in known_ages:
                continue
            first_tvl_on = _first_tvl_date(slug, get_json=get_json)
            if first_tvl_on is not None:
                readings.append(
                    AltSignalReading(
                        source=SOURCE, kind="venue_first_tvl", key=slug,
                        value=first_tvl_on, observed_at=at,
                    )
                )

    pool_ids = [pool_id for entry in entries for pool_id in entry.llama_pools]
    if pool_ids:
        try:
            pools_payload = get_json(POOLS_BASE)
        except http.FetchError:
            pools_payload = None
        if pools_payload is not None:
            for pool_id, pool_facts in parse_venue_pools(pools_payload, pool_ids=pool_ids).items():
                readings.append(
                    AltSignalReading(
                        source=SOURCE, kind="venue_pool", key=pool_id,
                        value=pool_facts, observed_at=at,
                    )
                )

    return readings
