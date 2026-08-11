# Evidence layer — design

**Status:** approved 2026-08-10. Implementation plan to follow.

## The decision this drives

Do thesis-driven candidates beat structure-only ones? The `distill` + `brain` tiers are the
expensive half of the system — 39MB of transcripts, 96MB of index, two 666-call LLM passes, and
`ingest-x`, the only command that spends real dollars. Nothing has ever measured whether they
contribute anything the price series does not already carry.

**The deliverable is a confidence interval, not a verdict.** You cannot prove a null; you can
only bound it. Design for the interval, report its width, and let the width say whether the
sample can answer the question. A CI spanning zero means *not demonstrated at this sample size*
— never *no effect*.

Two questions are deliberately **not** being answered here. Tuning the free parameters
(`SetupWeights`, `HalfLife`, `RR_HALF`, `PROXIMITY_SPAN`, `STOP_PAD_ATR`, `MIN_REWARD_RISK`)
comes second, because tuning a pipeline that contributes nothing is wasted work — and because
sweeping seven parameters against a young sample is how you fit noise. Expectancy after costs
comes third, and cannot be answered at all yet: see the `triggers_on` constraint below.

## Why the harness is the durable part

**Every probe in the repo runs `as_of = datetime.now(UTC).date()`.** Historical *resolution*
exists (`probe_replay` walks bars forward from a recorded decision); historical *generation* does
not. That is a limitation of the whole probe suite, not of this question. Point-in-time
generation turns every future probe from a snapshot into a distribution over time.

The evidence position as of 2026-08-10: 17 days live, 166 hand decisions across 99 assets, 17
orders placed — all paper/testnet — and **3 closed trades**. `probe_replay` is the only evidence
engine and 90 of its 134 rows are censored one way.

## Pre-registered readings

Fixed before any number arrives, because the structure-only arm takes its direction from the
weekly trend and is therefore a trend-following system — and trend-following works. Without this
written down first, a strong S arm reads as whatever the reader hoped for.

| Result | Reading |
|---|---|
| `T > trend×P` and `T > random×P` | theses earn their cost |
| `T > random×P` but `T < trend×P` | theses carry signal, but less than following the weekly. Keep the corpus; change how direction is derived. |
| `T ≈ random×P` | the direction call adds nothing |
| CI too wide to separate | underpowered — more as-of dates or more corpus, **not** a softer threshold |

Primary contrast is `T vs trend×P`. Everything else is descriptive. Stated in the report header
so a later reader cannot quietly promote a secondary result.

## Components

```
oracle/assemble.py    build_candidates + load_daily, moved out of setups_cli.py
                      new injectable param: marks_index (live by default, empty for
                      historical runs — a today mark index must not contradict a 2025 route)

oracle/asof.py        live_rows(rows, as_of)            → published_at <= as_of
                      synthetic_rows(assets, t, rule)   → duck-typed rows for the S/N arms
                      warmup guard                      → new refusal `insufficient_history`

oracle/replay.py      resolve(entry, stop, target, direction, bars, from_date, tail)
                      → (state, r, resolved_on, days)
                      states: nofill · stop · target · ambiguous · open
                      extracted from probe_replay, which then imports it

scripts/probe_evidence.py   arms × grid, clustering, report
```

`setups_cli.py` is 1,277 lines and eight probe scripts already import `build_candidates` and the
private `_load_daily` from it. The composition root is not a CLI concern; moving it is a
prerequisite here rather than separate cleanup.

`asof.py`'s warmup guard fixes a **live** defect. 95 of 329 daily series (29%) hold fewer than 90
bars, because `plan.py:60` windows each series to its own earliest mention minus 7 days — correct
for grading, which needs bars *forward* from publication, wrong for structure, which needs
lookback. Those assets currently refuse as `no_dealing_range`: a data verdict wearing a structure
verdict's name. That is the same conflation that recovered 256 refusals when "ranging" was split
out of `weekly_disagrees`.

## The arm matrix — two factors

```
                    UNIVERSE
                P = assets with a live      U = every sufficiently
                    thesis at t                 warmed asset at t

  thesis        T                           —  (undefined)
  trend         trend×P                     trend×U
  random        random×P                    random×U
  always-long   long×P                      long×U
  always-short  short×P                     —
  always-flat   flat×P                      —

  plus  T-excl = T with cfg/exclusions.yaml applied
```

**Direction value** — hold universe at P, vary the rule: `T` vs `trend×P` / `random×P` / `long×P`.

**Selection value** — hold the rule at trend, vary the universe: `trend×P` vs `trend×U`.

Comparing `T` directly against a universe arm would tangle selection and direction together.
Holding one factor fixed is what makes each answer mean one thing.

`long×P` is `core/score.py`'s existing `null_hit_rate` — always-long, same slate — lifted from the
per-person scorer to the setup level.

