# Phase 0 — Source Feasibility Findings

_Verified: 2026-07-22_

## Per-source feasibility

| Source | Access | Cost | Rate limits | Verified how | Recommendation |
|---|---|---|---|---|---|
| YouTube transcripts | ok | free | none hit | Task 4 integration spike — real transcript ingested to `data/transcripts/youtube/` | **Use — primary thesis channel** |
| Podcasts (RSS + Whisper) | ok | ~$0.36/hr (OpenAI Whisper API @ $0.006/min); free via local whisper.cpp | none | RSS enclosure → audio → Whisper; standard, well-trodden path | **Use — for audio-only shows** |
| X.com | via Grok | **~$1–5/mo** | tool calls billed $5/1k | Agent research (2026 xAI docs) | **GO — via Grok `x_search`, not official API** |
| Kalshi | ok | free | public | `curl` smoke → HTTP 200, real markets JSON | Use (Phase 5) |
| Polymarket (Gamma) | ok | free | public | `curl` smoke → HTTP 200, real markets JSON | Use (Phase 5) |
| On-chain (DefiLlama) | ok | free | public | `curl` smoke → HTTP 200, 8.4 MB protocols JSON | Use (Phase 5) |

## The X decision — GO, via Grok `x_search`

Tegan's "use Grok to distill my X list" idea turned out to be the *purpose-built* answer, not a workaround.

