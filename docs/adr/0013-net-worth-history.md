# Net Worth Has a History

[ADR-0007](0007-net-worth-reader.md) kept net worth as one number: tonight's total, plus last night's in `data/digest/state.json` to compare against. That answers "what moved overnight" and nothing longer. The ask is now the same number over time — the email's change over 1, 7, 30 and 365 days, and a chart in the dashboard over any range.

## Decision

Settled 2026-10-15. **A nightly ledger, `data/networth.jsonl`, one row per night, written by its own step.** This supersedes ADR-0007's *Where it lives* and *Memory* sections; *What counts* stands unchanged.

- **The sum moves to `core.networth`**, with the row schema and the one function that turns rows into "change over N days". It is pure, and two Surfaces need it, which is ADR-0004's test for `core`. The email and the dashboard therefore cannot disagree about what "30d" means.
- **Writing is a `networth` package with its own nightly step, `networth --record`**, not part of `digest`. The digest crashed for six nights in October 2026; a history kept inside it would have lost those nights with it. `digest` and the dashboard import the `networth` package's ledger *reader*. That is a Surface reading another Surface's record, which ADR-0004 does not address. It is allowed here because it is read-only and holds no logic; the logic is in `core`.
- **The ledger is irreplaceable.** Holdings move between nights, so a missing night can be estimated at best. Three things follow:
  - **It is merged, never copied.** `networth.jsonl` is in `oracle.mirror.SYNCED`, and a same-day re-run is settled by `recorded_at`, not by position in the file.
  - **A partial night is not recorded.** That covers a portfolio file that failed to load, no files at all, or a set of files that changed without `--books-changed`. A row summed from part of the money would read as a loss that never happened, and nothing downstream could tell.
  - **Each row says how far to trust it.** Stale prices and rows left out for a mismatched mark are recorded on the row, and such a row is never used as a baseline.
- **Rows carry a per-book breakdown.** ADR-0007 declined a per-mandate *delta in the email*, and the email still shows none. The ledger stores it because the chart can use it, and a value not stored on the night it happened cannot be recovered later.
- **`state.json` loses its `net_worth` key.** One source for "last night".

## Not done

- **No estimated backfill.** Today's holdings priced at last year's closes chart a portfolio that never existed. History starts the night this shipped.
- **The execution book stays out**, as under ADR-0007: venue positions are not a Mandate.
