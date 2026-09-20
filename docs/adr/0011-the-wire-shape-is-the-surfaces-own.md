# The Dashboard's Wire Shape Is Its Own, Not the View's Result Object

[ADR-0005](0005-per-view-result-objects.md) gave each View one bundled result object and deliberately refused schema versioning for it, on the stated grounds that "nothing outside it reads these files". [ADR-0009](0009-typescript-react-for-the-dashboard.md) put a browser outside it. Either the View's result object becomes a public contract that can no longer change freely, or something in between absorbs the change.

## Decision

Settled 2026-09-19. **The API never serializes a View's result object.** The dashboard Surface owns its own response models and translates each View's result into them; TypeScript types are generated from the resulting OpenAPI schema so the front end cannot drift from the API without the build failing.

This keeps ADR-0005 intact rather than arguing with it. `ReviewResult` stays free to gain, lose or reshape fields without a version number **because** the translation layer is where that change is absorbed. [ADR-0004](0004-sensor-view-surface-layering.md) says a Surface computes nothing of its own; it says nothing about shaping, and shaping is precisely a Surface's job.

The choice is also forced rather than merely preferred: `core.review.Reading` exposes `market_value`, `pnl` and `pnl_pct` as computed **properties, not fields**, so any reflexive whole-object serialization silently drops the three numbers the review table is largely made of, and produces a page that looks entirely plausible while being wrong.

## Considered and rejected

- **Serialize the result objects directly.** The property problem above makes this wrong on day one, and it would convert every View's internal shape into a contract the browser depends on.
- **Convert `core`'s records to a serialization library so they cross the wire unaided.** Rejected — `core` is the zero-I/O package every other package imports, and reshaping it for a browser's convenience inverts the dependency that makes it safe.
- **Hand-written TypeScript interfaces mirroring the API by eye.** Rejected: the drift is silent and shows up as a wrong number on screen rather than a failed build, which is the same failure mode the property problem already threatens.
