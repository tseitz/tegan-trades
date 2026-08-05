# CLAUDE.md — tegan-trades

Personal signal/trading platform: ingest trusted people → distill theses → cross-reference against ICT technicals. Full project context, design specs, and phase plans live in the vault: `/Users/tseitz/vault/Claude/Projects/tegan-trades/` (and `~/vault/Claude/Soul/` for who tseitz is).

## Workflow rules (these OVERRIDE default/skill behavior)

- **Work directly on `main`. Do NOT create git worktrees.** This is a brand-new solo project — worktrees add friction with no benefit. If a skill (e.g. superpowers `using-git-worktrees`, `executing-plans`, `subagent-driven-development`) wants to create a worktree, **skip that step** and just commit to `main`. This intentionally overrides the global worktree convention in `~/.claude/rules/`.

- **This is a uv workspace. Run everything from the repo root with `uv run <command>`. Do NOT `cd` into `packages/*/`.** The root `pyproject.toml` declares `[tool.uv.workspace]` over `packages/*`, so one `.venv` and one `uv.lock` at the root cover all seven packages and every console script. `cd packages/oracle && uv run setups` is wrong — it will try to build a second, divergent environment. There is no per-package `uv.lock` and no per-package `.venv`; do not create either. Adding a dependency means editing that member's `pyproject.toml` and running `uv sync` **from the root**.

- **`docs/IMPROVEMENTS.md` is a backlog of ideas and feature requests.** Work you would *do*, not things you *learned*. When you find a real gap, defect, or better approach *while building something else*, write it there and keep going — do not derail the current task to fix it, and do not silently drop it. Read it before starting new work; several entries are decisions already made but not yet executed. Delete entries when they're done.

  **The title test: if it reads as a statement rather than an instruction, it is a finding and does not belong here.** "On equities a stop is an intent, not a bound" is a fact about the world; it belongs beside the code that draws stops, where someone editing them will meet it. "Refuse a fill when the open has eaten the stop" is work. Findings filed as to-dos are read once by whoever triages the backlog and never by the person who needs them — and they are what makes entries long, because a fact needs explaining and a task does not.

  Two failure modes this has actually produced, both worth checking for: an entry that **duplicates documentation already in the code** and drifts from it (two entries were stale copies carrying *worse* numbers than the module they described), and **code comments citing `§n`**, which dead-end the moment that entry is correctly deleted. Point at the module or the probe instead; those outlive the tracker.

  **An entry says what to do next and why it's worth doing — never what you learned getting there.** It must cite evidence (a measurement, a count, a real example) but must not *contain* it: one line of number, then a pointer. **Roughly 15 lines is the ceiling**; if an entry is growing past it, the excess is a finding and belongs somewhere else. Do not add "what we measured", "what was tried", "corrections to this entry", or a dated narrative of a session — that is what git history and the code are for.

- **Findings live with the thing they're about, not in the tracker.** A constant's justification goes beside the constant (`STOP_PAD_ATR`, `MAX_TARGET_ATR`); an audit's results go in the probe that produced it (`scripts/probe_*.py`); a caveat about data goes in the reader that loads it (`oracle/decisions.py`). The test: *when would someone need to know this?* — put it where they'll be looking then. A warning nobody reads while editing the code has failed, however well written. `docs/TROUBLESHOOTING.md` holds runbooks for failures that are fixed but not update-safe.

## Repo layout

Seven workspace members under `packages/`, in pipeline order:

- `ingestion/` — transcript pullers + raw-transcript store. CLIs: `ingest-roster`, `ingest-channel`, `ingest-x`. **`ingest-x` is the only command in the repo that spends real money** (xAI, metered) — every other cost in the repo bills against the Max subscription. Read `docs/ARCHITECTURE.md` before running it.
- `distill/` — 🔴 LLM: transcripts → structured theses. CLIs: `distill-roster`, `distill-transcript`, `distill-canon`, `distill-triage`, `distill-migrate-ids`, `fetch-tickers`.
- `brain/` — 🔴 LLM: narrative stance extraction, retrieval, synthesis. CLIs: `brain`, `brain-extract`, `brain-index`.
- `oracle/` — price fetching, routing, grading, cross-reference. CLIs: `fetch-prices`, `fetch-funding`, `score-roster`, `setups`.
- `execution/` — 🔀 **the only package that holds a private key and sends a signed write.** Everything else in the repo reads. Turns an approved `Candidate` into a resting bracket order (limit entry + TP + SL) on Hyperliquid. CLIs: `execute` (pre-flight only; cannot place), `book` (what the account is holding; `--cancel` retires resting entries you select — never positions). Reached from `setups --execute`, which is **off unless typed** — testnet by default, mainnet needs a typed confirmation. Risk settings in `cfg/execution.yaml`; the key lives in `.env` and nowhere else.
- `core/` — pure logic and shared schema. Zero I/O, no network, no LLM. Imported by everything, imports nothing local.
- `llm/` — the **only** LLM boundary (`claude -p`, subscription auth). Exactly three call sites in the repo depend on it.

Other:

- `cfg/` — committed source of truth: `watchlist.yaml` (roster), `oracle_map.yaml` (price routing), `assets.yaml` + `tickers.json` (canon registry).
- `data/` — machine-generated ore. **Gitignored, never committed.**
- `docs/ARCHITECTURE.md` — data flow diagram + **which commands cost money**. Read before re-running anything.
- **A launchd job runs the whole cycle once a day** (`scripts/nightly.sh`), triggered by the laptop being open and awake after 06:15 rather than by a clock — it polls every 120s and gates on lid/power/battery. It spends real money via `ingest-x`. `cat data/nightly.gate` says why it hasn't gone; `touch data/nightly.pause` stops it; see README for the rest. Assume the corpus may have moved since you last looked.
- `docs/superpowers/plans/` — implementation plans.

## Running things

Always from the repo root:

```bash
uv sync                                # one venv, one lock, all seven packages
uv run setups                          # any of the 20 console scripts
uv run brain "where is my roster on ETH" --no-llm
uv run pytest -q -m "not integration"  # whole workspace; excludes live-network tests
uv run pytest packages/brain -q        # scope by path, not by --package
```

`uv run --package <name> pytest` scopes the *environment*, not collection — it still collects everything. Use a path to narrow a test run.

**Before re-running any pipeline command, check `docs/ARCHITECTURE.md` for its cost tier.** `distill-roster --force` and `brain-extract --force` are full-corpus LLM passes (666 calls each); both commands are resume-safe *without* `--force`.

Transcript fetching needs a clean IP — YouTube IP-blocks the caption (`timedtext`) endpoint for flagged/datacenter IPs. Set `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` (see `.env.example`) to route transcript fetches through a rotating residential proxy. Metadata (yt-dlp) fetches direct and is unaffected. The two `@pytest.mark.integration` tests in `packages/ingestion/tests/` hit YouTube live and fail without it — that's environmental, not a regression.
