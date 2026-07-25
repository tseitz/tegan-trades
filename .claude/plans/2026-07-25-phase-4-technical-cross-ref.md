# Phase 4 — Technical Cross-Reference

> **STATUS: DRAFT — not approved.** Scouted 2026-07-24 after Phase 3.5 shipped. Three
> prerequisites surfaced that change the shape of this phase; two are decisions only Tegan
> can make. Do not start implementing from this doc until the Design section is settled.

## Where this sits

`architecture.md` §C: **Phase 4 · Technical cross-ref — "Align thesis levels with my ICT
structure + TV webhook alerts" → "Setup candidates."** This is the Signal head's second half.
Phase 3.5 (Brain head) shipped in `e45577d`; the shared corpus, price oracle and canon layer
all exist. What does not exist is any link between *what a person said* and *what price is
actually doing*.

## What scouting found

### 1. The trading manifesto the whole phase is specified against does not exist

`architecture.md` and `_overview.md` both reference `[[_Trading Main]]` — *"my trading
manifesto, rules, and leaks (the system is built around these)"*. **There is no such file in
the vault.** Searched `~/vault` for `_Trading Main` and any `*trading*manifesto*`: nothing.

This is the crux. "Align thesis levels with **my** ICT structure" is unimplementable while
"my ICT structure" is undefined. The system needs Tegan's actual rules, concretely enough to
compute: which ICT constructs count (order blocks, FVGs, breakers, premium/discount,
liquidity sweeps, killzones?), on which timeframes, and what makes a confluence strong enough
to promote a thesis into a *setup candidate*.

**This is the gate. Everything below is provisional until it is written down.**

### 2. Thesis levels are an untyped bag of floats — and this is now load-bearing

Measured on the live corpus: **1,182 trade theses, all with levels** (`min_length=1` enforces
it), 1–9 levels each — 295 have exactly one, and the multi-level ones are where the ambiguity
bites. Nothing distinguishes trigger from target from stop. Real example from the 3.3 plan:
HYPE long `[40, 50, 30, 21, 19, 20]` where 40 is a trigger, 50 a target, the rest downside.

This is the enrichment specified as **3.3b** and deliberately deprioritized — see
[[phase-3-state]]. **The reason it was dropped does not apply here, and that is worth being
explicit about:**

> 3.3b was dropped because sharper grading cannot fix a *sample-size* constraint — per-person
> statistical ranking needs many independent resolving events, and precision per call does not
> create them. Cross-referencing has no such requirement. "Is price approaching Cred's stated
> 104k invalidation right now?" is actionable at n=1.

So the level-enrichment pass earns its cost under Phase 4 even though it did not under 3.3.
Same work, different justification. It should be scoped here, not resurrected as a grading task.

### 3. The oracle is daily-only, hardcoded in all three sources

`coinbase.GRANULARITY_DAILY = 86400`, `kraken.INTERVAL_DAILY`, `yahoo.interval="1d"`. ICT
structure is substantially intraday — order blocks and FVGs on 4H/1H, killzones by definition
intra-session. Daily bars can support level-proximity and daily-bias checks but not most of
what ICT actually means.

Coinbase's candles endpoint accepts other granularities (60/300/900/3600/21600/86400) and
Kraken has an `interval` param, so this is an extension rather than a new source — but the
720-candle and 300-candle caps bite far harder intraday, and the cache/backfill story changes
shape. Not free.

## Provisional tasks — do not start until the Design gate clears

1. **Level enrichment** (was 3.3b). LLM pass over the 1,182 trade theses → typed
   `{entry, targets[], stop, condition}` where `condition ∈ {touch, daily_close, weekly_close}`.
   Sidecar at `data/levels/`, keyed by content-addressed thesis id so a re-distill keeps it.
   Mirrors the triage-decisions sidecar. Costs tokens; scope before committing.
2. **Intraday oracle** (only if the manifesto needs sub-daily). Granularity parameter through
   `sources/*`, `cache`, `series`; explicit handling of the tighter candle caps.
3. **ICT structure primitives** — pure functions over `PriceSeries`, whatever the manifesto
   names. Same shape as `core/grade.py`: pure, duck-typed, no I/O.
4. **Cross-ref engine** — join enriched levels against computed structure → scored setup
   candidates. Pure; the interesting part is the scoring rule, which is a manifesto question.
5. **`setups` CLI + vault output** — mirror `distill-triage`'s approve-each flow and vault-note
   emission. Setup candidates are a promotion artifact, not a firehose mutation.
6. **TV webhook alerts** — deliberately last and probably a separate slice. Outbound
   integration, needs a real endpoint, and is worthless until 1–5 produce candidates worth
   alerting on.

## Patterns to Mirror

- `packages/core/src/core/grade.py` + `score.py` — pure, duck-typed, frozen-dataclass config,
  sum types so "no result" is never confused with "zero". The template for 3 and 4.
- `packages/oracle/src/oracle/route.py` + `cfg/oracle_map.yaml` — curated-first routing that
  refuses to guess. Any new symbol/timeframe work extends this, never bypasses it.
- `packages/distill/src/distill/triage_cli.py` — approve-each CLI, JSONL decision sidecar,
  vault-note append. Task 5 is this shape.
- `packages/brain/src/brain/extract.py` — per-item validation, drop-and-log, injected client.
  Task 1's enrichment pass should validate per thesis, not per batch.
- `.claude/plans/2026-07-24-phase-3.3-backtesting-roster-scoring.md` §7 — the original 3.3b
  spec for typed levels. Reuse it; don't redesign it.

## Considerations / open questions

- **The manifesto gate.** Nothing here is safely specifiable without it. Writing it is a vault
  task, not a repo task, and it is the single highest-leverage thing to do before Phase 4.
- **Phase 3.5 has two open loose ends**, both independent of Phase 4 and neither blocking it:
  (a) stance extraction is **16 of 666 transcripts** (~$260, 4–7h, resumable); (b) evidence-leg
  ranking is weak — scores compress into 0.72–0.81, so cosine barely discriminates. Diagnosis
  so far points at chunk granularity plus the absence of a lexical/BM25 leg. See
  [[brain-context-tier]].
- **Which corpus does Phase 4 read?** Enriched *trade theses* (Calls tier) are the natural
  input. But the Brain's `watching` field already captures invalidations in prose
  ("a weekly close under 2400") for stances that carry no `key_levels` at all. Those may be a
  richer source of levels than `key_levels` is — worth checking before assuming the Calls tier
  is the only input.
- **Staleness matters more here than anywhere.** A setup candidate built off a 14-month-old
  level is noise. `report.STALE_AFTER_DAYS` exists for the Brain; Phase 4 needs its own,
  probably stricter, rule.
- **Scope discipline.** Tasks 1–5 alone are comparable in size to the whole Brain head. The
  TV webhook (6) is a separate slice and should not be bundled.
