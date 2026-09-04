# Phase 5 — Alt-signal data sources — implementation plan

## Design

See `docs/superpowers/specs/2026-09-03-phase5-altsignal-design.md` for the approved design and
the deferred-items reasoning (Dune, Coinglass, Glassnode, fomo.family, the sentiment layer).

**One correction from scouting the real code**, which changes how this wires into `review`:
the design spec assumed alt-signal would thread through `core.setups.Context` the way funding
does (`Context.funding: FundingOutlook | None`, read by `core.review.review()`). **That's
wrong — funding never touches `review` at all.** `Context.funding` is read only inside
`core/setups.py` (the setups queue engine, `packages/core/src/core/setups.py:495,1198`).
`core.review.Reading` (`packages/core/src/core/review.py:234`) carries no funding field and no
general-purpose "extra signal" slot.

The actual precedent for "an independent confirmation line in `review`, not part of the
roster/chart verdict" is the **LEVELS section**, not funding:

- `core.nearby.levels_near(context, ...)` (`packages/core/src/core/nearby.py:87`) — a pure scan,
  independent of `Reading`.
- `review.levels.shortlist(pairs, ...)` (`packages/review/src/review/levels.py:55`) — pure
  ranking/capping, takes `[(Reading, levels), ...]`.
- `review/cli.py:main()` (`packages/review/src/review/cli.py:229-235`) — calls the scan, calls
  `shortlist`, calls `render_levels`, prints it as its own section after the main table.

Alt-signal follows this same three-step shape (scan → rank/format → print), not the
`Context.funding` shape. Tasks below reflect that.

## Patterns to mirror

- **Per-venue fetcher file** → `oracle/sources/hyperliquid.py` (`fetch()` returns
  `list[FundingRate]`, pure `parse_*` helpers, `http.post_json`/`get_json` for I/O).
- **Append-only store** → `oracle/funding_store.py` (`partition_path`, `append`, `read` with
  dedupe-by-key, JSONL partitioned by month, short field names).
