# The Dashboard Spends Nothing and Signs Nothing

A Surface you click is more dangerous than one you type. The terminal path to a real order is deliberately awkward — `setups --execute` is off unless typed, the configured network is left unset so it resolves to each venue's own rehearsal, and mainnet is gated behind a typed confirmation phrase — and a browser erases all of that awkwardness by design. Stale tabs, double-clicks and re-submitted POSTs have no terminal equivalent. So the dashboard's limits are drawn before it exists rather than after something goes wrong.

## Decision

Settled 2026-09-19. Two separate lines, not one, because conflating them bans things that are harmless:

- **The money line is absolute.** No request from the browser ever spends metered dollars. `ingest-x` is the only command in the repo that does, and it keeps its typed invocation and its own spend ledger. Subscription-billed full-corpus LLM passes (`distill-roster --force`, `brain-extract --force`) are also off the Refresh control; if they are ever wanted they get a different, separately labelled one, never the button reached for when a page feels stale.
- **The write line is permissive, because `data/` is regenerable ore.** Refresh may run the free fetches — prices, funding, alt-signal, the mirror pull — and they write. Losing what they wrote costs a re-run, which is the whole premise of the `data/` directory.
- **The signing key never enters the web process.** The dashboard may eventually record a trade *intent*; it does not import `execution`, does not load the key, and does not place orders. Signing stays where a human types a confirmation.

## Consequences

- The dashboard cannot place an order by accident regardless of what bug ships, because the credential is not in its address space. This property is free today and unrecoverable later.
- Refresh is slow by nature — seconds for a price fetch, minutes for a stale-cache mirror pull — so it cannot be a synchronous request. It runs as a background job the browser polls.
- The rule is stated positively so it can be checked: a reader asking why Refresh does not pull the X corpus finds the answer here rather than assuming an oversight.
