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
| 1 | **§32** — routes that resolve to the wrong instrument | Three of 307 priced assets are confirmably not what they claim, and one of them (`JPY`) is in tonight's queue. The check that found them already exists (§31). | free, network |
| 2 | **§27 residual** — targets vs the range rule | 68 candidates on a ranging weekly, only 20 target within 2% of the bound the rule names. Read §18 first. | free, local |
| 3 | **§6f residual** — routed but never fetched | 26 rows that route fine and were never fetched. Some are `needs_validation` guesses that may not resolve. | free, network |
| 4 | **§4** — more sittings | Needs a *mixed* population, which arrives on the market's schedule. | your attention |

**Waiting on a mixed sitting, not on code:** §11's recency half · §18 · §21's measurement ·
§27's option 2.

**By theme:** corpus supply §3 · §6 · §6b · §6d · §6f · §6h · §9 · §14 — durability §4b · §24 —
venue and execution §22 · §25 · §30 — routing §29 · §31 · §32 — scoring §1 · §2 · §8 · §12 ·
§15 · §19.

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

**JPY is no longer merely unrouted — it routes to the wrong instrument.** Yahoo's bare `JPY`
resolves to something marking 36.68 while the yen is 163.7 per USD (or 0.0061 the other way),
and a `JPY LONG` weekly candidate is in the live queue on that price. Worse than invisible.
See §30, which owns the class; this entry still owns the inversion rule.

---

## 30. A crypto short has no venue this repo can legally reach · `OPEN` — new 2026-07-28

Alpaca + Kraken covers **32 of 38 approved decisions**; the residue is almost entirely one
shape — a **short on a crypto asset**. Kraken spot is long-only and US margin is closed to
retail; Alpaca shorts equities and ETFs but lists no crypto. Four of six approved shorts route
to Alpaca; the two `SOL` shorts have nowhere to go.

**Do not solve this with a perp DEX.** Every venue checked writes US persons out of its terms
(Hyperliquid §1.5, Ondo Perps) — structural CFTC exposure, not a geoblock to route around.

**Options worth pricing, roughly in order:** an inverse ETF where the asset class has one; a put
on a listed proxy (`COIN`, `MSTR`, `IBIT`) via Alpaca options; or refusing crypto shorts and
**recording the refusal in the queue**, so the gap stays visible rather than silently dropped.
The third is the cheapest and should ship first regardless of what follows it.

**Measure before building:** six approved shorts is a thin base — confirm the rate holds over a
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

**Residual — it is a probe, not a gate.** Nothing runs it before an order. Per the gate/score
rule it is a **gate** (a missing fact, not a judgement): wire it into `execution/guards.py`
ahead of any live placement, and into CI so a curated edit cannot ship unchecked. The threshold
needs the care this entry already describes — compare against the scaled price where `scale` is
set, and skip nothing silently.

---

## 32. A bare ticker that resolves is not the right instrument · `OPEN`

§31 checks the *venue* side of a mapping. This is the same technique pointed at the *price*
side: `route()` validates that a `needs_validation` guess resolves to **a** tradeable symbol,
never that it is **the** one. Three of 307 priced assets are confirmably wrong, and they only
became visible because a second source was asked the same question.

Evidence, measured 2026-07-29 by comparing every venue's mark against our own close
(`scripts/probe_venue_coverage.py`): `JPY` prices at 36.68 against the yen's 163.7 · `WTI`
prices at 3.18 — that is W&T Offshore, the E&P company, while `OIL`'s curated `CL=F` route
correctly gives 81.75 · `PURR` prices at exactly 100x Hyperliquid's. All three are
`needs_validation`. `JPY` is in the live queue (§29).

**Promote the check, do not patch three tickers.** A route flagged `needs_validation` should
have to survive the cross-source comparison before it prices anything. The venue mark only
exists for assets a venue lists, so this hardens the tradeable half of the corpus, not the tail.

**Related, same cause, smaller:** `URANIUM` and `URA` are two canonical labels for one fund,
both routing to `URA`. A `cfg/assets.yaml` alias, not a routing change.

---

## 33. An equity order can open through its own stop · `PARTLY DONE`

`venue: alpaca` places into a market open 09:30–16:00 ET while the nightly runs at 06:15 and
`core.setups` assumes a 24/7 tape.

