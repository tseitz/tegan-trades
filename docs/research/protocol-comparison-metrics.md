# Protocol comparison metrics: which sources, which fields

Research for [#48](https://github.com/tseitz/tegan-trades/issues/48), under the map in
[#46](https://github.com/tseitz/tegan-trades/issues/46). The motivating card is
**"show me Hyperliquid (HYPE) beside Lighter (LIGHTER)"**.

**Provenance.** Two agents researched this independently and their findings are merged here. The
overlap is corroboration, not duplication — where both measured the same thing the numbers agreed,
and where they disagreed the disagreement is recorded and resolved. Everything labelled
**MEASURED** is a live call made while writing, with the URL given; every figure is from
**2026-09-13 / 2026-09-14 UTC** unless stated. Anything read only through a search-engine summary
of a page is labelled **quoted** and is lower confidence — treat those dollar figures as
approximate. The reproduction for most of the joined table is
`uv run python scripts/probe_perp_venue_fundamentals.py`.

---

## TL;DR

- **Six fields carry the comparison**: revenue (30d), open interest, market cap, float/FDV, TVL,
  and the forward unlock schedule. Volume and fees are supporting, not headline — a venue can
  manufacture both.
- **DefiLlama + CoinGecko cover every one of them for free and keyless.** No third source is needed.
- **DefiLlama cannot answer active users at all** — no endpoint exists, free *or* Pro. It also
  cannot serve perps volume (402) or unlocks (402) on the free tier; CoinGecko covers the first
  gap and an undocumented DefiLlama dataset covers the second.
- **The join key is CoinGecko's `id`, reached via `gecko_id` on the DefiLlama *parent*.** Never the
  ticker, never the slug, never `symbol`. `LIGHTER` is not a ticker; the token's on-chain symbol
  is **`LIT`**.
- **Open interest is not definitionally stable.** On Lighter, DefiLlama and CoinGecko agree with
  each other to 1.3% and both sit at **~1.97x** the figure computed from Lighter's own API. On
  Hyperliquid all three agree to 0.3%. Pick one source for both sides of a comparison; never mix.
- **#41 is still not answered, and the blocker changed from cost to coverage.** Dune's Hyperliquid
  tables are enterprise-gated and Lighter has no schema at all, so Dune can only produce a
  one-sided column — worse than none.

---

## 1. Which fields describe a protocol well enough to compare two

The ranking principle is already in this repo, in `scripts/probe_perp_venue_fundamentals.py`'s
docstring: **prefer the number that costs someone real money to produce.** Volume is free to fake
(trade with yourself, pay the fee). Open interest is not — it needs margin posted and left
exposed. Everything below is ordered by that test.

| Field | Free source | Confirmed at protocol granularity? |
|---|---|---|
| Revenue (30d) | DefiLlama | **Yes — MEASURED** |
| Fees (30d) | DefiLlama | **Yes — MEASURED** |
| Open interest | CoinGecko `/derivatives/exchanges`, DefiLlama `/overview/open-interest`, venue APIs | **Yes — MEASURED, but see §4** |
| Volume (perps) | CoinGecko `/derivatives/exchanges`, venue APIs | Yes — DefiLlama is **402** |
| Volume (spot DEX) | DefiLlama `/overview/dexs` | Yes — MEASURED |
| TVL | DefiLlama | Yes, but fragmented (§3) |
| Market cap · FDV · circulating/total/max supply | CoinGecko `/coins/markets` | **Yes — MEASURED** |
| Token emissions / unlock schedule | DefiLlama datasets host (**undocumented**) | Yes — MEASURED; the documented API is 402 |
| Active users | — | **No free source anywhere** |
| Liquidations | — | No free source; neither venue publishes a total |

### Tier 1 — the four that decide the comparison

| Field | Why it carries weight | Measured |
|---|---|---|
| **Revenue, 30d** | The only figure that can reach a token holder. Fees are gross; revenue is net of the supply side. | HYPE $59.49M · LIT $4.17M |
| **Open interest** | Standing risk backed by posted margin. The honest denominator for a valuation multiple. | HYPE $14.18B · LIT $0.551B *(venue)* / $1.09B *(aggregators)* |
| **Market cap** | What the comparison is *for* — you are buying the token, not the protocol. | HYPE $17.57B · LIT $1.10B |
| **Forward unlock schedule** | The one field that can reverse the verdict the other three give. See §5. | HYPE: schedule ends 2026-09-15, 611.8M TBD · LIT: 0 today, then 3.194M/wk from ~2026-12-29 |

`mcap / OI` is the single most useful derived number: **HYPE 1.24x, LIT 1.99x** — Lighter is the
*more* expensive of the two per dollar of real risk, despite being 16x smaller. Priced on volume
instead (`mcap / daily volume`: HYPE 3.23x, LIT 1.05x) Lighter looks like a 3x discount. That
inversion is the whole reason volume is Tier 2.

### Tier 2 — supporting, never the headline

- **Fees (30d)** — HYPE $76.07M, LIT $5.47M. Gross. The gap to revenue is the supply side
  (HLP/LPs), which a holder does not receive.
- **Volume (24h)** — HYPE $5.45B, LIT $1.05B. Cheap to inflate, and cheapest exactly where fees are
  lowest. **All 234 of Lighter's perp markets publish `taker_fee` 0.0000**
  ([`orderBookDetails`](https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails), MEASURED), so
  its fee line is not a take rate on its own book and its `fees / volume` of 0.0174% is not
  comparable to Hyperliquid's 0.0465%.
- **TVL** — HYPE $6.70B, LIT $696.8M. For a perp DEX this is *bridged collateral*, not lending
  liquidity, and it is fragmented across business lines. See §4.
- **Turnover (`volume / OI`)** — HYPE 0.38x/day, LIT 1.90x. Not proof of wash trading (HFT makers
  churn fast and legitimately), but it is the signature you would look for, and it is the cheapest
  cross-check available for free.
- **Float share** — HYPE 23.3%, LIT 25.0%. Only meaningful next to the unlock schedule; a low float
  with nothing scheduled is not the same risk as a low float with a cliff in 90 days.
- **FDV** — HYPE $75.47B, LIT $4.41B. Useful only as `FDV / revenue` (104.4x vs 86.5x), and see §5
  for why that ordering is misleading on its own. Hyperliquid's FDV is **4.3x** its market cap.

### Tier 3 — wanted, not reachably free

- **Active users / unique traders.** No free source serves this for both protocols (§2). Even with
  a paid source the field is conceptually ambiguous — wallet ≠ person, and airdrop farming inflates
  it — so a vendor number would need its own caveat rather than settling the question.
- **Liquidations.** The strongest honesty metric of the whole set — you cannot fake one, it costs
  real money — and neither venue publishes a clean total. Already recorded as unreachable in
  `scripts/probe_perp_venue_fundamentals.py`.
- **Book depth.** Resting size costs capital to post, so it ranks near OI for trustworthiness, but
  both venues truncate the returned book before a ±0.5% band closes — see
  `scripts/probe_book_depth.py`.

---

## 2. Which sources serve each field, and at what freshness

### DefiLlama free tier (`api.llama.fi`, no auth, no key)

The free/Pro split is published at
[api-docs.defillama.com/llms.txt](https://api-docs.defillama.com/llms.txt): 31 free endpoints on
`https://api.llama.fi`, 38 Pro-exclusive on `https://pro-api.llama.fi/{KEY}`, stated at **$300/mo**.
(A cheaper ~$49/mo website "Pro" plan exists that does **not** include API access — *quoted*, not
confirmed.)

| Field | Endpoint | Freshness (MEASURED) |
|---|---|---|
| Fees / revenue | `/summary/fees/{slug}?dataType=…` | **~27h.** Last series point `2026-09-13T00:00Z`, read at `2026-09-14T02:44Z`. `total24h` is *yesterday's complete UTC day*, not a trailing window. |
| Fees / revenue, all protocols at once | `/overview/fees?dataType=dailyRevenue` | Same. Returns ~2,333 rows in one call — the practical way to pull many protocols. |
| TVL | `/tvl/{slug}` (scalar), `/protocol/{slug}` (series + chain split) | **~45 min.** Last point `2026-09-14T02:02Z`, read at `02:44Z`. Roughly hourly. |
| Open interest | `/overview/open-interest`, `/summary/open-interest/{slug}` | Near-live snapshot. **But see §4.** |
| Market cap | `/protocol/{slug}` → `mcap` | Disagrees with CoinGecko — 1.1% on HYPE, 4.2% on LIT, same minute. Prefer CoinGecko. |
| Spot DEX volume | `/overview/dexs` (~1,357 rows) | daily |
| Chain TVL / stablecoin supply / chain DEX volume | already wired in `oracle/altsignal/defillama.py` | daily |

**`dataType` values that actually exist**, verified by response code on both protocols:

```
dailyFees  dailyRevenue  dailyHoldersRevenue  dailyProtocolRevenue  dailySupplySideRevenue   → 200
dailyUserFees  dailyBribesRevenue  dailyTokenTaxes                                           → 400
```

(The `FeeDataType` enum in `DefiLlama/api-sdk` lists more names than the API accepts — *quoted*.
None of them is a user count.)

**Use `dailyRevenue`, and do not sum the holders/protocol split.** `dailyFees = dailyRevenue +
dailySupplySideRevenue` holds exactly on both protocols (HYPE 59.488 + 16.576 = 76.064 vs 76.071;
LIT 4.168 + 1.298 = 5.466 vs 5.466). The *recipient* split does not:
`dailyHoldersRevenue + dailyProtocolRevenue` = $6.75M for Lighter against a `dailyRevenue` of
$4.17M — a 62% overcount. On Hyperliquid the same two fields are internally consistent (holders
$59.49M, protocol $0). One adapter is community-written and the other is not; the split is not a
comparable field.

**"Revenue" is adapter-defined, not one universal formula.** The `methodology` object on each child
in `/summary/fees/hyperliquid` spells it out: Hyperliquid Perps' Revenue is *"99% of fees go to
Assistance Fund for buying HYPE tokens, excluding builders fees"* — a buyback flow — while
Lighter's is straight treasury retention from *"maker, taker, transfer, and withdraw fees…
Liquidation fees excluded"*. Two equal revenue numbers do not mean two equal capital-return
policies. Hyperliquid Spot's Fees methodology also folds in *"HYPE burned in successful HIP-1
token-deployment auctions"*, an auction mechanic with no Lighter equivalent.

**`dataType=dailyRevenue` does not exist for every protocol.** `aster-perps` returns HTTP 400 on it
while serving `dailyFees` — re-verified. Quoting fees where revenue was wanted overstates the
holder's share, so any adapter must report which figure it used.

### What DefiLlama explicitly **cannot** answer

1. **Active users.** Not in the free list *or* the Pro list. `/userData/users/{slug}`,
   `/userData/chains/{chain}` and `/activeUsers` all 404; `users.llama.fi` 403s. No `FeeDataType`
   resembles a user count, and `/protocol/{slug}`'s full key set contains nothing of the kind.
   `oracle/altsignal/defillama.py`'s docstring already says this for chains; **this research
   confirms it holds at protocol granularity too**, which the docstring does not claim.
2. **Perps volume.** `/overview/derivatives`, `/overview/derivatives?dataType=dailyVolume` and
   `/summary/derivatives/{slug}` all return **HTTP 402** with *"Upgrade to the paid API plan"*.
   Spot DEX volume is free; perps volume — the thing that matters for these two — is not. That gap
   is real and the existing adapter docstring does not mention it.
   **Open interest is the exception:** `/overview/open-interest` is free and documented. The probe's
   docstring predates this and should be corrected.
3. **Token unlocks via the documented API.** `/api/emissions`, `/api/emission/{slug}` and
   `/emissionsBreakdown` are all 402. The dataset the website itself reads —
   `https://defillama-datasets.llama.fi/emissions/{slug}` — is publicly readable and returns the
   full schedule, but it is **undocumented, unversioned, and can be withdrawn without notice**.
   Treat it as a scrape, not an API.
4. **Treasury, raises, categories, forks, hacks, oracles, ETFs, bridges.** All Pro.
   `/treasury/hyperliquid` returns 400 on the free host.
5. **Anything cross-protocol or cohort-shaped** — flows between two protocols, wallet cohorts,
   retention. DefiLlama's endpoints are fixed aggregates, one protocol at a time.

### CoinGecko free tier (`api.coingecko.com/api/v3`, keyless)

**Token economics — MEASURED, one call for many ids:**
`/coins/markets?vs_currency=usd&ids=hyperliquid,lighter,aster-2` returns market cap, FDV,
circulating / total / max supply and 24h token volume. `last_updated` was **6 minutes old** when
read, making it the freshest source in the set. This is exactly the data DefiLlama does not carry —
confirmed by the absence of any such field in `/protocol/{slug}`.

```
hyperliquid  HYPE  mcap $17,574,506,494  fdv $75,474,821,087  circ 222,445,714 / total 955,307,079 / max 1,000,000,000
lighter      LIT   mcap  $1,101,355,994  fdv  $4,405,423,975  circ 250,000,000 / total 1,000,000,000 / max 1,000,000,000
```

**Perps volume and open interest — MEASURED, and this closes DefiLlama's 402 gap:**
`/derivatives/exchanges?per_page=250` returns 112 venues with `open_interest_btc`,
`trade_volume_24h_btc`, `number_of_perpetual_pairs`. BTC-denominated, so it needs a conversion, and
it is exchange-level rather than per-business-line — but it is free, keyless, and covers **both**
sides of this comparison with **one methodology**, which no other free source does.

```
Hyperliquid (Futures)             OI 182,812 BTC = $14.177B   vol24h  70,687 BTC = $5.482B   404 perps
Lighter                           OI  14,058 BTC =  $1.090B   vol24h  13,663 BTC = $1.060B   209 perps
Robinhood Chain Lighter (Futures) OI   4,783 BTC =  $0.371B   vol24h   3,972 BTC = $0.308B    57 perps
Aster (Futures)                   OI  31,210 BTC =  $2.420B   vol24h  17,867 BTC = $1.386B   570 perps
```

Note Lighter's Robinhood Chain instance is a **separate venue row**, exactly as DefiLlama splits it
into a separate protocol — and the two sources agree on it to within $1M.

**Rate limits.** The docs state **100 calls/min** for the Demo plan
([docs.coingecko.com](https://docs.coingecko.com/docs/common-errors-rate-limit)); the pricing page
adds a **10,000 calls/month** cap and puts the next tier at $35/mo for 300 calls/min
([coingecko.com/en/api/pricing](https://www.coingecko.com/en/api/pricing)). The *fully keyless*
path is lower and informal: one session here took a **429 on its very first `/ping`** and succeeded
~8s later, while a later burst of five consecutive `/simple/price` calls all returned 200. So
keyless works at comparison-card volumes but is not a guarantee — **register a free Demo key before
this goes anywhere near the nightly.**

### The venues themselves (free, keyless)

The only source for per-market detail, and the only way to see a venue's published fee schedule.

- **Hyperliquid** — `POST https://api.hyperliquid.xyz/info` `{"type":"metaAndAssetCtxs"}`, plus one
  call per HIP-3 builder dex enumerated by `{"type":"perpDexs"}`. **Core book alone undercounts OI
  by ~29%** ($10.05B core vs $14.18B all-dex, MEASURED) because equities, indices and commodities
  live only on the builder dexes.
- **Lighter** — `GET https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails` returns OI, mark,
  24h quote volume and the published taker fee for all 234 perp markets in one call.
  `/api/v1/exchangeStats` adds `daily_trades_count` (1,219,374) and a venue volume total.
  `/api/v1/info`, `/status` and `/layer2BasicInfo` all 403.

Neither venue publishes a user count. Hyperliquid's `stats-data.hyperliquid.xyz` paths all 403 from
this environment.

### Other sources checked (quoted from pricing pages, not fetched with credentials)

- **Token Terminal** — free to *view* on its own site; API access is "contact sales", no published
  free tier.
- **Artemis** — free dashboard tier that explicitly lists *daily active users* among its compared
  metrics, i.e. the one field neither free source has. Full API, Snowflake share and MCP access are
  behind its top paid tier. So Artemis is the likeliest eventual home for a real user count, but
  not through a free call.
- **Coinglass** (liquidations, per-venue) sits in the $300–400/mo bracket, the same place
  [#41](https://github.com/tseitz/tegan-trades/issues/41) parked Dune. Nothing here needs it yet.

---

## 3. The identifier problem, and the join key

A protocol carries at least five distinct identifiers, and **no two sources agree on any of them**.
All of the below is MEASURED on Hyperliquid and Lighter, not hypothetical.

### A DefiLlama slug is one *business line*, not the product

Hyperliquid is not one DefiLlama entry — it is a parent aggregate plus four independently tracked
children:

| Child | `slug` | `gecko_id` | `symbol` | category |
|---|---|---|---|---|
| Hyperliquid Bridge | `hyperliquid-bridge` | `null` | `HYPE` | Bridge |
| Hyperliquid HLP | `hyperliquid-hlp` | `null` | `HYPE` | Derivatives |
| Hyperliquid Perps | `hyperliquid-perps` | `null` | `HYPE` | Derivatives |
| Hyperliquid Spot Orderbook | `hyperliquid-spot-orderbook` | `null` | `HYPE` | Dexs |

Lighter is partitioned the same way: `lighter-perps`, `lighter-spot`, `lighter-bridge`,
`lighter-robinhood-perps`, `lighter-v1`.

Three things fall out of that table:

1. **`symbol` is not a key.** All four children report `HYPE`, but none of them mints a token —
   only the parent product has one. Joining or deduplicating by symbol would silently merge four
   different revenue streams.
2. **`gecko_id` lives only on the parent.** Every child's is `null`. A child's fee row reaches
   CoinGecko only by walking `child.parentProtocol → "parent#hyperliquid" → parent.gecko_id →
   "hyperliquid"`.
3. **`gecko_id` is a copy, not an independent cross-check.** DefiLlama populates it *from*
   CoinGecko. A mismatch is a real signal (no link, or the wrong coin linked); a match proves only
   that a link exists. Confirm the pairing by **price agreement**, the way `cfg/oracle_map.yaml`
   rows are confirmed.

### Verified failure modes

**A ticker is not an identifier.** The issue calls it "Lighter (LIGHTER)". The ERC-20 at Lighter's
own contract reports symbol **`LIT`** — confirmed against the contract, not a name match:

```
https://coins.llama.fi/prices/current/ethereum:0x232ce3bd40fcd6f80f3d55a522d03f25df784ee2
  → {"decimals": 18, "symbol": "LIT", "price": 4.4007…}
```

CoinGecko agrees (`id: lighter`, `symbol: lit`). This repo already carries the same warning in
harsher form: `Venue("lighter", …, "LIT")` in `scripts/probe_perp_venue_fundamentals.py`, and the
memory note *"Venue map: price, not name"*.

**A CoinGecko `id` is not the name.** `/search?query=aster` returns `aster-2` for the perp DEX; the
id `aster` is a different asset and `astar` a different chain. `/search?query=hype` returns eight
plausible-looking ids, one of which is Hyperliquid.

**A DefiLlama slug is not the DefiLlama parent id.** Aster's parent id is `parent#astherus` (a
previous name), but `/tvl/astherus` returns **400 "Protocol not found"** while `/tvl/aster` returns
$819.5M. Neither substitutes for the other, and `/protocol/parent%23hyperliquid` also 400s — the
path parameter is always the *slug*.

**A wrong slug fails silently.** `/tvl/aster-perps` and `/tvl/hyperliquid-perps` both return
**HTTP 200 with an empty body**, which parses as "no TVL" rather than "wrong name".

**`/protocols` has no parent row.** The 8,248-row list contains **no row with slug `hyperliquid` or
`lighter`** — only the children above, each with `gecko_id: null` and `mcap: null`, alongside
lookalikes from other parents (`Kinto Hyperliquid`, `Hyperliquid Strategies HYPE Staking`). The
parent object exists only via `/protocol/{slug}`, `/tvl/{slug}` and `/summary/fees/{slug}`.

**Parent and child are different numbers.** Picking the wrong level moves the answer:

| 30d fees | parent slug | perps child | gap |
|---|---|---|---|
| Hyperliquid | $76.07M | $72.62M | 4.5% |
| Lighter | $5.47M | $4.09M | **25%** |

**`chains` disagrees with itself inside one response.** `/protocol/hyperliquid`'s top-level `chains`
is `[]` while the same body's `currentChainTvls` carries real balances for `Arbitrum` and
`Hyperliquid L1`. **Read `currentChainTvls`' keys, never `chains`**, to answer "does this protocol
touch chain X".

**One protocol spans several chains, split into separate child protocols.** Lighter's TVL is
Ethereum $616.3M + Robinhood Chain $80.5M + Arbitrum $2.1k; its OI is `lighter-perps` (zkLighter)
$1.076B *plus* `lighter-robinhood-perps` $370M as a separate row — and CoinGecko splits the same
venue the same way. Hyperliquid's is Hyperliquid L1 $6.28B + Arbitrum $424.6M (the bridge). A
comparison that reads one chain reads a different protocol on each side.

Chain-name casing is at least forgiving: `/overview/fees/{ZkLighter,zklighter,zkLighter}` all return
the identical 12,200-byte body, even though `/summary/…` reports `ZkLighter` and `/overview/…`
reports `zkLighter`.

### The join key

```
DefiLlama parent slug ──(/summary/fees/{slug} → gecko_id)──▶ CoinGecko id ──▶ mcap, FDV, float
      │                                                            │
      │                                                            └──▶ /derivatives/exchanges id ──▶ OI, perps volume
      └──(hand-curated)──▶ venue API base URL ──▶ per-market detail, fee schedule
```

**`gecko_id` on the parent is the only machine-readable bridge between the two sources**, and it is
present only at the parent level (`/summary/fees/hyperliquid` → `gecko_id: "hyperliquid"`;
`/summary/open-interest/hyperliquid-perps` → `gecko_id: null`). Everything else is hand-curated and
must be confirmed by price agreement.

So a comparison entry needs **five identifiers stored explicitly**, not derived from one another:

```yaml
protocols:
  - asset: HYPE                       # ticker as written in a portfolio file
    llama_fees: hyperliquid           # parent slug, for fees/revenue
    llama_tvl: hyperliquid            # may differ — Aster is aster-perps / aster
    llama_oi: hyperliquid-perps       # child slug, for /overview/open-interest
    coingecko: hyperliquid            # market cap, FDV, float
    coingecko_derivatives: hyperliquid  # a DIFFERENT id namespace — see §4
    venue: hyperliquid                # which adapter reads per-market detail
```

That is `scripts/probe_perp_venue_fundamentals.py`'s `Venue` dataclass, which already carries
`llama_fees` and `llama_tvl` separately for exactly this reason. It belongs in `cfg/altsignal.yaml`
beside `chains:` and `markets:`, hand-curated and checked into git, on the same reasoning
`oracle_map.yaml` is. `core/altsignal.py`'s `AltSignalReading.key` being source-native already
anticipates this; what is missing is the **protocol-level entity mapping** that says "these N
DefiLlama slugs + this CoinGecko id + this token are all Hyperliquid". `AltSignalConfig` has no
shape for it today — only `ChainEntry` and `MarketEntry`.

---

## 4. What is comparable, and what is apples-to-oranges

### Genuinely comparable between two perp DEXes

- **Revenue (30d), from `dataType=dailyRevenue`** — same window, and the identity
  `fees = revenue + supplySide` holds on both, which is a real consistency check you can assert.
  Comparable *as a number*; read the methodology before reading it as a capital-return policy.
- **Market cap, FDV, circulating/total supply** — one CoinGecko call, one methodology, minutes fresh.
- **Open interest and perps volume from CoinGecko `/derivatives/exchanges`** — one vendor, one
  methodology, both venues, one call. This is the best free option precisely *because* it is a
  single source.
- **`mcap / revenue`, `FDV / revenue`, `mcap / OI`, buyback yield** — ratios built from the above.

### Not comparable, and why

**Open interest, if you mix sources.** This is the sharpest finding here. Measured within minutes of
each other:

| | DefiLlama `/overview/open-interest` | CoinGecko `/derivatives/exchanges` | venue's own API |
|---|---|---|---|
| Hyperliquid | $14.220B | $14.177B | $14.18B (all HIP-3 dexes) |
| Lighter (zkLighter only) | $1.0756B | $1.090B | **$0.551B** |

On Hyperliquid all three agree to **0.3%**. On Lighter the two aggregators agree with **each other**
to 1.3% and both sit at **~1.97x** the venue-derived figure. Lighter's `open_interest` field is
unambiguously one-sided base units (BTC: 2,099.97 at a $77,565 mark = $162.9M), so the most likely
reading is that the aggregators count both sides of the book for this venue and one side for
Hyperliquid — but two independent vendors agreeing means this is a **definitional split, not one
vendor's bug**, and it cannot be resolved from outside.

**Practical rule: choose one source for OI and use it on both sides.** CoinGecko
`/derivatives/exchanges` is the best single free choice. Reading Hyperliquid from its venue and
Lighter from an aggregator would report a 26x gap where the like-for-like gap is 13x.

**TVL across different protocol categories.** For these two it means bridged collateral sitting
against open positions — `OI / TVL` (HYPE 2.11x, LIT 0.79x) reads as leverage in use, and below 1
means deposits sit idle. For a lending market TVL is supplied capital that *is* the product; for a
liquid-staking protocol it is the staked asset; for a DEX it is LP depth. Four different quantities
under one heading. **The card must carry DefiLlama's `category` (`Derivatives`, `Dexs`, `Lending`, …)
and refuse to draw a TVL row when the two differ.**

TVL is ambiguous *within* one protocol too: Hyperliquid Bridge alone carries **$6.5B** (passive
custody) while Hyperliquid HLP carries **$187M** (capital actively taking the other side of trades).
A naive sum calls both "Hyperliquid TVL".

**Fees, and any take rate built from them.** Lighter publishes `taker_fee` 0.0000 on all 234 perp
markets, so its fee line arrives from liquidation fees, withdrawals and the Robinhood Chain instance
— not from a take rate on its own book. `fees / volume` is 0.0465% vs 0.0174%, and that gap measures
adapter scope, not pricing.

**`dailyHoldersRevenue` vs `dailyProtocolRevenue`.** Internally inconsistent across adapters (§2).

**"Unlocked" vs "circulating".** DefiLlama's emissions dataset reports HYPE at **388.15M unlocked**
today; CoinGecko reports **222.45M circulating** — a 75% gap, because foundation budget and grants
are unlocked but not in the float. The two words look synonymous and are not.

**Active users — not comparable, because no field exists**, and would not become comparable just by
buying one: wallet ≠ person, and airdrop farming inflates the count. A vendor number needs its own
caveat.

**Absolute anything, between protocols 16x apart in size.** Every headline figure should be a ratio
or a per-unit, or the larger protocol simply wins every row.

---

## 5. The field that reverses the verdict: forward unlocks

On the multiples alone Lighter is the cheaper token: `FDV / revenue` 86.5x against Hyperliquid's
104.4x, `mcap / revenue` 21.6x against 24.3x. The unlock schedule says otherwise.

From `https://defillama-datasets.llama.fi/emissions/{lighter,hyperliquid}` (undocumented; §2),
anchored on **today** rather than on the series' last point — Lighter's series runs to **2029-12-29**,
so reading the final point gives a 2029 answer:

| | HYPE | LIT |
|---|---|---|
| Unlocked today | 388.15M / 1.00B | 250.00M / 1.00B |
| Unlocked in the last 30d | 433,419 (~$34.2M) | 0 |
| Scheduled in the next 90d | 25,663 (~$2.0M) | 0 |
| Documented schedule ends | **2026-09-15** — i.e. no forward visibility | 2029-12-29 |
| `tbdAmount` (allocated, schedule unpublished) | **611.83M** | 250.00M |
| The event | — | Team 1.661M/wk + Investors 1.533M/wk **linear from ~2026-12-29** |

Lighter's Team (259.83M) and Investors (239.84M) allocations — **half the total supply** — are at
zero unlocked today and begin a combined **3.194M tokens/week** linear release in roughly 3.5
months. At the measured $4.40 that is **~$14.1M/week, ~$61M/month, against $4.17M/month of revenue**
— a ~15x overhang. Hyperliquid's forward schedule is not *zero*, it is *unpublished*: 611.8M of 1B
sits in `tbdAmount`, which is a different and arguably worse kind of unknown.

Two traps in this dataset:

- **`componentData.sections[*].emission30d` is not "tokens unlocked in the last 30 days".** It
  reports 37,018,525 for HYPE's Core Contributors where the `documentedData` series moved 433,419
  over the same 30 days — an 85x difference. Compute the delta from `documentedData` yourself.
- **`documentedData` extends into the future.** Take the last point *at or before now*, never
  `pts[-1]`.

---

## 6. Is this the manual-query use case #41 was waiting for?

**No. Not yet, and probably not for this card.** Three reasons, in order of weight.

**1. Dune's coverage is asymmetric, which is fatal for a side-by-side.** Dune's Hyperliquid data
exists and is good — 30+ decoded tables, curated perp collections refreshed hourly, actions and
fills ~20 minutes behind live, history from January 2025 — but
[the docs state plainly](https://docs.dune.com/data-catalog/community/hyperliquid/overview):
*"Raw, decoded, and curated Hyperliquid tables are private and available to enterprise customers and
trial accounts."* Only HyperEVM is ungated, and HyperEVM is not where the perp venue runs. Lighter
appears nowhere in [Dune's catalog](https://docs.dune.com/data-catalog/overview); it is a zk-rollup
whose trading is off-chain, so the only Dune-reachable surface is its Ethereum bridge contract —
deposits and depositor counts, not trades. A card that renders a real Dune column for HYPE and an
empty one for LIT is worse than a card with no such column, because it reads as "Lighter has no
users".

**2. Nothing on the Tier-1 list needs SQL.** Revenue, TVL, market cap, FDV, float, open interest,
perps volume and the unlock schedule are all free HTTP GETs today, verified above. Dune would be
paying credits to re-derive what two keyless APIs already serve as ready-made aggregates.

**3. The economics moved, but not in a way that changes the answer.** #41 recorded "10-minute
execution delay, 40 req/min, paid tiers ~$390/mo". Today: the Free plan carries **2,500
credits/month with API access included**, limited to the Small and Medium query engines; Analyst is
**$75/mo for 4,000 credits**, Plus **$399/mo for 25,000**
([credit system](https://docs.dune.com/learning/how-tos/credit-system)). Rate limits are 15 rpm on
low-limit endpoints and 40 rpm on high-limit ones for Free
([rate limits](https://docs.dune.com/api-reference/overview/rate-limits)). So a hand-run query is
now genuinely free — but free access to a dataset that does not contain Lighter is still not the
use case.

**The distinction that matters.** #41's precedent — per-venue liquidations and volume concentration
*by account* — is a gap because nobody exposes that granularity at *any* price through a REST
endpoint; raw SQL is the only path. "Protocol comparison" is a different shape of gap: its fields
are all served, just by two vendors instead of one. And the single field with no free source —
active users — would not be settled by Dune either, because the ambiguity there is conceptual
(wallet vs person, Sybil inflation), not a missing index.

**Recommendation:** keep #41 open; update it to record that the Free-tier economics improved and
that **the blocker is now coverage, not cost**. Revisit if Lighter gets a decoded Dune schema, or if
the comparison set narrows to chains Dune covers ungated.

---

## 7. How this fits the existing adapter layer

`core.altsignal.AltSignalReading(source, kind, key, value, observed_at)` fits without change. `key`
is documented as "whatever identifies the thing within its source", which is exactly the §3 problem
restated — so:

- `AltSignalReading(source="defillama", kind="protocol_revenue_30d", key="hyperliquid", …)` — `key`
  is the DefiLlama parent slug, as `chain_tvl` already uses the chain slug.
- `AltSignalReading(source="coingecko", kind="market_cap", key="hyperliquid", …)` — `key` is the
  CoinGecko coin id. A new adapter; CoinGecko is currently reached only from the probe script.
- `AltSignalReading(source="coingecko", kind="open_interest", key="hyperliquid", …)` — from
  `/derivatives/exchanges`. **Note the id namespaces differ**: `aster-2` is the coin, `aster` the
  derivatives exchange. Store both.
- Per-market detail stays with the venue adapters in
  `packages/oracle/src/oracle/sources/{hyperliquid,lighter,aster}.py`, which already log OI nightly
  into `data/interest/`.

**One reading per field, not a dict per protocol.** `pumpfun.py` is the only adapter that packs a
dict into `value`, and it does so because a graduation event is genuinely one thing. A comparison
row is six independent fields with six different freshnesses and six different failure modes — the
per-metric shape is what lets a partial sweep still be worth something, which is the convention
every other adapter in the package follows.

Mapping a reading back to a holding stays `review`'s job via `cfg/altsignal.yaml`, unchanged. That
file needs a `protocols:` block carrying the identifiers from §3.

**Where these findings should eventually live**, per this repo's convention that a finding sits
beside the thing it is about:

- The OI definitional split (§4) and the free-vs-402 correction (§2) →
  `scripts/probe_perp_venue_fundamentals.py`'s docstring, whose "volume is paywalled and the venues
  are not" block does not yet know that `/overview/open-interest` is free or that CoinGecko serves
  both venues.
- The `dailyRevenue` / holders-split inconsistency and the adapter-defined meaning of "revenue"
  (§2) → beside `fetch_llama` in the same probe.
- The perps-volume gap and the protocol-granularity confirmation of the active-users gap (§2) →
  `oracle/altsignal/defillama.py`'s docstring, which currently names only the chain-level user gap.
- The emissions-dataset traps (§5) → in whatever reads that dataset, if anything ever does.
- The identifier rules (§3) → `cfg/altsignal.yaml`'s header comment, beside the rows they govern.

---

## 8. Endpoints that failed, for the record

| Endpoint | Result |
|---|---|
| `api.llama.fi/overview/derivatives`, `?dataType=dailyVolume`, `/summary/derivatives/{slug}` | 402 — *"Upgrade to the paid API plan"* |
| `api.llama.fi/emissions`, `/emission/{slug}`, `/emissionsBreakdown` | 402 — Pro |
| `api.llama.fi/treasury/hyperliquid` | 400 |
| `api.llama.fi/userData/users/{slug}`, `/userData/chains/{chain}`, `/activeUsers` | 404 — no such endpoint |
| `users.llama.fi/users/{slug}` | 403 |
| `api.llama.fi/overview/{perps,dex-aggregators,royalties}` | 500 — not real dimensions |
| `api.llama.fi/summary/fees/aster-perps?dataType=dailyRevenue` | 400 — no revenue adapter |
| `api.llama.fi/tvl/astherus` | 400 "Protocol not found" |
| `api.llama.fi/tvl/{aster-perps,hyperliquid-perps}` | **200 with an empty body** — silent wrong answer |
| `api.llama.fi/protocol/parent%23hyperliquid` | 400 — use the slug |
| `api.coingecko.com/api/v3/ping` (keyless, cold) | 429 on first call, 200 ~8s later |
| `stats-data.hyperliquid.xyz/Mainnet/*` | 403 |
| `mainnet.zklighter.elliot.ai/api/v1/{info,status,layer2BasicInfo}` | 403 |
| `defillama.com/docs/api` | 403 to a scripted client; use `api-docs.defillama.com/llms.txt` |
| `docs.llama.fi`, `api-docs.llama.fi` | DNS failure from one sandbox — the live host is `api-docs.defillama.com` |

## Sources

- DefiLlama free/Pro endpoint split, rate tiers, $300/mo — <https://api-docs.defillama.com/llms.txt>
- DefiLlama API docs root — <https://api-docs.defillama.com/>
- DefiLlama emissions dataset (undocumented) — <https://defillama-datasets.llama.fi/emissions/hyperliquid>, <https://defillama-datasets.llama.fi/emissions/lighter>, <https://defillama-datasets.llama.fi/emissionsProtocolsList>
- DefiLlama price/symbol by contract — <https://coins.llama.fi/prices/current/ethereum:0x232ce3bd40fcd6f80f3d55a522d03f25df784ee2>
- CoinGecko rate limits — <https://docs.coingecko.com/docs/common-errors-rate-limit>
- CoinGecko pricing and monthly credits — <https://www.coingecko.com/en/api/pricing>
- CoinGecko markets / search / derivatives — <https://api.coingecko.com/api/v3/coins/markets>, <https://api.coingecko.com/api/v3/search>, <https://api.coingecko.com/api/v3/derivatives/exchanges>
- Hyperliquid info API — <https://api.hyperliquid.xyz/info>
- Lighter public API — <https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails>, <https://mainnet.zklighter.elliot.ai/api/v1/exchangeStats>
- Dune data catalog — <https://docs.dune.com/data-catalog/overview>
- Dune Hyperliquid dataset and its gating — <https://docs.dune.com/data-catalog/community/hyperliquid/overview>
- Dune credit system and plan prices — <https://docs.dune.com/learning/how-tos/credit-system>
- Dune API rate limits — <https://docs.dune.com/api-reference/overview/rate-limits>
