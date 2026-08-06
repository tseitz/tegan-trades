# Improvements & Known Gaps

**A to-do list, not a journal.** Things found while building that shouldn't derail the thing
being built.

**What an entry is:** what to do next, and why it's worth doing. It must *cite* evidence — a
measurement, a count, a real example — but must not *contain* it. One line of number, then a
pointer to where the full thing lives. **~15 lines is the ceiling.** An entry growing past it
has stopped being a to-do.

**What an entry is not:** what was measured, what was tried, corrections to itself, or a dated
narrative of a session. Findings belong with the thing they describe — a constant's
justification beside the constant, an audit's results in the probe that produced it, a data
caveat in the reader that loads it. Ask *when would someone need to know this* and put it where
they'll be looking. `docs/TROUBLESHOOTING.md` holds runbooks for failures that are fixed but
not update-safe.

**Delete entries when done.** Before deleting: move any live residual into the entry that still
owns it, and make every cross-reference self-contained — entries cite each other by number, in
this file *and from code*, and a deleted number is a dead link. Numbers are never reused.

Status: `DECIDED` (agreed, not executed) · `OPEN` (real gap, no decision) · `WATCHING` (may not
be a problem; revisit if it bites) · `PARTLY DONE` (a residual is named in the entry).

---

## Where to start

The mixed sitting arrived and the first thing it measured was a defect in the scorer, not a
weight: `agreement` and `freshness` are r=0.771 collinear, so 0.35 of the weight buys one
measurement twice (§11). The bottleneck is no longer variety in general — it is that the terms
still waiting each need a *specific* contrast the corpus has not thrown yet: funding history
depth for §21, failed-break states for §27, stated targets for §18.

| | Entry | Why now | Cost |
|---|---|---|---|
| 1 | **§32 residual** — curate the six the gate now refuses | They stopped pricing wrong and started not pricing. `WTI` → `CL=F` folds into `OIL`; `JPY` needs §29 first. | free, local |
| 2 | **§31 residual** — the guard nothing calls | `check_identity` is built and unit-tested; no path supplies it a mark, so a curated typo still places. Plus CI over the curated map. | free, network |
| 3 | **§27 residual** — targets vs the range rule | 68 candidates on a ranging weekly, only 20 target within 2% of the bound the rule names. Read §18 first. Now has §48's geometry behind it. | free, local |
| 4 | **§4** — the `agreement`/`freshness` overlap | The first re-weight with evidence behind it, and it removes a term rather than adding one. Read §11 first. | free, local |

**Still waiting on the corpus, not on code:** §18 (7 stated targets in 47 rows) · §21's
measurement (13 rows carry funding, 4 usable pairs) · §27's option 2 (`daily_trend` varies now,
but the failed-break state it names has n=1).

**By theme:** corpus supply §3 · §6b · §6d · §6f · §6h · §9 · §14 — durability §4b · §24 —
venue and execution §22 · §25 · §30 · §33 · §36 · §39 · §40 · §43 — routing §29 · §31 · §32 · §44 —
scoring §1 · §2 · §8 · §12 · §15 · §19 · §48.

---

## 1. Levels are not the product — sentiment and trust are · `PARTLY DONE`

The roster is for **sentiment** (direction and conviction) plus a **trust score**. Levels come
from elsewhere; take one if stated, never force it. Settled 2026-07-25 — do not re-litigate,
and do not resurrect level-aware grading.

**Residual:** all 3,427 stored theses were extracted under the old prompt that *required* a
level, so fabricated ones remain in `data/theses/`. Either let the corpus turn over naturally
or re-distill — a full-corpus LLM pass, so read §9 first. **Do not re-distill just for this.**

Evidence: 388 of 1,621 live readings had levels only *behind* entry — stops stuffed into
`key_levels` to satisfy the schema.

---

## 2. Rip out the fixed horizon constants · `DECIDED` (needs a replacement first)

The 7/30/180/365-day horizons are unvalidated guesses and should go. They can't just be
deleted — `core.grade.Horizons` and `core.setups.StaleAfter` are load-bearing.

**Candidate replacement: event-based.** A call is live until the same person restates or
reverses on that asset. `triage_cli.collapse_restatements` already computes the grouping and
`core/stance.py` tracks lean changes — no constants, and it answers the real question.

Evidence they can't be rescued: restatement cadence is near-identical across timeframe labels
(swing 11d, position 14d, scalp 28d), so it measures publishing schedule, not view horizon; and
the whole horizon sweep sits inside the bootstrap noise floor.

Interacts with §21, which needs *some* holding period to price carry. Use one global costing
constant if so — explicitly not a horizon.

---

## 3. Take another lap on ICT — mine TraderMayne's courses · `OPEN`

`Trading/_Structure.md` is reverse-engineered from a truncated list plus conversation.
TraderMayne publishes explicit courses and **all 15 episodes are already ingested and indexed**,
so `brain_search(..., person="TraderMayne")` answers questions for $0 today.

Unresolved in the spec: the missing third "thing" (ep 1), how the dealing range is bounded
(ep 4), whether the FVG middle candle must be displacement (eps 6, 8).

**Read those episodes before committing to an extraction pass** — the pass is only needed to
*update the spec*, and may only need to cover what's left. Note it is not a `distill-roster`
run: that prompt is built for trade theses and correctly returns EMPTY on methodology videos.

**The fourth unknown was not a corpus gap — it was the query.** "15m entry trigger — failed
breakdown, reclaim" retrieves nothing; asking what the entry trigger *is* returns phase 3 of the
course in full, at cosine 0.79-0.81. The mechanic, the reversal/continuation stop split, and the
timeframe hierarchy are now written into `Trading/_Structure.md` with citations. Slice 2's layer
3 shipped 2026-08-05 as `core.trigger`.

