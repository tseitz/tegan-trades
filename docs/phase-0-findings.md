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
