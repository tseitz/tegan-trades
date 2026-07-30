# Asset router — send each trade to the best available venue

## Design

### The problem

`Config.venue` is a **session** setting (`config.py:58`, default `hyperliquid`), and
`session.broker_for` returns exactly one broker off it (`session.py:43-46`). So every candidate in
a run goes to the same venue. Three things make that wrong, and only the third is new:

1. **Cost differs by `(asset, direction)`.** Settled and not to be re-derived — see
   `scripts/probe_book_depth.py`: *split by DIRECTION, not by asset class*; asset-class routing is
   strictly worse than either single venue. Measured 21-day carry on the 12 both-venue assets:
   median long **0.53%** of notional, tail uneven (BE 2.35% median / 7.19% p90); shorts are **paid**
   0.32–0.63%.
2. **Coverage differs.** Over 47 approvals / 37 distinct assets: HL 21, Alpaca 18, union 27.
   Alpaca-only = 6 (CHINA/FXI, CRM, GOOG, INTL, SBSW, VRT); HL-only = 9. A single venue forfeits
   part of the queue either way.
3. **The cost model is asymmetric, so the comparison cannot currently be made honestly.** The
   engine charges carry to Hyperliquid and **nothing** to Alpaca. Alpaca's real costs are gap risk
   and RTH-only stop election. §35 measured them — 3.5% of sessions gap past the median 4.53% stop,
   a **31% chance of at least one adverse gap over a 21-day hold**, wildly uneven per asset (USAR
   34%, XLE 2.4%) — and **nothing consumes that measurement.** §39 is a live case: VRT gapped
   -9.4%, the entry filled 23 points below plan, and the position round-tripped in 49 seconds.

Fixing (3) is the load-bearing part. Without it the router just ranks a priced venue against a
free one and always picks the free one.

### Decisions taken 2026-07-29 (Tegan)

- **Hyperliquid competes on cost everywhere it lists.** ToS §1.5 exposure is accepted as a known,
  priced risk. This **reverses §30's "do not solve this with a perp DEX"** — record the reversal
  rather than leaving the tracker contradicting the code.
- **Kraken is deferred.** It is a price source only: zero rows in `cfg/venue_map.yaml`, no `Broker`
  adapter. Router ships over Alpaca + HL, built so a third venue is a new row and an adapter, not
  a refactor. Kraken's eventual role is narrow — crypto **longs**, spot, long-only (§30).
- **One risk budget across venues** (see T6 for the quantity that pools and the one that cannot).
- **Lighter and Aster stay out** — no funds there.

### Shape

**`core/routing.py` — pure.** `(asset, direction, notional, listings, funding, gaps) → ranked
tuple[VenueChoice]`. Each choice carries `expected_cost` as a fraction of notional over
`CARRY_HOLD_DAYS`, and **which term dominated**, because "HL is 40bp cheaper" and "HL is 40bp
cheaper *because Alpaca gaps*" are different sentences to a person deciding. Pure and in `core`
so both the queue render and execution call the same function — a router that picks one venue at
execute time and displayed another in the queue is worse than no router.

**`core/gaps.py` — the missing Alpaca term.** Per-asset P(adverse gap > stop distance) × expected
excess loss, from cached daily bars in `data/prices/`. Per-asset is mandatory; the pooled 3.5% is
useless when the spread is 34% to 2.4%.

**One cost unit: expected fraction of notional over a 21-day hold.**

| Venue | Carry | Crossing | Structural |
|---|---|---|---|
| hyperliquid | `carry_cost(funding_annual, 21, side)` | probe_book_depth slippage | — |
| alpaca | 0 | spread | **gap term** |
| kraken *(later)* | 0 | spread | 0.50% round trip, long-only |

**Venue becomes per-candidate.** `Config.venue` degrades from *the decision* to a default and an
override. `session.broker_for` returns brokers **plural**, constructed lazily per venue so a run
touching one venue does not authenticate three.

**Gate vs score**, per `gates-vs-scores`: listing, shortability and legality are **gates** (a rule
or a missing fact); cost is a continuum, so it **ranks**. Show the winner, its cost, the dominant
term, and the runner-up in the queue — the new term has to be visible where the decision is made.

### Out of scope this slice

Kraken adapter · §39's pre-open gap reconcile (needs a scheduler) · Lighter/Aster · re-tuning
`min_budget_fill` / `max_position_frac` / `max_order_age_days` (§40 residual, needs the sidecar).

## Patterns to Mirror

**`execution/participation.py`** — the closest existing thing to `gaps.py`: a pure module whose
docstring *carries its own measurement*, and which **caps rather than refuses** because a market
too thin for the budget can still carry less. Mirror both properties.

```python
# It CAPS rather than refuses, which is the substantive difference from the perp gate. The
# size is derived from a risk budget, so a market too thin to carry that budget is not
# automatically a market not worth trading — it is a market that can carry *less*.
```

**`execution/liquidity.py`** — pure frozen dataclass + a gate reading it, with the measured
healthy/dead spread in the docstring. The shape for `VenueChoice`.

