# The Net Worth Reader

Ticket #60 was the last open branch on the rescope map (#46): a single cross-mandate number — "how much do I have, all in" — that no existing view can answer, because every existing view deliberately stays inside one Mandate's boundary.

## What counts

Net worth is each Mandate's holdings plus that Mandate's own cash, summed across all four Mandates, plus Treasury's balance once Treasury exists (contributing nothing today since Treasury isn't built — the reader's shape doesn't need to change when it is). Cash counts here even though it's excluded from `review`'s percent-of-mandate column: that exclusion is about a weight denominator, not about whether cash is an asset.

This is the one deliberate, narrow exception to "percentages never cross a Mandate boundary" — every other reader still respects it.

## Where it lives

`digest`, as a new pure module (`digest/networth.py`), matching the pattern its other pure modules already set (`diff.py`, `holdings.py`, `roster.py`). No new layer is needed under ADR-0004: `digest` is already a surface reading multiple views for its nightly report, and summing across those same views' results is the same kind of work, not a new kind. It isn't reachable from anywhere else (no `review`-CLI command) — nothing asked for that, and a pure module is cheap to lift out later if it's wanted.

## Fresh data, not state files

The reader reads each view's live result object (`ReviewResult`, and `TreasuryResult` once it exists) within the same nightly run digest already computes them in. A view's `state.json` exists to remember *across* runs; re-reading it here would be a second, possibly-stale path to data already live in hand this run.

## Memory: one number, reusing what already exists

`digest` already keeps `data/digest/state.json` to know what it said yesterday, specifically so a multi-day window doesn't repeat itself nightly. Net worth gets a `net_worth` key added to that same file rather than a new memory store — one total figure (e.g. "net worth: $X, +2.1% vs. yesterday"), not a per-mandate breakdown. A per-mandate delta would just re-report what `review`'s own allocation view and digest's PORTFOLIO section already show; the point of this number is being the one figure that isn't mandate-scoped.

## `Whole` is struck from the glossary

`CONTEXT.md` had defined `Whole` as the Mandate-scoped denominator. That word was never meant to be repo vocabulary — it was M1's own pie/slice UI language ("percentage of the pie") that got written down as if it were canonical. It's removed; "everything inside one Mandate, merged across its accounts" gets described in plain words wherever it's actually needed, and `Net worth` (this ADR) is now the one glossary term for the cross-mandate sum.
