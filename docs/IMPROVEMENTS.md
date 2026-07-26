# Improvements & Known Gaps

The backlog. Things found while building that shouldn't derail the thing being built.

**Rules for this file:** an entry earns its place by carrying *evidence* — a measurement, a
count, a real example — not a hunch. If it's just "we could do X better", leave it out. Record
what we know now so a future session doesn't have to re-derive it. Delete entries when done.

Status: `DECIDED` (agreed, not executed) · `OPEN` (real gap, no decision) · `WATCHING` (may not
be a problem; revisit if it bites).

---

## 1. Levels are not the product — sentiment and trust are · `PARTLY DONE`

**Done:** `min_length=1` is dropped, the prompt now says levels are optional and forbids
inventing one, and `TradeThesis` is distinguished from a lean by its `invalidation` alone.

**Residual: the existing corpus was extracted under the old prompt.** All 3,427 stored theses
were produced by a model that had to supply a level, so the fabricated ones are still in
`data/theses/`. The fix only applies to what gets extracted from here. Options: live with it and
let the corpus turn over naturally, or re-distill — which is a full-corpus LLM pass, so read §9
first. Do NOT re-distill just for this; the levels were never the product.

---

## 1b. Original rationale, kept for context

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

### First real session · 2026-07-25 — 7 candidates decided, and neither half is usable yet

`data/setups/decisions.jsonl` holds 10 rows over **7 distinct candidates** (ZEC and NEAR were
each revised). Two defects to fix before the mining pass is worth writing.

**a. Half the rows carry no ranker value — historical only, no code fix needed.** The sidecar
gained `score` / `proximity` / `inside_zone` / `agreement` / `newest_at` / `people` partway
through the session (first row with them: 18:15Z, minutes after `2141c35` landed). The five
earlier rows — including three of the five approvals (GOOGL, SPX, ETH) — have none. §4 correlates
*decision against score*; those rows cannot participate. `decision_record` already writes all six
fields unconditionally, so this cannot recur — don't "fix" it. Backfilling the old rows is **not
clean**: the corpus moved mid-session (ZEC's `newest_at` 07-15 → 07-24, agreement 2 → 3, score
0.665 → 0.790 between 18:02Z and 23:47Z), so a re-run yields today's score, not the decision-time
score. If backfilled, mark it `recomputed_at` — never as captured live.

**b. Zero `rejected` rows — the only verdict designed to calibrate.** Final tally is 5 approved,
1 `later`, 1 `archived`. Per `setups_cli.py:67-91`, `later` is reversible and `archived` is
*explicitly not a judgment*, so neither is a negative label. `rejected` is the one that carries a
reason (`trade_quality` → setups scorer, `view_wrong` → roster trust), and there are none. A
ranker cannot be validated against five positives and no negatives.

Note the two rows spelled `skipped` are **legacy vocabulary**, predating the four-way split; they
are honoured as permanent (`_PERMANENT`) so old passes don't resurface. Don't read them as a
current verdict.

**Next:** nothing to build — accumulate sessions until `rejected` has real rows, then correlate.
Do not start the correlation before then. Note this makes §4 gated on **decision volume**, which
is in turn gated on candidate supply (§5) and thesis freshness (§6) — those are the buildable
work that accelerates it.

**c. The score scale changed on 2026-07-26** (freshness + trend_alignment added, all four
existing weights cut — see §6). Every record now carries `score_version`; the 10 pre-existing
rows have no such field and are implicitly **version 1**. Their `score` values are *not*
comparable to anything recorded after that date, so the correlation must partition on
`score_version` rather than pooling. This is the second time the sidecar has changed shape
mid-life — the field exists so there is no third time that has to be reconstructed by hand.

**d. v1 was archived, not migrated · 2026-07-26.** `data/setups/decisions.jsonl` →
`data/setups/decisions.v1.jsonl`. The sidecar is append-only by design, so the whole file
moved rather than its rows being rewritten. All 7 v1 candidates therefore resurface and get
re-judged on the v2 scale — which is the point: 5 approvals and **zero rejections** were
never usable calibration, and re-judging them with the reject verdict available is worth more
than preserving them. **One knowingly-accepted cost:** the `AI long` archive was
score-independent ("I don't trade this") and comes back once; press `x` again. Building a
carry-forward path for a single row was not worth it.

---

## 4b. The decision sidecars are irreplaceable and unbacked · `OPEN` — new 2026-07-26

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

---

## 5. `trend_state` read only two swings · `FIXED 2026-07-25`

`core.structure.trend_state` compared the **last two swing highs and last two swing lows**.
Fixed by anchoring the comparison `TREND_DEPTH = 2` swings back and requiring each side to
clear `TREND_NOISE_FLOOR = 0.01`.

### It was mis-filed as a supply problem — it was a correctness bug

