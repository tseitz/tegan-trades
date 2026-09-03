# tegan-trades

Personal signal-and-judgment system. See the full design spec in the vault:
`~/vault/Claude/Projects/tegan-trades/architecture.md`.

**Boundary:** machine-generated → this repo; human/durable → Obsidian vault.
Raw transcripts (the "ore") live in `data/` (gitignored, regenerable-but-protect).

## Layout

A **uv workspace** — nine packages under `packages/`, one `.venv` and one `uv.lock` at the root.

- `packages/ingestion` — transcript pullers + raw-transcript store
- `packages/distill` — transcripts → structured theses (LLM)
- `packages/brain` — narrative stance extraction, retrieval, synthesis (LLM)
- `packages/oracle` — prices, routing, grading, cross-reference
- `packages/review` — what to do about positions you already hold
- `packages/core` — pure logic + shared schema, zero I/O
- `packages/llm` — the single LLM boundary
- `cfg/watchlist.yaml` — roster source-of-truth
- `docs/ARCHITECTURE.md` — data flow diagram and per-command cost map
- `docs/` — code-adjacent docs (feasibility findings, API notes)

## Usage

Run everything from the repo root — never `cd` into a package.

```bash
uv sync                                # install all nine packages into ./.venv
uv run setups                          # or any of the 24 console scripts
uv run pytest -q -m "not integration"  # whole workspace
```

`uv run` with no argument list shows nothing useful; `ls .venv/bin` is the quickest inventory
of available commands, and `docs/ARCHITECTURE.md` groups them in pipeline order with cost tiers.

## The nightly job

`scripts/nightly.sh` runs the whole cycle — refresh the corpus, re-price, settle yesterday's
orders, rebuild the queue. Awake, it takes 8–16 minutes. Fourteen steps, roughly:

`data-pull` → `verify-roster` → `ingest-roster` → `ingest-x` *(off by default)* → `distill-roster` → `brain-extract` →
`brain-index` → `fetch-prices` → `fetch-funding` → `reconcile` (both venues) →
`setups --list` → `fetch-tickers` → `canon-drift` → `backup`

It opens with `data-pull` and closes with `backup` on purpose: pull → work → push, so a second
machine cannot build tonight's queue on a stale corpus and then mirror that over the good copy.

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

**What it costs.** **Nothing in real money, as of 2026-08-18** — `ingest-x`, the only step
billed in actual dollars, is off by default (`NIGHTLY_WITH_X` in the script says why, and
`scripts/probe_x_contribution.py` is the measurement it rests on). What remains is the day's
distillation against the Max subscription. Both totals land in the log and in one line per night in
`~/vault/Trading/Trade Logs/Nightly.md`. **A missing line there is the signal** — a dead job
and a quiet market look identical otherwise.

### Stopping it

```bash
touch data/nightly.pause     # stop everything; delete the file to resume
touch data/nightly.no-x      # keep the free work, stop spending real money
                             # (redundant while ingest-x is off by default)
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

## Working on a second machine

`data/` is gitignored, so a fresh clone has an empty corpus and every command that reads down
into it returns nothing. The nightly's last step (`scripts/backup.sh`) already mirrors it to
Google Drive over the rclone API; `scripts/data-pull.sh` is the way back down.

```bash
rclone config create gdrive drive scope=drive   # once per machine, opens a browser
./scripts/data-pull.sh --dry-run                # what would come down, and who wrote it
./scripts/data-pull.sh                          # ~139MB, a few minutes
uv run brain-index                              # once, ~40min, free — see below
```

**Pull before you run anything, not after.** Both scripts are `copy`, so the last machine to
push wins per file. The nightly does this for you — `data-pull` is its first step — but a
command you type by hand does not, so pull first when you sit down mid-day. `data-pull.sh`
defaults to `--update` and will never replace a file that is newer locally; that guard protects
the machine you are sitting at, not the mirror, so it is not a substitute for pulling first.

One caveat the guard creates: `data/logs/nightly/history.jsonl` is append-only run health, so two
machines that both run a night diverge on it and the later push wins. It feeds the digest's run
health only — no trade or price data is affected.

**`data/brain/index.db` is deliberately not mirrored.** It is one 114MB SQLite file that changes
nightly, so syncing it would upload the whole thing every night to protect the only artifact that
costs nothing to rebuild. Run `brain-index` once on a new machine; it is incremental and free
after that. Until it runs, `brain` and its MCP server return nothing.

**`.env` is not mirrored and must never be.** It holds the Hyperliquid signing key, the Plaid
access tokens and the SMTP credentials. Copy it between machines by hand — never through Drive,
which is neither encrypted at rest under your control nor something you can revoke per file.

Portfolio files (`data/portfolios/*.yaml`) *are* mirrored, which is the point: they are
gitignored because share counts are not configuration, so Drive is their only sync path.
