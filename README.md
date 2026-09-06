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

**It runs on a DigitalOcean droplet, on a fixed schedule — not on the laptop.** `ssh
tegan-droplet` (repo at `/root/tegan-trades`), `crontab -l` there shows the real trigger:

```
0 11 * * * cd /root/tegan-trades && export PATH="/root/.local/bin:$PATH" && ./scripts/nightly.sh --skip reconcile-perps >> /root/tegan-trades/data/logs/nightly-cron.log 2>&1
```

11:00 UTC, every day, no lid or battery involved — the droplet has neither and is always on.
`data/nightly.gate`, `NIGHTLY_EARLIEST` and the lid/battery checks below still exist in the
script and still matter for `data-pull`/`backup` timing, but the lid/AC/battery gate itself is
Mac-only code (`if [ "$(uname -s)" = "Darwin" ]`) and is a no-op on the droplet's Linux box.

**The laptop's launchd job is uninstalled.** `scripts/com.tseitz.tegan-trades.nightly.plist`
still sits in `scripts/`, unused — it is not loaded on this machine, and the section below
describes what it did back when the laptop was the runner, kept for the day this needs to run
on a Mac again.

Historical note, kept because the reasoning still applies if this ever runs on a Mac: launchd's
`StartCalendarInterval` fires during *DarkWake* (a maintenance wake with the lid shut), and on
battery macOS goes straight back to sleep — the job isn't killed, it's frozen, thawing seconds
at a time until the lid opens. Measured twice (2026-07-27, 2026-08-01): a run that started at
06:16 reported `ingest-roster (10354s)` and finished at 09:39 — that step takes about five
minutes awake. `caffeinate -s` is honoured **only on AC power**, and a closed lid isn't "idle
sleep" either, so it can keep an awake machine from sleeping but can't wake a sleeping one. This
is why the droplet is the actual answer, not a `caffeinate` trick on the laptop.

Ask whether a run is running or deferred with `cat data/nightly.gate` — one line, rewritten every
run:

```text
2026-09-06 11:00  deferred: already ran today
```

Override the timing gate with `scripts/nightly.sh --force` (or `NIGHTLY_FORCE=1`) — it skips the
hour, lid, battery and once-a-day checks, but **not `data/nightly.pause`**, which stops spending
and so has to mean it. A forced run does not count as the day's run, so testing one by hand
leaves the scheduled one still to come.

**What it costs.** **Nothing in real money, as of 2026-08-18** — `ingest-x`, the only step
billed in actual dollars, is off by default (`NIGHTLY_WITH_X` in the script says why, and
`scripts/probe_x_contribution.py` is the measurement it rests on). What remains is the day's
distillation against the Max subscription. Both totals land in the log and in one line per night
in `Trade Logs/Nightly.md`.

**Fixed 2026-09-06: the digest no longer writes `--vault` on the droplet.** `digest --vault`
used to resolve the vault anchor to `~/vault/Trading` on whichever machine ran it. On the
droplet that is a bare local folder, not the real Obsidian vault (`~/Obsidian/Main Vault/Trading`
on the Mac) — same name, unrelated directory. The note was landing somewhere only reachable over
SSH, unread, from 2026-09-03 (when the droplet took over the nightly run) until this was caught.
Email (`digest --email`, `DIGEST_TO` in `.env`) is the channel that actually reaches Tegan, so
that's now the only one. `Trade Logs/Nightly.md` — written directly by `nightly.sh`, separate
from `digest --vault` — still lands in that same stranded droplet folder; it is a one-line
dead-job signal rather than something read routinely, and is left as-is pending a decision on
whether it's worth syncing back or dropping too.

### Stopping it

```bash
touch data/nightly.pause     # stop everything; delete the file to resume
touch data/nightly.no-x      # keep the free work, stop spending real money
                             # (redundant while ingest-x is off by default)
XAI_MONTHLY_CAP=5.00         # automatic backstop (default $15/month)
```

Sentinel files rather than flags, because the job runs while you are not at the keyboard and a
file left in `data/` explains its own silence. Pausing loses no data. Run `touch
data/nightly.pause` **on the droplet** — a copy on the laptop does nothing, since the laptop no
longer runs the job.

The monthly cap is a **trailing** check — spend is recorded after a run, so the run that
crosses the line completes and the next is skipped. Overshoot is bounded by one run, ~$0.25.

### Managing the cron job (droplet)

```bash
ssh tegan-droplet
crontab -l                  # see the schedule
crontab -e                  # change it
cd tegan-trades && ./scripts/nightly.sh --force   # run it once now, without waiting for cron
```

There is no install/uninstall step beyond editing the crontab — no plist, no `launchctl`. Logs
are in `/root/tegan-trades/data/logs/nightly/` (30 nights kept) and
`data/logs/nightly-cron.log` (cron's own stdout/stderr, catches anything that escapes the script
before logging starts). `data/logs/nightly/spend.json` has running spend.

### If this ever needs to run on the laptop again (legacy launchd path)

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
re-bootstrap after any edit. `launchd.out` / `launchd.err` in `data/logs/nightly/` would catch
anything that escapes the script under this path — including a failure to start at all, which by
definition the script cannot record.

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
