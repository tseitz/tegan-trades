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
down and the roster is majority-long. Correct behaviour, zero new candidates. **There is no
big supply lever left** — the honest answer is the nightly job and the 20 `later` rows
resurfacing, both on the market's clock. Plan around that rather than looking for another one.

| | Entry | Why now | Cost |
|---|---|---|---|
| 1 | **§27** — audit 20 `timeframe_conflict` rejections | 1,012 outcomes discarded by a gate nobody has read a single example from — **and §6f just produced 12 with a hand-verified trend state**, so the worked example is sitting there. | free, local |
| 2 | **§7** — ATR-relative stop padding | Needs `k` measured against the 84-candidate baseline, but nothing gates that measurement. | free, local |
| 3 | **§6f residual** — 25 routed-but-unfetched assets | 27 rows that route fine and were never fetched. `fetch-prices` is tier 🟡 (free). Caveat: several are `needs_validation` guesses (`ZT`, `AUD`, `BCOM`, `BAE`) that may simply not resolve. | free, network |
| 4 | **§4** — one stratified sitting | Unblocks §11's recency half, §18, §19(d)'s open half, §21. Machinery built and the scorer finally worth measuring — but **the queue is empty**, so this waits on the nightly. | your attention, once there is a queue |

**Done 2026-07-28:** §4's sampler and decision context; §11's cap and §19(d)'s distance
inflation, both as `SCORE_VERSION` 6; §6f's grouped unpriced tally and the `ETH/BTC` derived
series. The two terms that structurally could not vary now do, so a sitting is finally worth
holding — which is exactly when it turned out there was nothing left to judge. See §4's
residual.

**Waiting on §4's first stratified sitting, not on code:** §18 (`collapse` rep rule) · §21
(funding weighting) · §11's recency half. Each defers to a sidecar correlation that is now
valid to make and simply has no rows yet.

**Not blocked, but each needs its own measurement first:** §7 (ATR stop padding — measure `k`
against the 84-candidate baseline) · §15 (SMA confluence — does 50W actually mark turns).

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

**But the sitting cannot currently be held, and this is the thing to know before planning
around it.** `uv run setups` prints `Nothing to review`: of 69 candidates, **67 are already
decided** (29 approved · 20 later · 16 rejected · 12 archived across 77 zones) and the other 2
are on excluded assets. Decisions are keyed on `Candidate.key`, which is content-addressed on
the zone, so the `SCORE_VERSION` 6 bump correctly does *not* resurface them — a zone is not
re-judged because the scorer changed, and it should not be.

**So §4 is now gated on queue supply, not on attention or code.** Three things refill it, in
increasing order of how much control you have:

- **`later` rows resurfacing.** 20 of them, and `resurfaces` returns them once price enters the
  zone or another person backs it. Free, but it happens on the market's schedule.
- **Corpus growth via the nightly job.** Also passive, and slow at the margin — adding four
  voices took the corpus 3,851 → 4,471 rows and the queue only 49 → 53.
- **~~§6f~~ — tried 2026-07-28, and it did not work.** `ETH/BTC` was built and prices
  correctly; all 29 of its rows are refused because the weekly trend is down and the roster is
  majority-long. `BTC.D`, the other 44 rows, turned out to need a paywalled history endpoint.
  So the first two paths are the only ones, and neither is on your clock. Plan for the queue
  refilling slowly rather than for a lever that fills it.

**Consequently §11, §18 and §21 are no longer blocked on a fix — they are blocked on a
sitting.** Each still defers to a sidecar correlation; that correlation is now *possible*,
needs no conditioning, and simply wants data. §7, §15, §19(d) and §27 were never blocked, and
measure against candidate counts or price history instead.

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
- **Do not mine the 12 `archived` rows as clean negatives.** Confirmed with Tegan 2026-07-27:
  `x` was used for both "I don't trade this asset" and "stale, bury it". The meaning was never
  recorded and cannot be recovered. `x` now asks which kind it is, so this does not recur.
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

## 7. Stop sanity should be ATR-relative, not a minimum width · `WATCHING` — updated 2026-07-26

A minimum stop width was considered and declined, correctly — score saturation already caps the
ranking damage (RR saturates at 3.0, so 15.75 and 4.67 contribute identically).

The real concern is different: **GOOGL's 5.51 stop is roughly 1 ATR**, which ordinary noise
takes out. RR without survival probability is a half-metric. The right form is `stop >= k * ATR`,
not an absolute width. `Context` already carries ATR, so it's cheap.

**Partly relieved 2026-07-26 by weekly zones** (`core.setups.WEEKLY`). The same GOOGL long now
also surfaces as a weekly candidate with a 32.03-wide stop and RR 2.94, alongside the daily one
at 5.51 and 14.19. Narrow zones no longer *crowd out* wide ones — but they still top the list on
score, because `proximity` and `depth` reward being close to price and a tight zone is the one
price is sitting in. So the ATR check is still the right fix; it just isn't urgent.