- **xAI's Grok API exposes an `x_search` tool** with an `allowed_x_handles` parameter (**max 20 handles — his list is 17, fits exactly**) plus `from_date`/`to_date`. One daily call: pass the handles + a 24h window, prompt for a market-relevant sentiment/timing digest, get back a synthesized summary.
- **Cost ~$1–5/month.** Tokens are fractions of a cent per run; tool calls are $5/1,000 (≈150/mo → <$1). The official X API went pay-per-use and would run **~$50/mo** for the same list (and returns raw posts you'd still have to summarize). Scrapers are cheap but high-maintenance/fragile.
- **Build against the xAI Responses API** (Chat Completions is legacy for this). Confirmed 2026-07-26: the old **Live Search API was deprecated 2026-01-12**; `search_parameters` on Chat Completions is dead and `x_search` is now a server-side tool in the `tools` array on `POST https://api.x.ai/v1/responses`.

### API surface, verified 2026-07-26

| | |
|---|---|
| `allowed_x_handles` | array, **max 20** — our 17 fit. Mutually exclusive with `excluded_x_handles`. |
| `from_date` / `to_date` | ISO8601 `YYYY-MM-DD`, inclusive both ends — gives us the 24h window. |
| `enable_image_understanding` | bool — **the answer to "can it read their charts"**. Billed as image tokens, *not* as a tool invocation. |
| `enable_video_understanding` | bool. |
| Structured output | `text.format` = `{type: json_schema, name, strict: true, schema}`. Note **Responses API uses `text.format`, not Chat Completions' `response_format`** — easy to get wrong. Server-side tools run during the turn; the schema constrains the terminal message, so search-then-structure is one call. |
| Cost | **$5 / 1,000 invocations** for `x_search`, plus tokens. `grok-4.5` is $2/M in, $6/M out (<200k prompt). A daily digest is a few invocations and a few thousand tokens — **the ~$1–5/mo estimate holds.** |
| Latency | 60–120s for complex queries. Fine for a nightly job, too slow to sit behind an interactive prompt. |

### Live spike, 2026-07-26 — 5 real calls, ~$0.46 total

Run against the real 17 handles via `POST /v1/responses`, `grok-4.5`. Everything below is
measured, not documented.

#### 1. Structured output **silently disables** server-side tools — the blocking finding

| call | `x_search_calls` | result |
|---|---|---|
| with `text.format` strict json_schema | **0** | `{"posts_found":0,"theses":[]}` |
| identical call, no schema | **4** | 6 inline annotations, real posts |

**The search never runs when a strict schema is attached, and it fails open:** HTTP 200,
`status: completed`, `error: null`, schema-valid JSON saying zero posts — byte-identical to a
genuine quiet day. Adding `tool_choice: "required"` changed nothing; it was echoed back and
ignored. This is the same silent-failure class as §6d.

**Consequence: search-then-structure cannot be one call.** The integration is necessarily two
passes — (1) `x_search` *unstructured* to get text + annotations, (2) a separate structuring
pass over that text. Pass 2 needs no tools, so it can run on our existing `claude -p`
(subscription) rather than paying xAI for it.

**Second-order gotcha:** the model cannot see the tool's own `allowed_x_handles` or dates. Its
verbatim reasoning on attempt 1 was *"the message doesn't specify which accounts or the time
window"*, and it skipped searching. **The window must be restated in the prompt text.**

#### 2. Chart reading works, and is better than transcripts for levels

`enable_image_understanding: true`, real output:

    @trader1sz  SOLUSD 1H — 83.609 "Last week high", 81.612, 79.614, 77.617,
                            75.620 "Last week low", 73.558 "Monthly open", current ~73.749
    @trader1sz  BTCUSDT 1D — ~74000, 60000.00, zone 58000–62000, current 65450.01

Labelled, numeric, timeframe identified, and it volunteered which values came from the image
versus the text. It also declined to guess on unreadable charts when told to. Note §1 says
levels are not the product — but this is the highest-fidelity level source in the system.

**It is expensive.** That one call: **279,653 input tokens, 25 server-side tools, ~$0.40** —
roughly **10x** the text-only call (~$0.04). Image understanding must be a deliberate, scoped
mode, never always-on.

#### 3. How to detect silence — *not* the way the docs imply

- **Top-level `citations` was absent on every call** (`None`), despite the citations doc
  describing it. Do not build on it.
- **`usage.num_sources_used` was `0` even on calls that returned real cited posts.** It is
  not a usable signal. Do not build on it either.
- **What actually works:** `usage.server_side_tool_usage_details.x_search_calls > 0`, plus the
  presence of inline `annotations` on the `output_text` block.
- The genuine-empty case is honest: 1 `x_search_call`, **zero annotations**, and the text says
  "No posts found." It did not invent sentiment.

Annotation shape: `{"type": "url_citation", "url": "https://x.com/<handle>/status/<id>",
"start_index", "end_index", "title"}`.

#### 4. Provenance is best-effort, so validate it ourselves

Annotation quality is **inconsistent between calls**: the text-only call returned real
`start_index`/`end_index` spans (bindable to individual claims), while the image call returned
all zeros and `https://x.com/i/status/<id>` URLs carrying **no handle**. So per-claim binding
cannot be assumed.

Worse, in the empty-window test the model narrated that it searched **unrestricted** —
*"the search was not restricted to particular usernames"* — even though `allowed_x_handles`
was set. Results in the populated call did all come from roster handles, so the server-side
filter does appear to apply, but **we cannot verify it from the response.**

**Two integrity rules to build in from the start, both cheap:**
1. Every extracted X thesis carries its own `post_url`; drop any whose URL is not in the
   response's annotations.
2. Every `post_url`'s handle must be in `allowed_x_handles`; drop it otherwise, and count the
   drops. This is the only defence against the filter silently not applying.

#### 5. Cost model, measured

`cost_in_usd_ticks` is **1e-10 USD per tick** (verified against token prices on two calls) and
appears to exclude tool invocations. Text-only digest ≈ **$0.04–0.06**; with images ≈ **$0.40**.
A nightly text digest plus a weekly image pass lands around **$3/mo** — Phase 0's $1–5/mo
estimate survives, but only if images stay scoped.
- **HARD RULE — no unsourced claim enters the corpus.** When `x_search` finds no matching posts, Grok falls back to synthesizing from training data (i.e., inventing sentiment). Treat that as **silence, not signal** — critical in a quiet bear market.

> **CORRECTION 2026-07-26 — there is no `degraded` flag.** This entry originally said to "log
> the flag and treat `degraded: true` as silence." Re-checked against
> [docs.x.ai](https://docs.x.ai/developers/tools/x-search) and the
> [citations page](https://docs.x.ai/developers/tools/citations): **no such field is
> documented**, on `x_search` or on the response. A rule that keys off a non-existent field
> fails open — it would have silently admitted exactly the invented sentiment it was written
> to stop.
>
> **The real mechanism is the citations array.** A response carries `citations` — "a
> comprehensive list of URLs for all sources the agent encountered" — plus optional inline
> annotations (`type`, `url`, `start_index`, `end_index`, `title`) inside each `output_text`
> block. So the detection is **empty/absent citations = silence**, asserted by us, not
> reported by them.
>
> **This is weaker than what we have for YouTube, and the gap is structural.**
> `architecture.md` promises "every distilled claim links to the exact moment it was said — no
> LLM hallucination goes unchecked." A transcript thesis carries its own quote and timestamp.
> An `x_search` response gives a *synthesized* answer plus a **response-level** URL list —
> citations are not bound to individual claims. Nothing in the API stops the model attributing
> post A's take to post B.
>
> **Mitigation to build in from the start:** require every extracted X thesis to carry its own
> `post_url`, then **validate that URL against the response's `citations` array** and drop any
> thesis whose URL isn't there. That converts an unverifiable claim into a checkable one and
> restores most of the traceability guarantee. It is cheap and must not be deferred.

**Strategic placement:** X is **Brain-head fuel** (aggregate sentiment/timing), not Signal-head theses. The Grok digest is the right fidelity for that job — we do *not* need full-fidelity per-post capture. Thesis-grade signal comes from the YouTube/podcast pipeline.

## Roster feasibility takeaways (feeds Task 7)

- **6 of 17 X-only names have a transcribable long-form home** → ingest via YouTube, skip X for them: Nadeau (already a source), **CryptoCred + DonAlt (both via their Technical Roundup show)**, Magic Lines, Pierre, Mark Newton. Plus Mayne (already known).
- **"Technical Roundup" = CryptoCred + DonAlt's Mon/Wed/Fri show** — highest-leverage find; not a separate source.
- **TraderSZ (= Z$1, @trader1sz): likely dormant on free YouTube** (last public upload ~2022; content moved behind the paid tradersz.com). Flag before relying on it.
- **GCR: effectively retired** — rare one-off X posts only; no transcript path. The Grok digest catches him if he surfaces.
- **Guest-only voices** (thiccy, arthur0x, Rewkang, Lyn Alden, Willy Woo) require "find name across N host channels" ingestion, not single-feed — a Phase 1+ capability decision.

## Data platforms / alt-signal (Phase 5 sources)

Verified 2026-07-22. Tiered by signal-per-dollar for a solo dev, weighted to Tegan's priorities (funding/OI = "who's offsides", sentiment extremes, unlock catalysts, on-chain cycle).

### Grab — free (no or negligible cost)
| Platform | Data | Access | Verified |
|---|---|---|---|
| **Alternative.me Fear & Greed** | sentiment extremes | free, no key — `api.alternative.me/fng/` | ✅ curl 200 (live value 31 = Fear) |
| **Hyperliquid API** | funding / OI / order book (HL) | free, no key — `api.hyperliquid.xyz/info` `{"type":"metaAndAssetCtxs"}` | ✅ curl 200 (71KB, funding+OI) |
| **DefiLlama `/unlocks`** | token unlock/vesting schedules | free, no key | agent (NEW — free replacement for paid Tokenomist API) |
| **checkonchain** | BTC on-chain cycle (MVRV/SOPR/NUPL) | free charts, no API (manual) | agent (free Glassnode alternative for BTC) |
| **Dune** | custom on-chain SQL | free tier 2,500 credits/mo, **API included** | agent |
| **Flipside** | on-chain SQL | free Community API | agent (Dune fallback) |
| **Tokenomist / Token Terminal / LunarCrush / Nansen** | (their free web/lite tiers) | free tier only | agent |

### Cheap paid — worth it
| Platform | Data | Cost | Note |
|---|---|---|---|
| **Coinglass** (Hobbyist) | funding, OI, liquidation heatmaps (cross-exchange) | **$29/mo** | **PRIORITY** — best $/signal on the board; = "who's offsides". V4 API `open-api-v4.coinglass.com`, `CG-API-KEY` header |
| **LunarCrush** (Individual) | social sentiment, Galaxy Score/AltRank | ~$24/mo (free tier has AltRank) | Brain-head fuel |
| **Nansen** (Pro) | smart-money wallet flows | $49/mo — or **x402 pay-per-call ~$0.01–0.05/call** | pay-per-call is ideal for occasional checks |

### Skip for a solo dev (free/cheap alternatives cover them)
- **Glassnode** — API only at ~$999/mo; use **checkonchain** (free) for BTC cycle metrics.
- **Kaito AI** — ~$833/mo; Yaps program died Jan 2026. See note below.
- **Velo Data** ($199/mo) — Coinglass covers ~90% cheaper. **CryptoQuant** ($99/mo API), **Token Terminal** ($49/mo) — optional, not core.
- **Coin360**, **Blockchain Center Rainbow** — viz only, no real API moat; commodity/computable data.

**Decision (2026-07-22):** free sources approved for Phase 5. **Coinglass ($29/mo) deferred to Phase 5** — approved as the priority paid source, not purchased yet.

### Recommended stack (~$29–53/mo covers all four priorities)
- **Funding/OI:** Coinglass $29/mo + Hyperliquid free
- **Sentiment:** Alternative.me F&G free + LunarCrush free/$24
- **Unlocks:** DefiLlama `/unlocks` free (+ Tokenomist free web)
- **On-chain cycle:** checkonchain free + Dune free

### Gold standards (2026) & what changed while Tegan was away
- On-chain metrics: **Glassnode** (institutional, $$$) / **checkonchain** (free, BTC). Derivatives: **Coinglass** (trader gold standard) + **Hyperliquid** free. On-chain SQL: **Dune** (still king) / **Flipside** (free alt). Sentiment: **LunarCrush** + **F&G**. Unlocks: **Tokenomist** / **DefiLlama** (free).
- **Kaito Yaps shut down Jan 2026** (X cut engagement-farming API access); Kaito pivoted to **mindshare prediction markets on Polymarket** — which ties directly into our Phase 5 Kalshi/Polymarket work: we can read narrative attention *as a market* instead of paying for the feed.
- **Nansen** added **x402 USDC pay-per-call** (~$0.01/call) — subscription-free smart-money checks.
- **Hyperliquid's** free Info API is now a first-class funding/OI source (didn't exist meaningfully 2 yrs ago).
- **Dune** moved to credits with **free-tier API included** (previously paid-only).
