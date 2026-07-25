# CLAUDE.md — tegan-trades

Personal signal/trading platform: ingest trusted people → distill theses → cross-reference against ICT technicals. Full project context, design specs, and phase plans live in the vault: `/Users/tseitz/vault/Claude/Projects/tegan-trades/` (and `~/vault/Claude/Soul/` for who tseitz is).

## Workflow rules (these OVERRIDE default/skill behavior)

- **Work directly on `main`. Do NOT create git worktrees.** This is a brand-new solo project — worktrees add friction with no benefit. If a skill (e.g. superpowers `using-git-worktrees`, `executing-plans`, `subagent-driven-development`) wants to create a worktree, **skip that step** and just commit to `main`. This intentionally overrides the global worktree convention in `~/.claude/rules/`.

- **`docs/IMPROVEMENTS.md` is the issue tracker.** When you find a real gap, defect, or better approach *while building something else*, **write it there and keep going** — do not derail the current task to fix it, and do not silently drop it. Read it before starting new work; several entries are decisions already made but not yet executed. An entry must carry evidence (a measurement, a count, a real example), not a hunch. Delete entries when they're done.

## Repo layout

- `packages/ingestion/` — Python (uv) transcript ingestion spine (Phase 1). CLIs: `ingest-roster`, `ingest-channel`.
- `cfg/watchlist.yaml` — roster source of truth (who we ingest).
- `data/` — machine-generated ore (raw transcripts). **Gitignored, never committed.**
- `docs/superpowers/plans/` — implementation plans.

## Running the ingestion

From `packages/ingestion/`: `uv run ingest-roster` (sweep) or `uv run ingest-channel <@handle|URL>`.

Transcript fetching needs a clean IP — YouTube IP-blocks the caption (`timedtext`) endpoint for flagged/datacenter IPs. Set `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` (see `.env.example`) to route transcript fetches through a rotating residential proxy. Metadata (yt-dlp) fetches direct and is unaffected.