### Direction rules

- **trend** — weekly trend family; ranging → skip, tallied. A *strong* null. Cannot trip
  `weekly_disagrees`, so it is immune to the largest gate; this asymmetry is reported per arm as a
  gate-passage rate rather than hidden.
- **random** — seeded hash of `(asset, as_of, seed)`. A *true* null: no edge by construction, and
  because it can disagree with the weekly it receives exactly the same gate exposure as `T`. Run
  across **K=20 seeds**; the resulting distribution is the null band. One draw is itself a coin
  flip.

### The exclusions arm

`cfg/exclusions.yaml` is **off** for all primary arms — every entry was written after seeing the
asset perform ("This asset is straight down"), so applying it backwards is hindsight, and it
would inflate `T` specifically, the arm under test.

`T-excl` runs once with exclusions on. `T-excl − T` is the measured value of the discretionary
veto layer, which nothing currently grades. Cheap: exclusions are applied in `main()` at
`setups_cli.py:1169-1174`, *after* `build_candidates`, so off is the harness default and on is one
extra `exclusions.partition` call.

## Warming pass

The samplable window is `warm_depth − structure_warmup − resolution_tail`:

```
warm 1 year   →  365 − 365 − 90  =  negative. no window at all.
warm 18 mo    →  548 − 365 − 90  =  93 days   ≈ 3 months, one regime
warm 2 years  →  730 − 365 − 90  =  275 days  ≈ 9 months, multiple regimes
```

Two years is roughly the minimum that yields a usable window, and roughly where the sources cap
out anyway. `plan_fetches` gains a warmup floor:

```
start = min(earliest_mention − pad_days, grid_start − warmup)

  grid_start  earliest as-of date in the grid (≈2025-08)
  warmup      structure lookback, measured (365d starting guess)
  pad_days    existing DEFAULT_PAD_DAYS = 7, unchanged — the grading
              window still needs the bar preceding a publish date
```

The `min` keeps both requirements: grading still gets its bar before publication, and structure
gets lookback before the earliest as-of date.

- **Yahoo** serves multi-year spans in one request (232 of 329 series, the shallow ones)
- **Coinbase** tiles 300-candle pages
- **Kraken** hard-caps at 720 candles (~2 years) — accepted ceiling, 20 series

**Warmup length is measured, not assumed.** Sweep it and take the point where the
`no_dealing_range` refusal rate flattens. 365d is the starting guess only. This mirrors
`resample.straddles_the_split`, which measures the setup-rung split rather than assigning it by
asset class.

### Audit pass

Runs before any generation; failing series are excluded from the sample and **counted**, never
silently dropped.

- coverage and first/last bar per asset
- gap detection
- single-day-move screen — catches unadjusted splits and bad ticks alike. `parse_chart` reads
  `quotes["close"]`, not `adjclose` (`yahoo.py:48`). Yahoo's chart arrays are generally
  split-adjusted but not dividend-adjusted; a few percent of dividend drift over two years is
  marginal, an unadjusted split is catastrophic (a 4:1 stops out every long). Verify rather than
  assume.
- known corruption: `AI16ZUSD` holds 1 bar dated 1970-01-01

## Grid and data flow

~40 as-of dates, weekly on a fixed weekday, spanning ≈2025-08 → 2026-05 — corpus start 2024-07
plus 365d warmup at the front, 90d resolution tail off today at the back. Roughly 2,400 corpus
rows over multiple regimes. Weekly rather than daily because consecutive as-of dates share structure
and theses almost entirely: daily sampling gives ~7× the rows and nearly the same information,
shrinking naive CIs by √7 for free — which is not free, it is wrong.

```
PER AS-OF DATE t
  rows_live  = corpus rows where published_at <= t     (== t is included: known that day)
  warmed     = assets with >= warmup bars before t
  contexts   = build_context(bars[:t], weekly[:t], as_of=t)   ← the lookahead boundary
  for each arm (universe, rule):
      rows_arm   = real rows (T) or synthetic_rows(universe, t, rule)
      candidates = cross_reference(row, ctx) for each
  resolve each candidate against bars[t : t+90d]

REPORT
  per arm: n, fill rate, resolution rate, ambiguity rate, gate-passage rate,
           mean R/candidate, mean R/fill
  contrasts with asset-clustered bootstrap CIs
```

Contexts are built once per `(asset, t)` and shared across arms.

## Outcome metric

| State | Value |
|---|---|
| `nofill` — limit never touched | 0R. Fill rate reported separately; nothing has ever measured it |
| `stop` | −1R |
| `target` | +planned R |
| `ambiguous` — both hit same bar | **stop** (pessimistic, matches `probe_replay`) |
| `open` — filled, unresolved at tail | mark-to-market R on the last bar of the tail |

