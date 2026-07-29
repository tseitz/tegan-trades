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

The bottleneck is variety, not volume. §4's machinery is complete and §27 fixed the candidate
drought, but all 24 recorded decisions were drawn from the single population §27 released, so
`daily_trend` is constant across them. Terms needing contrast are unmeasurable until the nightly
mixes the queue again — a wait, not a task.

| | Entry | Why now | Cost |
|---|---|---|---|
| 1 | **§21 coverage** — widen `cfg/venue_map.yaml` | The queue is barely actionable: of 15 live rows, one had a venue entry, and ≥8 of the 12 unmapped are listed somewhere. Confirm each instrument; don't match strings. | free, local |
| 2 | **§27 residual** — targets vs the range rule | 68 candidates on a ranging weekly, only 20 target within 2% of the bound the rule names. Read §18 first. | free, local |
| 3 | **§6f residual** — routed but never fetched | 26 rows that route fine and were never fetched. Some are `needs_validation` guesses that may not resolve. | free, network |
| 4 | **§4** — more sittings | Needs a *mixed* population, which arrives on the market's schedule. | your attention |

**Waiting on a mixed sitting, not on code:** §11's recency half · §18 · §21's measurement ·
§27's option 2.

**By theme:** corpus supply §3 · §6 · §6b · §6d · §6f · §6h · §9 · §14 — durability §4b · §24 —
venue and execution §22 · §25 — scoring §1 · §2 · §8 · §12 · §15 · §19 · §29.

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

**The fourth unknown is a corpus gap, not a retrieval miss.** "15m entry trigger — failed
breakdown, reclaim" retrieves nothing useful, and §3 calls it the whole of slice 2's layer 3.
Needs a different source or Tegan's own definition; blocks slice 2 either way.

---

## 4. Revealed preference is the only ground truth · `PARTLY DONE` — absorbs §20

Four scoring systems (`core/rank.py`, `core/grade.py`, `brain/retrieve.py`, `core/setups.py`)
and zero closed loops. Mining `data/setups/decisions.jsonl` — approve versus reject, against
the ranker's own terms — is the only ground truth available.

**The machinery is done:** the queue draws a stratified sample (`oracle/queue.py`), every
decision records what else was on screen, and the reason vocabulary derives scope from cause.

**Residual: the recorded decisions cannot vary.** All 24 v6 rows came from the population §27
released, so `daily_trend` is constant and `trend_alignment` pinned at 0.0. Needs a mixed
sitting, which arrives on the market's schedule.

**No mandate to re-weight, and the bar is high.** `score` and `freshness` clear chance;
everything else spans it. Read the mining traps in `oracle/decisions.py` before touching this —
they are properties of the data and no test enforces them. Replay with
`scripts/probe_freshness_weight.py`, which reproduces the shipped scorer exactly.

---

## 4b. The decision sidecars are irreplaceable and unbacked · `PARTLY DONE`

**`data/setups/decisions.jsonl` is mirrored** to the vault by `oracle/decisions.py` — subordinate
to the primary, reconciled at startup, `--no-mirror` to disable.

**`data/triage/decisions.jsonl` is not**, and it is blocked on a layering question rather than
effort. `distill/triage_cli.py` has its own `record_decision`; importing `oracle.decisions`
would be a backwards dependency (pipeline order is ingestion → distill → brain → oracle), and
`core/` is barred from I/O.

**Pick one before the triage sidecar grows:** accept a small duplicate mirror in `distill`, add
a workspace member for shared file plumbing, or let `distill` depend on `oracle`. The loss is
milder than the setups one — triage records `approve`/`skip` only, with no reason.

---

## 6. No freshness loop · `OPEN`

The machinery is batch-historical; the use case is real-time. The question worth answering is
"price is approaching this level *now*", and that needs a scheduled ingest → distill → setups
pipeline.