The entry above framed this as "BTC yields no candidates." That is the *lesser* half. The gate
also pointed **backwards**: **ETH weekly read `uptrend`** on 2026-07-25 off `+3.4%` on highs and
`+0.3%` on lows, while highs were down 50% (4,956 → 2,466) and lows 57% (3,510 → 1,510). Since
weekly sets direction (`setups.py:458`), that verdict **permitted a long on a chart in freefall
— and ETH long was one of the five setups approved that day.** Across 217 cached assets, 16 had
a verdict that inverted under a corrected rule, 11 in the permissive direction. Separately, 13
of 110 decisive verdicts rested on a swing-to-swing move under 1%.

### The fix this entry originally proposed was the worst of the four tested

"Score how many recent swings agree" is **magnitude-blind** — one large drop and one small
bounce cancel. Measured: 28.6% decisive vs 50.7% for the code it would have replaced, and it
still got BTC wrong. Deleted rather than left as a suggestion. *Do not re-derive it.*

| Design | decisive | BTC | ETH | SOL |
|---|---|---|---|---|
| last-two (was) | 50.7% | ✗ | ✗ | ✓ |
| agreement vote (proposed here) | 28.6% | ✗ | ~ | ✓ |
| **anchored `k=2`, 1% floor (shipped)** | **44.7%** | ✓ | ✓ | ✓ |
| least-squares slope over 5 | 57.1% | ✓ | ✓ | ✓ |

**Slope was rejected despite being the most decisive.** Its window keeps structure price has
already left: on HOOD — topped at 154, bottomed at 63, now building higher highs and higher
lows — slope still reported `downtrend` because `150.47` and `139.75` were in its 5-swing
window. Some of its extra decisiveness is confidently wrong. Anchored also stays in the
manifesto's higher-high/higher-low vocabulary, so a verdict is checkable against a chart.

**Depth is the fix; the floor is the junior partner.** No noise floor at depth 1 gets ETH right
at any threshold — it only downgrades `uptrend` to `ranging`. Only reaching past the bounce
makes it `downtrend`.

### Effect

90 of 217 assets changed verdict, 5 formerly inverted. BTC `ranging → downtrend`, ETH
`uptrend → downtrend`, NVDA and TLT `→ ranging` (both genuinely undecided — NVDA's highs are
falling while its lows still rise). Decisive verdicts fell 110 → 97: **the rule is deliberately
less decisive, because accuracy was the goal and some old confidence was false.**

### Watch

- **`timeframe_conflict` rose 41 → 129** in the live run. Both legs use this function, so daily
  and weekly now disagree more often. Expected, but unmeasured — if it keeps climbing it may
  mean depth 2 is too coarse for the daily leg specifically, which has far more swings.
- **The ETH approval of 2026-07-25 is invalidated by this change** and is still sitting in
  `data/setups/decisions.jsonl` as `approved`. See §4 — it also means one of that session's
  five positives is not trustworthy ground truth.
- `TREND_NOISE_FLOOR` is marked TUNE. 1% was chosen to clear the observed sub-1% cluster, not
  fitted to anything.

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

## 6g. One missing `invalidation` discarded a whole document · `FIXED 2026-07-26`

**Fixed.** `extract.py` validates theses **one at a time** (`TypeAdapter(ExtractedThesis)`)
instead of validating the whole payload, so a bad row is dropped and counted while its
document survives. `distill-roster` reports the count — `TOTAL: … N theses dropped`, and a
`~ <doc>: dropped thesis[i] invalidation: …` line per drop.

**The retry rule changed with it, deliberately.** All-rows-invalid still retries: that points at
the prompt or the schema rather than one awkward call. One bad row among good ones does not —
the document is usable, and re-asking would cost a second call to lose the same row again.

**Honest caveat: the fix is unit-tested but has not yet fired on live data.** It hit twice in
two days (below), but the second occurrence was rescued by an accidental duplicate
`distill-roster` invocation, whose retry happened to produce a valid extraction — so that row
was transiently malformed, not unsalvageable. The next genuine occurrence is the real proof.

### The two occurrences that motivated it

- **Capital Flows `udGgR-6lyCQ`**, 2026-07-26 trial-cohort pass: `theses.3.trade.invalidation`
  was `None`; the whole video's extraction was rejected. 124 distilled, 1 failed.
- **krillin `x/LSDinmycoffee-2026-07-24`**, first nightly cycle: `theses.0.trade.invalidation`
  was `None`.

Same defect §1 recorded for `key_levels` `min_length=1` — "13 of the 98 re-distill failures
were exactly this constraint rejecting a whole video's extraction because one call had no
explicit level" — surviving in a different field after that one was fixed.

