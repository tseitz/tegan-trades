# Improvements & Known Gaps

The backlog. Things found while building that shouldn't derail the thing being built.

**Rules for this file:** an entry earns its place by carrying *evidence* — a measurement, a
count, a real example — not a hunch. If it's just "we could do X better", leave it out. Record
what we know now so a future session doesn't have to re-derive it.

**Delete entries when done.** An entry is a *to-do*, not a changelog — the fix itself, and the
reasoning behind it, belong in the code and in git history. Before deleting, do two things: move
any live residual into the entry that still owns it, and make every cross-reference to it
self-contained, because entries here cite each other by number and a deleted number is a dead
link. Numbers are never reused.

Status: `DECIDED` (agreed, not executed) · `OPEN` (real gap, no decision) · `WATCHING` (may not
be a problem; revisit if it bites) · `PARTLY DONE` (a residual is named in the entry).

---

## Where to start

**The thing that blocked the most work is fixed, and the bottleneck moved.** §4's sampling
defect — each sitting judging a narrower slice than the last, with the approval threshold
moving with it — was fixed on 2026-07-28: the queue draws a stratified sample, every decision
records what else was on screen, and §11 and §19(d) unclipped the two terms that could not
vary. Every entry that says "measure this against the sidecar first" now waits on one
`uv run setups` sitting.

**And that sitting has nothing to judge.** 67 of 69 candidates are already decided, so the
command prints `Nothing to review`. The bottleneck is no longer measurement design, or code, or
attention — it is **candidate supply**.

**§6f was tried as the fix for that and did not deliver it.** `ETH/BTC` now prices, and its 29
rows reach the gates instead of being invisible, but every one is refused: the weekly trend is
down and the roster is majority-long. Correct behaviour, zero new candidates.

**§27 was the supply lever, and it shipped on 2026-07-28** — this paragraph used to say no lever
was left. The `timeframe_conflict` gate was counting a one-sided daily reading as a
disagreement; releasing the 256 ranging-weekly refusals took candidates **74 → 97** and the
queue from `Nothing to review` to **22 rows**. §4 is unblocked.

| | Entry | Why now | Cost |
|---|---|---|---|
| 1 | **§4** — more sittings | Four have been held and 24 v6 rows exist, but the cohort is homogeneous: `daily_trend` has zero variance, so `trend_alignment` and §27's option 2 still cannot be measured. What is needed is a *mixed* population, which arrives as the nightly turns the queue over. | your attention |
| 2 | **§21 coverage** — widen `cfg/venue_map.yaml` | The queue is barely actionable: of 15 live rows exactly one had a venue entry, and at least 8 of the 12 unmapped are demonstrably listed somewhere. Each row needs the instrument confirmed, not the string matched — see the `SPX6900` trap. | free, local |
| 3 | **§27 residual** — targets vs the range rule | 68 candidates now live on a ranging weekly and only 20 sit within 2% of the range bound the stated rule names (median gap 9.5%). Read §18 first. | free, local |
| 4 | **§6f residual** — 25 routed-but-unfetched assets | 26 rows that route fine and were never fetched. `fetch-prices` is tier 🟡 (free). Caveat: several are `needs_validation` guesses (`ZT`, `AUD`, `BCOM`, `BAE`) that may simply not resolve. | free, network |

**Done 2026-07-28:** §4's sampler, decision context and reason vocabulary 2; §11's cap and
§19(d)'s distance inflation, both as `SCORE_VERSION` 6; §6f's grouped unpriced tally and the
`ETH/BTC` derived series; §27's audit and its option 1; §28's alias merges and §29's inverted-FX
guard; **ATR stop padding as `SCORE_VERSION` 7** — which also supplied the measurement
§19's last open claim was waiting on.

**Waiting on a *mixed* sitting, not on code:** §18 (`collapse` rep rule) · §21 (funding
weighting) · §11's recency half · §27's option 2 (daily leg as a score). Each defers to a
sidecar correlation that is valid to make; what the four sittings held so far cannot supply is
variation, because every row they drew was the population §27's option 1 released.

**Not blocked, but needs its own measurement first:** §15 (SMA confluence — does 50W actually
mark turns).

**The rest, by theme:** corpus supply §3 · §6 · §6b–§6h · §9 · §14 — durability §4b · §24 —
venue and execution §22 · §23 · §25 — scoring inputs §1 · §2 · §8 · §10 · §12 — environment §13.

---

## 1. Levels are not the product — sentiment and trust are · `PARTLY DONE`

**Done:** `min_length=1` is dropped, the prompt now says levels are optional and forbids
inventing one, and `TradeThesis` is distinguished from a lean by its `invalidation` alone.

**Residual: the existing corpus was extracted under the old prompt.** All 3,427 stored theses
were produced by a model that had to supply a level, so the fabricated ones are still in
`data/theses/`. The fix only applies to what gets extracted from here. Options: live with it and
let the corpus turn over naturally, or re-distill — which is a full-corpus LLM pass, so read §9
first. Do NOT re-distill just for this; the levels were never the product.

**The call, so it is not re-litigated (2026-07-25):** what the roster is *for* is **sentiment**
— direction and conviction — plus a **trust score** of how right each person has been. Levels
can come from elsewhere; if someone states one, take it, never force it. The evidence was that
**388 of 1,621** live readings (24%) had levels only *behind* entry — invalidations and stops
stuffed into `key_levels` to satisfy the schema — and 13 of 98 re-distill failures were
`min_length=1` rejecting a whole video over one call with no level.

Two consequences that outlive the fix: it **validates `core/grade.py`'s direction-and-horizon-
only design**, and it **buries 3.3b (level-aware grading) permanently — do not resurrect it.**

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

### The course is already ingested and already queryable · found 2026-07-26

It's a **complete 15-episode "whiteboard series"**, weekly on Mondays, 2026-04-16 → 2026-07-20,
all indexed. Episodes located by topic:

| ep | date | topic | ep | date | topic |
|---|---|---|---|---|---|
| 1 | 04-16 | liquidity, judas swing, displacement, premium/discount | 9 | 06-08 | OTE |
| 2 | 04-20 | market structure break vs. shift | 10 | 06-15 | AMD |
| 3 | 04-27 | liquidity sweeps | 11 | 06-22 | — |
| 4 | 05-04 | dealing ranges | 12 | 06-29 | — |
| 6 | 05-18 | fair value gaps | 13 | 07-07 | trade grading |
| 7 | 05-25 | order blocks | 15 | 07-20 | internal/external range liquidity |
| 8 | 06-01 | order blocks vs. FVGs | | | |

**This lowers the cost of §3 considerably.** An extraction pass is needed to *update the spec*;
it is NOT needed to *answer questions*, which `brain_search` now does for $0. Three of the four
listed unknowns have a dedicated episode (premium/discount → ep1, dealing range → ep4, FVG
displacement → ep6+ep8). Read them via `brain_search(..., person="TraderMayne")` before
committing to an extraction pass — the pass may only need to cover what's left.

**The fourth unknown is not in the corpus at all.** "15m entry trigger — failed breakdown,
reclaim" was the single worst-retrieving query of 12 (top1 0.634, 3/5 hits were Pierre's
chart-request chatter and an off-topic Magic Lines passage about crypto builders). Everything
else in the probe pulled clean course material, so this is a corpus gap, not a retrieval miss.
**Mining the courses will not resolve it** — and §3 calls it "the whole of slice 2's layer 3".
That needs a different source, or Tegan's own definition, and it blocks slice 2 either way.

---

## 4. Revealed preference is the only ground truth, and it cannot currently be read · `PARTLY DONE 2026-07-28` — absorbs §20

There are **four scoring systems** and **zero closed loops**:

| Scorer | Where | Validated against |
|---|---|---|
| Intrinsic thesis rank | `core/rank.py` | nothing |
| Backtest / skill edge | `core/grade.py`, `core/score.py` | market only, no preference signal |
| Brain retrieval | `brain/retrieve.py` | nothing (and the asset path barely discriminates — §8) |
| Setup candidates | `core/setups.py` | nothing |

Mining `data/setups/decisions.jsonl` — approve versus reject, against the ranker's own terms —
is the only ground truth available. The instrumentation for it is now complete. **The
measurement it supports is not valid, and that is what this entry is about.**

### What the sidecar holds

**77 rows** — 14 v2 · 8 v3 · 7 v4 · 48 v5 — as **29 approved · 16 rejected · 20 later · 12
archived**. `score`, `freshness`, `agreement` and `zone_timeframe` on all 77; `approach`,
`price` and `reward_risk` on 63; `reason` on 20 and `reason_note` on 19. Every row carries
`score_version`, so a correlation **must partition on it** rather than pooling — the scale
changed at v2, again at v3 (`proximity`+`depth` → one `approach` ramp), v4 and v5.

Replay it with `scripts/probe_freshness_weight.py` — free, local, and it reproduces the
shipped scorer exactly (`max |recomputed − stored| = 0.00e+00`), so its numbers are the real
ranker rather than a model of it.

### The blocker: a sitting is not a random sample of the queue

The queue is score-ordered and capped, so the first sitting spans a wide score range and each
later one works a narrower slice of the tail. A term cannot order what barely varies:

| sitting | n (appr v neg) | score range | `score` AUC |
|---|---|---|---|
| 18:38 | 10 v 9 | 0.397–0.812 | 0.856 [0.66, 1.00] |
| 19:53 | 3 v 3 | 0.540–0.580 | 0.222 [0.00, 0.67] |
| 20:00 | 6 v 4 | 0.403–0.527 | 0.458 [0.04, 0.88] |

The only sitting whose interval clears chance is the only one with a wide range — that is
restricted range doing the work, not the scorer improving and then failing.

**And the approval threshold moves between sittings.** The later sitting approved at a median
score of **0.484** while the earlier one *rejected* at a median of **0.518**;
`AUC(later approvals > earlier negatives) = 0.444`. "Approved" is not an absolute quality
label — it is relative to what else was on screen. **So decisions cannot be pooled across
sittings and treated as one labelled dataset, which is exactly what this programme assumed.**

### The current best estimate, with intervals

AUC = P(a random approval outranks a random negative). 0.5 is a coin flip; below 0.5 is
ordering backwards. Chosen over the mean gap because the queue is consumed as an *ordering*,
and because a mean gap can look healthy while the distributions interleave.

| term (v5 pooled, 19 v 16) | first sitting only | after both sittings |
|---|---|---|
| `score` | 0.856 | **0.671 [0.48, 0.84]** — includes chance |
| `freshness` | 0.922 | **0.727 [0.54, 0.88]** — the only term that clears it |
| `agreement` | 0.722 | 0.627 [0.46, 0.79] |
| `reward_risk` | 0.672 | 0.618 [0.45, 0.78] |
| `approach` | 0.678 | 0.559 [0.36, 0.75] |
| `trend_alignment` | 0.367 | 0.408 [0.25, 0.58] |

**Two saturation findings survived the intervals**, because they are facts about the terms
rather than about the labels — both the same clipped shape already fixed once for
`proximity`/`depth` in `SCORE_VERSION` 3. **Both are fixed as of 2026-07-28, `SCORE_VERSION` 6**,
and neither needed a correlation to justify:

- **`agreement_signal` capped at 3 while recorded counts run to 12**, pinning it at 1.0 for 12
  of 13 daily rows. At n≥3 it carried no information at all. Fixed — see §11, which also
  records that the new shape moves the within-sitting AUC to 0.672 [0.53, 0.78], clearing
  chance where the clamped version spanned it.
- **`reward_risk` was pinned at 3.0 for 12 of 18 weekly rows** — direct evidence for §19(d):
  distance inflates R:R, so the term was meaningful where zones are near and noise where they
  are far. "R:R is broken" was too broad; it was broken *on the far population*. Fixed — see
  §19(d), which unclipped the shape *and* re-pointed the term at a distance-corrected input.

### Both fixes are built · `2026-07-28` — the residual is that no stratified sitting exists yet

The call was "not *accumulate more decisions* — *make the decisions comparable*", and both
named candidates shipped together because neither is sufficient alone.

**The queue is sampled, not topped** (`oracle/queue.py`, new module — `setups_cli` was at the
800-line limit). The top `HEAD_SIZE = 5` by score are always shown, so the queue keeps doing
its other job; the remaining 20 slots are **one draw per equal-count stratum** across
everything below. Measured on the live 67-candidate queue: a stratified sitting spans
0.377–0.902 against `--sample top`'s 0.41–0.78, and two consecutive draws differ. Equal
*count*, not equal width — a width-uniform draw over-samples sparse extremes, which are
exactly the rows that would then be re-offered every sitting. `--sample top` restores the old
behaviour; ordering within the sitting is untouched, so §19(e)'s weekly-first rule still holds.

