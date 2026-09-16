# Sensor, View, and Surface: the Package Layering Rule

Nine packages existed with no named layering rule between them: `digest/cli.py` imports directly from both `oracle` (raw prices, routing, portfolios) and `review` (an already-computed grid), in the same file. The rescope map (#46) needed to place a new retirement allocator and a new treasury before a spec could be written, which meant fixing the rule those new packages — and the old ones — actually follow.

## Decision

Three layers, enforced only by import direction — no new base class or interface, just a rule about who may import whom:

- **Sensor** — reads from outside the repo (prices, broker connections, wallets). `oracle`'s fetch, route, `plaid.py`, `wallet.py`. A sensor never imports another sensor.
- **View** — reads sensors, answers one interpretive question. `review` (grid, levels, and now the retirement allocator — see below), the new `treasury/` package (yield trust-gate, cash-rate comparison). A view never imports another view.
- **Surface** — reads views only, never a sensor directly. `digest` (the nightly email), CLI output. This is the rule `digest` currently breaks.

## Package boundaries this settles

- **Retirement allocator lives inside `review`, widened** — not a new package. `review` already carries the grid and levels, and tickets #52/#53 already built benchmarks and percent-of-mandate weights there without friction.
- **Treasury splits across the layering rule.** Raw fetching (Plaid cash accounts, yield feeds) goes in `oracle`, alongside the existing `plaid.py`/`wallet.py` connection modules. The yield trust-gate (#59) and cash-rate comparison go in a new package, `treasury/` — not inside `review`, which just picked up retirement and would otherwise become every mandate's dumping ground.
- **`core` is unaffected.** It stays zero-I/O, imported by everyone, imports nothing local, and just gains modules (trust-gate scoring, benchmark math) the way it always has.
- **Crypto desk (`setups` + `execution`) is unchanged for now.** They answer two different questions — what to trade, and how to place it — but splitting them into separate packages is a real refactor of working code, not a boundary the rescope needs to draw. Deferred to its own future effort.

## `digest`'s violation, and how it gets fixed

`digest/cli.py` and `digest/render.py` import from `oracle` and `review` side by side today. Under this rule that's not allowed — a surface reads views only. This ADR doesn't fix it; ticket #56 ("The view-to-data contract"), unblocked by this decision, already asks the right question ("does `digest` read views' state rather than reaching into pipeline internals?") and owns the fix.

## Considered and rejected

- **New package for the retirement allocator.** Rejected — `review` already does this job, and two closed tickets already extended it that way with no sign of strain.
- **Treasury's logic folded into `review`.** Rejected — `review` just gained one mandate's worth of scope (retirement); a second would leave it named after neither job it does.
- **Splitting `execution` out of `oracle` now, since `oracle` is 10,213 lines and mixes fetching with the `setups` trade-finder engine.** Rejected for this map — real, but a separate refactor of already-working code, not a new boundary the rescope's destination requires. Out of scope; a future effort (`ask-matt` or `improve-codebase-architecture`, pending a plugin-config issue) picks it up separately.
