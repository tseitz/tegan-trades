# Benchmark Registry and the Held-Flat Anchor

`oracle/benchmarks.py` today is a 2-entry dict keyed by `domain` (crypto → BTC, everything else → S&P) — a relic from before Mandate existed. ADR 0001 already gave each mandate its own 1-3 benchmarks, tagged `symbol` / `held_flat` / `flat_rate`; this ADR settles how each tag is actually computed, and reshapes `benchmarks.py` to serve them.

## Benchmark set per mandate

Retirement = `held_flat` + `symbol:sp500`. SoFi = `flat_rate` (cash rate) + `symbol:sp500`. Robinhood = `symbol:sp500` for its equities, `symbol:btc` for its crypto sleeve. The crypto mandate = `symbol:btc` + `symbol:eth`. Each mandate's benchmarks answer its own question — SoFi asks "beat cash", retirement asks "beat the market and beat sitting still", crypto asks "beat the two majors" — rather than every mandate being judged against the same index.

## `benchmarks.py` becomes a named registry

A `symbol`-type benchmark now names a key (`sp500`, `btc`, `eth`), not a domain. The module changes from `domain -> (source, symbol)` to `key -> (source, symbol)`, looked up by that key. It also grows two computed benchmarks that aren't simple lookups: `held_flat` (below) and `flat_rate` (reads a hand-typed number straight from the mandate's own config — no live source, no fetch). It stays one module with one job — "what's the baseline to compare a mandate against" — just resolving three kinds of baseline instead of one.

## `held_flat`: anchor once, don't re-baseline

`held_flat` answers "did trading help, or should I have just sat still" — so it needs a starting snapshot to sit still *from*. Two shapes were considered:

- **Re-baseline to today, every time it's asked.** Rejected: right after a deposit, the held-flat line would jump up with it, making a same-day deposit look like a market-beating trade. The comparison would be lying exactly when it's checked most.
- **Anchor once, to the oldest day the account's transaction history reaches, and hold that basket un-rebalanced from there.** Chosen. The anchor is the oldest day Plaid's investment-transaction feed reaches for that account — up to 2 years back, counted from when the account was linked (Plaid's own limit, not a choice made here). From that day forward: every deposit buys into the *same weighted basket* at that day's prices (so new money reads as new money, never as skill), every withdrawal removes proportionally from that basket at that day's prices, and dividends are treated as reinvested into the same basket (so a dividend-heavy holding doesn't read as losing to itself).

This only applies where Mandate carries `held_flat` — today, retirement only. Crypto has no transaction feed to anchor against (self-custody wallet, not a broker), which is part of why it isn't offered `held_flat`.

## Reporting windows

7d / 30d / 90d / 1y / since-inception. Since-inception is capped at the account's transaction history for `held_flat` specifically (the anchor can't reach further back than Plaid does); a `symbol` benchmark has no such cap since its own price series runs as far back as the corpus does.

## Cash rate has no live source

`flat_rate` stays a hand-typed number in the mandate config, updated by hand when it changes — no scheduled reminder to check it. SoFi's advertised APY moves rarely, and SoFi already emails when it does; a second nag system would just be a second thing that goes stale.
