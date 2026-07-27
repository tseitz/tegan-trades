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
work that accelerates it. **Superseded 2026-07-27 — (b) is cleared, see below.**

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

### Second session · 2026-07-27 — the blocker is cleared, and the scorer does not predict the decision

`data/setups/decisions.jsonl` (v2) holds **14 rows over 14 distinct candidates**: 7 `rejected`,
3 `approved`, 4 `later`. All 14 carry `score_version: 2` and every ranker field, so defect (a)
does not recur. **(b) is cleared** — rejections exist, all 7 carry a reason, and all 7 carry a
free-text `reason_note` (added 2026-07-27, `f04fff3`).

**The correlation §4 has been waiting for is now runnable, and the first look is negative.**

| verdict | n | score min / median / max | freshness |
|---|---|---|---|
| approved | 3 | 0.596 / 0.734 / 0.756 | 0.70, 0.98, 0.98 |
| rejected | 7 | 0.595 / 0.741 / 0.777 | 0.00–0.88, six of seven ≤ 0.54 |
| later | 4 | 0.564 / 0.616 / 0.648 | 0.42–0.84 |

**a. `score` does not separate approve from reject.** The two distributions overlap almost
exactly and the rejected *median is higher*. The highest-scoring candidate of the session —
MON at 0.777 — was rejected, and the lowest approval (HOOD 0.596) sits 0.001 above the lowest
rejection (RIVN 0.595). With n=3 approvals this cannot support a strong claim, but the shape is
"no signal", not "weak signal", and that is the thing §4 existed to find out.

**b. `freshness` does separate — and it is the smallest weight in `_score` (0.15).** All three
approvals are ≥ 0.70; six of seven rejections are ≤ 0.54. The one exception, OIL at 0.88, was
rejected for the *asset* rather than the setup (note: "Same as CL"). Nothing else — proximity,
depth, agreement — tracks the decision this cleanly. Ties directly to §6.

**c. The notes say why, and the three-way enum could not have.** Of 7 rejections:

- **3 are staleness** — MON "Stale, the levels are a bit all over the place… may refer to the
  time it was called (april)", PUMP "Very stale", RIVN "Very stale at this point". Consistent
  with (b), and the reason they are legible at all is the note: two of the three were filed
  under different enum values.
- **3 are asset-level disinterest, not setup quality** — CL "not sure I'm shorting oil at these
  prices with the Iran conflict going on", OIL "Same as CL", PNUT "Zero interest in PNUT".
  These are **not scorer signal** and pooling them with trade-quality rejects would poison the
  correlation. Two were filed `other`, one `view_wrong`; the enum has no bucket for "I don't
  trade this asset", which is why the free-text field was worth adding.
- **1 is genuine setup quality** — PLUME "That exit is pretty high and I'm not sure I see the
  structure you're referring to here" (target/structure doubt).

**What this argues for**, per the gate/score rule: "Zero interest in PNUT" is a rule a human
would write, so it wants a **gate** — an asset exclusion list — not a score term. Staleness is
a continuum, so it stays a score, but underweighted. Do not build both at once, and re-measure
after each.

**The gate shipped 2026-07-27** (`cfg/exclusions.yaml`, `oracle/exclusions.py`). It was more
urgent than it looked: `drop_decided` keys on the *zone*, so rejecting a candidate buries one
order block and the next to form on the same instrument asks again — OIL and CL were back on 4
of the 59 undecided v3 rows the same day they were rejected, one at score 0.711. Seeded with
PNUT alone, deliberately: "not sure I'm shorting oil at these prices with the Iran conflict
going on" is a *conditional* pass and belongs in the sidecar as a rejection, not here as a
standing rule. The two are indistinguishable mechanically, which is why the list is
hand-curated rather than mined from `reason_note`.

**The freshness re-weight is still unbuilt, and should stay that way until v3 is measured.**
It is now the *second* pending scoring change behind §16, and shipping it before the next
session would leave two changes and one measurement.

**Measured 2026-07-27 across v3/v4/v5 — and it should stay unbuilt for a better reason now.
See §20.** The separation reported here and in the session below is a *daily*-population
effect; the weekly population is ordered by `approach` instead, and four of five terms sit at
or below chance on it. The re-weight is not "pending", it is refused in its global form.