- **Orchestrating CLI** → `oracle/funding_cli.py` (`_snapshot()` tries each source, catches
  `FetchError` per-source so one dead venue doesn't cost the others; `--report` summarizes).
- **Independent review section** → `core/nearby.py` + `review/levels.py` + the LEVELS block in
  `review/cli.py:main()`, as described above.
- **Probe-before-build for an unverified third-party API** → `scripts/probe_alchemy_wallet.py`
  and `scripts/probe_plaid_coverage.py`: a standalone script that hits the real API and reports
  what it actually returns, run once by hand before the real fetcher is written against it.

## Tasks

### 1. `core/altsignal.py` — the shared reading type
One dataclass, mirroring `core/funding.py`'s `FundingRate`:
```python
@dataclass(frozen=True, slots=True)
class AltSignalReading:
    source: str    # "defillama" | "pumpfun" | "kalshi" | "polymarket"
    kind: str      # "chain_tvl" | "stablecoin_supply" | "dex_volume" | "graduation" | "market"
    key: str       # chain slug, mint address, or market ticker
    value: float | dict
    observed_at: datetime
```
Generic because the four sources' payloads don't share a shape — unlike `FundingRate`, which
is one number for one venue. No pure helper functions needed yet (unlike `funding.py`'s
`annualized`/`carry_cost`) — nothing downstream computes on this data yet.
**Verify:** `uv run pytest packages/core/tests/test_altsignal.py`

### 2. `cfg/altsignal.yaml` — the hand-curated tracking list
```yaml
chains:
  - asset: SOL
    chain: solana     # DefiLlama's chain slug — not always the ticker
  - asset: ETH
    chain: ethereum

markets:
  - platform: kalshi
    ticker: "FED-25DEC"
    why: "Fed rate decision — roster references Fed policy constantly"
  - platform: polymarket
    slug: "will-btc-hit-150k-in-2026"
    why: "Direct crypto price-target market the roster has discussed"
```
`asset`/`chain` are separate fields (not one ticker) because DefiLlama's chain slugs don't
match ticker symbols (`solana`, not `SOL`) — same reasoning `cfg/oracle_map.yaml` gives for
never guessing a route by name match.

### 3. Three REST fetchers — `oracle/altsignal/{defillama,kalshi,polymarket}.py`
Each mirrors `oracle/sources/hyperliquid.py`'s shape: a pure `parse_*(payload) ->
list[AltSignalReading]` plus a `fetch(*, get_json=http.get_json) -> list[AltSignalReading]`.
- `defillama.py` — `/v2/historicalChainTvl/{chain}`, `/stablecoincharts/{chain}`,
  `/overview/dexs/{chain}` for each chain in `cfg/altsignal.yaml`. No auth.
- `kalshi.py` — market data endpoints for each ticker in `cfg/altsignal.yaml`. No auth needed
  for market reads, per the earlier research pass — **but that claim is not checkable anywhere
  in this codebase** (no existing Kalshi/Polymarket client to verify it against), unlike every
  other claim in this plan. Confirm with one real `curl` against a live market ticker before
  writing `parse_*`/`fetch()` against it — same "verify before trusting" instinct as task 4's
  pump.fun probe, just cheap enough here not to need its own script.
- `polymarket.py` — Gamma API (`gamma-api.polymarket.com`) for each slug. Same auth caveat as
  Kalshi — confirm with a real request first.
**Verify:** `uv run pytest packages/oracle/tests/test_defillama.py packages/oracle/tests/test_kalshi.py packages/oracle/tests/test_polymarket.py` (mocked HTTP, no live calls, same pattern as `test_funding_sources.py`)

### 4. Probe pump.fun before writing its fetcher
Add `scripts/probe_pumpfun_migrations.py`, mirroring `scripts/probe_alchemy_wallet.py`'s
shape and header style: hit a REST-capable indexer (Solana Tracker or similar — PumpPortal
itself is WebSocket-only for graduation events) and report what "recently graduated" actually
returns. Run once by hand; record the confirmed endpoint/shape in the script's own header,
the way `probe_alchemy_wallet.py` records the `sol-mainnet` vs `solana-mainnet` trap.
**This determines what task 5 actually calls — do not write `pumpfun.py` before this runs.**

### 5. `oracle/altsignal/pumpfun.py`
Same `fetch()`/`parse_*` shape as the other three, built against whatever task 4 confirmed.
**Verify:** `uv run pytest packages/oracle/tests/test_pumpfun.py`

### 6. `oracle/altsignal_store.py`
Mirrors `funding_store.py` exactly: `DATA_ROOT = .../data/altsignal`, `partition_path`,
`append(readings, *, root=DATA_ROOT)`, `read(*, root=DATA_ROOT, source=None, kind=None,
since=None)`. Dedupe key is `(source, kind, key, observed_at)`. Short JSONL field names
(`src`, `kind`, `k`, `v`, `t`), same rationale — a nightly sweep across four sources writing
every night.
**Verify:** `uv run pytest packages/oracle/tests/test_altsignal_store.py` (mirrors
`test_funding_store.py`'s five round-trip/partition/dedupe tests)

### 7. `oracle/altsignal_cli.py` — `fetch-altsignal`
Mirrors `funding_cli.py`: a `_snapshot()` that calls all four sources, catching `FetchError`
per-source (one dead API must not cost the other three, same as `_snapshot`'s per-venue
try/except), plus `--report` to summarize what's logged. Register in
`packages/oracle/pyproject.toml`'s `[project.scripts]`:
`fetch-altsignal = "oracle.altsignal_cli:main"`.
**Verify:** `uv run fetch-altsignal --dry-run` (funding_cli's `--dry-run` pattern — fetches,
prints counts, writes nothing) then `uv run pytest packages/oracle/tests/test_altsignal_cli.py`

### 8. Get canonical assets in `main()` — do NOT widen `Read`
Original plan widened the `Read` namedtuple with an `assets` field. **Rejected on review**:
`readings_for()` returns `[(book, *build_readings(...)) for book in books]`
(`cli.py:150`), and `digest/cli.py:402` unpacks it as `for book, readings, contexts in
readings_for(...)` — a fixed 3-tuple. Adding a field turns every element into a 4-tuple and
breaks that unpack with `ValueError: too many values to unpack`, in a package this task would
not otherwise have touched.

Instead, `review/cli.py:main()` recomputes canonical assets locally, using the `canonical_rows`
helper already in the same file (`cli.py:51-59`, the same computation `build_readings` does
internally): `registry = load_registry(CONFIG_DIR)` (already imported at `cli.py:24`), then
`canonical_rows(book, registry)`. Cheap — a registry load and a per-position dict lookup, not
O(corpus) — and `Read`'s shape stays untouched for every existing caller, including `digest`.
**Verify:** `uv run pytest packages/review/tests/test_cli.py` and
`uv run pytest packages/digest -q` both still pass unmodified.

### 9. `review/altsignal.py` — scan + format, mirroring `review/levels.py`
Pure module, no I/O of its own (reads are passed in, same as `levels.shortlist` takes
`levels_near(...)`'s output rather than calling it):
- `chain_lines(readings, assets, *, altsignal_cfg) -> tuple[...]` — one line per holding whose
  canonical asset has a `chains:` entry in `cfg/altsignal.yaml`, using the latest
  `oracle.altsignal_store.read(source="defillama", key=chain)` reading.
- `macro_block(*, altsignal_cfg) -> tuple[...]` — one line per `markets:` entry, using the
  latest `kalshi`/`polymarket` reading, independent of any holding.

**Missing `cfg/altsignal.yaml` → empty, not an error.** Task 2's real market list is a
follow-up decision (see Considerations below), so a fresh checkout will hit this immediately.
Mirror `oracle/route.py:225-231`'s convention for a missing hand-curated cfg file — no chains
tracked, no markets tracked, `chain_lines`/`macro_block` both return `()`, `review` prints
nothing extra rather than raising.
**Verify:** `uv run pytest packages/review/tests/test_altsignal.py`, including a
missing-file case.

### 10. `review/render.py` — `render_altsignal(...)`
Mirrors `render_levels`'s signature shape (takes already-ranked/formatted data, returns a
string block). Two sub-sections: per-holding chain lines, then the MACRO block.
**Verify:** extend `packages/review/tests/test_render.py` with a case for `render_altsignal`.

### 11. `review/cli.py:main()` — wire it in
After the existing LEVELS block (`cli.py:229-235`), add:
```python
chains = altsignal.chain_lines(readings, read.assets, altsignal_cfg=altsignal_cfg)
macro = altsignal.macro_block(altsignal_cfg=altsignal_cfg)
print()
print(render_altsignal(chains, macro))
```
pump.fun is deliberately **not** read here — per the design spec, it's stored-only this round
(`fetch-altsignal --report` is its only surface).
**Verify:** extend `packages/review/tests/test_cli.py` with a case asserting the new section
appears in `main()`'s output when `cfg/altsignal.yaml` has entries, and is absent when it
doesn't.

### 12. `docs/ARCHITECTURE.md` + `scripts/nightly.sh`
- Add `fetch-altsignal` as a 🟡 NETWORK row in the command-cost table, next to `fetch-funding`.
- Add a node to the Mermaid diagram.
- Add `step fetch-altsignal  uv run fetch-altsignal` to `nightly.sh`, ordered alongside
  `fetch-funding` (both are free, order-independent of `setups`/`review` — `review` reads
  `data/altsignal/` at report time, not at fetch time, so it just needs to run before `review`
  in the nightly sequence, same as `fetch-funding` needs to run before `setups`).

### 13. Tests roundup
- `packages/core/tests/test_altsignal.py` — the dataclass.
- `packages/oracle/tests/test_{defillama,kalshi,polymarket,pumpfun}.py` — mocked HTTP,
  mirroring `test_funding_sources.py`'s per-venue structure.
- `packages/oracle/tests/test_altsignal_store.py` — mirrors `test_funding_store.py`.
- `packages/oracle/tests/test_altsignal_cli.py` — mirrors funding CLI test structure.
- `packages/review/tests/test_altsignal.py` — `chain_lines`/`macro_block`, including the
  missing-`cfg/altsignal.yaml` case.
- `packages/review/tests/test_render.py` — add a `render_altsignal` case.
- `packages/review/tests/test_cli.py` — add a `main()` case covering the new section, and
  confirm `packages/digest`'s tests still pass unmodified (task 8's rejected-approach check).

## Considerations / open questions

- **Task 4's probe result is unknown until it runs** — task 5 cannot be scoped more precisely
  than "match whatever the probe finds" until then. This is by design, not a gap: it is the
  same shape as `probe_alchemy_wallet.py` finding the `sol-mainnet`/`solana-mainnet` trap
  before `wallet.py` was written.
- **Kalshi/Polymarket auth**: research found market-data reads need no key on either platform.
  If that turns out wrong for a specific ticker (e.g. a gated market), the fetcher should
  report it the way `fetch-funding` reports an unreachable venue — skip and log, not crash the
  whole snapshot.
- **`cfg/altsignal.yaml`'s market categories are confirmed**: Fed rate decisions, recession
  odds, and BTC/ETH price-target markets. The exact ticker/slug strings still need pulling from
  Kalshi's and Polymarket's live market lists at implementation time (they roll to new
  contract IDs per meeting/date) — that's a task-2 lookup, not a design decision.