**Every decision records what else was on screen** — `queue_mode`, `queue_band`, `queue_rank`,
`queue_size`, `queue_score_min`/`_max`, `queue_population`. Additive, so **`score_version`
stays at 5** and the v5 cohort is not re-partitioned (§21's precedent). The 77 existing rows
correctly have none of these and **must not be backfilled**, per the rule below.

`queue_mode` is the load-bearing field: it says whether a sitting may be pooled at all.
`queue_band` narrows that further — the head is still a score-ordered slice that marches down
between sittings, so only the `tail` rows are a genuine sample and a mining pass can say so.

### The blocker was also *understating* the scorer, which nobody expected

`scripts/probe_freshness_weight.py` now computes a **within-sitting AUC** — form the U
statistic inside each sitting, pool the counts, never compare across screens. That is the
standard remedy for this confound and it runs on the existing 77 rows. It costs **658 of 812
pairs** and improves every estimate:

| term | same-sitting (valid) | pooled (invalid) |
|---|---|---|
| `score` | **0.688 [0.54, 0.82]** | 0.568 [0.41, 0.71] — chance |
| `freshness` | **0.779 [0.66, 0.88]** | 0.636 [0.49, 0.78] — chance |
| `approach` | 0.625 [0.43, 0.81] — chance | 0.604 [0.43, 0.77] — chance |
| `agreement` | 0.627 [0.49, 0.75] — chance | 0.522 [0.39, 0.66] — chance |
| `reward_risk` | 0.629 [0.46, 0.79] — chance | 0.553 [0.41, 0.70] — chance |
| `trend_alignment` | 0.429 [0.29, 0.57] — chance | 0.475 [0.35, 0.60] — chance |

So `score` and `freshness` **both clear chance** once the labels are put on one scale. The
programme's headline finding — "almost nothing in the scorer is distinguishable from chance" —
was substantially an artefact of pooling incomparable screens, not a verdict on the scorer.
Conditioning on sitting partitions `score_version` for free, since a sitting is one run of one
build; the probe asserts that rather than assuming it.

**Still not a mandate to re-weight.** 154 pairs from eight sittings, every one of which was
itself a score-ordered slice. The correction makes the old data readable; it does not make it
sufficient.

### Residual: no stratified sitting has happened yet, and the queue is empty · `2026-07-28`

Everything above is machinery plus a retrospective correction. **The next `uv run setups`
sitting is the first one whose decisions need no conditioning at all** — and until a few exist,
`queue_band == "tail"` selects an empty set.

**The sitting could not be held until 2026-07-28, and now it can.** `uv run setups` printed
`Nothing to review` — of 69 candidates, **67 were already decided** (29 approved · 20 later ·
16 rejected · 12 archived across 77 zones) and the other 2 were on excluded assets. **§27's
option 1 fixed this the same day**: candidates went 74 → 97 and the queue now offers **22
rows**. What follows describes why the drought happened; it is no longer the blocker. Decisions are keyed on `Candidate.key`, which is content-addressed on
the zone, so the `SCORE_VERSION` 6 bump correctly does *not* resurface them — a zone is not
re-judged because the scorer changed, and it should not be.

**§4 was gated on queue supply; §27 supplied it.** The paths considered at the time, kept
because the reasoning about the passive two still holds:

- **`later` rows resurfacing.** 20 of them, and `resurfaces` returns them once price enters the
  zone or another person backs it. Free, but it happens on the market's schedule.
- **Corpus growth via the nightly job.** Also passive, and slow at the margin — adding four
  voices took the corpus 3,851 → 4,471 rows and the queue only 49 → 53.
- **~~§6f~~ — tried 2026-07-28, and it did not work.** `ETH/BTC` was built and prices
  correctly; all 29 of its rows are refused because the weekly trend is down and the roster is
  majority-long. `BTC.D`, the other 44 rows, turned out to need a paywalled history endpoint.
- **§27 — audited 2026-07-28, and this one is real.** The `timeframe_conflict` gate withholds
  **48 candidates** (74 → 122), and 0 of the 48 are zones price has passed — 67% approaching,
  33% inside. Unlike the two paths above it is on *your* clock: it is a decision about the
  gate, not a wait for the market. **This is the path to a non-empty queue.** See §27.

**The first post-§27 queue is homogeneous, and that bounds what it can measure.** All 22 rows
are `weekly ranging · daily downtrend` and all 22 are LONG — because the 70 already-decided
candidates *were* the pre-§27 population, so every undecided row is a newly-released one. So
`trend_alignment` is constant at 0.0 across the whole sitting and **cannot be measured by it**,
and neither can direction. What does vary and is minable: `score` (0.35–0.64), `freshness`
(0.07–0.95), `agreement` (1–8), `approach`, `reward_risk`. Read this sitting as a direct test of
whether §27's option 1 released anything worth trading, not as the balanced sample §4 wanted —
that arrives once these are decided and the nightly mixes the population again.

**Consequently §11, §18, §21 and §27's option 2 are no longer blocked on a fix or on supply —
they are blocked on a sitting, and one can now be held.** Each still defers to a sidecar
correlation; that correlation is now *possible*, needs no conditioning, and simply wants data.
§15, §19(d) and §27 were never blocked, and measure against candidate counts or price history
instead — as did the ATR stop padding that shipped as `SCORE_VERSION` 7.

### First post-§27 measurement · `2026-07-28`, 24 v6 rows over 4 sittings

The sittings happened. All four ran `queue_mode=full` — the whole qualified population fit on
screen, so nothing was sampled and there is no sampling confound to condition on at all. Better
than the stratified draw this entry was built to provide.

**Same-sitting AUC, now 175 valid pairs over 12 sittings** (was 154 over 8):

| term | previously recorded | now |
|---|---|---|
| `score` | 0.688 [0.54, 0.82] | 0.691 [0.56, 0.81] |
| `freshness` | 0.779 [0.66, 0.88] | 0.789 [0.68, 0.88] |
| **`agreement`** | 0.627 [0.49, 0.75] — chance | **0.680 [0.55, 0.78] — clears** |
| `approach` | 0.625 [0.43, 0.81] | 0.592 [0.41, 0.75] — chance |
| `reward_risk` | 0.629 [0.46, 0.79] | 0.643 [0.47, 0.80] — chance |
| `trend_alignment` | 0.429 [0.29, 0.57] | 0.446 [0.33, 0.57] — chance |

**§11's fix is validated against data that did not exist when it was made.** The entry predicted
the reshaped `agreement_signal` would move the within-sitting AUC to **0.672**; measured on the
new cohort it is **0.680**, and it now clears chance where the clamped version spanned it. That
is the first term in this programme to be changed on a structural argument and then confirmed
out of sample.

**The v6 cohort on its own cannot order approve from reject.** 9 approved v 6 rejected,
`score` AUC = **0.500** — an exact coin flip. The largest single inversion source is the
`SPX6900` daily rejection at 0.635, which beat six approvals and whose note reads *"we already
have an spx6900 entry which seems like a better option"* — a **duplicate**, not a judgement on
the setup. Excluding it moves the AUC only to 0.556. That row was produced by the ordering
defect §19(e) fixed the same day (weekly and daily of one thesis three rows apart), so it should
not recur, but it is in the record and a mining pass must not read it as trade quality.

**Still no mandate to re-weight.** The freshness sweep climbs monotonically to 0.70 with no
interior maximum — the same degenerate answer this entry already records as the tell that one
weight vector is fitting two populations.

**Two things bound what this cohort can say, both structural rather than bad luck:**

- **`daily_trend` has zero variance.** All 15 rows carrying it are `downtrend`, and all 15
  weeklies are `ranging`, because the queue was 100% the population §27's option 1 released.
  So **§27's option 2 remains unmeasurable** — the negatives it needs still do not exist.
  `trend_alignment` is constant at 0.0 across the same rows, which is why its AUC below 0.5
  carries no information here either.
- **4 of the 6 rejections used `other`.** That bucket calibrates nothing: `trade_quality` feeds
  the setups scorer and `view_wrong` feeds roster trust, and `other` is only readable if the
  free-text note happens to say something — which is exactly how the `SPX6900` confound above
  was recoverable at all. **This was investigated on 2026-07-28 and the diagnosis inverted —
  see the next subsection.** The answer was not "prefer the specific reasons"; it was that the
  specific reasons were the wrong five.

### The reason vocabulary was cutting across the categories, not along them · `FIXED 2026-07-28`

Hand-labelling all 29 vocabulary-1 rows against what their free text *literally says*, rather
than against the bucket they were filed under:

| What the note says | Rows | Filed as |
|---|---|---|
| **stale** — the call aged out | 8 | `trade_quality` ×4, `other` ×3, `archive/setup` ×1 |
| **not_my_market** / **unknown_asset** | 8 | `archive/asset` ×4, `archive/setup` ×3, `other`(reject!) ×1 |
| **far** — entry/target implausible vs price | 5 | `trade_quality` ×4, `view_wrong` ×1 |
| **dupe** — better zone already queued | 3 | `other` ×2, `view_wrong` ×1 |
| **view** — I read it differently right now | 3 | `view_wrong` ×1, `other` ×2 |
| price already past target (§19's gate, since fixed) | 1 | `trade_quality` |
| no note | 1 | `trade_quality` |

Three findings, each of which independently condemns the old vocabulary:

- **`other` was never a residue bucket.** All 9 rows have a nameable category — 3 stale,
  2 not-my-market, 2 dupe, 2 view. **Zero** are genuinely miscellaneous. Telling yourself to
  "prefer the specific reasons" cannot work when the specific reasons are the wrong five.
- **`trade_quality` was 80% not about trade quality** — 4 stale, 4 far, 1 dead gate, 1 blank of
  10. That is the bucket this section mines to calibrate the setups scorer.
- **`view_wrong` was 1-for-3** — `SPX` was far, `WLD` was a dupe.

**The reject/archive split was the mechanism.** The prompt asked scope first (reject vs archive)
and cause second, but the notes decide cause first and the cause implies the scope every time —
so the first question was asked before it had an answer, and it mis-routed in *both* directions.
All 3 `archive`+`setup` rows read as asset-level (`DOW` "not familiar with this asset", `CLSK`
"sideways **forever**", `INTL` "not really interested in this asset") and are therefore inert,
because `drop_decided` keys on the zone — they will resurface. Meanwhile `PNUT` "Zero interest in
PNUT" went in as `reject`+`other` and had to be hand-added to `cfg/exclusions.yaml` afterwards.
Behaviourally the two verdicts were already identical: both in `_PERMANENT`, both keyed on the
zone, differing only in whether the exclusions file was written.

**Vocabulary 2 is flat and derives scope from the reason** — `s`/`f`/`d`/`b`/`v` bury the zone,
`n`/`?` gate the asset and write `cfg/exclusions.yaml`. One keystroke, then an optional note.
Rows carry `reason_vocab: 2`; every vocabulary-2 string is distinct from every vocabulary-1
string, so a mining pass can partition on `reason` alone if it prefers. `?` also prints a
canon-review warning: "I don't recognise this ticker" is a *data* complaint, and `UROY` ("I see
URC?") and `DASH` ("able to know Doordash from DASH the crypto") are two live routing bugs that
were filed as decisions nobody would ever grep.

**The 29 rows keep their vocabulary-1 values** — the sidecar is append-only and §4(a) forbids
rewriting. Mine them through the table above, which is why it is recorded here rather than
applied to the file. Row identity by asset plus note snippet:

- **stale** — `MON` "levels a bit all over the place" · `PUMP` "Very stale" · `RIVN` "Very stale
  at this point" · `AVAX` "524 days old" · `RIVN` "tried this trade back when" · `SHIB` "Just
  old, not interested" · `DASH` "This one in particular is stale" · `INTL` "Stale, not really
  interested"
- **far** — `PLUME` "That exit is pretty high" · `XMR` "$799 at 130% is pretty optimistic" ·
  `SPX` "entry is absurdly low" · `SOL` "entry is absurdly high" · `TAO` "Target is pretty high"
- **not_my_market** — `PNUT` "Zero interest" · `ADBE` "not my edge" · `MELANIA` "do not want to
  trade this asset" · `CLSK` "sideways forever"
- **unknown_asset** — `DOW` "not familiar" · `TRAC` "probably a dead asset" · `UROY` "I see
  URC?" · `STABLE` "Never heard of this one"
- **dupe** — `SPX6900` "already have an spx6900 entry" · `WLD` "not as good as weekly" · `DASH`
  "Weekly already presented"
- **view** — `CL` "Iran conflict" · `OIL` "Same as CL" · `MU` "Memory is hot right now"
- **gate defect, not a judgement** — `OIL` "Price now is greater than target"
- **no note** — `ZEC`

**One check this unblocks, and it should be run on the next mining pass.** `freshness` is the
strongest term in the table above at AUC **0.789** — and **8 of 29 rejections say, in words,
"this is old"**. The AUC is therefore partly measuring the scorer agreeing with Tegan about age
rather than fresh setups performing better. Re-run it excluding `reason == "stale"` rows and see
whether it survives. Note this does not make the term useless even if it collapses: ranking by
freshness still surfaces what he will *look* at. It would only mean the term has no demonstrated
relationship to outcome, which is a different claim from the one this section currently implies.

**45% of negative decisions are queue defects, not judgements** — 8 stale + 5 far of 29. As
their own keys they become a per-sitting count that drives §6 and §18/§19 directly, instead of
being buried inside `trade_quality`.

**`b` (bad setup) has almost no support yet** — 1 row of 29, and that one has no note. Expected:
the queue has been serving so much stale and too-far material that genuine setup critique has
not had a fair shot. It is the bucket this section actually wants to grow. **If `b` is still
near zero after a few sittings, that is itself the finding** and should be written here.

### Do not re-derive these

- **Do not backfill missing fields on old rows.** A re-run yields today's values, not
  decision-time ones — ZEC's score moved 0.665 → 0.790 inside a single session. If ever
  backfilled, mark it `recomputed_at` and never as captured live.
- **Do not raise the `freshness` weight globally.** It is the cleanest separator on the daily
  population and near-chance on the weekly one, and §19(e)'s unconditional weekly-first sort
  puts weekly at the *top* of the queue, so the damage lands where it is most visible. The
  pooled sweep climbs monotonically and never turns over — **a sweep with no interior maximum
  is the tell that one weight vector is being fitted to two populations**, not a result.
- **Do not read the weekly-versus-daily split as established.** The point estimates differ in
  the direction described (weekly `approach` 0.738 / `freshness` 0.583; daily 0.333 / 0.861)
  but do not survive their own intervals at 11v17 and 18v11. Unproven, not disproven.
- **"One mixed session" could not break the timeframe confound, and now the queue does it for
  you.** There is no timeframe filter — `setups` always returns both — and the population
  flipped entirely between sessions: after v5 decided all 7 weekly rows, `--limit 0` returned
  23 candidates, every one daily. The confound was structural, because the ranker decided
  which population got judged. §19(e) records the exact mechanism found on 2026-07-28: with 28
  weekly rows sorted ahead of 39 daily ones, `--limit 25` was **25 weekly and zero daily**.
  The stratified draw spans both by construction, so this is now fixed rather than merely
  understood — but it is fixed only for sittings held *after* that date.
- **Do not mine the 8 reason-less `archived` rows as clean negatives.** Confirmed with Tegan
  2026-07-27: `x` was used for both "I don't trade this asset" and "stale, bury it". The
  meaning was never recorded and cannot be recovered — they carry no `reason` and no
  `reason_vocab`, which is how you identify them. The 4 archives that *do* carry a
  vocabulary-1 reason are labelled in the table above and are minable. Vocabulary 2 removed
  the key that produced the unlabelled ones.
- **Do not derive `cfg/exclusions.yaml` entries from decision prose automatically.** That
  file's header explains why: a temporary reservation promoted into a permanent rule silently
  deletes a market from the queue for good.

---

## 4b. The decision sidecars are irreplaceable and unbacked · `PARTIAL 2026-07-27` — setups mirrored, triage still not

`data/setups/decisions*.jsonl` and `data/triage/decisions.jsonl` sit under `data/`, which
`.gitignore:2` excludes and `docs/ARCHITECTURE.md` describes as machine-generated ore —
"regenerable", "never committed". That description is accurate for transcripts, theses,
stances, and prices. It is **false for these two files**: they are hand-entered judgment, they
are what §4 calls the only ground truth available, and nothing can reconstruct them.

**Evidence:** the only backup that exists anywhere is `data/triage/decisions.jsonl.pre-contentid.bak`,
created by hand during a migration and now stale. `data/setups/` has none.

Approvals do reach durable storage — `render_note` appends them to `~/vault/Trading/Trade
Logs/Setups.md`. **Rejections do not**, and rejections are the verdict §4 is actually waiting
on. So the failure mode is precise: lose `data/` and you keep the trades you liked and lose
every reason you passed on something.

Per the vault/repo boundary in `architecture.md` — "human-curated judgment lives in the vault"
— these belong on the vault side, or at minimum need a mirrored write. Decide before decision
volume accumulates, not after.

### `data/setups/decisions.jsonl` is mirrored · `FIXED 2026-07-27`

`oracle/decisions.py`. Every write goes to the sidecar first and then to
`~/vault/Trading/Trade Logs/decisions.jsonl`, beside the approvals note. Reconciled once at
startup, before the queue is built, so a restore lands before `load_decisions` reads the file.

**The mirror is subordinate, never a gate.** A vault that is unmounted or read-only warns and is
skipped — losing a backup is recoverable, losing a session's judgement is not. It warns rather
than passing silently, because a mirror everyone believes is running and isn't is worse than no
mirror: it is only ever consulted once the primary is already gone.

Both files are append-only, so one is normally a prefix of the other, which gives three cases
real meaning: mirror behind → copy forward; **mirror ahead → restore the primary from it**;
neither a prefix → touch nothing and warn, because splicing two histories invents a sequence
that never happened. Verified on real data — 14 rows seeded byte-identical, then the primary was
deleted and restored byte-identical from the mirror, with the run reporting
`restored 14 decision(s)` and correctly hiding all 14 as already decided.

Disable with `--no-mirror`; relocate with `--decisions-mirror`.

### `data/triage/decisions.jsonl` is still unmirrored · `OPEN`

20 rows, equally irreplaceable, and **blocked on a real layering question rather than on effort**.
`distill/triage_cli.py:166` has its own `record_decision`, and the obvious fix — import
`oracle.decisions` — is a **backwards dependency**: pipeline order is ingestion → distill → brain
→ oracle, and distill currently knows nothing about oracle. The other placement, `core/`, is
explicitly barred from I/O by CLAUDE.md ("pure logic and shared schema. Zero I/O").

So the choice is: (i) accept a small duplicate mirror in `distill`, (ii) add a seventh workspace
member for shared file plumbing, or (iii) let `distill` depend on `oracle`. **Not decided — pick
before the triage sidecar grows.** Note the loss here is milder than the setups one: triage
records `approve`/`skip` only, with no reason and no note, so it is thinner ground truth.

---

## 6. No freshness loop · `OPEN` — narrowed 2026-07-26

The machinery is batch-historical; the use case is real-time. Nothing runs on a schedule —
every Brain answer and every setups run is only as current as the last hand-run sweep.

**Original evidence, now spent:** `stale` was **2,913 of 3,459** rejections (84%). That half of
the entry was a *gate* problem, not a scheduling one, and is fixed — see below. The scheduling
gap is untouched: the question worth answering is "price is approaching this level *now*", and
that still needs a scheduled ingest → distill → setups pipeline. **Next: nightly via launchd**
(not cron, not the Claude scheduler — the pipeline must not need a session open, and the plist
needs `WEBSHARE_PROXY_*` or transcript fetches hit YouTube's IP block). Design for silent
failure: a dead nightly job and a quiet market look identical unless the note carries a last-
successful-run line.

### The staleness cliff is gone · `FIXED 2026-07-26`

`StaleAfter` → `HalfLife`: the per-timeframe windows became the shape parameter of
`freshness_signal = 1/(1 + age/half_life)` — 1.0 the day it was said, exactly 0.50 at the
window, never zero — and `freshness` is now a 0.15-weighted scoring term instead of a gate.

**Why it had to go beyond the raw count:** the constant was unfalsifiable. The gate that would
have produced evidence about where the line belongs was the gate under test, so nothing could
ever tell us 21 days was wrong for a swing call. Rejecting a loosened candidate as
`view_wrong` is now the evidence that tunes it, which is also why this unblocks §4.

**Also split out of the same pass:** a **ranging** weekly is no longer `weekly_disagrees`. It
is the absence of a macro opinion, not one against — 630 rows versus 1,617 genuine
contradictions, a fifth of everything reaching that gate discarded for the wrong reason. It
now scores `trend_alignment = 0.0` (weight 0.05) instead of dying.

**Measured effect:** 8 candidates → **49**. The rejection tally stopped being one constant
drowning everything: `weekly_disagrees` 1,617 · `timeframe_conflict` 798 ·
`wrong_side_of_range` 342 · `unknown_direction` 229 · `no_dealing_range` 74.

**The line that was drawn, worth not re-litigating:** gate a rule you wrote or a fact that is
missing; score a measurement on a continuum. `min_reward_risk` is the counter-example — it was
scored, measured, and *hardened*, because a 0.32-RR candidate surfacing mid-list feeds "I take
way too many trades". Softening is right for measurements and wrong for rules.

**Watch:** `--limit` now defaults to 25 with the held-back count printed. That cap is the only
thing bounding the queue, so it is doing the job the cliff used to do — badly is better than
invisibly, but it is a TUNE.

---

## 6f. The unpriced count was one number covering four different problems · `PARTLY DONE 2026-07-28`

Adding four voices took the corpus from 3,851 to 4,471 rows but candidates only from 49 to 53,
because unpriced assets went **128 → 201**. The original entry read that as a backlog of missed
opportunities. Re-measured properly on 2026-07-28 — 4,579 rows over 496 assets — it is four
unrelated problems that were being added together:

| group | assets | rows | examples | verdict |
|---|---|---|---|---|
| **not an instrument** | 27 | 172 | `__basket__` 53, `ALTS` 30, `CRYPTO` 19, `SPACEX` 25, `__macro__` 4 | correctly refused; **nothing to fix, ever** |
| **no route** | 116 | 130 | `conflict` 75 assets, `unmapped` 41 | the genuine gap, and a one-row-per-label long tail |
| **computable** | 15 | 136 | `BTC.D` 44, `CPI` 11, `ETH/BTC` 29 | real instruments, simply not built |
| **routed, never fetched** | 25 | 27 | `ZT`, `AUD`, `BAE`, `BCOM`, `BRK.B` | a `fetch-prices` gap, not a routing one |

**The headline is now grouped** (`setups_cli.format_unpriced`, `route.NOT_AN_ASSET` and
friends). `__basket__` alone was a fifth of the old number while being, by construction, the
extractor's placeholder for a thesis that is not about one thing — the entry's original ask.
Two reasons, `event` and `derived_ratio`, existed **only** as strings in `cfg/oracle_map.yaml`
and were never named in code, which is precisely how they stayed invisible; they are constants
now, and a reason no group claims prints as `ungrouped` rather than vanishing.

### `ETH/BTC` ships · `2026-07-28`

`route.DerivedRef` + `oracle/derived.py`. 29 rows, both legs already cached, and it is a
division — 727 bars from 2024-07-31, ratio 0.0299, which is ETH ~$3.6k over BTC ~$120k.

**The bar construction was the hard part, not the arithmetic.** `open` and `close` are exact
because both legs are quoted at the bar boundary; **the ratio's high and low are not computable
from daily OHLC**, because ETH's high and BTC's high are different instants. So the bars are
body-only. The tempting `n.high / d.low` is a true bound and a badly loose one for correlated
legs — measured, it yields **8 zones against the body-only series' 27**, so it materially
fabricates structure. Collapsing to closes is the opposite failure: zero-height blocks, every
candidate refused as `degenerate_zone`. The cost of body-only is stated rather than hidden —
wick information is gone, so intraday sweeps are invisible on this series.

**It produced zero candidates, and that is the correct answer today.** ETH/BTC's weekly trend is
a downtrend — 0.050 → 0.030 over two years — while the roster is 17 long / 12 short on it. So
all 17 longs are refused `weekly_disagrees` and all 12 shorts `timeframe_conflict`. The verdict
is robust: both bar constructions agree the weekly is down. **The gain is that 29 rows now
reach the gates and get counted instead of being invisible**, and they become eligible the
moment that trend turns. Do not read "0 candidates" as the work not landing.

### `BTC.D` is not what this entry claimed, and is not cheap · `OPEN`

The original text — "derivable from CoinGecko global data we already fetch" — is wrong twice,
checked 2026-07-28:

- **We do not fetch it.** `coingecko` appears in exactly one file, `distill/fetch_tickers.py`,
  and only for the ticker registry. There is no global-data fetch anywhere in the repo.
- **The history is paywalled.** Dominance needs BTC market cap over *total* market cap, as a
  series, because `setups` reads swings and order blocks off daily bars. `/global` is free but
  **snapshot only**. `/global/market_cap_chart` — the history endpoint — returns
  `error_code: 10005, "This request is limited to PRO API subscribers"`.
  `/coins/bitcoin/market_chart` is free, so the *numerator* is available and the denominator is
  not.

Remaining options, none of them a division: pay for CoinGecko Pro (every cost in this repo
except `ingest-x` bills against the Max subscription, so this is a real change of kind);
approximate the total by summing the top-N coins' `market_chart`, which is N rate-limited calls
and drifts as coins enter and leave the top N; or **snapshot `/global` nightly and accumulate
history forward**, which is free and has direct precedent in the funding logger (§22, §24 —
Lighter is snapshot-only and its column is thin until nights accumulate). The last one costs
nothing but yields no usable weekly structure for many months.

**`CPI`/`FEDFUNDS` (`rate`, 7 assets / 33 rows) is still open and is genuinely free via FRED** —
but it is context, not setups; a rate has no order block worth trading.

**Note this is a *supply* lever, not a data-quality one:** the macro/FX/aggregate vocabulary is
disproportionately what the two new macro voices (Capital Flows, Real Vision) talk about, so
the value of adding them is partly gated behind it.

---

## 6h. The roster's channel metadata is unverified and drifts · `WATCHING` — tool shipped 2026-07-26

Three roster facts were checked against reality on 2026-07-26. **Two were wrong**, and both had
been recorded by Phase 0 as verified:

| entry | recorded | actual |
|---|---|---|
| `@RealVision` | `access: ok`, verified | **does not exist** — no videos or streams tab |
| `@TraderSZ` | `status: dormant`, "dormant since ~2022" | **actively livestreaming** — /streams runs to 2026-07-28 |
| `TraderSZ (Z$1)` | "DEDUPED … same person" | **two different people** merged into one entry |

Each failed differently, and each failure was silent:

- **The RealVision typo** produced `0 ingested, 0 skipped, 0 stale, 0 failed` — see §6d.
- **The TraderSZ dormancy** was *right about the uploads tab* and wrong about the channel. His
  newest upload really is 2023-10-01; he moved to livestreams. `channel.resolve_recent` already
  reads both tabs, so only the marker was suppressing him — a voice sat unreachable for a
  research error nobody would ever re-check.
- **The bad merge** attached an X handle belonging to a *different trader* to his YouTube
  channel. Left alone it would have credited a stranger's posts to him in every agreement
  count, which is the exact failure mode the `aliases:` field exists to prevent — inverted.

**Why this is a class, not three incidents:** every `access:` and `status:` value in
`watchlist.yaml` is a hand-recorded research finding with no expiry and no verification. A
channel that renames, dies, or changes format does not announce it, and nothing in the sweep
distinguishes "correctly skipped" from "wrongly skipped".

### `verify-roster` shipped 2026-07-26 · this entry is now `WATCHING`

`uv run verify-roster` probes every declared YouTube channel across both tabs and diffs reality
against the recorded `access`. Free — no key, no proxy — and exits non-zero on any disagreement
so a scheduled run surfaces rather than scrolls past. Also does three structural checks that
need no network: a channel claimed by two people, an alias naming a *different* person, and a
non-dormant person with no feed at all.

**It found a third bad marker on its first run.** `@CryptoCon_` was recorded `access: ok` and
resolves to neither tab; `@CryptoConComic`, `@CryptoCon` and `@cryptocon_official` all 404 too,
so both the recorded handle and the channel id are wrong. Set to `unknown` — not `dormant`,
which would claim he stopped posting, when what we actually know is that we can't find him.

**It also shipped with a false positive, which is worth remembering.** The first version called
any written-off channel with videos `REVIVED`, and flagged Mark Newton's UC… channel — whose
newest item is *"Cnbc interview 3/28/17"*. An archive is not a feed. `REVIVED` now requires a
video inside `REVIVAL_WINDOW_DAYS` (180), with the date fetched via `hydrate` **only** for
channels where it could change the verdict, and a distinct `UNDATED` verdict when the date
can't be had — refusing to decide rather than guessing from a title.

Roster now verifies clean: **16 OK · 1 dormant (confirmed) · 0 problems.**

**Still uncovered:** X handles can't be probed for free (an xAI call costs money, and a check
that costs money doesn't get run), so the digest is verified only structurally. Podcast and
telegram feeds likewise.

---

## 6d. An unreachable channel reports as an up-to-date one · `OPEN` — new 2026-07-26

`@RealVision` was recorded in `cfg/watchlist.yaml` as a verified `access: ok` channel and does
not exist — yt-dlp reports *no videos tab and no streams tab*. The roster sweep summarised it
as:

    Raoul Pal (Real Vision) (@RealVision): 0 ingested, 0 skipped, 0 stale, 0 failed

**All four counters zero is indistinguishable from "this channel is already current"**, which
is what a healthy channel with no new uploads prints. `resolve_recent` does emit the real
error to stderr (`channel.py:73`), but it is buried among per-video warnings in a sweep that
printed hundreds of lines, and the summary line — the part anyone actually reads — reported
success. The correct handle is `@RealVisionFinance`; found only by testing handles by hand
after noticing the zero.

**Fix:** a target that resolved to *zero videos* is a distinct outcome from one where every
video was skipped, and `active_targets` knows the difference. It should be counted and
reported like `SkippedPerson` already is — an unreachable roster member is exactly the kind of
silent gap that class exists to surface.

**Why it matters beyond one typo:** this is a supply bug that hides itself. A channel that
renames its handle goes quiet permanently and the sweep keeps reporting fine, so the corpus
silently loses a voice and nothing says so. Every `access: ok` entry not yet ingested is
unverified in the same way.

---

## 6e. `backfill.max_videos` is a per-tab cap, not per-channel · `WATCHING` — new 2026-07-26

`resolve_recent` iterates `_TABS` (videos, streams) and applies the cap to **each**, as its
docstring states. So `max_videos: 20` yielded **40 ingested** for Traders Reality and 38 for
Real Vision. Not a bug — documented and deduped — but the knob reads like a per-channel budget
and isn't one, which matters when it is being used to bound a trial cohort's cost. Either
rename it or apply the cap after the merge.

---

## 6b. `brain/report.py` keeps its own staleness cliff · `OPEN` — new 2026-07-26

`brain/report.py:22,32` has `_DEFAULT_STALE_DAYS = 120` and its own `STALE_AFTER_DAYS` map.
That was a duplicate of `core.setups.StaleAfter` and is now a *divergence*: setups treats age
as a half-life, brain still treats it as a cliff. The same thesis can be current in one head
and dead in the other, with nothing reporting the disagreement.

Not urgent — the two heads answer different questions and a cliff may genuinely suit a
narrative report. But the constant should live in `core` once, with each head choosing how to
apply it, rather than being independently guessed in two places.

---

## 6c. 245 theses have no tradeable direction · `WATCHING` — new 2026-07-26

`direction` is `long` 2,524 · `short` 1,082 · **`neutral` 245**. Neutral theses can never
become setups, and now surface as `unknown_direction=229` in the rejection tally, which reads
as a failure rather than as a category. This is correct behaviour — they are Brain-head
material by design — but the tally should probably separate "couldn't" from "wasn't trying".
Revisit if the tally starts getting read for signal.

---

## 8. Evidence-leg retrieval doesn't discriminate · `NARROWED 2026-07-26` — it's the query shape, not the index

**The original claim was too broad, and the statistic behind it was the wrong one.**

Original: scores compress into **0.72–0.81**, cosine barely separates anything, queries return
Discord-giveaway chatter above real analysis.

**Absolute cosine is not comparable across queries** — it depends on how the *query* embeds.
The real measure of discrimination is separation from the corpus baseline for the *same* query.
Measured that way (`scripts/probe_retrieval.py`, 12 concept queries, 18,108 chunks):

| query | top1 | corpus p99 | corpus p50 | quality of top 5 |
|---|---|---|---|---|
| judas swing | 0.645 | 0.486 | 0.415 | 5/5 instructional |
| displacement, defined | 0.701 | 0.597 | 0.518 | 5/5 |
| FVG middle candle / displacement | 0.824 | 0.730 | 0.605 | 5/5 |
| dealing range boundaries | 0.800 | 0.698 | 0.560 | 5/5 |
| liquidity sweep / stop hunt | 0.817 | 0.669 | 0.588 | 5/5 |
| **15m entry trigger, failed breakdown** | **0.634** | 0.562 | 0.462 | **3/5 — Pierre chatter** |

**Top-1 landed above the corpus 99th percentile on all 12.** Mean separation (top1 − p50) =
**0.208**. Zero chatter hits across 60 passages. Note "displacement" at a weak-looking 0.701 with
five on-topic hits including a flat definition — low absolute score, good retrieval.

**So the split is by query shape:**

- **Concept / methodology queries discriminate well.** The query carries real semantic content
  for cosine to grip. This is now the basis of the `brain_search` MCP tool.
- **Asset-faceted queries ("where is my roster on ETH") still fail as originally described** —
  reproduced live 2026-07-26: passages [5] and [7] were Pierre advertising his paid Discord and
  taking chart requests. An asset name is a *facet*, not a concept; there is nothing semantic to
  match, so cosine ranks noise. **This half of §8 stands and is still `OPEN`.**

**Implication for the fix:** a lexical/BM25 leg would help the asset case and is largely wasted
on the concept case. Don't rebuild retrieval wholesale — the facet path is what needs work, and
facets are better served by filtering (the `assets` column) than by ranking.

**Dead end, do not re-test:** `query_embed` is identical to `embed` for this model, so the bge
query-prefix theory is disproven.

---

## 9. Audit extraction efficiency before considering the API · `OPEN`

**First, a correction to how this was originally framed.** "The direct API is 8–15× cheaper
(~$26 vs ~$183 per pass)" is true *per token* and misleading as a decision rule. `claude -p`
runs on the existing **$100/month Max subscription — marginal cost zero**. API tokens are
**incremental cash on top of that**. So switching billing paths only wins where volume genuinely
exceeds what the subscription carries. A full 666-transcript pass might; a freshness loop of
5–20 new videos a day almost certainly does not.

**What matters on *both* paths is waste**, because burning allowance still risks cap hits — and
a usage cap silently killing an entire sweep has already happened once (`8365729`).

**The audit, before any billing change or bulk pass:**
- **Where does the ~90% harness overhead actually go?** It's the headline number and nobody has
  broken it down. Some may be avoidable inside `claude -p`.
- **We send whole transcripts, and the corpus is 5.26% signal.** Extractive pre-filtering before
  the LLM call would cut input dramatically on *either* path, and it's the single biggest lever.
  Must stay extractive — abstractive summarizing would destroy `asset_heard`, `watching` and
  citation integrity.
- **Is the system prompt cached across calls?** It's identical every time.
- **Do retries and re-distills resend transcripts unnecessarily?**
- **Measure the real daily volume of a freshness loop (§6)** before pricing anything.

**Decision rule:** stay on the subscription unless the measured volume can't fit in it. Optimize
the waste regardless — it pays off either way.

---

## 10. `domain` is per-thesis and inconsistent per asset · `WATCHING`

`SPX` appears in the corpus as `crypto`, `macro`, *and* `stock` across different theses, so
`tier_for` can label the same asset differently depending on which thesis surfaces.

Contained for now: `tier_for` gates on domain specifically to stop the SPX/memecoin rank
collision leaking in. A per-asset domain consensus in `core/canon.py` would be steadier.

---

## 11. Agreement is date-blind · `PARTLY DONE 2026-07-28` — cap fixed, recency half still `OPEN`

`agreement_signal` counts distinct people and saturates at 3, so seven voices score 1.0 whether
they all spoke this week or one spoke six months ago.

**Evidence (live, 2026-07-25):** the ETH long candidate's seven supporters span **2026-01-20 to
2026-07-22** — Magic Lines' view is **186 days old** and counted equally with TraderMayne's from
three days prior. ZEC's second supporter is two months stale.

Not a staleness-gate failure: the old view is a `macro` thesis and legitimately survives a
360-day horizon. The question is whether a macro bull from January is the same evidence as a
swing bull from last week. Arguably cross-horizon agreement *is* meaningful confluence — but
right now it's indistinguishable, which is the actual problem.

Candidate fix: weight each voice by recency (`core.rank.recency_signal` already exists) rather
than counting heads. Interacts with §2 — if horizons go event-based, "current view" becomes
better defined and this may partly solve itself.

Dates are now shown per supporter in the queue and the vault note, so this is at least visible
rather than hidden.

### The cap is fixed · `2026-07-28`, `SCORE_VERSION` 6

§4 found `agreement_signal` pinned at 1.0 for 12 of 13 daily rows because it clamped at 3 while
recorded counts ran to 12 — at n≥3 it carried no information at all, and recency-weighting a
term that cannot vary would have changed nothing. That needed no measurement, being a fact
about the term rather than about the labels.

`min(count / cap, 1.0)` → **`count / (count + cap)`**, the hyperbola `freshness_signal` and
`approach_to` already use, so "more is better with diminishing returns" now has one idiom in
this codebase instead of three. `cap` becomes the half-way point rather than a ceiling: 3
voices score 0.50, 12 score 0.80, and every count is distinguishable from every other.

**It measurably orders the existing decisions better.** Replaying the new shape against the old
raw counts — legitimate, because `agreement` was always recorded as a head count, not as a
transformed value — the within-sitting AUC moves **0.627 [0.49, 0.75] → 0.672 [0.53, 0.78]**.
It now clears chance, where before it spanned it.

Note this also fixes `core.rank.score`, the intrinsic thesis ranker, which shares the function
and had the same clamp. That ranker carries no version stamp, so nothing partitions there.

### The recency half is still open

Weighting each voice by recency (`core.rank.recency_signal` already exists) rather than counting
heads. Needs a sidecar correlation, which §4's sampling fix made valid to run but which has no
stratified rows yet — one triage sitting supplies them.

---

## 12. Slice 2 needs the oracle at sub-daily granularity · `OPEN`

Layers 2–3 of `Trading/_Structure.md` (1H approach, 15m trigger) require a `date` → `datetime`
refactor through `Bar`, `PriceSeries`, `cache` (granularity in the key), all three sources, and
`core/grade.py`. **Done halfway it corrupts silently** — `PriceSeries.__post_init__` dedupes on
`bar.date`, so 24 hourly bars for one day collapse to 1 with no error.

Note the granularity needed is **900s (15m)**, not just 1H/4H. Coinbase supports it; its cap is
`MAX_CANDLES = 300` (the Phase 4 plan's "720" is wrong).

---

## 13. The Claude Code sandbox strips the Webshare proxy · `RESOLVED` (workaround) — keep this written down

**Kept because the fix is not update-safe and the failure is silent.** The sandbox bypasses
`session.proxies`, so every transcript fetch egresses from the *local* IP — the one YouTube
IP-blocks — and the error that surfaces is never the real one. It froze the corpus for two days
and cost hours to find. If transcripts start failing again, run the probe below **first**.

### The probe — proxied must differ from direct

```bash
uv run python -c "
from ingestion.env import load_env; load_env()
from ingestion.youtube import _proxy_config
import requests
px=_proxy_config().to_requests_dict()
def ip(p):
    s=requests.Session()
    if p: s.proxies.update(px)
    return s.get('https://api.ipify.org', timeout=(10,20)).text.strip()
print('direct', ip(False)); print('proxied', ip(True))"
```

Equal → the proxy is not applied. Two different residential IPs → applied *and* rotating.

### The fix — two parts, either alone is inert

1. `sandbox.excludedCommands: ["uv *"]` in `.claude/settings.json`. Entries are **command
   globs, not binary names**, and commands are invoked as `uv run …`, so a bare `"uv"` never
   matches.
2. A patch to the **global direnv `PreToolUse` hook**, which otherwise prefixes every command
   with `eval "$(direnv export bash …)" &&` — making `eval` the first token the sandbox matches
   on, so exclusion is inert for *every* command. It now emits `{}` when the command matches
   `^\s*uv\s`. Harmless here: this repo has no `.envrc`.

Verified 2026-07-25 in a fresh session with the sandbox ON: `direct 97.88.98.212` vs
`proxied 190.233.209.115`. **Consequence accepted deliberately: every `uv run …` in this repo
now runs unsandboxed.**

**When diagnosing this class, always run a control against an exclusion entry you did not add**
— `mkdir` against the global `"mkdir"` entry was still denied, which is what separated "my
config is wrong" from "the mechanism is broken".

### Second sandbox gap: the vault is a symlink · `FIXED, VERIFIED 2026-07-25`

Writing to `~/vault/Trading/…` failed with `Operation not permitted` despite `~/vault/Trading`
being in `allowWrite`. `~/vault` is a **symlink** to `/Users/tseitz/Obsidian/Main Vault`, and
macOS seatbelt matches the **resolved** path. `.claude/settings.local.json` now lists both.
**Any future vault path must be added in resolved form.**

### Disproven — do not re-test

- **`allowedDomains`.** Structurally cannot work: the sandbox's local proxy *terminates and
  re-originates* the CONNECT, so allowlisting `p.webshare.io` grants permission to fetch it,
  never to tunnel through it.
- **`&variant=gemini` on new uploads** — present on one failing video, absent on others failing
  identically.
- **Video-specific / newest-only** — videos already in the corpus failed identically. Always
  test a known-good control before believing "the new items are special".
- **Library out of date** — `youtube-transcript-api` 1.2.4 is current.
- **Webshare plan / bandwidth** — rotation demonstrably works outside the sandbox.
- **Proxy can't handle chunked bodies** — 837KB chunked+gzip succeeded 4/4 with keep-alive.

**Do NOT "fix" this by setting `prevent_keeping_connections_alive = False`.** It unmasks the
real error but the library sets it deliberately — without it the IP is not rotated, so it
trades a masked failure for broken rotation once egress is correct.

**Worth building:** a preflight that probes the exit IP across 2–3 fresh sessions and aborts
loudly when they are identical. The `TranscriptBlocked` abort path already exists and is the
right destination — it never fires because the block never arrives as `RequestBlocked`. That
turns this whole investigation into a 5-second error.

### Genuinely dead, independent of all the above

Captions disabled: `MvD7fQQ0szE` `Nlw-PZhoViQ` `S_obDkmaf8I` `duXvzmQVZ1Q` `ufwa9Ld47Jo`.
Deleted: `_IRMBuen60Y`, `VXL1FPbgW7E`. Correctly skipped, permanent.

---

## 14. X/Twitter ingestion is decided but entirely unbuilt · `DECIDED` (zero code)

`docs/phase-0-findings.md` chose **Grok `x_search`** over the official X API (~$1–5/mo vs
$200/mo). `cfg/watchlist.yaml` already encodes the intent: **17 channels marked `access: grok`**
and an `x_grok_digest:` list of 6 handles at line 283.

**Nothing reads that key.** `grep -rniE "grok|x_search|xai" packages/` returns zero hits;
`packages/ingestion/src/ingestion/` contains only `youtube.py`.

**What it costs today:** 6 roster voices are X-only and therefore invisible — `QuantMeta`,
`0xfhd_`, `thiccyth0t`, `GiganticRebirth`, `LomahCrypto`, plus `JustDeauIt`'s X feed (his
YouTube *is* ingested). Tom Lee is recorded at `watchlist.yaml:122` as X-only-plus-guest-spots,
so he's uncovered too.

**Design note before building:** posts are ~2 orders of magnitude shorter than transcripts, so
the per-item LLM economics are inverted — batching many posts into one `distill` call is the
obvious shape, and the current one-call-per-document `distill_all` loop does not fit it.
Interacts with §9 (the extractive pre-filter) — X needs no pre-filter at all.

---

## 19. Nothing measures how far price must travel to reach the entry · `PARTLY DONE 2026-07-27`

Found by triaging the live queue: SPX long with entry 5459.46 against price 7403.70 (a 26%
drawdown away), SOL short with entry 177.24 against price 75.41 — **price would have to 2.35x
to reach the entry**. Both were in the visible top 2. Verbatim: "entry is absurdly high here
relative to what will most likely happen."

These are *legitimate zones*. The SPX block is real structure and price genuinely could return
to it. What is broken is that nothing in the engine measures the journey, so the queue cannot
distinguish "needs a 26% drawdown first" from "needs 0.5%". Five mechanisms compound:

**(a) Zone selection has no reachability criterion.** `_newest_zone` takes the most recently
*confirmed live* zone in the timeframe; distance is never consulted. SOL had three live bearish
weekly zones — 169.22–295.00, **121.69–136.18**, and 177.24–202.19. Newest won. The 121.69 zone
was 61% away instead of 135%, and equally live.

**(b) Survivorship selects *for* distance, structurally.** Invalidation is the origin swing, so
a zone near price has a nearby invalidation and dies on any ordinary correction, while a zone
price has run far from has a distant one and survives indefinitely. Every SPX weekly bullish
block ever formed:

| OB candle | zone | invalidation | fate |
|---|---|---|---|
| 2024-09-06 | 5402.62–5623.89 | 5402.62 | died 2025-04-04 |
| 2024-11-01 | 5702.86–5850.94 | 5402.62 | died 2025-04-04 |
| 2025-01-10 | 5807.78–6021.04 | 5696.51 | died 2025-03-14 |
| **2025-04-17** | **5220.79–5459.46** | **4835.04** | **LIVE — 26% away** |
| 2025-12-19 | 6720.43–6861.59 | 6521.92 | died 2026-03-20 |

The December zone was **7% from price** and died on a normal pullback. The April 2025 zone
survives *because* price is 26% above it. Across the 16 unreachable candidates, **8 had at
least one nearer zone that died first** (SPX had four, at 7%, 19%, 21% and 24%).

**(c) `approach` floored at zero — `FIXED 2026-07-27`, `SCORE_VERSION` 5.** See `approach_to`.
16 of 69 candidates sat at exactly 0.00, making 26%, 135% and 10.01% indistinguishable. Now
`ARRIVAL / (1 + gap/span)`, mirroring `freshness_signal`. Those 16 now take 15 distinct values
spanning 0.043–0.312. **This bought resolution, not demotion** — measured, they moved *up* a
mean of 1.2 ranks, because a floored term cannot be pushed lower and restoring a tail can only
add score. Fixing (d) is what demotes them.

**(d) `reward_risk` *rewarded* the same distance · `FIXED 2026-07-28`, `SCORE_VERSION` 6.**
`R:R = |target − entry| / zone height`. With a structural target — the post-break extreme — the
numerator is literally *how far price ran away from the zone*, while the denominator is a fixed
candle height. Distance inflated it. And it saturated at `RR_SATURATION = 3.0`, so SPX's 9.06,
CLSK's 10.17, CL's 14.54 and TSLA's **23.24** all collected the full 0.20, indistinguishable
from a genuine 3.0. SPX's decomposition: approach 0.000/0.40, and **0.593 of the 0.60**
available from the other four terms — near-perfect, with a chunk of it earned by being
unreachable.

Both halves are fixed, and they were two defects rather than one:

- **The inflation** (§19d's own argument). `_score` now consumes a new
  `reward_risk_from_price = |target − price| / risk` — what is left to be made from where the
  market actually is. Live effect, and it lands exactly on the two candidates this entry opened
  with: **SPX weekly 9.06 → 0.91** and **SOL short 4.69 → 0.61**, the two largest demotions in
  the queue. TSLA, 2.0% away and genuinely reachable, goes 23.24 → **24.15** and keeps its
  rank. Far zones lose the ratio; near ones keep it.
- **The saturation** (§4's finding). `min(rr / 3.0, 1.0)` → `rr / (rr + RR_HALF)`, the same
  hyperbola §11's cap got. `RR_SATURATION` is renamed `RR_HALF` because 3.0 is now the value
  worth 0.5, not a ceiling.

**`reward_risk` itself is unchanged, deliberately, and must stay that way.** It is still
`|target − entry| / risk` — what the trade pays *if it fills* — and it remains what the queue
prints and what `MIN_REWARD_RISK` gates on. The gate must not be re-pointed at the price-based
number: "risking more than it stands to make" is a **rule**, while reachability is a
measurement on a continuum, and per §6's gates-vs-scores split a rule is gated and a continuum
is scored. Feeding the journey to that gate would silently convert reachability into one. The
evidence it stayed a score: candidate count and the whole rejection tally are byte-identical
across the change (69 candidates, `reward_risk_too_low=10`).

Both numbers are recorded on every decision, so which of them predicts an approval better is
now a measurement rather than an argument — the pattern §21 set for carry.

**Still open, and explicitly not done here:** the module's other claim, that a very high R:R is
*itself* the symptom of a broken denominator (the `WEEKLY` docstring calls a 14.19 "a symptom
of a broken denominator, not a good trade"). The new shape still says 23.24 beats 3.0; it
merely stops pretending they are equal. Treating an above-ceiling ratio as a penalty rather
than a maximum is a separate change and would need its own measurement.

### That claim is now measured, and it is right · `2026-07-28`

`scripts/probe_stop_padding.py` over the 93-candidate population. R:R and the stop's width in
ATRs move together, monotonically:

| R:R band | n | median stop in ATR |
|---|---|---|
| 0 – 3 | 28 | 1.29 |
| 3 – 10 | 44 | 1.23 |
| 10 – 30 | 18 | **0.76** |
| 30+ | 3 | **0.26** |

So the implausible ratios are substantially a *tight denominator*, not a rich target — which is
what the `WEEKLY` docstring asserted without evidence. The sharpest case is `SILVER long daily`
at **R:R 2930.22**, whose stop is 0.025 wide against an ATR of 1.545: **0.02 ATR**, a fiftieth
of an ordinary day's range. §27 flagged that row as unexplained and told the reader to come
here; this is the explanation.

**Stop padding does not fix this, and must not be sold as though it does.** `STOP_PAD_ATR = 1.0`
stops the engine *manufacturing* new inflated ratios — the sub-1-ATR population went 41 of 93 to
zero — but SILVER survives every multiple swept, because one ATR added to a 0.025-wide stop
still leaves a denominator that bears no relation to the zone. Whatever fixes the extremes is a
change to how an above-ceiling ratio is *treated*, and it still needs its own measurement.

**(e) Weekly-first ordering concentrates them at the top · `OPEN`.** `collapse` sorts weekly
before daily unconditionally. Weekly zones are systematically the far ones — mean gap **28.2%**
for the unreachable set vs **3.7%** for the rest. The live queue put CL (21.3% away, score
0.297) at #7 and TSLA (2.0% away, score **0.906**) at #8. A 0.61 inversion, by rule. The rule is
defensible on its own terms ("the macro is much stronger"); what was never measured is that it
promotes precisely the unreachable population.

**It was worse than "promotes", and this is the part that had never been noticed · found
2026-07-28 while building §4's sampler.** The default cap fell *entirely inside the weekly
band*. Measured on the live queue: 67 candidates, **28 weekly then 39 daily**, so `--limit 25`
showed **25 weekly and zero daily** — and TSLA at **0.90, the highest score in the whole
population**, sat at position 29 and was never on screen at all. Meanwhile the CLI printed
`showing the top 25 by score`, which was simply false: it was the first 25 in weekly-then-score
order. Two consequences:

- **This is the mechanism behind §4's "the population flipped entirely between sessions".**
  The queue served weekly until weekly ran out, then served daily. §4 called the confound
  structural because "the ranker decides which population gets judged" — correct, and this is
  precisely how.
- **Any conclusion drawn from a default-limit queue before 2026-07-28 saw a weekly-only
  sample**, whatever it thought it was measuring. That includes §4's own weekly-vs-daily
  split, which §4 already declines to treat as established for a different reason.

Fixed only in the sense that it can no longer hide: §4's head band sorts on score, so the
best-scoring candidate is always shown, and both queue messages now say what they actually did.
The regression test is `test_the_head_reaches_the_best_score_even_when_queue_order_buries_it`
in `packages/oracle/tests/test_queue.py`.

### The ordering rule is now narrowed to where it was actually argued for · `FIXED 2026-07-28`

The unconditional weekly-first sort had a second cost nobody had named: **it separated the two
rows describing one thesis.** Every weekly sorted ahead of every daily, so a thesis producing
both was split across the queue. Observed in a live sitting — SPX6900's weekly zone was row 1,
an unrelated WLD row was 2, and SPX6900's daily zone was row 3, judged against a memory of the
first rather than against the first. Verbatim: *"It's not a great experience to see a weekly
that looks good, say approve, then be presented with a daily that doesn't have as good of an
entry."*

`collapse` now sorts **thesis groups by their best zone, weekly first within a group**. So
§19(e)'s precedence survives exactly where it was argued — "the macro is much stronger" still
decides which expression of *one* thesis leads — and stops reordering unrelated theses against
each other, which it was never argued for.

**A group is ranked by its best zone rather than by its weekly one, and that is measured, not
assumed.** Across the 27 assets carrying both on 2026-07-28, the **daily** zone scored higher
**15 times to the weekly's 12** (mean 0.535 v 0.498, mean approach 0.564 v 0.532). Near parity,
no stable winner, and which one wins swings hard per asset — MORPHO weekly 0.616 v daily 0.368;
ZEC weekly 0.449 v daily **0.727**. Ranking a whole thesis by its weekly alone would bury a
strong daily behind a weak weekly, which is the harm this entry opened with.

**Consequence to expect: the queue is no longer monotonically descending by score.** ZEC's
weekly at 0.449 now sits above IBM's 0.702, because ZEC's *group* peaks at 0.727. That is the
pairing working. The head band is unaffected — it selects on score independently of list order,
so the best row is still always on screen.

**The rows also say they are a pair** (`setups_render.thesis_pairing`): `2 zones for this
thesis · 1 of 2`. Counted over the sitting's own rows, not the population — if sampling drew
only one of a pair, claiming "1 of 2" would point at a row that is not there to compare
against, so a lone sibling gets no marker.

**Showing only the better zone was considered and rejected.** It would show the daily 15 times
in 27 — not the weekly, as intuition suggested — and it would foreclose the question
`decision_record` keeps `zone_timeframe` to answer: *do weekly setups actually beat daily ones?*
The scores say near-parity, so only revealed preference can settle it, and collapsing the pair
destroys the only evidence that could.

Reproduce any of this with the probes in the 2026-07-27 session; all are free and local.

---

## 18. `collapse` picks the group's *oldest* target by construction · `OPEN` — new 2026-07-27

Found while fixing the stale-target defect (`SCORE_VERSION` 4, `_reasonable` now rejects a
stated target price has already reached). That fix stops the number being *wrong*; this entry
is about why it was reliably the **worst available** number, which is a separate mechanism and
is still live.

**The defect.** `collapse` picks a group's representative as
`rep = min(authored or members, key=lambda s: abs(s.target - s.entry))` (`core/setups.py:477`)
— documented as "if they can't agree how far price goes, the smallest claim is the one to hold
them to". In a market that has trended, the smallest stated target is *mechanically* the oldest
one: targets are set relative to the price at publication, so the earliest call in a rising
market is the lowest number in the group. Conservatism and staleness are the same sort order.

**Evidence.** The SPX weekly candidate had **12 people** in the group. The rep chosen was
TraderMayne, **2025-05-22**, published at 5842 with `key_levels=[6000, 5700]` — the single
oldest authored target of the twelve. Price was 7403.7. Eleven fresher views, several with
levels above spot (DataDash 2026-07-20 `[7400, 7700]`, TraderMayne 2026-06-17 `[8000]`), lost
to a 14-month-old one *because* it was the smallest.

**Why it was invisible.** The header date is the freshest view's (`views[0]`) and `freshness` is
`max(...)` over members, but `target`, `reward_risk`, `approach` and `price` all come from `rep`.
So the row read `called 2026-07-26 (1d ago) · freshness 0.95` directly above a target from a
call made 2025-05-22, with nothing marking them as different theses. Whatever replaces the
rep rule, **the queue should name the thesis the target came from** — one line, and it would
have made this self-evident rather than something to go find.

**Not obvious what the rule should be**, which is why this is `OPEN` and not `DECIDED`. "Newest
authored target" reintroduces the recency bias `min` was chosen to avoid; "nearest to price"
re-derives the smallest-claim logic against a better reference; median-of-authored discards the
"listen to them" provenance that `target_source` exists to preserve. Needs measuring against
§4's sidecar, not picking by argument. That measurement became valid on 2026-07-28 when the
queue started drawing a stratified sample; it now waits on one triage sitting, not on a fix.

---

## 15. No concept of moving averages as levels · `OPEN` — new 2026-07-26

Nothing in the repo computes a moving average. `core/levels.py` handles stated levels only,
`core/structure.py` knows swings, breaks and order blocks, and `grep -rn "sma\|SMA\|moving_aver"
packages/` returns nothing. Every level the engine reasons about is structural.

**Evidence (2026-07-26, Tegan on the GOOGL long):** the 50- and 52-week SMA sit in the same
273–303 region as the weekly order block, and that *confluence* — not the order block alone — is
what makes the zone worth entering. The engine surfaced the zone (§ weekly zones, shipped) but
is blind to the reason it's strong. Two independent reasons to buy the same area currently
score identically to one.

**Shape when built.** This is a **score**, not a gate, per the gates-vs-scores rule: "how much
confluence does this zone have" is a measurement on a continuum, and no rule says a zone without
an SMA is untradeable. So it wants a new `SetupWeights` term and a `SCORE_VERSION` bump, with
the new term shown in the queue row (a soft signal that isn't displayed is strictly worse than a
hard one — same argument that put `freshness` and `no macro alignment` on screen).

**Cheap to source.** `Context` is already built from full daily and weekly bar arrays, so a
50/52-week SMA is an average over `weekly[-50:]` — no new fetch, no new dependency, no cost
tier. Note the weekly series excludes the in-progress week (`oracle.resample.to_weekly`), so an
SMA computed from it is as-of last week's close.

**Open question before building:** which averages. 50W and 52W were the two named, but nothing
has measured whether either actually marks turns in this corpus — and a confluence term that
fires on an arbitrary average is worse than none, since it would launder a guess into the score.

---

## 21. Funding is a real cost and nothing in the scorer sees it · `PARTLY DONE 2026-07-27`

**Done: it is computed, displayed and recorded.** `Context.funding` carries a `FundingOutlook`
(median + p90, injected by `oracle.carry` — `core` still does no I/O); `cross_reference`
reports `funding_annual`, `carry`, `carry_reward_risk`, `carry_reward_risk_p90`; the queue
prints `adj` beside `R:R`; `decision_record` writes two new fields. `CARRY_HOLD_DAYS = 21`
prices it, deliberately not a horizon and invisible to `_score`.

**`_score` is untouched and `score_version` stays at 5** — ranking cannot have moved, which is
what makes the next session's decisions a clean measurement rather than a confound.

**Live effect on 44 candidates, 13 of which are carry-priced:** `MU short` 2.41 → **3.04**
(3.04 → 7.14 at p90 — a short collects when the crowd is long); `NVDA long` 2.85 → 2.27 →
**1.62** at p90; `SPX long` 9.06 → **9.43**, because SPX funding is *negative* and longs are
paid. No candidate tripped `carry_dominates`, so the gate is not over-firing.

**Residual — coverage.** Only 13 of 44 candidates priced, because `cfg/venue_map.yaml` covers
the 30 approved assets and the queue is mostly crypto alts outside it. Every unmapped asset
correctly gets `None` rather than a guessed zero, but a correlation over 13 rows is thin.
Widening the map is the cheap lever; do it before mining, not after.

### The coverage gap now blocks execution, not just carry · measured 2026-07-28

§27 released 23 candidates and the queue turned over, so this residual stopped being a thin-
correlation problem and became a "you cannot act on the queue" problem. Of the **15 rows in the
live queue, exactly one — `SILVER` — has any entry in `cfg/venue_map.yaml`.** Approving anything
else prints `! not executable — <asset> has no listing on this venue`.

**That message is mostly false.** Checked against the July funding log, which records every
market each venue actually reported:

| asset | hyperliquid | lighter | aster | in venue_map? |
|---|---|---|---|---|
| `WLD` | yes | yes | yes | **no** |
| `TAO` | yes | yes | yes | **no** |
| `DASH` | yes | yes | yes | **no** |
| `RKLB` | `xyz` | yes | yes | **no** |
| `BE` | `xyz` | yes | yes | **no** |
| `USAR` | `xyz` | — | yes | **no** |
| `ZM` | `xyz` | — | yes | **no** |
| `SPX6900` | as `SPX` | as `SPX` | as `SPXUSDT` | **no** |
| `HL`, `SGML`, `SBSW`, `INTL` | — | — | — | correctly absent |

So at least **8 of the 12** unmapped assets are listed somewhere and are being reported as
untradeable purely because the map has no row for them.

**`SPX6900` is the sharp one, and the file's own header predicted it.** That header documents
the collision in detail — "Hyperliquid's core book lists SPX6900, a memecoin, under `SPX`" — and
then maps only the `SPX` (index) side. The memecoin side, which the corpus also carries and which
is sitting at the top of the queue, has no entry, so the one asset the header warns about
loudest is the one that cannot be executed.

**Widening it is not a mechanical paste.** The header's whole argument is that name-matching a
venue ticker is "silently catastrophic", and `SPX6900 -> SPX` is precisely why: the right entry
for the memecoin is the symbol that would be *wrong* for the index. Each row needs the instrument
confirmed, not the string matched. Crypto majors (`WLD`, `TAO`, `DASH`) are low risk; the HIP-3
equities (`RKLB`, `BE`, `USAR`, `ZM`) need checking against the builder's listing; `SPX6900`
needs care in both directions.

**Do not let the four genuine absences be filled by guessing.** `HL`, `SGML`, `SBSW` and `INTL`
are listed by no venue in the log, and per this file's rule absence is a real answer.

**Residual — the measurement itself.** Was blocked on §4; **unblocked 2026-07-28**, when the
queue started drawing a stratified sample so a sidecar correlation is valid again. The point
stands: does `carry_reward_risk` separate approve from reject better than `reward_risk`? Needs
one session of decisions carrying both — and now one session genuinely suffices, which it did
not before.

**The comparison is now three-way, not two.** `SCORE_VERSION` 6 added
`reward_risk_from_price` (§19d) and it is the number `_score` actually consumes, so the
question is which of the three — nominal, carry-adjusted, or distance-corrected — best predicts
an approval. All three are recorded on every decision from v6 onward. Only then decide whether
the 0.20 `reward_risk` weight should consume the carry-adjusted number; that is a term-input
correction of exactly the kind §19(d) just made, not a re-weight, and it would bump
`score_version` to 7.

**Why it matters more than it looks.** Carry hits both legs of R:R in opposite directions —
subtracted from reward, added to risk — so a rate that sounds small moves the ratio a lot. A
nominal 8% target / 4% stop is R:R 2.0. Held 21 days:

| venue | NVDA median funding | long R:R | short R:R |
|---|---|---|---|
| Hyperliquid | 11.86%/yr | 1.56 | 2.62 |
| Hyperliquid at p90 | 31.44%/yr | 1.07 | 4.48 |
| Aster | 0.00%/yr | 2.00 | 2.00 |

At HOOD's p90 (38.39%/yr) a nominal 2.0 long lands at **0.93** — below 1. Same levels, same thesis —
the direction and the venue decide whether the edge survives. The queue currently prints one
R:R and it is the nominal one.

**This collides with §2 (rip out horizons), and the collision is real, not incidental.** Carry
is `rate × holding_period`, so pricing it needs *some* expected hold. §2's replacement is
event-based ("live until the person restates"), which yields a duration only in hindsight. The
cheap resolution is a single global constant for costing purposes only — explicitly not a
horizon, not per-timeframe, and not load-bearing for grading — measured from realised
restatement cadence (11–28d, §2). Do not rebuild per-label horizons to get this.

**Per the gate/score rule this is a score, not a gate.** Funding is a continuum. The one
plausible gate is "carry exceeds the target" — `CarryAdjustedRR.carry_dominates` — which is a
fact about the setup being dead rather than a judgement about how good it is.

**Measure before weighting.** The scorer already has six terms and §4 refuses a global
re-weight; adding a seventh blind would compound that. Baseline to measure against: the v5
population in `data/setups/decisions.jsonl`.

---

## 22. Lighter's funding history feed does not reconcile with its snapshot feed · `OPEN` — new 2026-07-27

`fetch-funding --backfill` covers Hyperliquid and Aster. **Lighter is snapshot-only**, so its
column is `n=1` in `--report` and will stay thin until enough nights accumulate.

**The evidence, and why it was not worked around.** Two Lighter endpoints disagree by roughly
10x with no derivable conversion:

| feed | BTC | ETH | shape |
|---|---|---|---|
| `/api/v1/funding-rates` | `2.4e-05` | `9.6e-05` | signed, 8-hourly |
| `/api/v1/fundings?resolution=1h` | `0.0002` | `0.0008` | unsigned magnitude + `direction` |

The snapshot feed's unit *is* established: it publishes Hyperliquid's rate at exactly 8.000x
Hyperliquid's own hourly figure on every symbol sampled (ETH, HYPE, ZEC, LINK, AVAX, DOGE),
so it is 8-hourly. The history feed is neither that value nor 8x nor 1/8 of it, and its sign
lives in a separate `direction` field. Guessing a factor here would put an 8x error into a
carry model — the exact failure `sources/lighter.py` is written to prevent.

**Next:** ask Lighter directly, or infer the unit by reconciling a realised payment against a
funded position. Do not ship a conversion inferred only from the ratio of the two feeds.

---

## 23. `--backfill DAYS` is a request, not a window — both venues cap it · `WATCHING` — new 2026-07-27

Neither venue honours the requested span, and they miss it in opposite directions:

- **Hyperliquid** returns at most **500 rows per symbol**. At hourly settlement that is
  ~20.8 days, so `--backfill 30` silently yields 21. Needs pagination on `startTime`.
- **Aster** ignores the window entirely — `limit=1000` reaches back as far as the rows exist,
  which is why the first backfill wrote partitions from **2025-08** onward. Harmless (more
  history is better) but the flag does not mean what it says.

Not urgent: the log is append-only and the reader dedupes, so re-running costs nothing and
the extra Aster depth is a bonus. It matters the moment anyone reads "30 days" as a
guarantee — a distribution computed over 21 days of one venue and 11 months of another is
not a comparison.

---

## 24. `data/funding/` is not regenerable ore, and the tree says it is · `OPEN` — new 2026-07-27

Same class as §4b. `docs/ARCHITECTURE.md` describes everything under `data/` as
machine-generated and regenerable; that is true of `data/prices/` and false here in the
degree that matters. Hyperliquid and Aster serve bounded history (500 rows / 1000 rows), so a
gap older than that window is **permanently unrecoverable**, and Lighter has no usable
history at all (§22) — its column exists only for nights the logger actually ran.

Milder than the decision sidecars: this is measurement, not judgement, and most of it can be
re-pulled within the venues' windows. But a machine asleep for a month costs a month of
Lighter coverage outright. Decide whether it wants the vault mirror `oracle/decisions.py`
already implements before the log is long enough to be worth losing.

---

## 25. Route each order to the venue that is actually cheapest · `DECIDED` — gated on measurement, new 2026-07-27

**The intent:** once a candidate is approved, pick the venue to execute it on rather than
assuming one. The machinery to *price* that choice shipped with §21; what is missing is the
choice itself.

### The finding that matters, so it is not re-derived: split by DIRECTION, not asset class

The obvious-looking split — "crypto on Hyperliquid, equities on Aster" — is **strictly worse
than either single venue**, and the reason is not obvious until the numbers are in front of
you. Hyperliquid's funding is positive on every approved equity; Aster's is zero on every one.
Zero funding is only an advantage when you would be *paying* it:

| | Hyperliquid | Aster | better |
|---|---|---|---|
| **long** NVDA | pay 0.68% | pay 0.00% | Aster |
| **short** NVDA | **collect 0.68%** | collect 0.00% | Hyperliquid |
| **long** CRCL | pay 1.04% | pay 0.00% | Aster |
| **short** CRCL | **collect 1.04%** | collect 0.00% | Hyperliquid |

Across all 11 mapped equities: **longs cheaper on Aster 11/11, shorts better on Hyperliquid
11/11** (21-day carry, 30-day medians, measured 2026-07-27). Routing all equities to Aster
would send every short to the venue that pays nothing — and the corpus is 1,082 shorts against
2,524 longs, so that is ~30% of theses handed the worst available side of the trade.

### The thin-book objection was tested and does not hold at retail size

Walking both real order books (NVDA, 21-day hold, all-in round trip):

| size | HL slippage | HL total | Aster slippage | Aster total |
|---|---|---|---|---|
| $2,000 | 1.5bp | 0.88% | 18.1bp | **0.25%** |
| $10,000 | 2.6bp | 0.89% | 23.3bp | **0.30%** |
| $50,000 | 5.3bp | 0.91% | 47.6bp | **0.55%** |

Hyperliquid carries a fixed 0.86% drag (0.68% carry + 0.18% fees). Aster's slippage is ~10x
worse and still never catches up. **Do not re-argue this from liquidity alone** — the 24h
volume gap (NVDA $90M vs $82k) predicts the opposite of what the books actually do at this size.

### Why it is not built yet

- **Aster's zero is a policy, not a structural property.** NVDA's p90 on Aster is already
  12.97%. If it starts charging, the entire rationale evaporates and the position is sitting on
  a book with **$426 available within 10bp of mid**, against Hyperliquid's **$317,593**.
- **Exit risk is not entry risk.** The table above is a calm snapshot with US cash markets
  closed. Entries are chosen; stops are not. A stop-market into that book during a gap is
  where the 0.68% saving gets returned several times over.
- **Two collateral pools.** Hyperliquid settles USDC, Aster USDT, with no cross-margin — a
  winning crypto position cannot margin a losing equity one. For a small account that
  fragmentation plausibly costs more than the 0.3–1.0% being chased.
- **Aster rate-limits by IP with escalating bans up to 3 days**, which is a live hazard for
  anything running from the nightly job.

### The trigger, so this is a measurement and not a judgement call

Build it when **Aster's equity funding median is still ~0% over 60+ days** of logged data
(`uv run fetch-funding --report --window 60`) and typical size stays under ~$25k. Until then a
single venue — Hyperliquid, for unified margin and one integration — is the right default.

### What is already in place, and what is left

Already built: `cfg/venue_map.yaml` is keyed `(asset, venue)`; `carry.outlooks_for(venue=…)`
re-prices the whole queue; `setups --funding-venue aster` runs today. So the residual is small
— roughly, choose the venue per candidate by the sign of funding and record which venue the
decision assumed.

**Keep execution routing separate from price routing.** `cfg/oracle_map.yaml` answers "what is
this worth" and must keep pointing at the index (`^GSPC`); `cfg/venue_map.yaml` answers "where
do I trade it" and points at whatever instrument the venue lists (`SPY`, `xyz:SP500`,
`US500`). Collapsing the two regrades every stored thesis against an instrument at ~1/10 scale
— see that file's header on the SPX scale trap.

**One measurement caveat that flatters Aster:** Hyperliquid's `l2Book` returns only ~20 levels
per side, so its depth above is a floor and its slippage a ceiling. Aster was queried at
`limit=500`. Re-measure both at equal depth before committing capital to the split.

### Metals: `xyz` beats both alternatives, and Lighter's depth is a mirage · measured 2026-07-28

**Read the testnet warning before anything else here.** Approving `SILVER LONG` in a live sitting
printed `! would fail the liquidity gate on testnet data — xyz:SILVER traded $0 in 24h`. That
number is the **testnet mock book**, which is exactly what `execute.py` says it is ("on the
rehearsal venue the liquidity gate is measured but not enforced, because the mock book makes
``check_liquidity`` unenforceable"). It was briefly written up here as evidence that the mainnet
book was dead. **It is not.** Measured on mainnet, `xyz:SILVER` is the *best* venue of the three.
Never quote a testnet liquidity number as a fact about a market.

`scripts/probe_book_depth.py` walks all three books — free, public endpoints, no key, no order.
Buy-side slippage vs mid, one snapshot 2026-07-28:

| asset | venue | symbol | spread | depth ±10bp | $2k | $10k | $50k |
|---|---|---|---|---|---|---|---|
| SILVER | hyperliquid | `xyz:SILVER` | **0.2bp** | $1.72M | **0.1bp** | **0.4bp** | **0.9bp** |
| SILVER | aster | `XAGUSDT` | 1.7bp | $1.21M | 0.9bp | 0.9bp | 1.9bp |
| SILVER | lighter | `XAG` | 2.8bp | **$2.03M** | 3.0bp | 4.3bp | 4.7bp |
| GOLD | hyperliquid | `xyz:GOLD` | 0.2bp | **$2.92M** | 0.1bp | 0.1bp | 0.1bp |
| GOLD | aster | `XAUUSDT` | **0.1bp** | $2.05M | 0.1bp | 0.1bp | 0.1bp |
| GOLD | lighter | `XAU` | 1.6bp | $2.55M | 0.8bp | 0.8bp | 1.4bp |
| BTC | hyperliquid | `BTC` | 0.2bp | **$12.1M** | 0.1bp | 0.1bp | 0.1bp |
| BTC | aster | `BTCUSDT` | **0.0bp** | $10.7M | 0.0bp | 0.0bp | 0.0bp |
| BTC | lighter | `BTC` | 0.7bp | $3.45M | 0.4bp | 0.4bp | 0.5bp |

**"Lighter is the better venue for hard assets" is not supported.** It was worth checking and the
measurement says otherwise: Lighter carries the *most* resting size within 10bp on silver
($2.03M against Hyperliquid's $1.72M) and still costs **3–5x more to cross** at every size,
because its spread is 2.8bp against 0.2bp. **Depth within a band and cost to execute are
different quantities**, and this is the case that separates them — the size is there, it is just
not near the touch. Any future venue comparison must rank on slippage, not on depth.

**So §25's single-venue default survives, for a reason it did not state.** Hyperliquid wins or
ties on all three assets here, metals included. The original argument was unified margin and one
integration; the measured argument is that it is simply the tightest book.

**The margin caveat is real and unchanged.** Hyperliquid's *core* book carries 177 markets and no
metals — silver and gold exist only on the `xyz` HIP-3 builder, and `broker.py` records that each
builder has its own pool ("under manual mode each dex has its own pool. Detected, never assumed").
So trading `xyz:SILVER` fragments collateral away from core regardless. That is a cost the venue
choice does not remove; it just is not a reason to prefer Lighter or Aster, which fragment it too.

**The ranking is stable; the magnitudes are not.** A second SILVER snapshot minutes later put
Lighter's spread at 1.6bp rather than 2.8bp and its slippage at 0.8/1.0/2.2bp rather than
3.0/4.3/4.7bp. Hyperliquid was still tightest on both, so *which venue wins* survived the
re-measure while *by how much* did not. Do not quote the multiplier; quote the ordering, and
re-run before sizing anything on it.

**The caps differ, so depth is not like-for-like.** Aster answers at 500 levels, Lighter caps at
100 orders (500 is rejected as `invalid param`), Hyperliquid returns ~20. Slippage at these sizes
is set by the top of book so the caps do not explain the ordering, but the depth column is not a
fair comparison. Both snapshots were taken with US cash markets closed — §25's warning that "exit
risk is not entry risk" applies with full force.

**Liveness metadata is already solved, and not the way this entry assumed.** Routing needs to
know a venue's market is real; `oracle/liveness.py` answers that from the funding log rather
than from config — a market is dormant when its venue cohort reported and it did not. Decided
2026-07-27 while removing `DXY -> xyz:DXY` ($0 volume, $0 OI, nothing quoted, and zero funding
rows against 502 for every sibling): a curated `dormant:` flag in `venue_map.yaml` rots toward
"looks alive", which is the same error the liquidity gate exists to fix. **Do not add liveness
fields to that file.** Depth and slippage — what routing actually ranks on — are a different
measurement and still need a real store; funding rows answer *does this market exist*, not
*which venue is cheaper*.

---

## 27. `timeframe_conflict` counted a one-sided daily reading as a two-timeframe disagreement · `PARTLY DONE 2026-07-28` — option 1 shipped, option 2 open

### Option 1 shipped · `2026-07-28`

**The daily leg may now only contradict a weekly that has an opinion.** `cross_reference` runs
the daily check only when `weekly_family is not None`; a ranging weekly means there is no macro
view to conflict *with*, so the refusal was reporting the daily leg alone under a name claiming
otherwise. Measured effect, matching the audit's prediction exactly:

| | before | after |
|---|---|---|
| candidates | 74 | **97** (+23) |
| `timeframe_conflict` | 1,017 | **761** — precisely the genuine-conflict population |

**`SCORE_VERSION` stays at 6.** No term and no weight changed and no existing candidate's score
moves, so v6 is not re-partitioned — §21's precedent. `weekly_trend` and `daily_trend` are now
written on every decision (additive, no bump, per §4(d)), which is what makes option 2 minable:
while the gate refused every contradicting row the sidecar accumulated **zero** negatives for
the daily leg, so the gate was destroying the evidence needed to evaluate its own replacement.

**§4's queue is no longer empty** — 22 rows to review, so the first stratified sitting can
happen. That was the point of picking this entry.

**The rule this encodes, in Tegan's words (2026-07-28):** *"we can take trades in a weekly range
without knowing a hard trend. We just assume it's going to the previous high or low until
invalidated."* A range has its own thesis; a weekly without a trend is not a weekly without a
reason to act.

### Residual: the released candidates do not target what that rule names · `OPEN`

Checked immediately after shipping, because the rule above makes a claim about the *target* and
the engine picks targets by a different mechanism. Of the **68** candidates now live on a
ranging weekly, only **20 are within 2%** of the dealing-range bound their direction points at;
**median gap 9.5%**. Worst cases are `structural` targets: `XMR` long targets 799.9 against a
range high of 426.3 (**87.6% beyond** it), `SUI` long targets 0.7824 against a range high of
1.415 (**44.7% short** of it). The exact matches — `SPX6900`, `WLD`, `MAG7` at 0.0% — are all
`structural` targets that happen to coincide with the bound.

So option 1 is right about *whether* to take these trades and silent about *where they go*.
This is §18's territory (`collapse` picks the group's representative, and `target_source` is
`stated` / `nearest` / `structural`), not a new gate — but it is the first population where the
stated rule and the computed target can be checked against each other, and they disagree.
**Do not "fix" this by clamping targets to the range bound before reading §18** — that would
overwrite a person's stated level with a derived one, which is what `target_source` exists to
prevent.

### The audit that produced this · `2026-07-28`

The audit §27 asked for is done. `scripts/probe_timeframe_conflict.py` — free, local, reads the
price cache and reproduces the shipped engine exactly (74 candidates and
`timeframe_conflict=1017` both match `setups --list --limit 0`). **Every number below is from
one run at 2026-07-28.**

The original open question was "is `TREND_DEPTH = 2` too coarse for the daily leg". The answer
is **no, and the question was pointed the wrong way** — but the audit found something larger on
the way past it.

### The gate costs 48 candidates, and they look like ordinary candidates

Neutralising *only* the daily leg — `daily_trend` reaches exactly one decision
(`setups.py:791`) and is otherwise carried for display, so forcing it to `ranging` disables
that one check and nothing else:

| | candidates |
|---|---|
| gate on (shipped) | **74** |
| gate off | **122** (+48, +65%) |

**The 48 are indistinguishable from the 74 on the term that measures tradeability:**

| | n | `approach` mean | median | at or inside the zone |
|---|---|---|---|---|
| kept by the gate | 74 | 0.541 | 0.542 | 27 (36%) |
| refused by the gate | 48 | 0.508 | 0.478 | 16 (33%) |

That is the honest form of the argument for releasing them: they are not a junk tail. The gate
is not filtering out distant or unreachable zones — `approach` already ranks those (§19) — it is
removing an ordinary slice of the population on a criterion unrelated to how good the setup is.

**A stronger claim was made here on 2026-07-28 and withdrawn the same day. Do not restate it.**
The first pass reported that 0 of the 48 were zones price had already traded through, and read
that as "the gate refuses candidates at the moment they become tradeable". **The control shows
the baseline is also 0 of 74** — `price_past_stop` refuses traded-through zones upstream and
`approach_to` returns 0.0 for them, so *every* candidate in the engine is approaching-or-inside
by construction. The measurement was real and said nothing whatever about this gate.

### A quarter of the refusals have nothing to conflict with

`_family_of(RANGING)` is `None` and the weekly check skips `None`, so a thesis reaches the daily
leg by two different routes:

| why it reached the daily leg | n | |
|---|---|---|
| weekly **agreed** with the thesis | 761 | 74.8% — a genuine two-timeframe conflict |
| weekly was **ranging** | 256 | 25.2% — no macro opinion to conflict with |

**The name is false for 256 of them**: there is no timeframe *conflict* when only one timeframe
has an opinion. This is the identical shape §6 fixed for `weekly_disagrees`, where 630 rows were
dying "for the absence of an opinion rather than the presence of a contrary one" — and that fix
took candidates 8 → 49. Of the 48 candidates the gate withholds, **23 are this population** and
25 are the real-conflict one, so the two routes cost about the same.

### The verdict pairs are all one shape

| weekly | daily | thesis | n |
|---|---|---|---|
| downtrend | uptrend | short | 398 |
| uptrend | downtrend | long | 235 |
| ranging | downtrend | long | 179 |
| downtrend | uptrend_failed_breakout | short | 112 |
| ranging | uptrend | short | 68 |

**745 of the 761 real conflicts are "the daily is retracing against the weekly"** — which is the
textbook entry condition, not a contradiction. The 112 `uptrend_failed_breakout` shorts are the
sharpest case: a failed daily breakout *inside a weekly downtrend* is bearish continuation, yet
`_family_of` maps the failed-break states to the bullish family (correctly, for its own stated
purpose — "the pullback is the long entry") and that makes them conflict with a short. The
docstring's own reasoning argues against the use the gate puts it to.

### The two legs do not read the same amount of history — and the entry had this backwards

Both legs use `TREND_DEPTH = 2` and `SWING_WIDTH = 2`, but a daily swing and a weekly swing are
not the same distance in time. Measured over all 315 priced assets:

| leg | n | p25 | median | p75 |
|---|---|---|---|---|
| weekly | 179 | 98 | **125** | 149 days |
| daily | 288 | 15 | **21** | 27 days |

**The weekly leg reads 6x more history than the daily leg.** §27 previously guessed the opposite
("anchoring two swings back spans a much longer stretch of daily history than of weekly") — that
sentence was wrong and is why the depth hypothesis looked plausible. A 3-week reading is being
used to veto a 4-month one.

### `TREND_DEPTH` is not the knob · sweep has no interior optimum

| daily depth | median span | vs weekly | conflicts | candidates |
|---|---|---|---|---|
| **2** (shipped) | 21 d | 17% | 1,017 | **74** |
| 3 | 32 d | 25% | 1,030 | 61 |
| 4 | 42 d | 34% | 1,126 | 62 |
| 6 | 66 d | 53% | 965 | 58 |
| 8 | 86 d | 69% | 346 | 78 |
| 12 | 124 d | 99% | 312 | 87 |

Deepening the anchor **makes it worse before it makes it better** — depths 3–6 cost candidates
outright (74 → 58) while conflicts stay flat or rise. Conflicts only collapse at depth 8–12, and
by then the daily leg reads 69–99% of the weekly's span: it has stopped being a second opinion
and become a copy of the first. **Agreement bought by redundancy is not a fix.** Same tell §4
recorded for the freshness sweep — a sweep with no interior maximum means the shape is wrong,
not that the constant needs tuning.

**So `TREND_DEPTH = 2` is doing its job.** The daily leg is *correctly* reading a short-term
move. The defect is that a short-term reading is wired as a **veto** instead of as **timing**.

### The worked example, hand-verifiable

`ETH/BTC` — weekly verdict independently confirmed by §6f (0.050 → 0.030 across two years):

| leg | span | swing highs | swing lows | verdict |
|---|---|---|---|---|
| weekly | 238 days | −6.6% | −23.9% | `downtrend` |
| daily | **28 days** | +3.7% | +10.1% | `uptrend` |

**12 short theses refused because of a four-week bounce inside a two-year decline.** For a short,
that bounce is the rally into the bearish zone — the entry. The released `ETH/BTC` short scores
0.719 and is the second-best candidate the gate is withholding.

### Option 2 — convert the daily leg to a scoring term · `OPEN`, blocked on evidence not effort

The remaining **25** candidates sit behind a *genuine* two-timeframe disagreement, which option 1
deliberately did not touch. Per §6's gates-vs-scores rule a continuum belongs in the score, so
the shape is a seventh `SetupWeights` term shown in the queue, and `SCORE_VERSION` → 7.

**Three things make it a decision rather than a change:**

- **There is no spare weight.** The six weights sum to exactly 1.0 (`approach` 0.40,
  `reward_risk` 0.20, `agreement` 0.20, `freshness` 0.15, `trend_alignment` 0.05). A seventh
  term takes weight from the other six, which is the global re-weight §4 refuses without
  evidence and §21 warned adding a seventh term blind would compound.
- **The obvious mapping may have the wrong sign.** `agrees 1.0 / ranging 0.5 / contradicts 0.0`
  encodes "daily disagreement is bad", but 745 of the 761 real conflicts are the daily
  *retracing* against the weekly, which is the entry condition. The hypothesis worth testing
  instead is that the informative state is the **failed break** — `downtrend_failed_breakdown`
  under a long, `uptrend_failed_breakout` under a short: the counter-move tried to continue and
  couldn't. Note `_family_of` already treats failed breaks as permissive for exactly that
  reason, and that mapping is what generates the 112 `downtrend / uptrend_failed_breakout /
  short` refusals — its docstring argues against the use the gate puts it to.
- **It is a timing term, not a trend term, and should not be named one.** The daily leg reads a
  median of 21 days. The real entry trigger is sub-daily (§12) and §3's fourth unknown — the
  "15m failed breakdown, reclaim" — is a corpus gap, so a daily-bar trigger is a proxy for
  something the spec does not yet define.

**`trend_alignment` will need restating either way.** It is two-valued (`ALIGNED` 1.0 /
`UNALIGNED` 0.0) *because* a contradicting weekly is gated — its docstring says "there is no
third value". A daily analogue has three, so `ranging` would mean 0.0 on one leg and ~0.5 on
the other. Fix or document; do not leave it implicit.

**One thing is cheap right now:** v6 has **zero** decision rows (the queue emptied the moment v6
shipped), so a bump to 7 currently strands no cohort. That argues for doing term surgery before
sittings accumulate — but it does not solve the weight problem, which is the binding constraint.

**Next step is data, not code:** hold sittings now that the queue has rows, then mine
`daily_trend` against approve/reject. That is the record-both-then-measure pattern §21 set for
carry and §19(d) for the two R:R numbers.

### Do not re-derive these

- **The daily leg spans *less* history than the weekly, not more** — 21 days against 125. The
  pre-audit text asserted the reverse; that error is what made "TREND_DEPTH is too coarse" look
  like the answer.
- **Do not tune `TREND_DEPTH` on the daily leg.** Swept 2→12: no interior optimum, worse at
  3–6, and "fixed" at 8–12 only by making the daily leg redundant with the weekly.
- **"0 of the 48 blocked candidates had price past its zone" is true and is not evidence.**
  So is 0 of the 74 kept ones — `price_past_stop` removes them upstream. Any future "look how
  well-placed the blocked ones are" measurement needs the kept population as its control, or
  it will re-discover a property of the engine and report it as a property of this gate.
- **Do not read the 1,017 as waste the way `weekly_disagrees` at 1,869 is not waste.** What is
  established is narrower: the blocked population resembles the kept one on `approach`, and a
  quarter of the refusals are not conflicts at all.
- **`SILVER long daily` comes back with R:R 2930.22.** Unrelated to this gate — it is §19's
  still-open "a very high R:R is itself the symptom of a broken denominator". Do not let it
  discredit the other 47; note it and read §19.

---

## 28. Eight instruments were routed under seventeen asset keys · `FIXED 2026-07-28`

**Shipped the same day it was found.** Five groups were verified as one instrument and folded
into `cfg/assets.yaml` aliases; two were verified as genuinely different and left split; one was
a live wrong-direction bug and its route was removed.

| canonical | absorbed | evidence |
|---|---|---|
| `GOLD` | `XAU`, `GC` | all 17 rows explicitly gold |
| `SILVER` | `XAG` | all 6 rows explicitly silver; `XAG` is the ISO 4217 code for a troy ounce, not a derivative |
| `OIL` | `CL` | all 9 rows crude — **none are Colgate**, which the routing header warns `CL` resolves to |
| `RUT` | `RTY` | Russell 2000 |
| `NKY` | `N225` | Nikkei |

Folded in **canon, not routing**, and that placement is the whole point: `oracle_map.yaml` would
have deduped *prices* while leaving the corpus split across two keys, so `collapse` would still
group them apart and `agreement` would still count them separately. The now-redundant routing
entries were removed, with a header note saying not to re-add them — if an alias ever misses, the
label falls through to `unpriceable` and shows up in the unpriced tally rather than resolving to
Colgate. Measured after: `SILVER` 46 → 52 rows and 10 → 11 people; `GOLD` 149 rows, 13 people;
candidates 97 → 93 as the duplicate rows collapsed.

**Left split deliberately: `EUR`/`EURUSD` and `GBP`/`GBPUSD`.** A currency is not a currency
pair, and the corpus proves it — one `EUR` row is a **EUR/GBP cross** and a `GBP` row is
**British Pound futures (6B)**. Folding those into the dollar pair would file a cross as a USD
trade. They are safe today only because EUR and GBP are the pair's *base* currency, so direction
agrees; that is a coincidence of convention, not a reason to merge.

**Two regression tests now hold the line**, both reading the committed `cfg/` rather than a
fixture, because this is a curation defect and no fixture can catch it:
`test_no_two_asset_keys_route_to_one_instrument_unless_declared` (with an explicit
`INTENTIONAL_SHARED_SYMBOLS` allowlist, so a new duplicate must be argued for in writing) and
`test_a_bare_currency_never_routes_to_a_pair_that_inverts_it`.

### The original entry, kept for the evidence

## 28a. Eight instruments routed under seventeen asset keys · `was OPEN` — found 2026-07-28

Found while previewing §4's first sitting: rows 2 and 6 of the queue were `SILVER LONG` and
`XAG LONG`, identical in every number (R:R 19.19, scored 17.57). They are the same trade.
`cfg/oracle_map.yaml` routes both to Yahoo `SI=F`, and nothing upstream knows they are one asset.

| source | symbol | asset keys |
|---|---|---|
| yahoo | `GC=F` | `GOLD`, `XAU`, `GC` |
| yahoo | `SI=F` | `SILVER`, `XAG` |
| yahoo | `CL=F` | `OIL`, `CL` |
| yahoo | `EURUSD=X` | `EURUSD`, `EUR` |
| yahoo | `GBPUSD=X` | `GBPUSD`, `GBP` |
| yahoo | `USDCAD=X` | `USDCAD`, `CAD` |
| yahoo | `^N225` | `N225`, `NKY` |
| yahoo | `^RUT` | `RUT`, `RTY` |

**Confirmed same underlying, not merely similar.** `XAG` is the ISO 4217 code for one troy ounce
of silver (as `XAU` is for gold) — a spelling of the metal, not a derivative of it. Spot and
front-month futures differ by basis, but that distinction does not exist here: both keys already
route to `SI=F`, so they resolve to the same file and the same bars.

**The measured cost, split into the part that is real and the part that was overstated.** An
earlier version of this entry claimed the `agreement` term was badly diluted — "a roster split
between gold and XAU produces two candidates of one voice each rather than one of two". Measured
2026-07-28 over 4,609 corpus rows, **that is mostly false**, because the same people tend to use
both spellings:

| group | rows per key | distinct people, largest single key | union after merge | voices gained |
|---|---|---|---|---|
| gold | `GOLD` 132 · `XAU` 14 · `GC` 3 | 13 | 13 | **0** |
| oil | `OIL` 46 · `CL` 9 | 9 | 9 | **0** |
| rut | `RUT` 3 · `RTY` 1 | 2 | 2 | **0** |
| silver | `SILVER` 46 · `XAG` 6 | 10 | 11 | +1 |
| n225 | `NKY` 4 · `N225` 1 | 2 | 3 | +1 |
| eur | `EURUSD` 14 · `EUR` 12 | 4 | 5 | +1 |
| cad | `USDCAD` 1 · `CAD` 1 | 1 | 2 | +1 |
| gbp | `GBPUSD` 4 · `GBP` 3 | 3 | 5 | +2 |

So merging moves `agreement` by 0–2 voices and by **exactly zero on the two largest groups**.
Do not justify this work on the scoring term.

**What is real, and was observed rather than predicted:** the queue offered `SILVER LONG` and
`XAG LONG` as rows 2 and 6 of the same sitting, identical in every number (R:R 19.19, scored
17.57), and again as rows 14 and 19 on the daily. That burns two of a sitting's limited slots on
one trade, double-counts in §4's mining pass as two independent decisions, and lets the same zone
be approved once and rejected once with nothing flagging the contradiction. `collapse` groups by
asset key, so it cannot merge them.

**Distinct from the collisions already known** (`SPX` → Coinbase memecoin, `GOLD` → Gold.com Inc
on Yahoo — see `cfg/oracle_map.yaml`'s header). Those are *one key pointing at the wrong
instrument*; this is *many keys pointing at one right instrument*. The existing header warns
about the first and is silent on the second.

**Where the fix belongs: `core/canon.py`, not the routing map.** `cfg/assets.yaml:36` already
carries `SILVER: [Silver]` as the canon entry — the aliases simply do not list the tickers, so
`resolve_asset` never folds `XAG` into `SILVER`. Fixing it in `oracle_map.yaml` would dedupe
prices while leaving the corpus still split across two keys, which is the half-fix that leaves
`agreement` broken. Check whether the same gap exists for crypto aliases before assuming it is
FX/metals only.

**Do not simply delete the duplicate keys.** Both spellings appear in the corpus because both
are said out loud; the registry needs to *alias* them, so a thesis mentioning either still
resolves.

---

## 29. A bare currency cannot be priced off a pair that inverts it · `OPEN` — new 2026-07-28

Found while fixing §28. `cfg/oracle_map.yaml` routed `CAD` to `USDCAD=X`. CAD is the **quote**
currency of that pair, so the two move opposite ways, and nothing in the engine flips direction:

> TTrades, `direction=long`: *"Bullish CAD futures (**equivalently bearish USDCAD**) off a daily
> V-shaped reversal, fair value gap fill and ideal…"*

The extractor read it correctly. Routing then priced it against `USDCAD=X` and the engine scored
it as **long USDCAD — the opposite trade**. One thesis, so the blast radius was tiny; the class is
the dangerous one, because the output looks entirely normal.

**Removed the route 2026-07-28** rather than guessing a fix. `CAD` is now unpriced, which is
visible in the tally, and `test_a_bare_currency_never_routes_to_a_pair_that_inverts_it` fails the
suite if any 3-letter key is routed to a pair it is not the base of. `EUR`→`EURUSD=X` and
`GBP`→`GBPUSD=X` pass, because those currencies *are* the base.

**The real prize is `JPY`: 13 theses, currently unrouted and therefore invisible** — Capital
Flows, Checkmate, Traders Reality, Raoul Pal, Benjamin Cowen, TraderMayne. Yahoo's `JPY=X` *is*
USDJPY, so routing it without inverting would create 13 wrong-direction theses in a single edit.
That is precisely the trap "they're the same thing, merge them" walks into, and it is why §28's
merges were verified row by row instead of applied by pattern.

**Shape when built:** an explicit `invert: true` on the routing entry, consumed where direction
is resolved — not a negated price series, which would break every level, zone and structural
target. Note this makes `direction` a function of the route, which nothing else in the engine
assumes today, so it needs its own tests rather than a flag bolted onto `route.py`.

**Do not "fix" this by adding a synthetic inverted series.** Order blocks, the dealing range and
`structural_target` are all computed from bars; inverting the bars would invert every level too
and produce zones that look plausible and are wrong. The inversion belongs on the *thesis
direction*, at the single point where a route is applied to a row.

**Deprioritised by Tegan 2026-07-28:** "I don't trade much forex so ok to log that for now." So
the 13 JPY theses stay invisible until FX matters — but they are a known, quantified supply gap
rather than a mystery, and the guard test means nobody re-creates the bug by accident.