**Headline: mean R per candidate *generated*,** nofills counted as 0 — that is what the queue
actually delivers. Secondary: mean R per fill, fill rate, resolution rate.

No fixed horizon. §2 wants the 7/30/180/365 constants removed and `probe_replay` explicitly
refuses to invent a "trade expires after N days" cutoff; forcing one here would resurrect exactly
that. The tail is a measurement boundary, not a claim that the trade ended.

## Constraints, stated so they cannot be forgotten

**`triggers_on=False` throughout.** H1 data only goes back weeks, so the historical path tests the
daily/weekly engine *without* the entry trigger. **That is not the system you would trade.** It
bounds every conclusion and belongs in the report header.

**Arms are outcome-comparable, not score-comparable.** The S arms degenerate several terms by
construction — `trend` cannot trip `weekly_disagrees`, freshness is always maximal,
`agreement_count` is always 0, `target_source` is always structural. Enforced in code: the report
emits no cross-arm score column.

## Failure modes and dispositions

**Lookahead leakage** — the one that silently invalidates everything.

| Vector | Disposition |
|---|---|
| `build_context` reading bars after `t` | truncate + `on_or_before`; **tested, not assumed** |
| today's mark index | inject empty `marks_index` |
| today's funding outlooks | `funding_venue=None` |
| today's `listings_map` | an asset that listed in 2026 looks tradeable in 2025 — measure, disclose |
| today's routing map / canon | unavoidable — first commit is 2026-07-22, the window predates the repo. Identity check per `probe_replay`'s `IDENTITY_PAD` |
| `exclusions.yaml` | off for primary arms |

Defended by a **falsification test**: generate at `as_of = t` with the series truncated at `t`,
then again with the full series, and assert the candidate sets are identical. If any later bar
reaches anything, the sets differ and the test fails.

**Survivorship** — corpus assets that no longer route are silently absent, and survivors did
better. Unfixable, but **both P and U are drawn from today's routable set, so the bias is largely
common to all arms and substantially cancels in the contrasts.** Absolute levels carry the
caveat; the contrasts are far more robust. Report the count of non-routing corpus assets as a
bound.

**Ambiguity rate** — `probe_replay` found 69% of stops reached on the very bar that filled the
entry, so the ambiguous class may be large and the pessimistic convention may be driving the
headline. Run the whole thing again resolving ambiguity optimistically as a bound. **If the
conclusion flips, the finding is "cannot be settled without intraday bars"** — an honest result.

**Multiple comparisons** — nine arms, a dozen possible contrasts. Primary is pre-registered;
the rest are descriptive.

## Testing

| Target | Tests |
|---|---|
| `oracle/replay.py` | table-driven: nofill · stop-only · target-only · both-same-bar→stop · open→mark-to-market · gap through stop. Pure and deterministic |
| `oracle/asof.py` | row-filter boundary (`published_at == as_of` included), each direction rule, warmup guard emits `insufficient_history` |
| `oracle/assemble.py` | behaviour-preserving move — existing tests pass with only import paths changed |
| leakage | the truncation falsification test |
| determinism | same inputs → identical outputs, seeds included |

## Sequencing — five commits, gate green at each

The first three are verifiable by **nothing changing**, which is the property worth having while
the diffs are mechanical.

1. **Extract `assemble.py`.** Pure move + import updates across 8 probes and the CLI.
   *Verify:* `./scripts/check.sh` green; `setups --list` output byte-identical.
2. **Warmup floor in `plan.py` + audit pass.** Run `fetch-prices`.
   *Verify:* series-depth histogram before/after; the 95 thin series shrink; audit reports gaps,
   split candidates, the 1970 row.
3. **Extract `oracle/replay.py`;** rewire `probe_replay`.
   *Verify:* `probe_replay` output unchanged.
4. **`oracle/asof.py` + the leakage test.** First genuinely new logic.
5. **`probe_evidence.py`** — arms, clustering, report.

## Patterns to mirror

- **Sum-typed refusals** — `core/setups.py`'s `NotASetup`, `core/grade.py`'s
  `Pending`/`Ungradeable`. `insufficient_history` joins this family; never collapse "we don't
  know" into "it failed."
- **Findings live in the probe** — `scripts/probe_replay.py`'s docstring is the model: state what
  the probe answers, which properties can invert a conclusion, and which section to read first.
- **Measure rather than assign** — `oracle/resample.py`'s `straddles_the_split` decides the setup
  rung by measurement because the asset-class table it replaced was wrong three times. Warmup
  length gets the same treatment.
- **Bootstrap with a fixed seed** — `core/score.py`'s `BOOTSTRAP_ITERATIONS` / `DEFAULT_SEED`,
  reused rather than reinvented; cluster by asset, not by row.
