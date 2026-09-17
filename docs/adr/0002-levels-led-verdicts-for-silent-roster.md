# Levels-Led Verdicts for Silent-Roster Mandates

`core.review.verdict_for` checks the roster before the chart: a `SILENT` roster returns `NO_VIEW`
regardless of what `Location` says. On the 78 retirement holdings — where the roster corpus never
speaks about individual equities — the chart reading is still computed (`Reading.location`) but
never reaches the verdict column. `review/levels.py`'s LEVELS section exists only to recover that
thrown-away read.

The map settled that retirement (and any `risk_posture: conservative` mandate, per
[0001](0001-split-mandate-from-domain.md)'s `leads_with`) inverts the priority: the chart leads,
an opinion is a bonus on top — not the other way round.

## Decision

Add a second verdict function for `leads_with: levels` mandates, alongside the existing
`verdict_for` (untouched, still used by `leads_with: sentiment` mandates). It does not touch the
shared `VERDICTS` grid.

**Two chart-only calls, both trend-gated — mirroring `chart_trims`, which already lets a falling
weekly override a silent roster at resistance:**

- `SELL_ZONE` — `AT_RESISTANCE` + `DOWNTREND`. This case already fires today via `chart_trims`
  (labelled `TRIM`); the levels-led path relabels it.
- `BUY_ZONE` — `AT_SUPPORT` + an uptrend. New. The trend gate is deliberate and the only new
  condition: plain support with no trend confirmation is noisy (support breaks constantly), and
  it is easier to loosen a gate later than to walk back a noisy signal.

**Distinct verbs, not `ADD`/`TRIM`.** An opinion can only *upgrade* a level-led call (agree →
stronger; disagree → shown, not overridden — the chart has final say, matching the existing
asymmetry where a bullish roster into resistance stays `HOLD`). "Upgrade" needs a state below it
to upgrade from; reusing `ADD`/`TRIM` would leave nothing to upgrade to.

**`ADD` and `TRIM` are what the upgrade upgrades *to*.** `BUY_ZONE` plus a bullish roster is
`ADD`; `SELL_ZONE` plus a bearish roster is `TRIM`. Written out because the paragraph above only
implies it, and the whole two-verb design rests on it. The two new verbs are the lower states and
the existing pair is the ceiling, so a levels-led mandate reaches the same top answers a
sentiment-led one does — it just needs the chart to get there first.

A **thin** roster (fewer than `MIN_VOICES`, per `core.review`) does not upgrade. `BUY_ZONE` is
not asking anyone to move money and `ADD` is, which is the line `MIN_VOICES` was drawn on.

**Trend is the only gate to `BUY_ZONE`/`SELL_ZONE` — an opinion cannot manufacture one on its
own.** A bullish call at plain `AT_SUPPORT` with no trend confirmation stays labelled
`AT_SUPPORT`; it does not get promoted to `BUY_ZONE`. Start narrow, loosen later if the trend gate
turns out too strict — same reasoning as the gate itself.

**Every other combination prints its location, not a generic `WATCH`.** `MID`, `ABOVE_RANGE`,
`BELOW_RANGE`, and `AT_RESISTANCE`/`AT_SUPPORT` without trend confirmation all print as
themselves. There is no `WATCH` bucket on this path — every holding always has a location, so
there is always more specific context than "watch."

**`NO_READ` is unchanged.** `UNREADABLE` (no range structure to read at all) still returns
`NO_READ` on both paths — it means the chart itself is blank, which is exactly as true for a
levels-led mandate as a sentiment-led one.

**`NO_VIEW` is retired, but only on the levels-led path.** A silent roster stops being a dead end
and becomes an input the chart consults instead. `leads_with: sentiment` mandates keep `NO_VIEW`
exactly as today.

**`review/levels.py`'s "standing on" section folds into the main verdict** for levels-led
mandates — it is redundant once the verdict itself carries the location. The "closing in" section
(a level the position hasn't reached yet) stays separate; it is not this holding's verdict yet,
just a heads-up. Lower priority than the rest of this decision — wording (e.g. how to label an
approaching `BUY_ZONE`) is left open for whoever builds it.

## Considered and rejected

- **Reusing `ADD`/`TRIM` for the chart-only calls.** Rejected — opinion is meant to *upgrade* a
  level-led call, which requires a lower state to upgrade from. `chart_trims` already reuses
  `TRIM` today, but this decision moves that case onto the new levels-led path under `SELL_ZONE`
  too, so both chart-only calls read consistently.
- **Adding a third lean (`NONE`) to the shared `VERDICTS` grid.** Rejected — it would change
  `leads_with: sentiment` mandates' behavior too. A separate function keeps sentiment-led review
  exactly as documented and tested today.
- **Letting a matching opinion promote a plain location into `BUY_ZONE`/`SELL_ZONE`.** Rejected
  for v1 — trend is the only gate, deliberately narrow, consistent with starting narrow on the
  trend gate itself.
- **A generic `WATCH` fallback for every non-zone case.** Rejected — it answers "what should I
  pay attention to" with nothing, the same complaint that motivated `review/levels.py` in the
  first place. Printing the location itself costs nothing and answers "watch what?"
