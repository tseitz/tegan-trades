# tegan-trades

A personal signal and trading platform. Trusted voices and outside data feeds are distilled into a view on an asset; higher-timeframe chart structure says when to act on that view; the result is reported per pot of money, against what that pot is supposed to beat.

This file is the glossary. Decisions live in `docs/adr/`; work lives in GitHub issues.

## Money

**Mandate**:
What a pot of money is for: what it must beat and how much risk it may take, independent of the asset classes it holds. Retirement, savings, robinhood, crypto and Treasury are today's five mandates, one per file — "not five accounts" because the mandate is the purpose, not the login. A Mandate's benchmarks measure the **whole pot**; none is scoped to a subset of it.
_Avoid_: Account, portfolio, bucket, strategy.

**Domain**:
An asset class — `crypto` or `stock`. It selects a price source. It is **not** a statement of purpose; that is the Mandate.
_Avoid_: Using it to mean what the money is for.

**Net worth**:
Everything you have, summed across every Mandate: each Mandate's holdings plus its own cash. Settled 2026-09-15 ([ADR-0007](adr/0007-net-worth-reader.md)) as the one deliberate, narrow crossing of the Mandate boundary — a percentage inside a Mandate still never crosses it; this is the sole reader that sums across all of them. Each dollar lands in exactly one Mandate, which is what the Deployed/Idle line exists to guarantee.
_Avoid_: Whole, total, book — this repo does not have a standing word for "everything inside one Mandate merged across its accounts"; describe that in plain words where it's needed instead of reaching for a proper noun.

**Benchmark**:
What "doing nothing instead" would have returned for a Mandate, so performance can be judged against the Mandate's own goal rather than a generic index. A Mandate carries one to three — a market index, a flat synthetic rate, or its own holdings left untraded — capped there so the comparison stays legible rather than open-ended.
_Avoid_: Index, baseline, target.

**Horizon**:
How long a Mandate's position is expected to be held — `scalp`, `swing`, `position`, or `macro` — carried as a Mandate field. Three unrelated things share this vocabulary: a Mandate's own Horizon, a Thesis's stated timeframe, and the day-count constants used in call scoring. Settling which one a conversation means is worth a beat before using the word.
_Avoid_: Timeframe, period — without saying which of the three you mean.

**Holding**:
Something currently owned, listed in a portfolio file. Distinct from a Candidate, which is not owned yet.
_Avoid_: Position, lot.

## Reading a market

**Lean**:
What the sentiment sources currently think about an asset — bullish, bearish, or silent. Derived from the corpus, never from the chart.
_Avoid_: Sentiment, bias, stance, opinion.

**Location**:
Where price sits relative to higher-timeframe structure: at support, at resistance, mid, above the range, below it. Derived from the chart, never from the corpus.
_Avoid_: Position (means a Holding), level (means the zone itself).

**Level**:
One higher-timeframe price zone that price can arrive at — a weekly or daily order block, a live fair-value gap, or a dealing-range edge.
_Avoid_: Line, support, resistance, zone.

**Reading**:
A Lean and a Location paired for one asset, producing a Verdict.
_Avoid_: Signal, analysis, score.

**Verdict**:
The advice a Reading produces. There are **two sets**, and a Mandate only ever sees one of them — which it gets is decided by whether levels or sentiment leads ([ADR-0002](adr/0002-levels-led-verdicts-for-silent-roster.md)).

- _Sentiment leads_: `ADD`, `TRIM`, `HOLD`, `WATCH`, `NO_VIEW` (the Lean is silent), `NO_READ` (the chart is blank).
- _Levels lead_: `BUY_ZONE`, `SELL_ZONE`, the Location printed as itself (`AT_SUPPORT`, `MID`, `ABOVE_RANGE`, `BELOW_RANGE`), and `NO_READ`. There is no `WATCH` here — every holding has a Location, so there is always something more specific to say. `NO_VIEW` is retired on this path: a silent Lean is an input the chart consults, not a dead end.

`UNREADABLE` is a **Location**, not a Verdict — it means the chart itself is blank. `NO_READ` is its verdict twin, and means the inputs were missing, not that the answer was "do nothing".
_Avoid_: Recommendation, call, action, rating. Listing all the verbs as one flat set — half of them can never appear on a given Mandate.

**Candidate**:
A trade idea not yet owned, produced by `setups` and ranked for the queue. Becomes a Holding only if taken.
_Avoid_: Setup, opportunity, idea, signal.

## Safety and yield

**Safety**:
Whether a yield venue is safe enough to park money in — a hard gate a venue must clear entirely, not a score. Settled 2026-09-14: needs at least one audit on record, plus a minimum on-chain age gated by fork lineage. A venue that clears the gate still gets a separate safety score shown alongside it (incentive mix, history stability, incidents) — flagged, never hidden behind one pass/fail. Full reasoning: [ADR-0006](adr/0006-yield-venue-safety-gate.md).
_Avoid_: Trust — reserved for the crypto roster's unrelated per-voice credibility grade (see issue #1), not this concept.

**Treasury**:
The Mandate that holds money parked for yield, benchmarked against a flat cash rate and nothing else. It is the floor the rest of the book is supposed to beat, so it must stay bond-like. Settled 2026-09-15 ([ADR-0008](adr/0008-treasury-as-a-pot-and-the-deployed-idle-line.md)).
_Avoid_: Savings — that is the SoFi Mandate, which is Treasury's benchmark rather than its contents.

**Deployed**:
Money parked somewhere *because of* the yield, where the principal is meant to hold its value. Treasury owns it. Distinct from a Holding kept for price exposure that happens to accrue — mETH, stkAAVE, staked SOL, locked veAERO all stay in their own Mandate, because the wrapper does not change what you chose to be exposed to.
_Avoid_: Staked, earning, yielding — those describe the mechanism, and the line here is drawn on intent.

**Idle cash**:
Dry powder sitting in a Mandate, waiting to be spent on that Mandate's kind of thing. It stays where it is and is counted there. Treasury **reads** it to advise on it, and never owns it.
_Avoid_: Uninvested, spare. Do not call it Treasury's — it is not.
