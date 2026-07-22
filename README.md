# tegan-trades

Personal signal-and-judgment system. See the full design spec in the vault:
`~/vault/Claude/Projects/tegan-trades/architecture.md`.

**Boundary:** machine-generated → this repo; human/durable → Obsidian vault.
Raw transcripts (the "ore") live in `data/` (gitignored, regenerable-but-protect).

## Layout
- `packages/ingestion` — transcript pullers + raw-transcript store
- `config/watchlist.yaml` — roster source-of-truth
- `docs/` — code-adjacent docs (feasibility findings, API notes)