---

## 4. Revealed preference is the only ground truth · `PARTLY DONE` — absorbs §20

Four scoring systems (`core/rank.py`, `core/grade.py`, `brain/retrieve.py`, `core/setups.py`)
and zero closed loops. Mining `data/setups/decisions.jsonl` — approve versus reject, against
the ranker's own terms — is the only ground truth available.

**"Only" is no longer true: §48 replays the same rows against price for a second, absolute
label.** It cannot re-weight anything yet (2 winners in 44 resolved rows), but it is the loop
this entry says does not exist, and it scores the rejects too.

**The machinery is done:** the queue draws a stratified sample (`oracle/queue.py`), every
decision records what else was on screen, and the reason vocabulary derives scope from cause.

**The mixed-sitting residual is closed** — v7's 23 rows span two sittings with `daily_trend`
taking four values, giving 203 same-sitting pairs against the old 154. `score`, `freshness` and
now `agreement` clear chance; everything else spans it. Read the mining traps in
`oracle/decisions.py` first — they are properties of the data and no test enforces them. Replay
with `scripts/probe_freshness_weight.py`, which reproduces the shipped scorer exactly.

**Next, and the first re-weight with evidence behind it:** `agreement` and `freshness` correlate
at r=0.771, so 0.35 of the weight buys one measurement twice (§11). Resolving that is a
*removal*, not a new term — which is why it clears this entry's bar where adding one would not.

---

## 4b. The decision sidecars are irreplaceable and unbacked · `PARTLY DONE`

**All of `data/` is now mirrored off-machine nightly** by `scripts/backup.sh` — so nothing here
is a data-loss risk any more, only a question of where a record's *authoritative* copy lives.

**`data/setups/decisions.jsonl` is also mirrored** to the vault by `oracle/decisions.py` —
subordinate to the primary, reconciled at startup, `--no-mirror` to disable.

**`data/triage/decisions.jsonl` has no vault mirror**, and it is blocked on a layering question
rather than effort. `distill/triage_cli.py` has its own `record_decision`; importing `oracle.decisions`
would be a backwards dependency (pipeline order is ingestion → distill → brain → oracle), and
`core/` is barred from I/O.

**Pick one before the triage sidecar grows:** accept a small duplicate mirror in `distill`, add
a workspace member for shared file plumbing, or let `distill` depend on `oracle`. The loss is
milder than the setups one — triage records `approve`/`skip` only, with no reason.

---

## 6b. `brain/report.py` keeps its own staleness cliff · `OPEN`

`brain/report.py:21-22` has `_DEFAULT_STALE_DAYS` and its own `STALE_AFTER_DAYS` map. That was
a duplicate of `core.setups.StaleAfter` and is now a *divergence*: setups treats age as a
half-life, brain still treats it as a cliff. The same thesis can be current in one head and
dead in the other, with nothing reporting the disagreement.

Not urgent — the two heads answer different questions and a cliff may genuinely suit a
narrative report. But the constant should live in `core` once, with each head choosing how to
apply it.

---

## 6d. An unreachable channel reports as an up-to-date one · `OPEN`

A target that resolves to *zero videos* prints `0 ingested, 0 skipped, 0 stale, 0 failed` —
identical to a healthy channel with no new uploads. `@RealVision` was recorded `access: ok`,
did not exist, and was found only by testing handles by hand.

**Fix:** zero-videos is a distinct outcome and `active_targets` knows the difference. Count and
report it the way `roster.SkippedPerson` / `unreachable_active` already do.

**Why it matters beyond one typo:** a channel that renames goes quiet permanently and the sweep
keeps reporting fine — the corpus silently loses a voice. Every `access: ok` entry not yet
ingested is unverified in the same way.

---

## 6f. Assets that never reach the gates · `PARTLY DONE`

The unpriced tally is now grouped by cause (`setups_cli.format_unpriced`), which split one
number into four unrelated problems. Two remain:

**Routed but never fetched — 26 rows, the cheap one.** They route fine and `fetch-prices` was
never run for them. Free. Caveat: several are `needs_validation` guesses (`ZT`, `AUD`, `BCOM`,
`BAE`) that may simply not resolve.

**`BTC.D` needs a paywalled endpoint.** Dominance needs BTC market cap over *total* as a series;
`/global` is snapshot-only and `/global/market_cap_chart` is PRO-only. Options: pay (a change of
kind — everything but `ingest-x` bills against the Max subscription), approximate by summing
top-N, or snapshot `/global` nightly and accumulate forward. The last is free and yields no
usable weekly structure for months.

`CPI`/`FEDFUNDS` is free via FRED but is context, not setups — a rate has no order block.

**This is a supply lever, not a data-quality one:** the macro/FX vocabulary is disproportionately
what the two macro voices talk about.

---

## 6h. The roster's channel metadata is unverified and drifts · `WATCHING`

Every `access:` and `status:` value in `cfg/watchlist.yaml` is a hand-recorded research finding
with no expiry. Three were wrong: a channel that doesn't exist, one marked dormant that had
moved to livestreams, and two different people merged into one entry.

`uv run verify-roster` now probes every declared YouTube channel across both tabs and exits
non-zero on disagreement. Roster verifies clean: 16 OK · 1 dormant · 0 problems.

**Still uncovered:** X handles can't be probed for free, so the digest is verified only
structurally. Podcast and telegram feeds likewise.

---