**Queuing is confirmed and needs no work.** A `gtc` bracket placed at 22:45 ET with the market
shut came back `accepted` with both legs `held` — evidence in `alpaca_wire.ACCEPTED_STATUSES`.
No scheduler required.

**The gap loss is NOT the residual, and an earlier read of this entry had it backwards.** A
limit is a worst price, not a target: a buy limit at 391.91 fills at 380 if the market gaps
there. The stop leg is then born already through the market and exits at once, so the trade
round-trips for roughly the spread. The limit *is* the protection — there is no Alpaca flag
for "don't fill at the open" and none is needed.

**What the venue will not catch:** `stop_loss.stop_price must be <= base_price - 0.01`, so the
stop is validated against the *entry limit* and never against the market. A bracket that will
fill straight through its own stop is accepted without complaint — verified on paper.

**The real defect is that a round-trip consumes the candidate.** The bracket was accepted, so
`store.placed_keys` marks the key placed and `Session.prepare` refuses it forever after as a
duplicate — the setup is burned by a gap that cost nothing and never gave the trade. Fix that
before anything about the open. `already_placed` needs to distinguish a live position from a
flat round-trip.

**Holding-side gaps are §35 and are a different problem.** No guard reaches those.

---

## 34. The liquidity gate does not cover equities, and is off rather than absent · `OPEN` — new 2026-07-28

`Config.liquidity_enforced` returns False for `venue: alpaca` because `AlpacaBroker.liquidity`
honestly reports "not measured" and an unmeasured market is a refusal — so enforcing would
refuse every equity. Correct, and documented at both sites, but it means **the one venue that
can trade a $14 microcap has no depth check at all** while the perp venue has three.

Two of the three measures genuinely do not transfer: an equity has no open interest, and the
order-entry API publishes no book. The third does — Alpaca's market-data snapshot endpoint
carries a daily bar and a quote, so 24h dollar volume and near-touch depth are both reachable
with the key the venue already needs.

**Build it as its own check rather than widening `check_liquidity`**, whose three refusal codes
are named for perp facts. Until then the exposure is bounded by `MAX_DEPTH_FRACTION` not
applying: on `USAR` at $14 a 1%-risk order is small, but nothing enforces that.

---

## 35. On equities a stop is an intent, not a bound · `OPEN` — new 2026-07-29

A stop is a market order once triggered, so on a gapped open it fills at the open and not at
the stop. Realised loss then exceeds `risk_pct` by whatever the gap was, and **nothing in the
repo says so** — `describe()` prints "risk $X (1.00% of equity)" as though it were a bound.

Measured over 2,500 sessions across ten of the queue's own equities (2026-07-29): 3.5% of
sessions gap past the median 4.53% stop; over a 21-day `CARRY_HOLD_DAYS` hold that is a **31%
chance of at least one adverse gap wider than the stop**. It is not a tail. The rate is very
uneven — `USAR` gaps past it on 34% of sessions, `XLE` on 2.4% — so a per-asset number is
worth more than the pooled one.

**Cheapest honest fix is wording, not machinery**: have the confirmation preview say the
number is the intent on a continuous tape. Anything more is a real modelling decision — sizing
off gap-adjusted risk, or a per-asset gap premium — and should not be reached for first.

**Do not "fix" this with a stop-limit.** A limit on the stop leg is what makes a gap
un-exitable rather than merely expensive; `alpaca_wire` omits it deliberately.

---

## 36. Two perp guards do not transfer to equities, and one is a latent trap · `OPEN` — new 2026-07-29

**`max_notional_frac: 3.0` exceeds what a US broker will hold overnight.** Reg T allows 2x on
an overnight position; 3.0 was measured against Hyperliquid perps. Inert today — the widest
implied leverage across approved Alpaca-listed decisions is 0.58x — but the tightest stop the
engine has *ever* produced (0.51%) implies 1.96x, which is inside a rounding error of the
limit. Wants a per-venue ceiling, not a lower global one; the perp number is correct for perps.

**`oracle.liveness` has no equity equivalent.** It derives a market's health from the funding
log, and equities have no funding. So the Alpaca venue has no liveness signal at all, where the
perp venues have one that already caught `xyz:DXY`. Volume from the market-data snapshot is the
obvious substitute and overlaps §34 — build them together or not at all.