**`core/funding.py`** — the cost-unit convention, and the reason to be pedantic about units:

```python
# Never store an annualized figure alone, and never store a rate without its interval: the
# pair is the observation. A bare 8h rate read as hourly is wrong by 8x.
def carry_cost(annual_rate: float, days: float, side: str) -> float:
```

**`execution/venues.py`** — one table rather than a condition repeated in four places; the
per-venue fact lives in the table. Where the third venue should slot in.

**`sizing.apply_caps`** — four ceilings as one `min()` that **reports which one bound**. The
pooled-risk ceiling joins this, and must report itself the same way.

## Tasks

> **Progress.** T1–T7 ✅. T5–T7 notes below; T1–T4 shipped in `88c0213`.
>
> **T5 deviated from the plan in one way worth recording: the map is EAGER, not lazy.** The plan
> asked for lazy construction so a run touching one venue does not authenticate three, but lazy
> would surface a missing key *mid-triage* — and `Session.open`'s whole ordering argument is that
> a session dying mid-triage discards judgement, the scarce input. So `Desk.open` connects up
> front to the venues the router names (`venue_routing.candidate_venues`), which is neither lazy
> nor "all of them". That function deliberately uses the venues that **quote**, not the ones that
> **win**: a winner depends on `can_short`, which is read from Alpaca's own account, so choosing
> by winner lets a short-heavy queue gate Alpaca out for want of the read that opening Alpaca
> would have supplied — and it stays gated, having proved itself unnecessary.
> Also: the network is **translated, not copied** (`mainnet`→`live` is a tier, not a word), and a
> bad venue/network pair raises rather than degrading to `unreachable` — a yaml typo reported as
> an outage sends someone to check the wrong thing.
>
> **T6 turned out not to be correctness-in-advance.** Measured live 2026-07-30, the book already
> sits at **3.98%** of the 5% ceiling — seeded from the order log, four live keys on Alpaca paper
> plus three on HL testnet — so the next full-size order lands at 4.96% and the one after is
> refused. The ceiling is not 5% by taste: `max_position_frac: 0.20` already says five positions
> fit a 1x account, and five at the 1% budget risk 5%, so the two settings are one decision with
> two names. Running it live found a defect no unit test had: the refusal compared risk-dollars
> left against a *notional* needed, and that notional was the post-cap size, which had rounded to
> zero — "leaving $8.27 against the $0.00 this order needs". Now pinned by test.
>
> **T7's live half was not the one §36 described.** Per position the venue ceiling cannot bind
> while `max_position_frac` (0.20) sits below the multiplier, so that half is genuinely
> correctness-in-advance and is now pinned by a test that will fail if either number moves. The
> half that *is* live is headroom: on a 4x account Alpaca's `buying_power` is **day-trading**
> buying power, and these positions are held ~21 sessions, so sizing against it invites a Reg T
> call at the close. `regt_buying_power` is the overnight figure; both read 24,971.52 today
> because the account is still `multiplier: 1`, with `max_margin_multiplier: 4` configured — one
> settlement away from a silent factor of two.
>
> **T1 ✅ · T2 ✅ · T3 ✅ · T4 ✅** (`oracle/venue_routing.py` + `format_routing`;
> 1653 green). T4 notes: routing shows **with or without a session**, since which venue is
> cheapest is a fact about the candidate and deferring it to placement would reveal it after the
> judgement was spent. Only `can_short` needs a session, and "not asked" is now a *distinct*
> refusal from a measured "cannot short" (`REFUSAL_SHORT_UNKNOWN`) because Alpaca has been seen
> reporting `no_shorting: false` while `shorting_enabled` was false. Also separated **unlisted**
> from **gated** in the render — different problems, different fixes — which closes §30's
> surviving recommendation as a side effect. Kraken is deliberately not in `ROUTED_VENUES`: the
> cost shape exists but nothing could be placed there yet.
> T3 found one bug worth remembering and one limitation now filed as §43. The bug: `total` sums
> only priced terms, so a venue with *nothing* priced totalled 0.0% and beat a real measured cost
> — unmeasured beating measured, the exact defect this slice removes, one level up. Fixed by
> gating an unpriced venue out (a missing fact is a gate) and by requiring **evidence parity** for
> `decisive`, since `total` is a lower bound whenever a term is unpriced. §43 is the residual:
> `crossing` is unpriced everywhere, and it decided `GOOGL` (0.116%) and `HOOD` (0.132%) — both
> just over the 10bp floor with a same-order unknown missing. T4 must not present those as firm.
>
> Live routing over the approved queue: **alpaca 8 · hyperliquid 17 · no venue 10 · refused 2.**
> Two findings that change T3's premise, both measured:
> **(a) At the median the venues are a wash** — median shrunk gap cost 0.595% over 21 sessions vs
> HL median long carry 0.53%. So the router's value is entirely in the tails (PLTR 5.23%, GOOG
> 4.12%, INTL 3.27% vs BE 0.00%, RKLB 0.04%, GOOGL 0.08%), not in the average trade. T3 must
> therefore surface *why* a venue won, not just that it did — a 6bp win is noise, a 5% win is not.
> **(b) The gap cost is only defined for Alpaca-listed instruments.** Futures and indices trade
> ~24h and are not tradable on Alpaca at all, so `core/routing.py` must not ask for a gap term on
> a non-Alpaca asset — there is no such quantity, and inventing one charged `YM` 6.33%.