## 8. Evidence-leg retrieval doesn't discriminate on asset queries · `PARTLY DONE`

**Concept queries are fine** — top-1 beat the corpus 99th percentile on all 12 probed, zero
chatter in 60 passages. The original "retrieval is broken" claim used absolute cosine, which
isn't comparable across queries. `scripts/probe_retrieval.py` has the measurement.

**Asset-faceted queries still fail.** "Where is my roster on ETH" returns Discord-advertising
chatter in the top 10. An asset name is a *facet*, not a concept — there is nothing semantic for
cosine to grip.

**Implication for the fix:** facets are better served by filtering (the `assets` column) than by
ranking. A lexical/BM25 leg would help here and is wasted on the concept case. Don't rebuild
retrieval wholesale.

---

## 9. Audit extraction efficiency before considering the API · `OPEN`

**The framing to avoid:** "the direct API is 8–15x cheaper" is true per token and misleading as
a decision rule. `claude -p` runs on the existing Max subscription at marginal cost zero; API
tokens are incremental cash. Switching only wins where volume exceeds what the subscription
carries.

**What matters on both paths is waste** — burning allowance still risks cap hits, and a usage
cap silently killing a sweep has already happened once.

The audit, before any billing change or bulk pass:
- **We send whole transcripts and the corpus is 5.26% signal.** Extractive pre-filtering is the
  single biggest lever. Must stay extractive — abstractive summarising would destroy
  `asset_heard`, `watching` and citation integrity.
- Is the system prompt cached across calls? Do retries resend transcripts?
- Measure the real daily volume of the nightly cycle before pricing anything.

---

## 11. Agreement is date-blind · `PARTLY DONE` — measured 2026-07-31

**Yes for the term, no for the score.** Discounting each voice by its age beats counting heads
(paired +0.103 [+0.020, +0.195] at a 7-day half-life); swapping it into the composite moves
nothing. Sweep, method and the reason both are true: `scripts/probe_recency_weight.py`.

**Do not ship the term on its own.** The gain cannot reach the score because `agreement`
already correlates with `freshness` at r=0.771 *before* any weighting — a sharper agreement
term is a third view of what the scorer already carries twice (§4).

**Residual: the two terms should probably become one**, recency-weighted agreement being the
better candidate to survive. That is a re-weight, so it is gated on §4's mandate. Interacts with
§2 — event-based horizons would give "current view" a definition and stop the half-life being a
free constant.

---

## 12. Slice 2 needs the oracle at sub-daily granularity · `OPEN`

Layers 2–3 of `Trading/_Structure.md` (1H approach, 15m trigger) require a `date` → `datetime`
refactor through `Bar`, `PriceSeries`, `cache` (granularity in the key), all three sources, and
`core/grade.py`.

**Done halfway it corrupts silently** — `PriceSeries.__post_init__` dedupes on `bar.date`, so 24
hourly bars for one day collapse to 1 with no error.

The granularity needed is **900s**, not just 1H/4H. Coinbase supports it; its cap is
`MAX_CANDLES = 300`.

---

## 14. X/Twitter ingestion is decided but entirely unbuilt · `DECIDED` (zero code)

Grok `x_search` was chosen over the official X API on cost — ~$1–5/mo metered against $200/mo
for the cheapest tier that allows the reads this needs.
`cfg/watchlist.yaml` already encodes the intent — 17 channels marked `access: grok` and an
`x_grok_digest:` list. **Nothing reads that key.**

**What it costs today:** 6 roster voices are X-only and therefore invisible — `QuantMeta`,
`0xfhd_`, `thiccyth0t`, `GiganticRebirth`, `LomahCrypto`, plus `JustDeauIt`'s X feed. Tom Lee is
X-only-plus-guest-spots, so he's uncovered too.

**Design note before building:** posts are ~2 orders of magnitude shorter than transcripts, so
the per-item LLM economics invert — batching many posts into one `distill` call is the obvious
shape, and the current one-call-per-document loop does not fit it.

---

## 15. No concept of moving averages as levels · `OPEN`

Nothing in the repo computes a moving average; every level the engine reasons about is
structural. Evidence: on the GOOGL long the 50- and 52-week SMA sit in the same 273–303 region
as the weekly order block, and that *confluence* is what makes the zone worth entering. Two
independent reasons to buy an area currently score identically to one.

**Shape when built:** a score, not a gate — a new `SetupWeights` term with a `SCORE_VERSION`
bump, shown in the queue row. Cheap to source: `Context` already carries full weekly bars, so a
50W SMA is an average over `weekly[-50:]`. Note the series excludes the in-progress week.

**Open question first:** which averages. Nothing has measured whether 50W or 52W actually marks
turns in this corpus, and a confluence term that fires on an arbitrary average launders a guess
into the score.

---

## 18. `collapse` picks the group's *oldest* target by construction · `OPEN`

`collapse` picks a group's representative as the smallest stated claim (`core/setups.py`). In a
market that has trended, the smallest target is *mechanically* the oldest one — targets are set
relative to price at publication, so conservatism and staleness share a sort order.

Evidence: the SPX weekly candidate had 12 people in the group and chose a 14-month-old target
of 6000 against a price of 7403, beating eleven fresher views.

**Not obvious what the rule should be**, which is why this is `OPEN`. "Newest authored"
reintroduces the recency bias `min` avoided; "nearest to price" re-derives smallest-claim
against a better reference; median discards the provenance `target_source` exists to preserve.
Needs measuring against the sidecar (§4).

**Whatever replaces it, the queue should name the thesis the target came from** — one line, and
the defect would have been self-evident rather than something to go find.

---

