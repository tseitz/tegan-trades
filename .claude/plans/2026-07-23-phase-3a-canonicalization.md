# Phase 3a — Read-time Canonicalization Layer

> Combined design + tasks. Ephemeral working doc (gitignored). Intent-level — I execute
> inline/supervised. Grounded in the brainstorming session 2026-07-23.
> **Fully deterministic — no LLM calls — so building AND running it costs zero distillation
> credits.** Ideal work while the usage window restores.

## Why

The firehose has heavy person/asset label drift: ~350 distinct asset labels (4+ spellings of BTC;
one-off theme/basket descriptions), multi-author feeds filed under a show name, and auto-caption
mishearings the model silently corrects. Phase 3 groups by person + asset and 3.3 scores by person,
so dirty keys corrupt everything downstream. Fix at **read-time** (resolve on read; never mutate or
re-distill the regenerable ore) via **deterministic curated registries** + a **CoinGecko static
snapshot** for ticker validation.

## Design (approved)

- **`core/canon.py`** — the contract, alongside `thesis.py`:
  - `Registry` — value holding people map, asset alias map, ticker snapshot.
  - **pure** `resolve(thesis, registry) -> ResolvedThesis` — no I/O, a non-mutating *lens* over a
    stored `Thesis`. Never touches the file.
  - `ResolvedThesis` (or `Resolution`) — `{ person_canonical, asset_canonical, asset_valid: bool,
    asset_rank: int | None, unresolved: list[str] }`. Consumers read canonical fields; the
    rank/unresolved metadata feeds the report and (later) 3b confidence weighting.
  - `load_registry(config_dir) -> Registry` — the I/O half (reads the files), kept OUT of `resolve`
    so the core stays trivially testable without fixtures on disk.
- **Registries:**
  - `watchlist.yaml` — extend each person with optional `aliases: [...]` and `members: [...]`
    (multi-author feeds: Technical Roundup = Cred+DonAlt; 1000x = Avi+Jonah). Already the people
    source-of-truth — no second people file.
  - `config/assets.yaml` *(new)* — canonical ticker -> aliases (crypto + indices/commodities/equities);
    theme/basket junk collapses to a `__basket__` / `__macro__` sentinel, never a fake symbol.
  - `config/tickers.json` *(new, committed)* — CoinGecko snapshot trimmed to top-N by market cap
    (~500-1000; covers roster midcaps like GRASS #150). `symbol -> { name, market_cap_rank }`.
- **`distill-canon` CLI** — read-only **report** by default (coverage %, unmapped labels
  frequency-sorted, **suspect tickers** = not-in-snapshot / absurd rank, multi-author reminders);
  `--review` walks unmapped labels and **writes the YAML** for you.
- **`fetch-tickers`** — one-time CoinGecko `/coins/markets` fetcher -> `config/tickers.json`.
  Run manually (network is outside the sandbox); resolve-time never hits the network.

**v1 deliverable:** the `distill-canon` report/review CLI (immediately usable — surfaces drift, lets
you curate). `resolve()` stands ready for 3b/3.3 to import but is not wired into any ranking path yet.

## Tasks (TDD: RED -> GREEN per unit)

1. **`core/canon.py` — types + pure `resolve`.** `Registry`, `ResolvedThesis`; `resolve(thesis,
   registry)`. Tests: alias hit -> canonical; already-canonical passthrough; basket sentinel;
   unknown label -> `unresolved`; CoinGecko-validated vs suspect (rank present/absent). Pure, no I/O.
   - *Consideration:* case/whitespace normalization on lookup keys (`btc`==`BTC`==`Bitcoin `).

2. **`load_registry(config_dir)`.** Reads `watchlist.yaml` (aliases/members), `assets.yaml`,
   `tickers.json` into a `Registry`. Mirror `ingestion/roster.py` `load_watchlist` (`yaml.safe_load`,
   `_REPO_ROOT/config`). Tests with small fixture files.
   - *Consideration:* tolerant of a person lacking `aliases`/`members` (optional keys).

3. **`fetch-tickers` script** (`distill/fetch_tickers.py` + `[project.scripts]`). Mirror
   `ingestion/youtube.py` `_TimeoutSession` (`requests` + default timeout). Paginate `/coins/markets`
   ordered by market cap, top-N, write `config/tickers.json`. Test with mocked HTTP (no live call).

4. **`distill-canon` report** (`distill/canon_cli.py` + entry point). Sweep `data/theses/`, apply
   `resolve`, format: coverage %, unmapped freq-sorted, suspect tickers, multi-author reminders.
   Mirror `roster.py` sweep + `cli.py` argparse. Tests with fixture theses.

5. **`distill-canon --review`.** Walk unmapped labels; prompt map-to-existing-or-new; append to the
   right YAML (asset -> `assets.yaml`; person -> `watchlist.yaml` aliases). Test the writer with
   mocked input; assert YAML round-trips and existing entries are preserved.

6. **Seed data (run, not code):** `fetch-tickers` to produce `config/tickers.json`; run the report
   against the current firehose; hand-curate `config/assets.yaml` + `watchlist.yaml` aliases from the
   frequency-sorted unmapped list. Commit registries + snapshot.

## Patterns to mirror

- Config load + repo-root path: `ingestion/roster.py` (`load_watchlist`, `DEFAULT_WATCHLIST`, `_REPO_ROOT`).
- HTTP with timeout: `ingestion/youtube.py` `_TimeoutSession` (`requests`).
- CLI argparse + entry points: `distill/cli.py` (`roster_main`) + `pyproject.toml [project.scripts]`.
- Firehose sweep: `distill/roster.py` (glob + per-file loop).
- Pydantic model style: `core/thesis.py`.

## Verify (levels, all credit-free)

- Static: ruff/format on new files (don't touch pre-existing lint).
- Unit: `uv run pytest` in `core` + `distill` — new tests green, existing green.
- Real: run `distill-canon` against the current firehose; confirm the report surfaces the known
  drift (BTC/Bitcoin collapse, DeFi Report alias, `Cards`/`Shener` suspects) and coverage climbs as
  `assets.yaml` is curated.

## Notes / carried-forward

- Corpus is still on the **old prompt** (no `asset_heard`) until the deferred `--force` re-distill.
  3a works on it regardless; `asset_heard` (when present post-redistill) is an extra suspect signal.
- Deferred (unchanged): price cross-reference; key_levels number-garble audit; per-author splitting
  (via `members` when 3.3 lands); CoinGecko live API; name->symbol auto-alias from the snapshot; 3b.
- Data hygiene (junk stubs `dQw4w9WgXcQ`/`jpQb-Aia57k`, DeFi Report bare name) is absorbed by 3a
  read-time (junk resolves to `unresolved`, DeFi Report via alias) — no separate hand-fix needed.
