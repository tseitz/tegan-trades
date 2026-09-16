# Per-View Result Objects, Not a Shared Envelope

ADR-0004 named the layers (sensor, view, surface) but left `review`'s actual output shape alone: `readings_for()` returns a bare tuple — `(Portfolio, list[Reading], tuple[Context, ...])` — and `review.render()` takes the readings list plus several more loose keyword arguments (`portfolio`, `as_of`, cash, staleness) alongside it. Nothing bundles what a renderer needs into one object, so a future JSON or dashboard output would have to reassemble the same arguments a third time.

The instinct going in was a shared envelope — one wrapper shape every view returns, so terminal, email, and a future JSON output can never disagree. That's rejected below: `review` and `treasury` answer different questions with different data, and forcing them into one shape would either strip fields down to their intersection or pad both with fields the other doesn't use.

## Decision

- **Each view returns one bundled, typed result object per call** — `review` gets a `ReviewResult` (holding what `readings_for()` and `render()`'s loose arguments carry today, in one place), `treasury` gets its own `TreasuryResult`. The shared discipline is the habit — one object in, no loose extra arguments — not a shared field set. If two views' shapes turn out to converge later, that's a fact to notice then, not something to force now.
- **A view that needs memory across runs gets one snapshot file**, `data/<view>/state.json`, mirroring the file `digest/state.json` already established. One JSON document per view, all four mandates keyed inside it. It is a snapshot, overwritten each run — losing it costs one repeat, nothing more, the same tolerance `digest`'s own state already assumes.
- **No schema-versioning scheme.** A shape change means the old state file no longer parses as expected; the fix is to delete and let the next run rebuild it. Single-user repo, nothing outside it reads these files.
- **ADR-0004's rule gets one named exception:** "a surface reads views only" holds except where a domain has no view at all and building one is explicitly out of scope. Today that's the crypto/execution trading book — `digest`'s HOLDING section prices it by reading `oracle`/`execution` directly, and that stays, because `review` has no data about open trading positions and a trading-book view isn't part of this rescope. This is not a general escape hatch: it applies only to a domain the map has named out of scope, not to any future convenience.
- **The one part of ADR-0004's rule `digest` actually breaks:** it loads portfolio files straight from `oracle.portfolios`, duplicating a lookup `review.cli` already wraps for the identical purpose. That's a real violation, and the fix is to route it through `review` instead.

## Considered and rejected

- **A shared envelope type across all views.** Rejected — `review`'s data (holdings, verdicts, chart location) and `treasury`'s (yield, trust score) share nothing but the fact that both get rendered. A common shape would mean the intersection of two unrelated domains, which is no fields at all, or padding each with the other's unused ones.
- **Durable history instead of a snapshot for view state.** Rejected for the same reason `digest`'s state already is a snapshot: nothing here is a record that must survive loss — it's memory that only prevents a repeat. `oracle.decisions`/`queue_snapshot` already own the append-log side of the repo for the cases that do need it.
- **Formal schema versioning (a `schema_version` field, migration code).** Rejected as effort spent for a shape only this repo's own code ever reads. Delete-and-rebuild is the entire migration story a personal, single-user project needs.
