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

## The nightly job

`scripts/nightly.sh` runs the whole cycle: `verify-roster` → `ingest-roster` → `ingest-x` →
`distill-roster` → `fetch-prices` → `setups --list`. Scheduled by launchd at **06:15 local**.

**It does not wake the Mac.** launchd runs a missed `StartCalendarInterval` job when the
machine next wakes, and coalesces several missed days into one run — so on a laptop that sleeps
this is really "the first wake after 06:15". That is fine: `ingest-x` resumes from the last
captured day rather than assuming yesterday, so a gap is collected on the next run (up to 7
days automatically; beyond that it warns rather than skipping silently).

**What it costs.** Roughly **$0.25/night of real money** (xAI, the `ingest-x` step — the only
command in the repo billed in actual dollars) plus the day's distillation against the Max
subscription. Both totals land in the log and in one line per night in
`~/vault/Trading/Trade Logs/Nightly.md`. **A missing line there is the signal** — a dead job
and a quiet market look identical otherwise.

### Stopping it

```bash
touch data/nightly.pause     # stop everything; delete the file to resume
touch data/nightly.no-x      # keep the free work, stop spending real money
XAI_MONTHLY_CAP=5.00         # automatic backstop (default $15/month)
```

Sentinel files rather than flags, because the job runs while you are not at the keyboard and a
file left in `data/` explains its own silence. Pausing loses no data.

The monthly cap is a **trailing** check — spend is recorded after a run, so the run that
crosses the line completes and the next is skipped. Overshoot is bounded by one run, ~$0.25.

### Install / uninstall

```bash
# install
cp scripts/com.tseitz.tegan-trades.nightly.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tseitz.tegan-trades.nightly.plist

# is it loaded? what's the schedule?
launchctl print gui/$(id -u)/com.tseitz.tegan-trades.nightly

# run it once now, without waiting for the schedule
launchctl kickstart -p gui/$(id -u)/com.tseitz.tegan-trades.nightly

# uninstall — unschedule, then remove the copy launchd actually reads
launchctl bootout gui/$(id -u)/com.tseitz.tegan-trades.nightly
rm ~/Library/LaunchAgents/com.tseitz.tegan-trades.nightly.plist
```

`bootout` alone stops it until the next login; the file in `~/Library/LaunchAgents` is what
makes it come back, so **remove both** to uninstall properly. Editing the plist in `scripts/`
changes nothing on its own — launchd reads the copy in `~/Library/LaunchAgents`, so re-copy and
re-bootstrap after any edit.

Logs are in `data/logs/nightly/` (30 nights kept), running spend in
`data/logs/nightly/spend.json`, and `launchd.out` / `launchd.err` catch anything that escapes
the script — including a failure to start at all, which by definition the script cannot record.
