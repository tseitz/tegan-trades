# Treasury as a Pot, and the Deployed–Idle Line

The rescope map (#46) named Treasury as one of four apps and settled that it is read-only. It did not settle whether Treasury *holds* anything. [ADR-0007](0007-net-worth-reader.md) assumed it did ("plus Treasury's balance once Treasury exists") while [ADR-0004](0004-sensor-view-surface-layering.md) placed it as a view, which holds nothing. Grilling on 2026-09-15 settled it: Treasury is a fifth Mandate. It owns money, it carries a benchmark, and it is bond-like — the floor the rest of the book is supposed to beat.

That raises the question ADR-0006 never had to ask. The obvious line — "anything earning yield is Treasury" — pulls `METH` and `STKAAVE` out of `crypto.yaml`, and those are ETH and AAVE price exposure that happens to accrue. Putting them in the stable floor makes the floor risk-on.

## The line is intent, not yield

- **Deployed** — money parked somewhere *because of* the yield. The yield is the whole point; the principal is meant to hold its value. Treasury owns it.
- **Price exposure that accrues** — `METH`, `STKAAVE`, staked SOL, locked veAERO. You chose to hold ETH, AAVE, SOL and AERO. The wrapper does not change that choice. These stay in their own Mandate, and their yield is reported beside them in `review`.
- **Idle cash** — dry powder sitting in a Mandate, waiting to be spent on that Mandate's kind of thing. It stays where it is. Treasury **reads** it to advise on it; it never owns it.

Net worth therefore counts each dollar exactly once: deployed rows in Treasury, idle cash in its home Mandate.

## Benchmark: `flat_rate` only

SoFi's hand-typed cash rate, per [ADR-0003](0003-benchmark-registry-and-held-flat-anchor.md). No index. Treasury is the floor, so judging it against equities would make it look bad every good year, and a section that always reads as a failure stops being read.

SoFi savings is the benchmark, not the contents — a thing cannot be its own baseline, so SoFi stays its own Mandate.

## The file

`data/treasury.yaml`. Hand-kept, one row per parked amount, carrying the venue it sits in. Three facts in `oracle/portfolios.py` force all three of those choices:

- **Outside `data/portfolios/`**, because `portfolios.py`'s account list globs `*.yaml` in that directory. A parked USDC row has no chart and no Lean, so every Treasury row would print `NO_READ` in `review` nightly. Keeping it out means no skip-list to maintain, and the glob keeps meaning "pots you read on a chart."
- **Rows, not a bare `cash:` figure**, because `portfolios.py` rejects a portfolio whose `positions:` list is empty. A cash-only file would not load at all. A row is also the better shape: a parked amount has a venue, an APY and a Safety grade, which a float cannot carry.
- **Hand-kept, not synced**, because `write_positions` replaces everything below the `positions:` marker, so a synced file cannot also hold hand-typed rows — an off-chain T-bill would be erased nightly. Deploying money is rare and deliberate, so typing the row is not a burden.

## Two jobs, one engine

Two different questions need the same machinery — the yields feed, the Safety gate, the APY ranking:

- **"Where should idle cash sit?"** changes what you are exposed to. This is `treasury/`.
- **"Am I leaving free yield on something I already own?"** changes nothing about your exposure. This is a note on the holding's own row in `review`.

Splitting them without duplicating the engine follows ADR-0004 exactly: the **Safety gate and yield ranking are pure, so they live in `core/`** — which ADR-0004 already anticipated, saying `core` "just gains modules (trust-gate scoring, benchmark math) the way it always has." The **yield feed is a sensor, so it lives in `oracle/`**. Both views read down; neither imports the other.

The `review` note is narrow on purpose:

- **Same-asset wrappers only.** "You hold ETH; mETH pays 2.19% for the same ETH exposure." Anything that changes what you are exposed to is a trade idea, and `setups` already owns trade ideas.
- **Only where the current state is detectable.** `METH` and `STKAAVE` resolve by contract address (confirmed on-chain, issue #49). Native Solana stake accounts are invisible to Alchemy's token-balance endpoint, and 2.34 of 8.92 SOL is already staked — so advising on SOL would nag nightly about something already done. SOL joins when a stake-account reader exists.

## Surfaces

`uv run treasury` in the terminal; a `digest` section that prints **only when there is something to say** (money deployed, or idle cash a gated venue beats), matching digest's diff discipline; and the net-worth total.

## Considered and rejected

- **Treasury as a view that owns nothing.** Rejected — it is part of net worth and behaves like a bond, which means it holds principal. A view has no balance to report.
- **Drawing the line on yield rather than intent.** Rejected — it puts AAVE and ETH price risk inside the pot defined as the stable floor, which is the one thing the floor must not have.
- **`wallet-sync` routing every stablecoin into Treasury.** Rejected — it needs a load-time exemption for cash-only files, a rule for the hand-typed rows the sync would erase, and recognition of receipt tokens like `aUSDC` as deployed positions. It also mislabels dry powder: an idle stablecoin in the crypto wallet is money waiting to buy a coin, not money parked for yield.
- **`flat_rate` plus an index.** Rejected — see Benchmark above.
- **`review` importing `treasury` for the yield note.** Rejected — ADR-0004 forbids a view importing a view, and the shared logic is pure, so `core` is where it belongs anyway.
- **Advising on yield whose current state cannot be read.** Rejected — one wrong nag every night is how a section teaches you to skip it.