**d. `reward_risk` and `price` were never recorded · `FIXED 2026-07-27`.** `decision_record`
wrote neither. R:R is a weighted term in `_score` *and* the headline number in the queue, so its
weight was the one thing decisions could not be mined against at all; `price` fixes a decision
to a market state, without which "was this judged at the zone or halfway to target" is
unanswerable once prices move. Both are now written. **The 14 existing rows lack both and must
not be backfilled** — a re-run yields today's values, not the decision-time ones, which is the
same trap as (a). This is the third shape change to the sidecar; `score_version` still covers
scale comparability, and these two are additive, so no version bump.

**Next:** accumulate a second real session before acting on (a) or (b) — n=3 approvals is not
enough to re-weight against. When acting, change one term and re-measure; the sidecar now
records enough to tell whether it helped. **Partly overtaken 2026-07-27 — mining (a) found a
defect rather than a weighting problem. See §16, and re-read (a) with this correction:**

**Correction to (a): the wash was cancellation, not absence of signal, and one term was
measuring the wrong thing.** Broken into components, three of the four recorded terms separate
approvals from rejections in the right direction — `freshness` 0.884 vs 0.406, `agreement`
3.67 vs 2.14, `trend_alignment` 0.667 vs 0.286. `proximity`, the *heaviest* weight at 0.25,
ran backwards: 0.523 approved vs **1.000** rejected, with all 7 rejections pinned at exactly
1.00 and no candidate below 1.00 ever rejected. `corr(proximity, freshness) = −0.599`, so this
is (b) seen from the other side rather than a second finding. Net of the two, `score` came out
0.696 vs 0.695.

**A caveat that matters for how much weight to put on the above:** `depth` (0.15) was never
recorded, so the correlation could only ever see part of the ramp. Reconstructing the residual
`0.15·depth + 0.20·rr_term` from the stored scores gives **0.243 approved vs 0.246 rejected** —
no signal in the hidden half either, and not separable into which of the two terms carried it.
That gap is closed going forward: v3 records `approach`, which is the whole ramp.

### Third session · 2026-07-27 — 25 rows on v5, and `archived` has quietly become the reject key

The sidecar is now **54 rows: 14 v2 · 8 v3 · 7 v4 · 25 v5**. The v5 pass is larger than every
earlier session combined and is the first *mixed* one (18 daily / 7 weekly); v2, v3 and v4 were
weekly-only runs, which turns out to matter more than the row count — see §20, which is the
real result of mining this session and which **refuses the freshness re-weight** this entry had
queued up.

Verdicts: **10 approved · 8 archived · 6 later · 1 rejected.**

**`rejected` is scarce again, and this time the negatives went somewhere that records nothing.**
`_ask_reason` fires only on reject (`setups_cli.py:448-451`), so `archived` writes no `reason`
and no `reason_note`. Eight permanent suppressions this session carry no stated why — and
§4(c) is explicit that the notes, not the enum, are what made the last session's rejections
legible.

**Confirmed with Tegan 2026-07-27: `x` was used for a mix of both meanings** — some "I don't
trade this asset", some "stale/bad, bury it". So those 8 rows are **not minable and cannot be
made minable retroactively**, for the same reason §4(a) refuses backfilling: the meaning was
never recorded and a re-run cannot recover it. They are counted as negatives in §20 with that
contamination stated at every point of use.

Three consequences, in increasing order of how much they cost:

- **The vocabulary no longer matches usage.** `ARCHIVED` is documented as "explicitly NOT a
  judgment", and §4's mining rule therefore excludes it — which would discard 8 of the 9
  negatives. Excluding them leaves n=1.
- **Archive doesn't do what "suppress permanently" implies.** `drop_decided` keys on
  `candidate.key`, the *zone* — so archiving PENDLE buries one order block and the next PENDLE
  zone asks again. That is precisely the defect `cfg/exclusions.yaml` was built for a day
  earlier (§4, the gate), and archive does not feed it.
- **It recurs every session until the prompt changes.** This was the cheapest fix in the entry
  and the only one that compounds.

**Do not derive `cfg/exclusions.yaml` entries from these 8 automatically** — that file's own
header explains why, and the mix confirmed here is exactly the case it warns about: a temporary
reservation promoted into a permanent rule silently deletes a market from the queue for good.

### Archive now asks which kind it is · `FIXED 2026-07-27`