## 19. Reachability · `PARTLY DONE`

Four of five mechanisms are fixed: `approach` no longer floors at zero, `reward_risk` no longer
rewards distance, the queue no longer buries the best-scoring candidate, and thesis pairs sit
together. See `core/setups.py` for each.

**Residual: a very high R:R is itself the symptom of a broken denominator.** Now measured and
confirmed — R:R and stop width in ATRs move together monotonically (R:R 30+ → median 0.26 ATR;
R:R 0–3 → 1.29). `SILVER long daily` shows R:R 2930 off a 0.02-ATR stop.

Stop padding stops the engine *manufacturing* these but does not fix the extremes. Treating an
above-ceiling ratio as a penalty rather than a maximum is a separate change and needs its own
measurement. Full table in `scripts/probe_stop_padding.py`.

---

## 21. Funding is a real cost and nothing in the scorer sees it · `PARTLY DONE`

**Done: computed, displayed, recorded — and now reaching the queue.** `Context.funding` carries
a `FundingOutlook`, the queue prints `adj` beside `R:R`, and every decision records both
numbers. `_score` is untouched.

**Coverage is done.** `cfg/venue_map.yaml` went 30 assets to 155 / 363 (asset, venue) pairs,
each confirmed against the venue's mark rather than by name (§31). No confirmed pair is left
unmapped; live rows pricing carry went 1 of 15 → 4 of 11. The one gap remaining is a single
fact, not a research task: `STX`, `CRDO`, `GLW` and `IREN` sit only on Hyperliquid's `para`
builder, unmapped because `execution/broker.py` assumes USDC collateral and only core and
`xyz` are verified.

**Residual — the measurement.** Which of three ratios best predicts an approval: nominal,
carry-adjusted, or distance-corrected. All three are recorded from v6 on. Waits on a mixed
sitting (§4). Only then decide whether the `reward_risk` weight should consume the
carry-adjusted number. **The newly mapped markets carry days of funding history, not months** —
the queue marks a thin median `n=`, and the correlation is not worth running until they thicken.

---

## 22. Lighter's funding history feed does not reconcile with its snapshot feed · `OPEN`

`fetch-funding --backfill` covers Hyperliquid and Aster. **Lighter is snapshot-only**, so its
column stays thin until nights accumulate.

Two Lighter endpoints disagree by roughly 10x with no derivable conversion:
`/api/v1/funding-rates` is signed and 8-hourly; `/api/v1/fundings?resolution=1h` is unsigned
magnitude plus a `direction` field, and is neither 8x nor 1/8 of the other.

**Next:** ask Lighter directly, or infer the unit by reconciling a realised payment against a
funded position. **Do not ship a conversion inferred from the ratio of the two feeds** — a
guessed factor puts an 8x error into the carry model, the exact failure `sources/lighter.py` is
written to prevent.

---

## 24. Mirror the funding log the way decisions are mirrored · `OPEN`

`data/funding/` is a record, not a cache — both venues serve bounded history, so a night the
logger missed is permanently unrecoverable, and Lighter has no usable history at all (§22).
Why, in full: `oracle.funding_store`'s docstring. `docs/ARCHITECTURE.md` no longer claims
otherwise.

**Decide whether it wants the vault mirror `oracle/decisions.py` already implements.** Milder
than the decision sidecars — this is measurement, not judgement, and most is re-pullable inside
the venues' windows — but a machine asleep for a month costs a month of Lighter coverage outright.

Less urgent since `scripts/backup.sh`: the log is copied off-machine nightly, so disk loss no
longer costs it. What a mirror would still buy is a copy that is *readable from the vault*
alongside the decisions it explains, which is a different want.

## 25. Route each order to the venue that is actually cheapest · `PARTLY DONE` — 2026-07-30

Shipped: `core/gaps.py` prices Alpaca's gap term, `core/routing.py` ranks venues on one unit,
`execution/desk.py` opens both at once, and an approved candidate is placed on the venue the
queue said it would be — never silently on the other one. Kraken is priced and deliberately not
routable (no map rows, no adapter). Plan: `.claude/plans/2026-07-29-asset-router.md`.

**Residual — `crossing` is unpriced on every venue (§43), and it decided two live rows.** Until
it is measured, a sub-10bp margin is not a real margin; `Decision.decisive` already refuses to
call those firm, so this is a precision limit and not a correctness one.

**Residual — a venue-level cost has no home.** Kraken's 0.50% round trip and Alpaca's
zero-commission are per-venue constants, not per-asset measurements, and nothing carries them.
They only matter once a third venue is routable.

**Read `scripts/probe_book_depth.py` before re-opening this.** It records what is already
settled, including the counterintuitive part: split by *direction*, not asset class.

**Keep execution routing separate from price routing.** `cfg/oracle_map.yaml` answers "what is
this worth" and must keep pointing at the index; `cfg/venue_map.yaml` answers "where do I trade
it". Collapsing them regrades every stored thesis against an instrument at ~1/10 scale.

**Unresolved risks:** Aster's zero funding is a policy, not a structural property; exit risk is
not entry risk, and a stop-market into a thin book during a gap returns the saving several times
over; the two venues have separate collateral pools.

---

## 27. The daily leg vetoes a weekly it should only time · `PARTLY DONE`

`timeframe_conflict` refuses when the daily trend contradicts the thesis. But the daily leg
reads a median of 21 days against the weekly's 125, and 745 of 761 genuine conflicts are "the
daily is retracing against the weekly" — the textbook entry condition.

**Shipped:** a ranging weekly can no longer be contradicted, since a conflict needs two views.
Candidates 74 → 97.

