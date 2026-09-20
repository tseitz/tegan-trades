# The Dashboard's Visual Language: Dense, Dark, and Colour Only for Meaning

[ADR-0009](0009-typescript-react-for-the-dashboard.md) bought React so the review screen could be sorted, collapsed and interrogated. It said nothing about how the screen should look, and #84 deferred that on purpose: the first port was checked against the terminal cell by cell, which is only possible while the page is unstyled semantic HTML. That check has passed. Four more screens (`treasury`, `setups`, `compare`, and whatever follows) will be built on whatever is decided here, so the decision is about the next four screens more than this one.

## Decision

Settled 2026-09-20. **Tailwind v4 for the mechanism, and a token layer as the only place the look is defined.**

Tailwind v4 needs no config file — the theme is CSS, in `@theme` in `web/src/index.css`, which is also where the two dense tables get their shared `.data-table` rules. Component libraries were not taken on; `shadcn/ui` installs on top of exactly this setup, one component at a time, and can be reached for when a real popover or dialog appears rather than in advance.

The direction itself, which is the part that outlives the mechanism:

- **Dark, dense, monospace numbers.** `tabular-nums` everywhere, tight rows, a sticky column header. The largest Mandate is 78 Holdings and the screen is read like a terminal, not a document.
- **Colour means something or it is not used.** Four things earn it: which way a verdict points, the sign of a profit-and-loss figure, a stale or mismatched header, and a Holding with no price. Nothing is coloured for decoration.
- **A number never wraps; text may.** This is what keeps eleven columns inside the viewport without a horizontal scrollbar. A wrapped figure reads as two figures.
- **The Surface styles, it does not word.** Every string on the screen still comes from `render.py` through the wire. This decision governs weight, colour and spacing only.

## Considered and rejected

- **A classless base such as Pico.** Genuinely tempting, because the markup was already pure semantic HTML and one import would have styled all of it. Rejected on density: those defaults are tuned for documents, and a 78-row grid fights them on every screen.
- **Hand-rolled CSS with custom properties and no framework.** Zero dependencies, and the JSX would have stayed as clean as it was. Rejected because the fourth screen is the one that matters: nothing stops each new screen inventing its own class names, and a token layer is precisely the thing that is re-derived otherwise.
- **`shadcn/ui` up front.** It is the destination, not the starting point. Taking Radix now would have paid for components no screen has asked for yet.

## Consequences

- The JSX is noisier than it was. Layout carries utility classes; the density and table rules stay in `index.css` under named classes, because both tables need them and a copy per table is how they drift.
- `--spacing-shell-header` is read in two places — the shell header's own height and the sticky `thead` offset beneath it. They are one fact, and a header padding change that ignores it hides the column names.
- `web/src/grid/tone.ts` holds a deliberately partial map of the verdict vocabulary that `core/review.py` owns. An unknown verdict renders uncoloured rather than mis-coloured. The column headers `P&L` and `P&L %` are matched by text for the same reason: no match means no colour, never a wrong colour.
- The commit gate is unaffected. Tailwind touches neither `gen-api-types.sh --check` nor `tsc`, and `scripts/check-web.sh` needed no change.
- Still no front-end tests, as ADR-0009's spec decided. That gap is wider now that the screen has behaviour of its own, and it stays open deliberately until the shape settles.
