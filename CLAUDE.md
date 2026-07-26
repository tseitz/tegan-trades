# CLAUDE.md — tegan-trades

Personal signal/trading platform: ingest trusted people → distill theses → cross-reference against ICT technicals. Full project context, design specs, and phase plans live in the vault: `/Users/tseitz/vault/Claude/Projects/tegan-trades/` (and `~/vault/Claude/Soul/` for who tseitz is).

## Workflow rules (these OVERRIDE default/skill behavior)

- **Work directly on `main`. Do NOT create git worktrees.** This is a brand-new solo project — worktrees add friction with no benefit. If a skill (e.g. superpowers `using-git-worktrees`, `executing-plans`, `subagent-driven-development`) wants to create a worktree, **skip that step** and just commit to `main`. This intentionally overrides the global worktree convention in `~/.claude/rules/`.

- **This is a uv workspace. Run everything from the repo root with `uv run <command>`. Do NOT `cd` into `packages/*/`.** The root `pyproject.toml` declares `[tool.uv.workspace]` over `packages/*`, so one `.venv` and one `uv.lock` at the root cover all six packages and every console script. `cd packages/oracle && uv run setups` is wrong — it will try to build a second, divergent environment. There is no per-package `uv.lock` and no per-package `.venv`; do not create either. Adding a dependency means editing that member's `pyproject.toml` and running `uv sync` **from the root**.

- **`docs/IMPROVEMENTS.md` is the issue tracker.** When you find a real gap, defect, or better approach *while building something else*, **write it there and keep going** — do not derail the current task to fix it, and do not silently drop it. Read it before starting new work; several entries are decisions already made but not yet executed. An entry must carry evidence (a measurement, a count, a real example), not a hunch. Delete entries when they're done.

## Repo layout

Six workspace members under `packages/`, in pipeline order:

- `ingestion/` — transcript pullers + raw-transcript store. CLIs: `ingest-roster`, `ingest-channel`, `ingest-x`. **`ingest-x` is the only command in the repo that spends real money** (xAI, metered) — every other cost in the repo bills against the Max subscription. Read `docs/ARCHITECTURE.md` before running it.
- `distill/` — 🔴 LLM: transcripts → structured theses. CLIs: `distill-roster`, `distill-transcript`, `distill-canon`, `distill-triage`, `distill-migrate-ids`, `fetch-tickers`.
- `brain/` — 🔴 LLM: narrative stance extraction, retrieval, synthesis. CLIs: `brain`, `brain-extract`, `brain-index`.
- `oracle/` — price fetching, routing, grading, cross-reference. CLIs: `fetch-prices`, `score-roster`, `setups`.
- `core/` — pure logic and shared schema. Zero I/O, no network, no LLM. Imported by everything, imports nothing local.
- `llm/` — the **only** LLM boundary (`claude -p`, subscription auth). Exactly three call sites in the repo depend on it.

Other:

- `cfg/` — committed source of truth: `watchlist.yaml` (roster), `oracle_map.yaml` (price routing), `assets.yaml` + `tickers.json` (canon registry).
- `data/` — machine-generated ore. **Gitignored, never committed.**
- `docs/ARCHITECTURE.md` — data flow diagram + **which commands cost money**. Read before re-running anything.
- `docs/superpowers/plans/` — implementation plans.

## Running things

Always from the repo root:

```bash
uv sync                                # one venv, one lock, all six packages
uv run setups                          # any of the 13 console scripts
uv run brain "where is my roster on ETH" --no-llm
uv run pytest -q -m "not integration"  # whole workspace; excludes live-network tests
uv run pytest packages/brain -q        # scope by path, not by --package
```

`uv run --package <name> pytest` scopes the *environment*, not collection — it still collects everything. Use a path to narrow a test run.

**Before re-running any pipeline command, check `docs/ARCHITECTURE.md` for its cost tier.** `distill-roster --force` and `brain-extract --force` are full-corpus LLM passes (666 calls each); both commands are resume-safe *without* `--force`.

Transcript fetching needs a clean IP — YouTube IP-blocks the caption (`timedtext`) endpoint for flagged/datacenter IPs. Set `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` (see `.env.example`) to route transcript fetches through a rotating residential proxy. Metadata (yt-dlp) fetches direct and is unaffected. The two `@pytest.mark.integration` tests in `packages/ingestion/tests/` hit YouTube live and fail without it — that's environmental, not a regression.
