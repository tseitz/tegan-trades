# Phase 3b — Ranking + Triage/Promotion

## Design

**Goal:** turn the firehose into *what earns attention*. Rank every thesis on intrinsic
signals, present the top of the queue interactively, and let a human promote the keepers into
a durable vault note. Precedes 3.3 (accuracy scores) and 3.5 (synthesis), so ranking uses
**intrinsic signals only** — no per-person accuracy yet.

**Load-bearing constraint (from `architecture.md` §D + living-schema):** `data/theses/` is
regenerable ore — a `--force` re-distill rewrites it with freshly-extracted theses (new `id`s,
new content). Therefore:

> **Promotion = snapshot into the vault, NOT a `status` flip in the firehose.** The vault
> note's *existence* is the durable record of promotion. We never mutate stored theses.
> This mirrors the 3a `canon.resolve` principle: read-time lenses, never write back to ore.

**Decisions settled (this session):**
- Ranking model: **transparent weighted sum** — each signal normalized 0–1 × named weight in a
  `RankWeights` constant, summed. Tunable, explainable.
- Queue UX: **interactive CLI, approve-each** — mirrors `canon_cli --review` (`input_fn`/`out`
  injection for testability). `[a]pprove / [s]kip / [x]archive / [q]uit`.
- Vault note: **append to a running-log note** (`Promoted Theses.md`), one `##` section per
  promotion. (Split to one-file-per-thesis later if per-thesis journaling/backlinks are wanted.)

**Signal definitions (all tunable, v1 best-guess):**
- `conviction`: low=0.33, med=0.66, high=1.0
- `confidence`: the stored 0–1 float, as-is
- `recency`: linear across the **corpus** span (newest `published_at`→1.0, oldest→0.0) — data
  is not live, so relative-to-corpus, not wall-clock.
- `agreement`: distinct canonical persons (excluding author) with a thesis on the same
  `(asset_canonical, direction)`, normalized `min(count/3, 1)`.
- `asset_rank`: crypto CoinGecko rank → decaying curve; resolved-but-no-rank (stocks/indices)
  → neutral 0.5; unresolved asset → 0.
- Default weights: conviction .30 · confidence .25 · recency .20 · agreement .15 · asset_rank .10

**Triage-decisions sidecar:** JSONL under `data/triage/decisions.jsonl` (gitignored ore —
keyed by firehose `id`, which re-distill invalidates anyway). Records skip/archive/promoted so
the queue doesn't re-surface a decided thesis across runs. The vault note — not this file — is
the durable promotion record.

## Patterns to Mirror
- `packages/core/src/core/canon.py` — pure read-time lens, `@dataclass(frozen=True)`, no I/O in
  the resolve path. `rank.py` is the same shape: pure scoring, corpus context passed in.
- `packages/distill/src/distill/canon_cli.py` — `scan()` walks `data/theses/*/*.json`;
  `review(..., input_fn=input, out=print)` injects I/O for unit tests. `triage_cli.py` copies
  this exactly.
- `packages/distill/src/distill/store.py` — `DATA_ROOT`, `load_theses` for reading the corpus.

## Tasks

### 1. `packages/core/src/core/rank.py` — pure ranking contract (TDD)
- `RankWeights` frozen dataclass + `DEFAULT_WEIGHTS`; `CONVICTION_SCORE` map.
- Per-signal fns: `conviction_signal`, `confidence_signal`, `recency_signal(published_at, *,
  newest, oldest)`, `agreement_signal(count, *, cap=3)`, `asset_rank_signal(rank, *, resolved)`.
- `AgreementIndex` frozen dataclass built from the corpus: `(asset_canonical, direction) →
  frozenset[person_canonical]`; `build_agreement_index(pairs)`; `.count_for(asset, direction,
  self_person)`.
- `score(thesis, resolved, index, *, weights=DEFAULT_WEIGHTS, newest, oldest) -> float`.
- Tests `test_rank.py`: each signal (boundaries), asset_rank None/unresolved/ranked, agreement
  excludes self, score composition & determinism, weight override.

### 2. `packages/distill/src/distill/triage_cli.py` — interactive queue (TDD)
- `RankedThesis` dataclass (thesis, resolved, score, source_path).
- `rank_corpus(theses_root, registry) -> list[RankedThesis]`: walk docs, `resolve` each,
  build `AgreementIndex`, score, sort desc; skip empty docs.
- Decisions sidecar: `load_decisions(path) -> dict[id,str]`, `record_decision(path, id, decision)`
  (append JSONL). `rank_corpus` caller filters out already-decided ids.
- Vault note: `render_note(ranked) -> str` (`##` section: person · asset · direction ·
  timeframe; invalidation + key_levels; summary; quotes; source URL). `append_note(vault_path,
  section)`.
- `triage(ranked, *, decisions_path, vault_path, input_fn=input, out=print) -> dict` — loop:
  print header + summary, read a/s/x/q, on approve append note + record promoted, on s/x record,
  on q break. Returns counts.
- `main(argv)`: `--top N` (default 20), `--vault PATH` (default project vault note),
  `load_registry`, `rank_corpus`, `triage`.
- Tests `test_triage_cli.py`: `rank_corpus` ordering + empty-doc skip + decided-filter;
  `render_note` shape; `triage` with injected `input_fn` iterator (approve→note written +
  sidecar; skip/archive→sidecar only; quit→stops); decisions round-trip.

### 3. Wiring
- `packages/distill/pyproject.toml`: entry point `distill-triage = "distill.triage_cli:main"`.
  (No new deps — json/yaml already present.)
- Verify: `uv run pytest -m "not integration"` green in both packages; `uvx ruff check`.

## Considerations / open questions
- **Sandbox friction (expected):** writing to the vault path (`~/vault/...`) and to
  `data/triage/` from a subdir cwd may be sandbox-blocked (same class as `fetch-tickers`).
  Handle via `sandbox-friction` → `update-config` when we first run it live; unit tests use
  `tmp_path` so they're unaffected.
- **macro_lean in the queue:** kept (they still earn attention); no hard filter since we chose
  weighted-sum over filter-gate. Revisit if they dominate noisily.
- **Agreement has no time window in v1** (whole corpus). Fine at ~2-month span; add a window if
  the corpus grows to span regimes.
- **Corpus is on the OLD prompt** (no `asset_heard`) until the deferred `--force` re-distill.
  Ranking doesn't depend on `asset_heard`, so 3b is credit-free and correct now; re-distill only
  improves asset resolution later.
- Deferred, unchanged: re-distill on credit restore; `config/` sandbox-write friction.