`x` prompts `[a]sset (never show this asset) / [s]etup (just this zone)`, records the answer as
`reason` (`ARCHIVE_ASSET` / `ARCHIVE_SETUP`) alongside the note, and — for an asset-level
archive — appends the asset to `cfg/exclusions.yaml` with the note as its required reason. That
is the half that makes it *stick*: `drop_decided` keys on the zone, so before this an
asset-level archive buried one order block and the next one asked again.

**This is not the automatic derivation `exclusions.py` refuses**, and the distinction is worth
keeping straight: that rule is about reading permanence out of free prose, and here the prompt
*asks* and you answer. The reason you type is the entry's reason; nothing is inferred.

Four deliberate choices, each of which could have gone the other way:

- **Unrecognised input falls to `setup`, not `asset`** — the opposite default from `_ask_reason`,
  which falls to `other`. There every branch is inert; here one branch removes a market from the
  queue permanently, so the ambiguous case must land on the harmless side.
- **The exclusion write is subordinate to the sidecar and never raises.** Missing file, missing
  reason, unwritable path and already-excluded all record the decision and print what happened.
  A blank reason writes *nothing*, because `load` refuses reason-less entries and a bad write
  would turn a mistyped prompt into a queue that cannot start.
- **All four outcomes print**, including the ones that did nothing. A gate everyone believes is
  running and isn't is the §6h failure class exactly.
- **The append is textual and verified in memory before it lands**, so the ~35-line header —
  where the rejection-versus-exclusion distinction is actually written down — survives, and a
  file that gates the whole queue is never left half-edited.

Verified end-to-end against the real `cfg/exclusions.yaml` shape: asset-level wrote and quoted
the entry, setup-level left the file untouched, a blank reason recorded the decision and skipped
the write, and re-archiving an already-excluded asset kept the committed reason.

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

## 17. The nightly's `failed` count was never zero, so it could not report anything · `FIXED 2026-07-27`

The 06:30 run reported `10 failed` and **every one was expected**: 6 with captions disabled, 2
deleted, 2 livestreams that had not aired. A counter that always reads ten cannot report the
eleventh. That is exactly the silent-failure shape §6 asks the nightly to design against, and it
had been sitting in the summary line the whole time.

The cost was never the network — none of these reach the retry backoff, because the exceptions
marking them permanent are not `RequestException` and so bypass `_TRANSIENT` on the first
attempt. The cost was the signal.