**Open — make the daily leg a score, not a gate.** Blocked twice: the six weights already sum to
1.0, so a seventh takes weight from the others (the global re-weight §4 refuses without
evidence); and the obvious mapping may have the wrong sign, since the retrace is the entry. The
hypothesis worth testing instead is that the informative state is the **failed break**. Needs
`daily_trend` mined against approve/reject, so it waits on a mixed sitting (§4).

**Open — the released candidates don't target what the range rule names.** Of 68 on a ranging
weekly, only 20 target within 2% of the dealing-range bound their direction points at; median
gap 9.5%. This is §18's territory, not a new gate. **Do not clamp targets to the range bound
before reading §18** — that overwrites a stated level with a derived one.

Audit, sweep, and the findings not to re-derive: `scripts/probe_timeframe_conflict.py`.

---

## 29. A bare currency cannot be priced off a pair that inverts it · `OPEN`

`cfg/oracle_map.yaml` routed `CAD` to `USDCAD=X`. CAD is the **quote** currency, so the two move
opposite ways and nothing in the engine flips direction — a bullish-CAD thesis was scored as
long USDCAD, the opposite trade. Route removed;
`test_a_bare_currency_never_routes_to_a_pair_that_inverts_it` now guards the class.

**The real prize is `JPY`: 13 theses, unrouted and therefore invisible.** Yahoo's `JPY=X` *is*
USDJPY, so routing it without inverting would create 13 wrong-direction theses in one edit.

**Shape when built:** an explicit `invert: true` on the routing entry, consumed where direction
is resolved. **Not** a negated price series — order blocks, the dealing range and
`structural_target` are all computed from bars, so inverting them produces zones that look
plausible and are wrong. The inversion belongs on the *thesis direction*.

**Deprioritised by Tegan 2026-07-28:** "I don't trade much forex so ok to log that for now."

**JPY is no longer merely unrouted — it routes to the wrong instrument.** Yahoo's bare `JPY`
resolves to something marking 36.68 while the yen is 163.7 per USD (or 0.0061 the other way),
and a `JPY LONG` weekly candidate is in the live queue on that price. Worse than invisible.
See §30, which owns the class; this entry still owns the inversion rule.

---

## 30. A crypto short's only venue is one whose terms exclude us · `DECIDED` — revised 2026-07-29

The residue of Alpaca+Kraken coverage is almost entirely one shape — a **short on a crypto
asset**. Kraken spot is long-only and US margin is closed to retail; Alpaca shorts equities and
ETFs but lists no crypto. (Coverage counts once quoted here were re-measured and had reversed
within a day; ask `probe_venue_coverage.py`, do not quote a number from prose.)

**Tegan's decision 2026-07-29 reverses this entry's original "do not solve this with a perp
DEX".** Hyperliquid is in scope and competes on cost, its §1.5 Restricted-Persons exposure
accepted as a known risk rather than a blocker. The fact is unchanged — every perp DEX checked
writes US persons out of its terms — so it stays recorded here as the largest unpriced term in
any routing decision (§25).

**Still worth building, and not superseded:** where *no* venue can reach an asset, **record the
refusal in the queue** so the gap stays visible rather than silently dropped. Cheapest of the
options and independent of the venue question. An inverse ETF or a put on a listed proxy
(`COIN`, `MSTR`, `IBIT`) via Alpaca options remain the alternatives if the exposure is later
reconsidered.

**Measure before building:** eight approved shorts is a thin base — confirm the rate holds over a
wider sample first. Source: `data/setups/decisions.jsonl`.

---

## 31. A venue mapping can be verified from price, and should be · `PARTLY DONE`

`cfg/venue_map.yaml` is hand-curated because a name match is "silently catastrophic". Alpaca
sharpens that — ~11,000 tickers, where nearly any short string resolves to something real and
liquid (`CL` is Colgate-Palmolive there, `HL` is Hecla Mining).

**Built as a probe, and it earned its keep immediately.** `scripts/probe_venue_coverage.py`
compares every venue mark against our own close, which both widened the map to 155 assets (§21)
and caught three faults in the *curated* half: `RUT` carried IWM with no `scale`, so an order
quoted on the index would have gone out at a tenth of the price; `URANIUM` named two different
uranium funds across its venues; `xyz:DXY` no longer exists. Regression tests hold all three.

**Residual — the guard exists and nothing calls it.** `guards.check_identity` refuses on
`core.identity`'s verdict and is unit-tested, but no code path supplies it a mark: `execution`
cannot import `oracle`, so the caller (`desk`/`plan`) has to fetch via `oracle.marks` and pass
it in. Until then a curated typo still places.

**Residual — no CI check over the curated map.** `--mapped-only` re-validates every hand-typed
row and nothing runs it, so a bad edit ships. It must compare against the *scaled* price where
`scale` is set; `core.identity.compare` takes `scale` for exactly this, and the probe
deliberately does not pass it (a scaled comparison would have printed MATCH for the RUT bug
this entry was opened by).

---

## 32. A bare ticker that resolves is not the right instrument · `PARTLY DONE` — 2026-08-01

The check is a gate now rather than a probe. `oracle/confirm.py` refuses a `needs_validation`
route a venue contradicts — in `fetch_cli` before the merge, and again in `setups_cli` at
context build, because the bad series were already cached and nothing re-fetches them. Six
caught, not the three this entry named: `JPY`, `PURR`, `ROBO`, `RTX`, `STRK`, `WTI`. They
report under `wrong instrument` in the unpriced tally.

