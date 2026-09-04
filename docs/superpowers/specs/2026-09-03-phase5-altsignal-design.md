# Phase 5 — Alt-signal data sources

## Design

### Motivation

The original Phase 0 plan (`~/vault/Claude/Projects/tegan-trades/roster.md`, `architecture.md`)
named a Phase 5 "alt-signal" tier: outside data that confirms or denies what the roster says,
the same way funding/OI already does. It named Kalshi, Polymarket, DefiLlama (unlocks only),
checkonchain, Dune, and Coinglass (deferred, paid). This spec picks that phase back up, widens
DefiLlama past just unlocks, and evaluates two newer social/memecoin platforms (pump.fun,
fomo.family) that weren't in the original plan.

### Scope — this build

| Source | What we pull | Cost |
| --- | --- | --- |
| **DefiLlama** | Chain TVL history, stablecoin supply by chain, DEX volume by chain | Free, no key |
| **pump.fun** | "Graduation" events — a token surviving its bonding curve and reaching real liquidity | Free, no key |
| **Kalshi** | A hand-picked list of macro/crypto markets (Fed decisions, CPI, recession odds, BTC/ETH price targets) | Free, no key for market data |
| **Polymarket** | Same, via the public Gamma API | Free, no key for market data |

### Explicitly out of scope (see `docs/IMPROVEMENTS.md`)

- **Dune Analytics** — free tier's 10-min execution delay and 40 req/min cap rule out nightly
  automation. §53: revisit once a specific question needs its SQL-over-everything model.
- **Coinglass** — still paid-only ($29/mo), as the original plan already found. Not revisited here.
- **Glassnode** — confirmed still paid-only, worse than expected (real API access starts near
  $999/mo). checkonchain, already on the roster, remains the free substitute.
- **fomo.family** — no public API, and its Terms of Service explicitly forbid scraping. Not
  built. If specific-trader tracking is wanted later, that's Solana wallet-tracking off a
  public address (closer to `oracle/wallet.py`'s model), independent of fomo.family.
- **A "sentiment" synthesis layer** reading across roster + all alt-signal sources — §54:
  belongs on `brain/synthesize.py` once this data exists to feed it, not built alongside it.

### Architecture — extends `oracle`, no new package

Mirrors the existing `funding` subsystem file-for-file:

```
packages/oracle/src/oracle/
  altsignal/
    __init__.py
    defillama.py      # chain TVL, stablecoin supply, DEX volume
    pumpfun.py         # nightly catch-up poll for graduations (see below)
    kalshi.py          # hand-picked markets from cfg/altsignal.yaml
    polymarket.py      # same, via Gamma API
  altsignal_store.py   # append-only log under data/altsignal/, partitioned by month
  altsignal_cli.py     # `fetch-altsignal` — snapshot mode + `--report`
```

This is the same split `oracle/sources/` (`hyperliquid.py`, `aster.py`, ...) + `funding_store.py`
+ `funding_cli.py` already uses for funding. One small file per source, one shared store, one CLI.

### `pump.fun` — nightly poll, not a live listener

pump.fun's "graduation" signal is normally read off a live WebSocket (PumpPortal). This repo's
nightly job runs once and exits — nothing else stays running. Rather than add a new always-on
process (its own health check, its own restart-on-crash story), `pumpfun.py` polls once a night
for "what graduated in the last 24 hours" via a REST-capable indexer (Solana Tracker or similar
— PumpPortal itself is WebSocket-only for this event).

**Follow this repo's own established pattern before writing the real fetcher**: probe first.
Add `scripts/probe_pumpfun_migrations.py`, mirroring `scripts/probe_alchemy_wallet.py` and
`scripts/probe_plaid_coverage.py`, to confirm which provider reliably lists recent graduations
before `pumpfun.py` is written against it. The exact endpoint is not locked in this spec —
that's what the probe is for.

### `core` — one new reading type

`core/funding.py` defines `FundingRate` as the shared type that `oracle` sources produce and
`review` consumes. Alt-signal gets the same treatment: a new `core/altsignal.py` with one
small, deliberately generic dataclass, since the four sources' payloads don't share a shape:

```python
@dataclass(frozen=True, slots=True)
class AltSignalReading:
    source: str      # "defillama" | "pumpfun" | "kalshi" | "polymarket"
    kind: str         # "chain_tvl" | "stablecoin_supply" | "dex_volume" | "graduation" | "market"
    key: str          # chain name, mint address, or market ticker
    value: float | dict
    observed_at: datetime
```

`altsignal_store.py` appends `AltSignalReading` rows the same way `funding_store.py` appends
`FundingRate` rows — one JSONL file per month, deduped on `(source, kind, key, observed_at)`.

### Config — `cfg/altsignal.yaml`

A new hand-curated file, same spirit as `cfg/oracle_map.yaml`: explicit, checked into git,
never auto-discovered.

```yaml
chains:
  - ethereum
  - solana
  # DefiLlama's chain TVL is a DeFi-usage metric — a chain with ~no native DeFi (e.g. plain
  # BTC) will read as ~0. That's an accurate reading, not an error.

markets:
  - platform: kalshi
    ticker: "FED-25DEC"
    why: "Fed rate decision — roster references Fed policy constantly"
  - platform: polymarket
    slug: "will-btc-hit-150k-in-2026"
    why: "Direct crypto price-target market the roster has discussed"
```

### Consumption — one consumer, asymmetric by source

Per the "prove it before deepening" approach `review`'s own LEVELS section already follows:

- **DefiLlama** → `core.setups.Context` gains an optional `altsignal: AltSignalReadings | None`
  field, the same slot `funding: FundingOutlook` already occupies. `core.review.review()` reads
  it and prints one line per crypto holding when the chain in `cfg/altsignal.yaml` matches the
  holding's asset (e.g. a SOL position shows "Solana chain TVL: +12% (7d)" beside its verdict).
- **Kalshi / Polymarket** → not tied to one holding. A small new "MACRO" block in `review`'s
  output, printed once per run, independent of the position table.
- **pump.fun** → **stored only this round.** `review` confirms things already held or already
  theorized about; a brand-new graduating memecoin is neither. Visible via
  `fetch-altsignal --report`. Wiring it into a consumer is deferred until it's decided whether
  it becomes a discovery feed (new candidates) — that decision belongs with §54's sentiment
  layer, not this build.

### Cost tier

All four sources are 🟡 **NETWORK** — free tier, third-party API, rate-limitable — the same
tier `fetch-funding` already carries. `fetch-altsignal` slots into `scripts/nightly.sh` next to
it, and `docs/ARCHITECTURE.md`'s command-cost table and diagram get a new row/node for it.

### Testing

Mirrors the existing funding tests: one `test_<source>.py` per `oracle/altsignal/*.py` module
(mocked HTTP, no live calls), a `test_altsignal_store.py` for the append/read/dedupe round
trip, and a `test_altsignal_cli.py` for the CLI's snapshot and `--report` modes. `core/altsignal.py`
gets its own unit tests for the dataclass and any pure helpers, same as `core/funding.py`.

## Tasks

(To be broken out by `writing-plans` / `pre-implementation-review` in the next step.)