**Separate and unfixed: there is no stop buffer at all.** `OrderBlock.stop` returns the far edge
exactly, so `Candidate.stop == Candidate.entry_bottom` in every row the queue prints, and a wick
one tick into the zone is a stop-out. (The docstring claiming "just past the far edge" was
corrected 2026-07-26; the behaviour was always the edge itself.)

**Correction 2026-07-27:** this entry also claimed "a fill at the far edge is a zero-risk trade
whose RR divides by ~0". It cannot happen — `cross_reference` always enters at `near_edge`
(`setups.py:684`), so risk is the zone's full height and the zero case is already refused as
`degenerate_zone`. The ATR argument below is unaffected; only that one consequence was wrong.

**Correction — this is NOT independent of the ATR question, and NOT a one-line change.** An
earlier version of this entry said both; both were wrong.

- A buffer proportional to the zone's own height is the wrong shape: it hands the *tightest*
  zones the smallest cushion. GOOGL's 5.51-wide daily block would get 0.28 while the 32-wide
  weekly block gets 1.6 — backwards, since the tight zone is the one noise takes out. That is
  the same reasoning that declined a minimum stop width above, so the buffer wants the same ATR
  yardstick. One fix, not two.
- It therefore cannot live on `OrderBlock`, which has no access to ATR. It belongs in
  `cross_reference`, where `Context.atr` is in scope and `Setup.stop` is already a distinct
  field from `block.stop`. Roughly: pad the stop, derive `risk` from the padded value, keep
  `block.stop` raw. ~10 lines. Unknown ATR must skip padding rather than fail it, per the rule
  `_reasonable` and `imbalance.is_displacement` already follow.

**Measure `k` before shipping it.** Padding widens risk, so it lowers *every* RR and silently
drops candidates through `MIN_REWARD_RISK`. Baseline to measure against: 84 candidates (30
weekly / 54 daily) at 2026-07-26.

**Known test consequence:** `core/tests/test_setups.py` drives a `degenerate_zone` refusal with
a zero-height block, and `_ctx` defaults to `atr=5.0` — padding would make that zone non-
degenerate and the reason unreachable. `test_every_zone_level_refusal_the_engine_emits_is_
classified_as_one` asserts exact set equality, so it will fail loudly. That fixture needs an
`atr=None` variant.

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
in `packages/oracle/tests/test_queue.py`. **The ordering rule itself is untouched and still
`OPEN` on its own terms** — this was a cap-and-ordering interaction, not an argument about
whether weekly should outrank daily.

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

## 27. `timeframe_conflict` is now the second-largest gate and has never been examined · `WATCHING` — re-measured 2026-07-28

Both legs of `trend_state` use the same anchored rule (`TREND_DEPTH = 2`, `TREND_NOISE_FLOOR
= 0.01`, `core/structure.py:35`), so daily and weekly disagree far more often than before that
rule shipped. The rise was recorded at the time as "expected, but unmeasured" and never
followed up.

| measured | `timeframe_conflict` | `weekly_disagrees` |
|---|---|---|
| 2026-07-25, pre-fix | 41 | — |
| 2026-07-25, post-fix | 129 | — |
| 2026-07-26 (§6 tally) | 798 | 1,617 |
| **2026-07-28 (`setups --list --limit 0`)** | **1,000** | **1,843** |
| 2026-07-28, after §6f priced `ETH/BTC` | 1,012 | 1,860 |

**Read the like-for-like comparison, not the headline.** Against the 2026-07-26 tally — the
only other one taken under the current scoring regime — it grew 1.25x while `weekly_disagrees`
grew 1.14x and the corpus grew too. So this is *not* a runaway; the 41 → 1,000 span crosses two
scoring regimes and is not a trend. The last row is not growth at all: pricing `ETH/BTC` simply
let 29 previously-invisible rows reach the gates. What stands is that it is the second-largest
rejection reason, discarding ~1,000 outcomes, and nobody has looked at a single one.

**Start with those 12 `ETH/BTC` rows, because the trend state is already hand-verified.** The
series is a downtrend on the weekly (0.050 → 0.030 across two years, and both candidate bar
constructions agree) and an uptrend on the daily, which is what refuses all 12 shorts. That
makes it the cheapest possible worked example of the question below: is a weekly-vs-daily
disagreement over a two-year decline and a recent bounce a real conflict, or is
`TREND_DEPTH = 2` measuring two different things on the two legs? One asset, a verified
verdict, no chart-reading required to establish the ground truth.

**The open question is the original one:** is `TREND_DEPTH = 2` too coarse for the *daily* leg
specifically, which has far more swings than the weekly? Anchoring two swings back spans a much
longer stretch of daily history than of weekly, so the two legs may be measuring different
things and calling it disagreement. Cheap to check — sample 20 conflicts and read the two
verdicts against a chart. Free and local.

**Note for whoever picks this up:** a conflict is not necessarily waste. `weekly_disagrees` at
1,843 is the gate working as designed. This entry claims only that 1,000 is a large number
nobody has audited, which is exactly the shape the `price_past_stop` defect turned out to be.