**Residual — the six are unpriced, not fixed.** Each needs a curated `oracle_map` row or a
decision that it has none. `WTI` most likely routes to `CL=F` and then folds into `OIL` through
`oracle.instruments`, one instrument under two labels. `JPY` must not be routed at all without
§29's `invert`, or it creates 13 wrong-direction theses.

**Residual — 121 guesses that no venue lists are unchecked**, and pass by design: refusing on
silence would remove `CAT`, `CVX`, `NKE` and most of the equity corpus to catch nothing. Alpaca
cannot close it — our close comes from Yahoo by ticker and Alpaca's by the same ticker, so both
resolve `WTI` to W&T Offshore and agree. A non-circular equity source is the open question.

---

## 33. Hyperliquid's duplicate guard cannot tell a live bracket from a dead one · `PARTLY DONE`

`Broker.live_keys` narrows the guard from "an order was once sent" to "something is still
working", so a bracket that round-tripped flat no longer burns its candidate. `AlpacaBroker`
implements it against the venue; `HyperliquidBroker` returns every key unchanged, keeping the
old conservative meaning.

**The blocker is the wire, not the guard.** `wire.order_requests` sends no `cloid`, so the
venue knows those orders only by an oid this repo would have to index itself. Sending
`candidate_key` as the `cloid` on the entry leg makes the same question answerable there —
that is how Alpaca can answer it at all.

**Two readers depend on that now, not one.** `Broker.states` is the single place a candidate is
asked about, and `book --reconcile` (§40) reads it too — so on Hyperliquid the order log cannot
be settled either, not just the guard. The `cloid` buys both.

**One settled fact worth not re-deriving** (`alpaca_wire`, verified on paper): a GTC bracket
sent with the market shut is `accepted`, so the 06:15 nightly needs no scheduler.

**This entry used to claim a gap "costs a spread rather than a stop". That was wrong** — see
§39, which VRT proved live. Holding-side gaps are measured in `core.gaps` and no guard
reaches those.

---

## 36. Two perp guards do not transfer to equities · `PARTLY DONE` — 2026-07-30

**The leverage half is done and the venue states its own ceiling.** `Account.overnight_multiplier`
is `min(multiplier, 2)` and caps as `CAP_VENUE_LEVERAGE`, so nothing is written down to drift and
`max_notional_frac: 3.0` stays correct for perps. `Account.headroom` now sizes against
`regt_buying_power` too, which is the live half: on a 4x account Alpaca's `buying_power` is the
*day-trading* figure, and these positions are held ~21 sessions. The paper account reports
`multiplier: 1` with `max_margin_multiplier: 4` configured, so the divergence is one settlement
away and currently invisible — both fields read 24,971.52.

**Residual — no equity liveness signal, and half of it is now closed.** `oracle.liveness` derives
health from the funding log and equities have no funding; `execution.participation` is the
equity equivalent. **`session.describe` now prints the market on every equity order** — sessions,
shares/day, trades/day, and this order's share of one — rather than only when the participation
ceiling bound, which had made a market trading 175 times a day render identically to one trading
44,000 whenever neither tripped the 1% cap.

**Still open: the queue cannot show it, and the blocker is a live broker, not layering.** An
earlier version of this entry called it a backwards dependency the shape of §4b. It is not —
`oracle` already declares `execution` and `setups_cli` already imports from it. The cost is that
`AlpacaBroker.depth` needs an *open broker*: Alpaca credentials would become required to list
setups, and the nightly's `setups` step would gain a live-venue dependency it does not have, so
an expired key would stop the queue being built at all.

So the choice is about when the call is paid, not about who may import whom: fetch for the top N
only, have the nightly cache depth to `data/` and read the cache at queue time, or leave it at
the confirmation prompt. Until then thinness is visible only *after* approval.

---

## 39. Refuse a fill when the open has eaten the stop · `OPEN` — new 2026-07-29

A resting bracket is live at the open, so a gapped open silently converts the approved trade
into a different one. Evidence and mechanism: `execution/plan.py`, above `build`.

**Read the prior close at placement, then re-check at the open and refuse when the gap has
consumed a set fraction of the planned stop distance.** Needs a scheduler — a GTC bracket cannot
do this itself — so it is a pre-open reconcile step, not a guard inside `plan.build`.

The threshold is the open question. `VRT` consumed 91% of its stop; a rule near 50% would have
refused it and is inert on an ordinary open. Measure before picking — `book --reconcile` records
each entry's realised fill, so the plan-versus-open comparison accumulates instead of needing a
hand query per order.

## 40. Nothing sums — sizing is per-trade and the account is not · `PARTLY DONE`

All three caps are built, plus the detection gap behind them. `execution/budget.py` gates the
portfolio total, `max_position_frac` caps concentration, `uv run book` lists what is holding the
budget and cancels what you select, and `book --reconcile` asks the venue what became of every
order the log still calls `placed` — which found the three 2026-07-29 rejections and recorded
`VRT`'s real fill at 243.33 against its approved 266.52 (§39). Replay and sweep:
`scripts/probe_portfolio_budget.py`.

**The risk half is now built too.** `execution/portfolio.py` pools risk across venues at 5% of
combined equity — `max_position_frac`'s 20% restated in risk terms, since `1/ceiling` positions
fit at 1x. Measured against the sitting in question it admits five orders and refuses the sixth;
measured live 2026-07-30 the book already sits at 3.98% of the ceiling. Buying power is
deliberately *not* pooled: no transfer path exists between a margin pool and equity buying power.
`--risk` in the probe is the evidence.

**Residual — risk from positions opened by hand is invisible.** The pooled total is seeded from
the order log, so it counts only what this repo placed and the venue still reports live. It reads
as a lower bound and says so, but a hand-opened position genuinely does not appear.

