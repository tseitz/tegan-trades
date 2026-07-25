# tegan-trades

Personal signal-and-judgment system. See the full design spec in the vault:
`~/vault/Claude/Projects/tegan-trades/architecture.md`.

**Boundary:** machine-generated → this repo; human/durable → Obsidian vault.
Raw transcripts (the "ore") live in `data/` (gitignored, regenerable-but-protect).

## Layout

A **uv workspace** — six packages under `packages/`, one `.venv` and one `uv.lock` at the root.

- `packages/ingestion` — transcript pullers + raw-transcript store
- `packages/distill` — transcripts → structured theses (LLM)
- `packages/brain` — narrative stance extraction, retrieval, synthesis (LLM)
- `packages/oracle` — prices, routing, grading, cross-reference
- `packages/core` — pure logic + shared schema, zero I/O
- `packages/llm` — the single LLM boundary
- `cfg/watchlist.yaml` — roster source-of-truth
- `docs/ARCHITECTURE.md` — data flow diagram and per-command cost map
- `docs/` — code-adjacent docs (feasibility findings, API notes)

## Usage

Run everything from the repo root — never `cd` into a package.

```bash
uv sync                                # install all six packages into ./.venv
uv run setups                          # or any of the 13 console scripts
uv run pytest -q -m "not integration"  # whole workspace
```

`uv run` with no argument list shows nothing useful; `ls .venv/bin` is the quickest inventory
of available commands, and `docs/ARCHITECTURE.md` groups them in pipeline order with cost tiers.
