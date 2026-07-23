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
- **Build against the xAI Responses API** (Chat Completions is legacy for this).
- **HARD RULE — honor the `degraded` flag.** When `x_search` finds no matching posts, Grok falls back to synthesizing from training data (i.e., inventing sentiment). We must log the flag and treat a `degraded: true` response as **silence, not signal** — critical in a quiet bear market. This aligns with our traceability principle: no unsourced claim enters the corpus.

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