`invalidation` being required on a `trade` is *correct* and deliberate (architecture.md: "a
thesis without an invalidation is incomplete"). The defect was that validation was
**batch-atomic**. Expected to bite hardest on X, where a chart post carrying drawn levels but
no stated stop is the ordinary case rather than the exception.

---

## 6f. 429 rows are about things the oracle cannot price, and most of them are computable · `OPEN` — new 2026-07-26

Adding four voices took the corpus from 3,851 to 4,471 rows but candidates only from 49 to 53,
because unpriced assets went **128 → 201**. 152 distinct assets, **429 rows**, route nowhere.
They sort into four groups, and only one is genuinely hopeless:

| group | examples | rows | verdict |
|---|---|---|---|
| **Ratios** | `ETH/BTC` | 28 | **computable from two series already cached** |
| **Aggregates / dominance** | `BTC.D` 44, `ALTS` 30, `TOTAL3` 15, `TOTAL`/`TOTAL2` 7, `ALTBTC`, `MEMECOINS` | ~110 | derivable from CoinGecko global data we already fetch |
| **Macro series** | `CPI` 11, `FEDFUNDS` 7, `FED_FUNDS_RATE` 5, `FED` 5 | ~28 | free via FRED; context not setups |
| **Private / unpriceable** | `SPACEX` 25, `ANTHROPIC` 3 | ~28 | correctly rejected, leave alone |
| **Sentinels** | `__basket__` 53, `__macro__` 4 | 57 | not assets — the extractor's placeholder for a thesis that isn't about one thing |

**`ETH/BTC` is the cheap win**: 28 rows, and both legs are already in `data/prices/`. A ratio
series is a division, not a new source. `BTC.D` at 44 rows is the single biggest real asset on
the list and is a well-understood computation.

**`__basket__` at 53 rows deserves separate attention** — it is a sentinel, not an asset, and
it is currently counted in the "no price source" tally as though it were a routing failure.
That inflates the number and hides the real gap. Sentinels should be classified before routing
is attempted, the way `unknown_direction` now is.

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

**Separate, unfixed, and cheaper: there is no stop buffer at all.** `OrderBlock.stop` documents
itself as "just past the far edge" (`core/structure.py:324`) but returns `self.bottom` exactly,
and `Candidate.stop == Candidate.entry_bottom` in every row the queue prints. Two consequences:
a wick one tick into the zone is a stop-out, and a fill at the far edge is a zero-risk trade
whose RR is a division by ~0. Whatever `k * ATR` lands on, the buffer is a one-line change and
independent of it.

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

## 11. Agreement is date-blind · `OPEN`

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

**Symptom:** `ingest-roster` returned `0 ingested, 662 skipped, 60 stale, 10 failed`, with every
missing video failing as `ChunkedEncodingError: IncompleteRead(N read, M more expected)` or
`RetryError: too many 429 error responses`. Corpus frozen at 2026-07-23.

**Root cause: the Claude Code sandbox silently bypasses `session.proxies`**, so every transcript
fetch egressed from the *local* IP — the one YouTube blocked during the checkonchain backfill on
2026-07-24. The Webshare account was never at fault and rotation works fine.

The one-line proof — same code, same credentials, only the sandbox differs:

| | Sandboxed | Unsandboxed |
|---|---|---|
| Direct (no proxy) | 97.88.98.212 | 97.88.98.212 |
| Proxied, call 1 | 97.88.98.212 | **189.50.230.176** |
| Proxied, call 2 | 97.88.98.212 | **24.152.70.248** |

Sandboxed, proxied == direct: the proxy is not applied at all. Unsandboxed, two consecutive calls
return two different residential IPs: the proxy is applied *and* rotating.

**Workaround (verified):** run any transcript-fetching command with the sandbox disabled. After
doing so, `ingest-roster` returned **4 ingested, 662 skipped, 60 stale, 6 failed** and all 6
residual failures are the permanently-dead set below.

**Permanent fix — `sandbox.excludedCommands: ["uv *"]` in `.claude/settings.json`, plus a patch to
the global direnv hook. Both are required; either alone does nothing.**

`allowedDomains` was tried and **removed as dead config — it cannot work here.** The sandbox
exports `HTTPS_PROXY=http://srt:...@localhost:63350`, a local filtering proxy. When the code sets
`session.proxies` to Webshare, requests emits `CONNECT www.youtube.com:443` to `p.webshare.io:80`;
that is intercepted, and the sandbox proxy **terminates and re-originates** the connection from
the local IP. Webshare is structurally cut out of the path, so allowlisting `p.webshare.io` only
grants permission to *fetch* it, never to *tunnel through* it.

Excluding the command from the sandbox is therefore the only mechanism that restores the proxy.
It is scoped to `uv` rather than `ingest-roster` because commands are invoked as
`uv run ingest-roster` — the first token is `uv`, so a binary-name entry would never match.
**Consequence, accepted deliberately: every `uv run ...` in this repo now runs unsandboxed.**

#### Attempt 1 (`excludedCommands: ["uv"]`) failed — and why · 2026-07-25

After a restart the probe still returned `direct == proxied`. Two independent defects:

1. **A global `PreToolUse` Bash hook rewrote every command**, prefixing
   `eval "$(direnv export bash 2>/dev/null)" && `. The first token the sandbox matched on was
   therefore always `eval`, never `uv`. Proof: `ps -o args= -p $$` returned
   `(eval):1: operation not permitted: ps` — zsh's error prefix for code run under `eval`.
2. **Entries are command globs, not binary names.** The docs' own example is `"docker *"`. A bare
   `"uv"` would not match `uv run ingest-roster` even without the hook.

Control that isolated defect 1: `mkdir /Users/tseitz/.claude/sandbox-probe-dir` — a bare, exact
first-token match against the **global** `excludedCommands` entry `"mkdir"` — was still denied. So
exclusion was inert for every command, not just `uv`. **Always run a control against an entry you
did not add**; it separates "my config is wrong" from "the mechanism is broken".

**Attempt 2 — `VERIFIED WORKING 2026-07-25`.** The direnv hook in `~/.claude/settings.json` now
emits `{}` (no rewrite) when the command matches `^\s*uv\s`, and the project entry is `"uv *"`.
Harmless here — this repo has no `.envrc`, so `uv` never needed direnv.

Probe result in a fresh session, sandbox ON, no `dangerouslyDisableSandbox`:
`direct 97.88.98.212` vs `proxied 190.233.209.115`. The proxy survives. `uv run ingest-roster`
no longer needs the sandbox escape hatch.

### Second sandbox gap: the vault is a symlink · `FIXED, VERIFIED 2026-07-25`

Writing to `~/vault/Trading/Trade Logs/Setups.md` failed with `Operation not permitted` even
though `~/vault/Trading` was in `allowWrite`. `~/vault` is a **symlink** to
`/Users/tseitz/Obsidian/Main Vault`, and macOS seatbelt matches the **resolved** path — so the
symlink entry granted nothing. `.claude/settings.local.json` now lists both the symlink paths and
the resolved `~/Obsidian/Main Vault/...` ones. Any future vault path must be added in resolved
form. Confirmed working: `touch` succeeded through **both** the resolved and the symlink path.

The probe (proxied must differ from direct):

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

### Why it cost hours to find — three layers of masking

The true error is `IpBlocked`. Nothing ever printed it:

| Layer | Behavior | Symptom produced |
|---|---|---|
| `WebshareProxyConfig.prevent_keeping_connections_alive` -> `True` (`proxies.py:181`) sets `Connection: close` (`_api.py:41`) | YouTube's block page arrives as a truncated chunked body | `ChunkedEncodingError: IncompleteRead` |
| urllib3 `retries_when_blocked` adapter | retries 429 on the **same** connection = same blocked IP | `RetryError: too many 429s` |
| `youtube._TRANSIENT` catches `RequestException` | treats both as flaky, retries 4x with backoff | ~20 wasted attempts, misleading final error |

Byte counts differed on every attempt (1188, 2591, 3880, 6745...), which is what sold the
"proxy truncates mid-stream" theory.

**Disproven leads — do not re-test:**
- *`&variant=gemini` on new uploads.* Present on `JY_wY8XXjYU`, absent on others failing identically.
- *Video-specific / newest-only.* Videos **already in the corpus** (`UIv9IQ4uXEA`, `Tv6DJTNobJ4`)
  failed identically. This is what collapsed the video-specific theory — always test a known-good
  control before believing "the new items are special".
- *Library out of date.* `youtube-transcript-api` 1.2.4 **is** current PyPI latest.
- *Webshare plan/bandwidth.* Rotation demonstrably works outside the sandbox.
- *Proxy can't handle large/chunked bodies.* 837KB chunked+gzip succeeded 4/4 with keep-alive.

**Do NOT "fix" this by overriding `prevent_keeping_connections_alive -> False`.** It unmasks the
real error, but the library sets it deliberately (`proxies.py:39`: without it "your IP won't be
rotated"), so it trades a masked failure for broken rotation once egress is correct.

**Worth building anyway:** a preflight that probes the exit IP across 2-3 fresh sessions and
aborts loudly when they're identical (proxy not applied) or block-flagged. The `TranscriptBlocked`
abort path already exists and is the right destination — it just never fires, because the block
never arrives as `RequestBlocked`. That turns this entire investigation into a 5-second error.

### Genuinely dead, independent of all the above

Captions disabled: `MvD7fQQ0szE` `Nlw-PZhoViQ` `S_obDkmaf8I` `duXvzmQVZ1Q` `ufwa9Ld47Jo`.
Deleted: `_IRMBuen60Y`. Correctly skipped, permanent — these are the 6 residual failures.


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
