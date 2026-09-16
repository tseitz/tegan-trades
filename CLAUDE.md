# CLAUDE.md — tegan-trades

Personal signal/trading platform: ingest trusted people → distill theses → cross-reference against ICT technicals. Full project context, design specs, and phase plans live in the vault: `/Users/tseitz/vault/Claude/Projects/tegan-trades/` (and `~/vault/Claude/Soul/` for who tseitz is).

## Workflow rules (these OVERRIDE default/skill behavior)

- **Default is `main`, no worktree.** Skills must not create one on their own. If tseitz asks for a worktree by name, create it at `.claude/worktrees/<branch-name>` (gitignored) per the convention in `~/.claude/rules/`. **`git worktree add` fails on this repo** until you apply the git-crypt fix in `docs/TROUBLESHOOTING.md`.

- **This is a uv workspace. Run everything from the repo root with `uv run <command>`. Do NOT `cd` into `packages/*/`.** One `.venv` and one `uv.lock` at the root cover every member and console script; `cd packages/oracle && uv run setups` builds a second, divergent environment. Never create a per-package lock or venv. Add a dependency by editing that member's `pyproject.toml` and running `uv sync` **from the root**.

- **The backlog is GitHub Issues.** `gh issue list` — see `docs/agents/issue-tracker.md`; the ranked view is the pinned **Where to start** issue. Read it before starting new work: several issues are decisions already made but not yet executed. Close an issue when it's done. Labels are `status:open` · `status:partly-done` · `status:decided` · `status:watching` for state, and `corpus-supply` · `durability` · `venue-execution` · `routing` · `scoring` for theme. The triage labels in `docs/agents/triage-labels.md` are a separate vocabulary, owned by `/triage`.

  **Comments across the repo cite entries as `§n`** — roughly 199 of them, in `cfg/`, `scripts/` and `docs/`. Resolve them with `docs/agents/improvements-map.md`, which also lists the refs that no longer resolve and the `§` that never meant this tracker.

  **Filing is the skills' job, and needs no permission.** `/wayfinder`, `/to-tickets` and `/triage` write issues as their normal output; asking before every one stalls them. **This overrides §4b of `~/.claude/rules/common/development-workflow.md`** — that rule's caution is carried by the title test instead.

  **The title test: if it reads as a statement rather than an instruction, it is a finding and does not belong here.** "On equities a stop is an intent, not a bound" is a fact about the world; it belongs beside the code that draws stops. "Refuse a fill when the open has eaten the stop" is work.

  **An issue says what to do next and why it's worth doing — never what you learned getting there.** Cite evidence, don't contain it: one line of number, then a pointer. **Roughly 15 lines is the ceiling.** This governs backlog items, not `/to-spec` output — a spec is a different artifact, and `/to-tickets` is what turns it into items that obey the rule.

- **Findings live with the thing they're about, not in the tracker.** A constant's justification goes beside the constant; an audit's results go in the probe that produced it (`scripts/probe_*.py`); a caveat about data goes in the reader that loads it. The test: *when would someone need to know this?* — put it where they'll be looking then. A warning nobody reads while editing the code has failed, however well written. `docs/TROUBLESHOOTING.md` holds runbooks for failures that are fixed but not update-safe.

## Repo layout

Workspace members under `packages/`, in pipeline order. **Each module's docstring carries its own reasoning — read it there, not here.**

- `ingestion/` — transcript pullers + raw-transcript store. `ingest-roster`, `ingest-channel`, `ingest-x`. **`ingest-x` is the only command in the repo that spends real money** (xAI, metered); every other cost bills against the Max subscription.
- `distill/` — 🔴 LLM: transcripts → structured theses. `distill-roster`, `distill-transcript`, `distill-canon`, `distill-triage`, `distill-migrate-ids`, `fetch-tickers`.
- `brain/` — 🔴 LLM: narrative stance extraction, retrieval, synthesis. `brain`, `brain-extract`, `brain-index`, `brain-mcp`.
- `oracle/` — price fetching, routing, grading, cross-reference, plus the broker and wallet connections, both read-only by construction. `fetch-prices`, `fetch-funding`, `fetch-altsignal`, `score-roster`, `setups`, `verify-roster`, `plaid-link`, `plaid-sync`, `wallet-sync`.
- `execution/` — 🔀 **the only package that holds a private key and sends a signed write.** Everything else in the repo reads. `execute` (pre-flight only), `book`. Reached from `setups --execute`, which is **off unless typed** — testnet by default, mainnet needs a typed confirmation. Risk settings in `cfg/execution.yaml`. The signing key lives in `.env`, which is **not** single-purpose: it also holds the digest's `DIGEST_SMTP_*` credentials, which sign nothing.
- `digest/` — **a diff, not a report.** What changed since last night. Sits above the pipeline and reads down; nothing imports it. `digest`.
- `review/` — **the mirror image of `setups`.** That queue asks *should I open this*; this asks *should I keep what I already hold*. `review`, free, places nothing. Portfolio files live in `data/portfolios/*.yaml`, **gitignored because this repo is public and share counts are not configuration**.
- `core/` — pure logic and shared schema. Zero I/O, no network, no LLM. Imported by everything, imports nothing local.
- `llm/` — the **only** LLM boundary (`claude -p`, subscription auth). Four call sites depend on it.

Other:

- `cfg/` — committed source of truth: `watchlist.yaml` (roster), `oracle_map.yaml` (price routing), `assets.yaml` + `tickers.json` (canon registry).
- `data/` — machine-generated ore. **Gitignored, never committed.**
- `CONTEXT.md` is the glossary; `docs/adr/` holds the hard-to-reverse decisions; `docs/ARCHITECTURE.md` has the data flow and **which commands cost money** — read it before re-running anything.
- **A cron job on a DigitalOcean droplet (`ssh tegan-droplet`, repo at `/root/tegan-trades`) runs the whole cycle daily at 11:00 UTC** (`scripts/nightly.sh`; see `crontab -l` there, not launchd). **It spends no real money by default** — `--with-x` restores the one command that does. `cat data/nightly.gate` says why it hasn't gone; `touch data/nightly.pause` stops it. Assume the corpus may have moved since you last looked. `digest` runs last, deliberately outside the `step` framework, and without `--vault` on the droplet.

## Running things

Always from the repo root:

```bash
uv sync                                # one venv, one lock, every member
uv run setups                          # any console script
uv run brain "where is my roster on ETH" --no-llm
./scripts/check.sh                     # THE GATE — ruff + the suite pre-commit runs. ~14s.
uv run pytest packages/brain -q        # scope by path, not by --package
```

`scripts/check.sh` is the only thing that reproduces the commit gate. Running `pytest -m "not integration"` by hand also collects the `needs_ore` tests — they pass on a populated `data/` and fail on a fresh clone, so the command that looks equivalent is not. `uv run --package <name> pytest` scopes the *environment*, not collection; narrow a run with a path.

**Before re-running any pipeline command, check `docs/ARCHITECTURE.md` for its cost tier.** `distill-roster --force` and `brain-extract --force` are full-corpus LLM passes (666 calls each); both are resume-safe *without* `--force`.

Transcript fetching needs a clean IP — YouTube IP-blocks the caption endpoint for flagged and datacenter IPs. Set `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` (see `.env.example`) to route through a rotating residential proxy; metadata fetches direct and is unaffected. The two `@pytest.mark.integration` tests in `packages/ingestion/tests/` hit YouTube live and fail without it — environmental, not a regression.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `tseitz/tegan-trades`, driven by the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and one `docs/adr/` at the repo root. See `docs/agents/domain.md`.