### T1 · Record the reversals in `docs/IMPROVEMENTS.md`
Do first, so the tracker stops contradicting what we're about to build. §30: perp DEX now in
scope, exposure accepted and priced — keep the *measurement* ask (six shorts is a thin base).
§25: rewrite the Aster-gated trigger ("until then a single venue — Hyperliquid — is right") to the
three-venue framing; mark it in progress. §36: per-venue ceilings are now required by the router,
not latent. Keep each ≤15 lines per CLAUDE.md.
*Considerations:* §30's "record the refusal in the queue" option is still the right shape for
assets no venue can reach — that survives the reversal and should not be deleted with it.

### T2 · `core/gaps.py` — per-asset gap cost · TDD
Turn §35's measurement into a consumable term. Read cached daily bars, compute per-asset
P(adverse overnight gap > planned stop distance) and expected excess loss beyond the stop, return
a fraction of notional over `CARRY_HOLD_DAYS`. Ship a `scripts/probe_gap_cost.py` alongside so the
number is re-measurable, matching the other probes.
*Considerations:* direction matters — an adverse gap for a long is a gap down. Sample size per
asset is the real risk: 10 assets × 2,500 sessions gave §35 its numbers, but a thin or newly
listed asset will have far fewer, and an unmeasured asset must **not** silently price at zero —
that is exactly the current bug. Decide explicitly what an unmeasured asset costs, and make it
visible; `participation.check_depth`'s "an unmeasured market must not refuse" is the precedent for
the reasoning, though the answer here may differ.

### T3 · `core/routing.py` — pure ranking · TDD
The cost table above, plus the gates. Returns all candidate venues ranked, never just the winner —
the runner-up and the margin are what make the choice reviewable.
*Considerations:* what happens when the margin is inside the noise? `probe_book_depth` warns *"the
ordering is stable; the magnitudes are not"* — a re-measure moved Lighter 3.0→0.8bp. So a
near-tie should resolve by a stated stable preference (venue with the better structural story),
not by a coin-flip on 5bp. Also: HL slippage numbers are a *ceiling* because `l2Book` returns ~20
levels regardless of ask — do not compare them to a 500-level venue's numbers as like-for-like.

### T4 · Surface the choice in the queue · `oracle/setups_cli.py`
Venue, cost, dominant term, runner-up. Also closes §36's "surfacing `Depth` in the queue render is
the remaining half".
*Considerations:* the queue is already dense; this is four new facts per row. Probably a single
column plus detail on the expanded view rather than four columns.

### T5 · Multi-broker session · `execution/session.py`
`broker_for` → lazy per-venue map. `credentials_for` already keys by venue (`config.py:214`), and
`Config.liquidity_enforced` already branches on it (`config.py:84`) — both are the right shape
already.
*Considerations:* a venue whose credentials are absent should degrade to "not routable" rather
than raise, so a missing Kraken key never blocks an Alpaca trade. Rehearsal networks differ per
venue (`venues.NETWORKS`), so a mixed run is testnet-HL + paper-Alpaca simultaneously — the typed
real-money confirmation must be per venue, and `venues.REAL_MONEY` already models this correctly.

### T6 · Pooled risk budget · `execution/budget.py`, `sizing.apply_caps`
**Two quantities, and conflating them is the bug to avoid.** *Risk* (stop distance × size) pools
into one portfolio number across venues. *Buying power / notional headroom* does **not** — there
is no transfer path, crypto cannot fund equity buying power. So: one pooled risk ceiling spanning
venues, the existing per-account budget ceiling unchanged per venue.
*Considerations:* builds directly on `6e404e4` — the within-venue running total already exists and
is deliberately local as well as live-read, because before the open an accepted bracket does not
reduce buying power. That reasoning extends to the pooled total unchanged. Equity is per-venue and
unpoolable, so "risk_pct of combined equity" needs combined equity read across venues, and a venue
that fails to answer must not silently shrink the denominator.

### T7 · Per-venue ceilings · §36
`max_notional_frac: 3.0` is a perp number; Reg T allows 2x overnight. `account.multiplier` is read
live, so the venue states its own ceiling rather than it being written down.
*Considerations:* currently unreachable because `max_position_frac: 0.20` dominates on Alpaca —
so this is correctness-in-advance, not a live bug. Do not lower the global; the perp number is
correct for perps.

## Verification

`uv run pytest -q -m "not integration"` green at each task. Then the real check, which no unit
test substitutes for: `setups --execute` on **rehearsal networks only** (HL testnet + Alpaca
paper) over the live queue, confirming the router's chosen venue and cost per candidate match what
the queue displayed. Both venue hazard memories exist because live runs caught what tests did not.