**Residual — three numbers chosen rather than measured, and each needs the sidecar (§4).**

- `min_budget_fill: 0.5` — the floor under a shrunk order. Nothing has measured whether
  budget-shrunk orders are approved and held like full-size ones, and that needs shrunk orders
  to exist first.
- `max_position_frac: 0.20` — binds on 22 of 47 approved decisions and cuts their realised
  risk to a median 0.56%, because `1/ceiling` positions fit at 1x. Concurrency and per-trade
  risk are one choice with two names; the sweep in `--sizing` is what to read before moving it.
- `max_order_age_days: 14` — nothing measures how long a zone actually takes to be reached.

**Residual — reconcile settles the submission, not the trade.** A filled entry is recorded with
its price; how the position *ended* (which exit leg hit, at what price) is not. That is the
outcome half of §4's ground truth rather than a hole in this entry, and `OrderState` already
carries the leg statuses it would read.

**Slippage is the real risk, not the stop distance** — so liquidity sets the size and
`risk_pct` is the ceiling it may not reach (`execution/participation.py` is the first instance).
Do not tune against the current approval rate; it is inflated by deliberate over-approval
while testing.

---

## 41. Most of the roster's Dow conviction is in the key with no route · `OPEN` — new 2026-07-29

`DJI` 22 theses and `YM` 25 (the Mini Dow future, `YM=F`). `DJI` reaches Alpaca as DIA; `YM`
reaches nothing, so over half the Dow view is unreachable rather than duplicated.

`oracle.instruments` will not fold these — `YM=F` and `DIA` are different series, correctly. A
continuous front-month future carries roll discontinuities its ETF does not, so pointing `YM`
at DIA is not the trivial edit `DJI`'s was: the zones would be drawn on one instrument and the
conviction measured on another. Decide whether `YM` is a `tradeable: DIA` row or genuinely a
separate market before it is worth 25 theses.

## 42. RUT's Lighter and Aster rows need a re-probe on the new basis · `OPEN` — new 2026-07-29

Both carried `scale: 10.05` measured against `^RUT`. Moving RUT's execution pricing to IWM
(`oracle_map`'s `tradeable`, see §41) makes those numbers stale by construction — not wrong by a little, wrong
by their whole reason for existing. They were removed rather than flipped to 1:1, because
guessing 1:1 wrong sends an order at ten times the intended price, and that exact bug has been
caught in this file once already (see the RUT comment history in `cfg/venue_map.yaml`).

Both are *probably* 1:1 with the ETF now. `scripts/probe_venue_coverage.py` is what settles it;
it needs the venues reachable, which is why this wasn't done in the same pass. Until then RUT
refuses on those two with `REFUSAL_UNLISTED` instead of `REFUSAL_PROXY` — both refuse, so
nothing regressed. Note both books were measured effectively dead (<$2k/day), so this may not
be worth reviving at all; check volume before spending the probe.

---

## 43. `crossing` is the last unpriced routing term, and it decides real calls · `OPEN` — new 2026-07-29

`core/routing.py` prices carry (`core.funding`) and gap (`core.gaps`), but **spread + slippage is
unpriced on every venue**, so every quote carries one unknown. Because both sides carry the *same*
unknown, the evidence-parity rule in `Decision.decisive` cannot catch it.

That is not academic. Routing the live queue on 2026-07-29, `GOOGL` went to Alpaca on a **0.116%**
margin and `HOOD` on **0.132%** — both just over `NOISE_FLOOR` (10bp) and therefore reported
decisive, while the missing term is plausibly the same order of magnitude. Anything in the
10–25bp band is currently a coin flip wearing a verdict.

**Bounded once, cheaply, and it lowers the urgency: liquid names are single-digit bp.** Alpaca
quotes for `BE` on 2026-07-29 put best bid and ask on the same exchange one cent apart — 0.6bp,
and NBBO is never wider than one exchange. So crossing cannot flip a 12bp margin *on a liquid
name*; it plausibly can on a thin one (`INTL` trades ~9k shares/day). Rank the close calls by
liquidity before spending anything here.

**It is a slice, not a cache — that was the first read and it was wrong.** Three reasons.
`probe_book_depth.py`'s own docstring says magnitudes are unstable (a SILVER re-measure minutes
later moved 3.0bp → 0.8bp), so one snapshot is a confident wrong number and the term needs a
logged distribution like `data/funding/`. Alpaca publishes no book on the order-entry API at all,
so its side must come from quotes — and raw quote records are **per-exchange, not NBBO**, so a
naive bid/ask difference across two venues' books overstates badly. And HL's `l2Book` returns ~20
levels regardless of what is asked, making its depth a floor and its slippage a ceiling, never
like-for-like against a 500-level venue.

Do not raise `NOISE_FLOOR` to paper over it: that discards real tail differences to hide one
missing measurement.

---

## 44. Nothing compares the instruments an asset could be expressed as · `OPEN` — new 2026-07-31

Which instrument stands for an asset is typed by hand in `cfg/oracle_map.yaml` (`symbol`,
`tradeable`) and `cfg/venue_map.yaml`, and nothing below revisits it: `core/routing.py` ranks
venues for an instrument already fixed, and `guards.check_liquidity` can only refuse the result.
So `OIL` prices on `CL=F` and executes on three perps, and `USO` was ruled out on roll decay
without anyone measuring its depth. Each choice was argued against the one alternative that came
to mind, never against the set.

