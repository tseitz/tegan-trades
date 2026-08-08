# tegan-trades

Personal signal-and-judgment system. See the full design spec in the vault:
`~/vault/Claude/Projects/tegan-trades/architecture.md`.

**Boundary:** machine-generated → this repo; human/durable → Obsidian vault.
Raw transcripts (the "ore") live in `data/` (gitignored, regenerable-but-protect).

## Layout

A **uv workspace** — seven packages under `packages/`, one `.venv` and one `uv.lock` at the root.

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
uv sync                                # install all seven packages into ./.venv
uv run setups                          # or any of the 20 console scripts
uv run pytest -q -m "not integration"  # whole workspace
```

`uv run` with no argument list shows nothing useful; `ls .venv/bin` is the quickest inventory
of available commands, and `docs/ARCHITECTURE.md` groups them in pipeline order with cost tiers.

## The nightly job

`scripts/nightly.sh` runs the whole cycle — refresh the corpus, re-price, settle yesterday's
orders, rebuild the queue. Awake, it takes 8–16 minutes. Fourteen steps, roughly:

`verify-roster` → `ingest-roster` → `ingest-x` → `distill-roster` → `brain-extract` →
`brain-index` → `fetch-prices` → `fetch-funding` → `reconcile` (both venues) →
`setups --list` → `fetch-tickers` → `canon-drift` → `backup`

`./scripts/nightly.sh --list` prints them in order and is the authoritative list — this one is
prose and will drift. A failing step does not abort the run; the exit code reflects the worst of
them, so a partial night is visible rather than passing as a good one.

**It runs when you open the laptop, not at a set time.** launchd starts the script every 120s;
the script's gate decides in ~50ms whether to go and exits if not. It runs once a day, no
earlier than **06:15 local**, when the machine is genuinely awake — lid open, or on AC power —
and not under 30% battery. Open the lid after 06:15 and the queue is ready within the quarter
hour.

Waiting to be opened is the trade, made on purpose: **nothing here wakes the Mac, so a day the
laptop never opens is a day the cycle does not run.** No data is lost when that happens —
`ingest-x` resumes from the last captured day rather than assuming yesterday, so a gap is
collected on the next run (up to 7 days automatically; beyond that it warns rather than
skipping silently).

Why it is not simply scheduled for 06:15: launchd fires a missed `StartCalendarInterval` during
*DarkWake*, a maintenance wake with the lid shut, and on battery macOS puts the machine back to
sleep — the job is not killed, it is frozen, thawing seconds at a time until you open the lid.
Measured twice (2026-07-27, 2026-08-01): a run that started at 06:16 reported
`ingest-roster (10354s)` and finished at 09:39 — that step takes about five minutes awake —
while `distill-roster`, which happened to begin after the lid opened, took 149s. `caffeinate` cannot
fix it — `-s` is honoured **only on AC power** and a closed lid is not "idle sleep", so `-i`
does not cover it either. It can keep a machine awake; it cannot wake a sleeping one.

Ask why it has not gone yet with `cat data/nightly.gate` — one line, rewritten every poll:

```text
2026-08-01 06:18  deferred: lid closed on battery — macOS sleeps through the run
```

Override the timing gate with `scripts/nightly.sh --force` (or `NIGHTLY_FORCE=1`) — it skips the
hour, lid, battery and once-a-day checks, but **not `data/nightly.pause`**, which stops spending
and so has to mean it. A forced run does not count as the day's run, so testing one by hand
leaves the automatic one still to come. Tune with `NIGHTLY_EARLIEST=0615` and
`NIGHTLY_MIN_BATTERY=30`.

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
