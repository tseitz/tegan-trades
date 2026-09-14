# tegan-trades

A personal signal and trading platform. Trusted voices and outside data feeds are distilled into a view on an asset; higher-timeframe chart structure says when to act on that view; the result is reported per pot of money, against what that pot is supposed to beat.

This file is the glossary. Decisions live in `docs/adr/`; work lives in GitHub issues.

## Money

**Mandate**:
What a pot of money is for: what it must beat and how much risk it may take, independent of the asset classes it holds. Retirement, savings, robinhood and crypto are today's four mandates, one per portfolio file — "not four accounts" because the mandate is the purpose, not the login.
_Avoid_: Account, portfolio, bucket, strategy.

**Domain**:
An asset class — `crypto` or `stock`. It selects a price source. It is **not** a statement of purpose; that is the Mandate.
_Avoid_: Using it to mean what the money is for.

**Whole**:
The denominator a percentage is taken against: every position inside one Mandate, merged across its accounts. Percentages never cross a Mandate boundary.
_Avoid_: Total, book, net worth.

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
The advice a Reading produces: `ADD`, `TRIM`, `HOLD`, `WATCH`, or `UNREADABLE`. `UNREADABLE` means the inputs were missing, not that the answer was "do nothing".
_Avoid_: Recommendation, call, action, rating.

**Candidate**:
A trade idea not yet owned, produced by `setups` and ranked for the queue. Becomes a Holding only if taken.
_Avoid_: Setup, opportunity, idea, signal.

## Trust and yield

**Trust**:
The property that makes a yield venue acceptable to park money in. Deliberately undefined so far — "mostly blue chip, but open to a validated fork" is an intent, not a definition. Pinning it down is open work.
_Avoid_: Safety, risk score, quality.