**Split into three, `ingestion/deadletters.py` + `ChannelResult`:** `dead` ("never going to
work, stop asking", recorded in `data/transcripts/_dead.json`), `pending` ("not yet, ask again
tomorrow"), `failed` ("nobody expected this"). Live result, verified twice:

    TOTAL: 0 ingested, 818 skipped, 107 stale, 5 dead, 4 pending, 0 failed

Second pass byte-identical — dead videos are counted, not silently dropped, and not re-fetched.

### The live run found a defect the unit tests did not · worth not re-deriving

The first version buried anything raising `TranscriptsDisabled`. TTrades' **`Je7cd9HJUBE`
("Morning Q&A", published 2026-07-27) raised exactly that in the 06:30 nightly and ingested
cleanly at 11:00 the same day** — YouTube generates automatic captions hours after an upload.
That rule would have permanently discarded a same-day video from an active roster member.

Hence `CAPTION_GRACE_DAYS = 2`: a missing transcript on a video younger than the grace is
`pending`, not dead. An unknown publication date counts as young, because burying on a fact we
don't have is the same mistake.

**Metadata-side failures are never buried at all, and the asymmetry is deliberate.** yt-dlp
reports them as prose on a `DownloadError` *before* hydration succeeds, so there is no
`published_at` to age-gate against — the thing that makes a transcript verdict safe cannot be
applied. Both "This video is not available" and "This live event will begin" route to `pending`
and retry forever. Cost: two wasted hydrates a night. Alternative: discarding a video for good
because yt-dlp reworded something. `failed` still lands on zero, which was the point.

`_IRMBuen60Y` and `VXL1FPbgW7E` are genuinely deleted (§13) and will therefore be retried
nightly forever. Accepted knowingly — the registry is for things we can prove are permanent
*and* age-check.

### The nightly is spread across three hours of sleep, and only partly fixable

Same run, from `pmset -g log`: launchd fired the missed 06:15 job at **06:30:03 during a
DarkWake** — not on lid-open — then the machine stayed shut on battery, surfacing for ~2-second
darkwake slices every ~15 minutes. The lid opened at **09:38:04**; the job finished at **09:50**.
So `ingest-roster (8881s)` is 2h28m of wall clock over a few minutes of work, and the run
effectively completed *because* the laptop was opened.

The plist now wraps the script in `caffeinate -s -i -m`. **This only helps where macOS allows
it**, and the limits are policy rather than configuration:

- `-s` (prevent system sleep) is **only honoured on AC power** — caffeinate(8) says so outright.
- `-i` (prevent idle sleep) works on battery, but **a closed lid is not idle sleep**.

So: plugged in → completes in minutes at 06:15. Lid open on battery → likewise. **Lid closed on
battery → still crawls, and no setting changes that.** Leaving it plugged in overnight is the
actual fix. The flags cost nothing when they can't be honoured.

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

**(d) `reward_risk` *rewards* the same distance · `OPEN` — the load-bearing one.**
`R:R = |target − entry| / zone height`. With a structural target — the post-break extreme — the
numerator is literally *how far price ran away from the zone*, while the denominator is a fixed
candle height. Distance inflates it. And it saturates at `RR_SATURATION = 3.0`, so SPX's 9.06,
CLSK's 10.17, CL's 14.54 and TSLA's **23.24** all collect the full 0.20, indistinguishable from
a genuine 3.0. SPX's decomposition: approach 0.000/0.40, and **0.593 of the 0.60** available
from the other four terms — near-perfect, with a chunk of it earned by being unreachable.

The module already knows the number is a symptom — the `WEEKLY` docstring calls a 14.19 R:R
"a symptom of a broken denominator, not a good trade" — but nothing acts on it. Candidate fixes,
unmeasured: measure reward from *price* rather than entry; or treat R:R above a ceiling as the
symptom it is rather than a maximum.

**(e) Weekly-first ordering concentrates them at the top · `OPEN`.** `collapse` sorts weekly
before daily unconditionally. Weekly zones are systematically the far ones — mean gap **28.2%**
for the unreachable set vs **3.7%** for the rest. The live queue put CL (21.3% away, score
0.297) at #7 and TSLA (2.0% away, score **0.906**) at #8. A 0.61 inversion, by rule. The rule is
defensible on its own terms ("the macro is much stronger"); what was never measured is that it
promotes precisely the unreachable population.

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
§4's sidecar, not picking by argument.

---

## 16. A zone price had traded clean through scored *maximum* depth · `FIXED 2026-07-27`

Found by running §4's correlation, which is why §4's "accumulate another session first" was the
wrong call: the mining pass was worth doing for what it revealed about the *scorer*, not for the
n it could support.

**The defect.** `OrderBlock.depth_at` delegated to `position_in_range`, which **clamps**. For a
bullish block, price below `bottom` clamped to position 0.0, so depth came back `1.0 - 0.0` =
**1.0** — the maximum — for a zone price had abandoned. Nothing gated it either: `wrong_side_of_
range` is about the dealing range, a different object, and zones only die at `invalidation`,
which sits *past* the stop.

The clamp is correct for its other caller. Premium/discount genuinely does want "a price beyond
the range is at the extreme". Read as depth it inverts, so the fix is a containment check in
`depth_at` and **not** a change to `position_in_range`.

**Measured on the live queue, 2026-07-27 (`scripts/probe_zone_side.py`, re-runnable, free):**

| | before | after |
|---|---|---|
| candidates | 82 | **69** |
| price approaching | 47 (57.3%) | 47 (68.1%) |
| price inside the zone | 22 (26.8%) | 22 (31.9%) |
| **price past its own stop** | **13 (15.9%)** | **0** |

`depth == 1.0` held in exactly 13 of 82, and those 13 were precisely the wrong-side set —
nothing legitimately rests at the far edge, which makes that value a clean diagnostic. They
**outscored everything else** (mean 0.621 vs 0.562) and took ranks 2, 9, 11, 13, 22, 23, 29, 33,
41, 53, 54, 67, 75. `GBPUSD long` was **#2 of 82** with an entry *above* spot. The extremes were
not marginal wicks: APP +27.6%, TSLA +14.0%, AMZN +13.4%, MSFT +10.4%, ASML +9.9% past entry.

**Two changes, one pass, both in `SCORE_VERSION` 3:**

- **`price_past_stop`**, a new zone-level refusal. `block.stop` is the far edge, so price beyond
  it means an entry at `near_edge` is already stopped. A **gate**, per the gates-vs-scores rule:
  whether the stop has been taken is a fact about the trade, not a measurement. It fires 100
  outcomes → 13 candidates, and sits *after* `degenerate_zone` because a zero-height zone puts
  every price on its far side.
- **`proximity` + `depth` → one `approach` term** at their combined 0.40. They were never
  independent — disjoint domains, so neither varied while the other did, and together they
  already formed one monotone ramp. `ARRIVAL = 0.625` reproduces the old 0.25/0.15 split
  exactly, so the collapse changed the signal's *shape* and not its calibration, keeping the
  measured effect attributable to the gate.

**Why the collapse was worth doing rather than deferring as cosmetic** — an earlier draft of
this said it was a pure refactor with zero behaviour change. That was wrong, and the reason is
the point: the two-term form could express `proximity 0.00, depth 1.00` — "no progress toward
the zone" and "all the way through it" at once. TSLA, MSFT, AMZN and APP all carried exactly
that pair. A single ramp cannot be written down that way.

**Side effect worth noting:** `approach` now takes 52 distinct values across 69 candidates,
where `proximity` was pinned at 1.00 for every one of the 22 inside their zone. The recorded
field discriminates where it previously could not — see the §4 caveat.

**Not fixed here, deliberately:** the queue is still ordered by a scorer that has never been
validated, and `freshness` is still 0.15 despite being the cleanest separator in the sidecar.
One change, then re-measure. §4's next session is now the measurement.

**That measurement happened 2026-07-27 and refused the change — see §20.** `freshness` is the
cleanest separator on the *daily* population only; on the weekly one it is near-chance (AUC
0.583) and `approach` is the separator instead. Raising it globally would help one population
and hurt the other. Do not ship it off the sweep in `scripts/probe_freshness_weight.py`; the
sweep has no interior maximum, which is the artefact rather than the answer.

---

## 20. One weight vector is ranking two populations that disagree about which term matters · `OPEN` — new 2026-07-27

**This blocks the freshness re-weight that §4, §6 and §16 all queued up as the next change.
Do not ship that re-weight globally.** Found by going to measure *how far* to raise it.

Reproduce with `scripts/probe_freshness_weight.py` — free, local, re-runnable, reads the
sidecar only. It replays the shipped scorer exactly (`max |recomputed − stored| = 0.00e+00`),
so the numbers below are the real ranker and not a model of it.

**The statistic is AUC** (P(a random approval outranks a random negative); 0.5 is a coin flip,
**below 0.5 is ordering backwards**). Chosen over the mean gap because the queue is consumed as
an *ordering* and because a mean gap can look healthy while the distributions interleave —
which is exactly how §4's first correlation read as "no signal" when it was two terms
cancelling. This entry is that same lesson one level up.

### The finding

| population | n (appr v neg) | approach | freshness | agreement | reward_risk | trend_align |
|---|---|---|---|---|---|---|
| **weekly** | 11 v 17 | **0.738** | 0.583 | 0.455 | 0.456 | 0.463 |
| **daily** | 9 v 4 | **0.333** | **0.861** | 0.444 | **0.833** | 0.597 |

`approach` orders the weekly queue and runs **backwards** on the daily one. `freshness` orders
the daily queue and is near-chance on the weekly one. They swap, and the scorer has one
`SetupWeights` for both.

**The mechanism is visible in the spread, and it is the §16 lesson again — a term that doesn't
vary cannot discriminate:**

| | n | min | median | max | sd |
|---|---|---|---|---|---|
| weekly `approach` | 18 | 0.000 | 0.240 | 0.893 | 0.301 |
| daily `approach` | 13 | **0.464** | 0.628 | 0.915 | **0.146** |
| weekly `freshness` | 28 | 0.005 | 0.434 | 0.976 | 0.300 |
| daily `freshness` | 13 | 0.082 | 0.755 | 0.968 | 0.250 |

Daily zones are all *near* price — half the spread and a floor at 0.464 — so `approach` has
almost nothing to say there. Weekly `freshness` has plenty of spread but doesn't track the
decision, because the weekly rejections are §19's unreachable ones, which are *fresh* and
heavily agreed: SPX rejected at freshness 0.955 with 12 people, OIL at 0.968 with 8. Those two
notes are explicit that distance, not staleness, was the reason — "the entry is absurdly low
relative to the price", "Price now is greater than target".

**So the pooled sweep is a cancellation artefact.** Pooling both timeframes, AUC climbs
monotonically with the freshness weight and never turns over — 0.856 at the current 0.15,
0.911 by 0.50, flat thereafter. The data's unconstrained answer is "weight freshness at 1.0",
i.e. rank the queue by recency and delete structure from the ordering. A sweep with no interior
maximum is the tell that one weight vector is being fitted to two populations.

### Two more saturated terms, found the same way

Both are the `RR_SATURATION` shape §16 already fixed once for `proximity`/`depth`:

- **`agreement_signal` caps at 3 and the recorded counts run to 12.** It is pinned at 1.0 for
  **12 of 13 daily rows** and 9 of 28 weekly, which is why it sits at 0.455/0.444 — below
  chance on both — despite raw counts that look separated (v5 approved mean 6.1 vs 2.9).
  §11 asks whether agreement should be recency-weighted; this says the cap is the more urgent
  half, because at n≥3 the term currently carries no information at all.
- **`reward_risk` splits exactly the way §19(d) predicts**: 0.833 on daily, 0.456 on weekly,
  pinned at 3.0 for 12 of 18 weekly rows. That is direct evidence for §19(d)'s claim that
  distance inflates R:R — the term is meaningful where zones are near and noise where they are
  far. It also means "R:R is broken" is too broad; it is broken *on the far population*.

### Caveats, all of which cut against acting fast

- **n is small everywhere.** The daily arm is 9 approvals vs 4 negatives — 36 pairs.
- **Timeframe is confounded with session.** v2, v3 and v4 were weekly-only runs and v5 was
  mixed, so "weekly" is largely the earlier sessions. v5 splits the same way internally
  (daily freshness 0.861 / approach 0.333; weekly freshness 1.000 / approach 0.800) but its
  weekly arm has **one** approval, so that within-session control is thin. **The cheapest way
  to break the confound is one mixed session, not more code.**
- **`archived` is mixed evidence** — confirmed 2026-07-27 as part asset-disinterest, part
  staleness — and it is 8 of the 9 v5 negatives. `archived` alone scores AUC 0.887 against
  approvals while the single `rejected` row scores 0.600. One archived row, CRV, is the
  dominant inversion under current weights: it outranks 6 of the 10 approvals on its own, and
  dropping it lifts the composite v5 AUC from 0.856 to 0.912.

### What to do

Not obvious, which is why this is `OPEN`. Ruled out: a global freshness raise — it would help
the daily rows and hurt the weekly ones, and §19(e) puts weekly at the *top* of the queue via
the unconditional weekly-first sort, so the damage lands where it is most visible.

The live options, none measured:

1. **Per-timeframe weights.** Honest about what was measured, and `SetupWeights` is already a
   dataclass so `WEEKLY`/`DAILY` variants are cheap. Costs the ability to compare two scores
   across timeframes — which §19(e) shows the queue is already doing badly by rule.
2. **Fix the saturations instead of the weights.** Three of five terms are pinned for much of
   the queue. A term restored to varying may separate on both populations, which would make
   the split unnecessary. Strictly less invasive and addresses a defect rather than fitting to
   n=13.
3. **Score the two queues separately** rather than collapsing them, which subsumes §19(e).

### The sample doubled the same day, and almost all of the above dissolved · 2026-07-27

Two more sittings added 23 daily decisions (48 v5 rows: 19 approved, 16 negative, 13 later).
**Every headline number above fell, and the probe now prints 95% bootstrap intervals because
the point estimates were being over-read — including by me, in the tables above.**

| term (v5 pooled, 19v16) | first sitting only | after both sittings |
|---|---|---|
| `score` | 0.856 | **0.671 [0.48, 0.84]** — includes chance |
| `freshness` | 0.922 | **0.727 [0.54, 0.88]** — the only term that clears it |
| `agreement` | 0.722 | 0.627 [0.46, 0.79] |
| `reward_risk` | 0.672 | 0.618 [0.45, 0.78] |
| `approach` | 0.678 | 0.559 [0.36, 0.75] |
| `trend_alignment` | 0.367 | 0.408 [0.25, 0.58] |

The first sitting's 0.856 had a CI of [0.66, 1.00] — consistent with almost anything above
chance. It was one sitting read as a result. **The per-timeframe table's cells now all carry a
'?': the weekly-versus-daily split above does not survive its own intervals.** Treat §20's
central claim as *unproven*, not disproven — the point estimates still differ in the direction
described, but 11v17 and 18v11 cannot establish it.

**The real finding is that the measurement design is broken, not just underpowered.** A sitting
is not a random sample of the queue. The queue is score-ordered and capped, so the first sitting
spans a wide score range and each later one works a narrower slice of the tail — and a term
cannot order what barely varies:

| sitting | n | score range | `score` AUC |
|---|---|---|---|
| 18:38 | 10v9 | 0.397–0.812 | 0.856 [0.66, 1.00] |
| 19:53 | 3v3 | 0.540–0.580 | 0.222 [0.00, 0.67] |
| 20:00 | 6v4 | 0.403–0.527 | 0.458 [0.04, 0.88] |

The only sitting whose interval clears chance is the only one with a wide range. That is
restricted range doing the work, not the scorer improving and then failing.

**And the approval threshold moves between sittings.** The later sitting approved candidates at
a median score of 0.484 while the earlier one had *rejected* at a median of 0.518 —
`AUC(later approvals > earlier negatives) = 0.444`. "Approved" is not an absolute quality label;
it is relative to what else was on screen that sitting. **So decisions cannot simply be pooled
across sittings and treated as one labelled dataset, which is what §4's whole revealed-preference
programme has assumed.** Partly confounded — most of the earlier sitting's negatives are
`archived`, which is mixed evidence — so this is a strong hypothesis rather than a settled
finding, and the clean test is a sitting with a wide score range and real rejections in it.

**What this changes:** the freshness re-weight stays refused, now for a better-supported reason.
And §4's next step is no longer "accumulate more decisions" — it is "make the decisions
comparable". Candidates: triage a *randomised* or score-stratified slice rather than the top of
the queue, so range stops tracking sitting order; or record decisions with the sitting's score
range so the analysis can condition on it. Neither is built.

### Correcting the obvious next step — it is not "run a mixed session"

A first draft of this entry said to run one mixed session to break the confound. **Both halves
of that are wrong, and the reason is itself part of the finding.**

**There is no timeframe filter to avoid.** `setups` has no such flag; the queue is always both.
What made v2-v4 weekly-only is §19(e)'s unconditional weekly-first sort meeting the default
`--limit 25`: when the weekly pool is at least 25, the visible queue *is* the weekly pool and
nothing else is reachable. So the confound is structural, not a session choice — the ranker
decides which population gets judged, and it has been feeding itself weekly rows.

**And the next session will not be mixed either — it will be all daily.** Measured on the live
queue, 2026-07-27: `uv run setups --list --limit 0` returns **23 candidates, every one daily,
zero weekly**, because v5 decided all 7 weekly rows and `drop_decided` removes them. The
population flipped entirely between one session and the next.

That is still worth running, but for a narrower reason than "breaks the confound": right now
`daily` is almost exactly `v5` and `weekly` is almost exactly `v2-v4`, so a second, independent
daily session at least stops the daily arm being one sitting. **The weekly arm cannot be
improved this way at all** — it needs weekly candidates to exist, which is a supply question,
not a triage one.

**So the honest reading is that the two populations are measured at different times by
construction**, and that is a stronger argument for options 1 and 3 than any of the AUC numbers
above: whatever the weights should be, the queue currently cannot present both populations in
one sitting, so it cannot be calibrated against both in one sitting either.

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

**Residual — the measurement itself.** Still unrun, and still the point: does
`carry_reward_risk` separate approve from reject better than `reward_risk`? Needs one session
of decisions carrying both. Only then decide whether the existing 0.20 `reward_risk` weight
should consume the adjusted number (that is a term-input correction, not a re-weight, but it
would bump `score_version` to 6).

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

**Measure before weighting.** The scorer already has six terms and §20 refuses a global
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