**Next: nightly via launchd** — not cron, not the Claude scheduler, because the pipeline must
not need a session open. The plist needs `WEBSHARE_PROXY_*` or transcript fetches hit YouTube's
IP block.

**Design for silent failure:** a dead nightly job and a quiet market look identical unless the
note carries a last-successful-run line.

The gate half of this entry is fixed — age is a half-life, not a cliff, and a ranging weekly no
longer counts as disagreement. That took candidates 8 → 49. The rule it set, worth not
re-litigating: **gate a rule you wrote or a fact that is missing; score a measurement on a
continuum.**

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
- Measure the real daily volume of §6's loop before pricing anything.

---

## 11. Agreement is date-blind · `PARTLY DONE`

**Residual: weight each voice by recency** rather than counting heads. `core.rank.recency_signal`
already exists.

Evidence: one ETH candidate's seven supporters span 2026-01-20 to 2026-07-22 — a 186-day-old
view counted equally with one from three days prior. Not a staleness failure; the old view is a
macro thesis and legitimately survives. The question is whether a macro bull from January is the
same evidence as a swing bull from last week.

Needs a sidecar correlation, so it waits on a mixed sitting (§4). Interacts with §2 — if
horizons go event-based, "current view" becomes better defined and this may partly solve itself.

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

`docs/phase-0-findings.md` chose Grok `x_search` over the official X API (~$1–5/mo vs $200/mo).
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

**Done: computed, displayed, recorded.** `Context.funding` carries a `FundingOutlook`, the queue
prints `adj` beside `R:R`, and every decision records both numbers. `_score` is untouched.

**Residual — coverage, and it now blocks execution rather than just measurement.** Of 15 live
queue rows exactly one had an entry in `cfg/venue_map.yaml`; everything else prints
`not executable`, and that is mostly false — ≥8 of the 12 unmapped are listed on some venue per
the funding log.

**Widening it is not a mechanical paste.** Name-matching a venue ticker is silently
catastrophic: the right entry for the `SPX6900` memecoin is the symbol that would be *wrong* for
the S&P index. Crypto majors are low risk; HIP-3 equities need checking against the builder's
listing. **Do not fill the four genuine absences by guessing** — `HL`, `SGML`, `SBSW`, `INTL`
are listed by no venue, and absence is a real answer.

**Residual — the measurement.** Which of three ratios best predicts an approval: nominal,
carry-adjusted, or distance-corrected. All three are recorded from v6 on. Waits on a mixed
sitting (§4). Only then decide whether the `reward_risk` weight should consume the
carry-adjusted number.

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

## 24. `data/funding/` is not regenerable ore, and the tree says it is · `OPEN`

Same class as §4b. `docs/ARCHITECTURE.md` describes everything under `data/` as regenerable;
that is true of `data/prices/` and false here. Both venues serve bounded history, so a gap older
than that window is **permanently unrecoverable**, and Lighter has no usable history at all
(§22) — its column exists only for nights the logger actually ran.

Milder than the decision sidecars: this is measurement, not judgement, and most can be re-pulled
within the venues' windows. But a machine asleep for a month costs a month of Lighter coverage
outright. **Decide whether it wants the vault mirror `oracle/decisions.py` already implements.**

---

## 25. Route each order to the venue that is actually cheapest · `DECIDED` — gated on a trigger

Once a candidate is approved, pick the venue rather than assuming one. The machinery to *price*
that choice already exists: `cfg/venue_map.yaml` is keyed `(asset, venue)`,
`carry.outlooks_for(venue=…)` re-prices the queue, and `setups --funding-venue aster` runs
today. The residual is small — choose per candidate by the sign of funding, and record which
venue the decision assumed.

**The trigger, so this stays a measurement and not a judgement call:** build it when Aster's
equity funding median is still ~0% over 60+ days of logged data
(`uv run fetch-funding --report --window 60`) and typical size stays under ~$25k. Until then a
single venue — Hyperliquid — is right, and it is also simply the tightest book.

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
