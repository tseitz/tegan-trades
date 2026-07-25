# Improvements & Known Gaps

The backlog. Things found while building that shouldn't derail the thing being built.

**Rules for this file:** an entry earns its place by carrying *evidence* — a measurement, a
count, a real example — not a hunch. If it's just "we could do X better", leave it out. Record
what we know now so a future session doesn't have to re-derive it. Delete entries when done.

Status: `DECIDED` (agreed, not executed) · `OPEN` (real gap, no decision) · `WATCHING` (may not
be a problem; revisit if it bites).

---

## 1. Levels are not the product — sentiment and trust are · `DECIDED`

**The call (2026-07-25):** we went too hard, too fast at extracting exact price levels from
videos. What the roster is actually *for* is **sentiment** — direction and conviction — plus a
**trust score** of how right each person has generally been. Real levels can come from
elsewhere. If someone happens to state a level, take it; never force it.

**What to change**
- Drop `TradeThesis.key_levels` `min_length=1` (`core/thesis.py:38`). It forces the model to
  emit levels whether or not the speaker gave any.
- `core/levels.py` stays, demoted from load-bearing to opportunistic. Its abstention path is
  already the designed fallback, so nothing breaks.
- Phase 4 leans on structural targets, which is already the fallback.

**Evidence**
- **388 of 1,621** live readings (24%) had levels *only behind* entry — those are invalidations
  and stops stuffed into `key_levels` to satisfy the schema, not targets.
- 13 of the 98 re-distill failures were exactly this constraint (`min_length=1`) rejecting a
  whole video's extraction because one call had no explicit level.

**Consequences worth noting**
- This *validates* `core/grade.py`'s direction-and-horizon-only design — it was already grading
  the right thing.
- It buries **3.3b (level-aware grading) permanently.** Don't resurrect it.
- It raises the priority of the trust score, which is the roster-scoring work in
  `oracle/score_cli.py` — currently honest but underpowered (see §4).

---

## 2. Rip out the fixed horizon constants · `DECIDED` (needs a replacement first)

**The call (2026-07-25):** the 7/30/180/365-day horizons are unvalidated guesses and should go.

**They cannot simply be deleted** — they're load-bearing in two places:
- `core.grade.Horizons` — how long a call is measured over
- `core.setups.StaleAfter` — how long a stated view stays actionable

**Candidate replacement: event-based, not clock-based.** A call is live until the same person
restates or reverses on the same asset. `triage_cli.collapse_restatements` already computes
exactly that grouping, and `core/stance.py` already tracks lean changes. No constants, entirely
data-driven, and it answers the real question ("is this still their view?") instead of a proxy.

**Evidence the current constants can't be rescued**
- Restatement cadence is near-identical across timeframe labels (swing 11d, position 14d, scalp
  28d) — it measures publishing schedule, not view horizon.
- A dense per-label horizon sweep found no distinct optimum per label.
- The whole horizon sweep sits *inside* the bootstrap noise floor (Kendall tau p05 +0.44), so
  the constants are unfalsifiable with current sample size.

---

## 3. Take another lap on ICT — mine TraderMayne's courses · `OPEN`

**Why:** the current spec in `Trading/_Structure.md` is reverse-engineered from a **truncated
"3 Things" list** (announced three, listed two) plus conversation. TraderMayne is the primary
source for this method *and publishes explicit courses on it*. Mining those would replace
inference with the actual system.

**Specifically unresolved in the spec today**
- The missing third "thing" (premium/discount was inferred, not stated)
- Whether the FVG middle candle must be displacement, and how displacement is really defined
- How the dealing range is bounded (2-bar pivot on weekly is a guess)
- The 15m entry trigger — "failed breakdown, reclaim" needs real definition, and it's the whole
  of slice 2's layer 3

**Practical note that will cost time if missed:** the ingestion spine handles YouTube already,
but **the distill prompt is built for trade theses and correctly returns EMPTY on methodology
videos** (observed: "How To Use SMT Divergence" → 0 theses, which was the right answer). Mining
courses needs a *separate methodology-extraction pass* with its own prompt and schema — it is
not a `distill-roster` run.

**Output:** update `Trading/_Structure.md`, which is the spec `core/structure.py` implements.

---

## 4. Nothing is validated against revealed preference · `OPEN` — highest leverage

There are now **four scoring systems** and **zero closed loops**:

| Scorer | Where | Validated against |
|---|---|---|
| Intrinsic thesis rank | `core/rank.py` | nothing |
| Backtest / skill edge | `core/grade.py`, `core/score.py` | market only, no preference signal |
| Brain retrieval | `brain/retrieve.py` | nothing (and it barely discriminates — §8) |
| Setup candidates | `core/setups.py` | nothing |

Mining `data/triage/decisions.jsonl` (approve vs skip) against the rankers was agreed during
Phase 3 and has never happened, because triage has been run **once**, promoting 7.

**This is why "7 candidates from 3,427 theses" is unanswerable** — admirably selective or badly
miscalibrated, and nothing in the system can distinguish them.

**Unblocked by:** using `setups` and `distill-triage` for real, a few sessions. Costs no tokens,
needs no new code, and produces the only ground truth available.

---

## 5. `trend_state` is noisy — two swings decide everything · `OPEN`

`core.structure.trend_state` reads only the **last two swing highs and last two swing lows**.
One unusual swing flips the verdict.

**Evidence:** BTC weekly reads `ranging` as of 2026-07-24, and since ranging permits neither
direction, **BTC — 28.5% of the corpus — yields zero candidates.** That may be correct, but it
rests on four data points.

Candidate fix: score the structure *sequence* (how many recent swings agree) rather than a
two-point comparison, so the state degrades gradually instead of flipping.

---

## 6. No freshness loop · `OPEN`

The machinery is batch-historical; the use case is real-time. Nothing runs on a schedule —
every Brain answer and every setups run is only as current as the last hand-run sweep.

**Evidence:** `stale` is **2,872 of 3,427** rejections (84%) in the live setups run. Partly an
artifact of scanning two years of corpus at one as-of date, but the underlying gap is real: the
question worth answering is "price is approaching this level *now*", which needs a scheduled
ingest → distill → setups pipeline.

---

## 7. Stop sanity should be ATR-relative, not a minimum width · `WATCHING`

A minimum stop width was considered and declined, correctly — score saturation already caps the
ranking damage (RR saturates at 3.0, so 15.75 and 4.67 contribute identically).

The real concern is different: **GOOGL's 5.51 stop is roughly 1 ATR**, which ordinary noise
takes out. RR without survival probability is a half-metric. The right form is `stop >= k * ATR`,
not an absolute width. `Context` already carries ATR, so it's cheap.

Revisit if narrow zones keep topping the list.

---

## 8. Evidence-leg retrieval doesn't discriminate · `OPEN`

Brain retrieval scores compress into **0.72–0.81** — cosine barely separates anything, because
every chunk is "a person talking about markets in ASR speech." Queries return Discord-giveaway
chatter above real analysis.

**Not a coverage problem** — all 666 transcripts are indexed (18,108 chunks). Adding corpus does
not fix it. Hypothesis: chunk granularity plus a missing lexical/BM25 leg.

**Dead end, do not re-test:** `query_embed` is identical to `embed` for this model, so the bge
query-prefix theory is disproven.

---

## 9. Extraction runs on the expensive path · `OPEN`

~90% of every `claude -p` extraction call is harness overhead; the direct API is **8–15× cheaper**
(~$26 vs ~$183 per full corpus pass). The whole extraction spine was built on the expensive path.

Worth fixing before any bulk re-extraction — including the methodology pass in §3.

---

## 10. `domain` is per-thesis and inconsistent per asset · `WATCHING`

`SPX` appears in the corpus as `crypto`, `macro`, *and* `stock` across different theses, so
`tier_for` can label the same asset differently depending on which thesis surfaces.

Contained for now: `tier_for` gates on domain specifically to stop the SPX/memecoin rank
collision leaking in. A per-asset domain consensus in `core/canon.py` would be steadier.

---

## 11. Slice 2 needs the oracle at sub-daily granularity · `OPEN`

Layers 2–3 of `Trading/_Structure.md` (1H approach, 15m trigger) require a `date` → `datetime`
refactor through `Bar`, `PriceSeries`, `cache` (granularity in the key), all three sources, and
`core/grade.py`. **Done halfway it corrupts silently** — `PriceSeries.__post_init__` dedupes on
`bar.date`, so 24 hourly bars for one day collapse to 1 with no error.

Note the granularity needed is **900s (15m)**, not just 1H/4H. Coinbase supports it; its cap is
`MAX_CANDLES = 300` (the Phase 4 plan's "720" is wrong).