`scripts/probe_book_depth.py` already computes what this needs — VWAP fill against mid,
`depth10bp` both sides — but takes the instrument as given and compares venues for it.

**Supply evidence, do not auto-select.** A scorer that picks instruments reintroduces the exact
silent-catastrophe risk hand-curation exists to prevent. Enumerate the candidate expressions with
depth and spread and let the curator decide, as `probe_venue_coverage.py` does for identity.

Would give §41 (`YM` against DIA for the Dow) a basis rather than a judgement call. Needs §32
first: ranking expressions is meaningless until each candidate is confirmed to be the asset.

---

## 45. A short `assetCtxs` silently shortens the mark sweep · `OPEN` — new 2026-08-04

Hyperliquid returns `universe` and `assetCtxs` as parallel arrays paired purely by index.
`oracle/marks.py` and `oracle/sources/hyperliquid.py` zip them with `strict=False`, so a short
`assetCtxs` drops the tail of the universe and the run reports a smaller number rather than an
error — indistinguishable from a dex that genuinely lists fewer markets. `execution/broker.py`
uses `strict=True` for the same pairing, because refusing one order is cheap; refusing a whole
sweep is not, which is why the two disagree on purpose.

Never observed — this is an unguarded contract, not a known defect. Cost of being wrong is
prices silently missing for the tail of a dex's universe, which grades and routes as "no data".

Fix is a length check per dex that skips and *names* the mismatched dex, rather than either
truncating silently or raising and losing the other dexs. `fetch-funding` already reports
unreachable venues this way (`nightly.sh` greps `^  ! (hyperliquid|lighter|aster)`); this needs
the same treatment for a venue that answered but answered raggedly.

---

## 47. The circuit breaker cannot bound its own overshoot · `OPEN` — new 2026-08-04

`brain/sweep.py` submits every transcript to the pool before the breaker can be consulted:
`extract_all` builds the full `futures` list, then evaluates `consecutive_failures` in the
`as_completed` consumer. Workers only check `abort` on entry, so the number of calls that slip
through after the cap is hit is however many the pool starts before the main thread catches up —
a scheduling race, not a bound. Measured at `max_workers=1`: 6 on a laptop, 7 on a CI runner.

It does stop, and the §-docstring's 466-call incident cannot recur. But the guard's bound is
whatever the machine does, and at the default concurrency the overshoot is larger and untested.

Fix is to check `abort` in the submit loop, or submit in chunks of `max_workers`, so overshoot is
bounded by concurrency rather than by timing. `test_circuit_breaker_aborts_after_consecutive_failures`
was loosened to a majority-aborted assertion because the precise one encoded this laptop's speed;
tighten it back once the bound is real.

---

## 48. The entry and the target are on two different clocks · `OPEN` — new 2026-08-04

Replaying 137 recorded candidates: median stop **1.9** of the instrument's own daily ranges from
entry, median target **6.7** (p90 21.4), and **69% of stops reached on the bar that filled the
entry**. Not a small-stop artifact — the bar that trades down to a resting limit is by
construction moving hard against it. Independently: **40% never fill at all**.
`scripts/probe_replay.py`; do not read its win rate, 90 of 134 rows are censored one way.

The H1 trigger shipped 2026-08-05 and **does not settle this**. It makes the engine far more
selective (5 of 142 would have been offered) but its stop is *tighter* — 1.17 vs 2.02 daily
ranges on the 5 comparable rows, the direction this entry warns about. Coherent tightness
around a current entry may beat incoherent width around a stale one; n=5 cannot say.
`scripts/probe_trigger_replay.py`. Decisions now carry `trigger_state`/`_entry`/`_stop`, so the
sample grows from here — re-run the same-bar test against both geometries once it has.

Two trigger residuals, both deliberately unset rather than guessed: a fired trigger never
expires (measure fill-lag first), and a candidate whose trigger cannot be computed is refused
rather than falling back to an H12 trigger under a weekly setup.

**The target half is largely addressed** (`core/exits.py`, 2026-08-05): median target distance
4.0 daily ranges against the 7.1 above, p90 8.9 against 21.4. Different populations — today's
queue, not these rows — so treat it as strong indication, not proof.

**The stop half is untouched and is what is left of this entry.** Nothing about the 67%
same-bar rate changed; the entry is still a resting limit into a zone, which is the mechanism
that selects for it. Note `probe_replay.py` cannot confirm any of this — it reads recorded
decisions, so it reports whatever engine wrote them until new rows accumulate. Section 3 of
`probe_target_reachability.py` is the same statistic on the live queue.

---

## 49. Exit levels are structural edges, not liquidity pools · `OPEN` — new 2026-08-05

`core/exits.py` ships the target as the nearest thing price must negotiate, which removed the
unreachable targets (31 of 49 → 0, `scripts/probe_target_reachability.py`). What it uses for
"a level" is still crude: one order-block edge, one post-break extreme, one range boundary.

The roster's own vocabulary is finer and is worth adopting — TraderMayne, `EfpLqyt2yEw`:
targets are **equal highs and equal lows**, meaning two or more swing points at roughly one
price with time and space between them, and they split into *internal* range liquidity (take
partials) and *external* (the target). Ask `brain_search` for the episode before building.

Build a `core/liquidity.py` that clusters confirmed swings into pools with a tolerance and a
separation rule, classify each against the dealing range, and drive both target and ladder off
pools rather than raw edges. Tune the tolerance with a probe — 14 raw swings sat between entry
and target at the median before truncation, so clustering is the whole difficulty.

Then decide whether an authored target should rejoin the ladder as a rung. It is dropped
outright today, which is right for the *target* and possibly wasteful for the runners.
