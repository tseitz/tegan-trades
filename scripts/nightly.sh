#!/bin/bash
# The nightly cycle: refresh the corpus, re-price, and rebuild the setups queue.
#
# Ordered so the free checks run before anything is spent, and so a step that fails cannot
# silently poison the next one:
#
#   1. verify-roster   free    catches a channel whose marker no longer matches reality
#   2. ingest-roster   free    new YouTube transcripts
#   3. ingest-x        $$      xAI — the ONLY step that spends real money. OFF by default
#                              since 2026-08-18; see NIGHTLY_WITH_X for why and how to restore
#   4. distill-roster  Max     LLM extraction, subscription-billed
#   5. brain-extract   Max     LLM stance extraction — capped per night, see below
#   6. brain-index     free    local embeddings; MUST follow brain-extract
#   7. fetch-prices    free
#   8. fetch-funding   free    what holding a position costs — must precede setups
#   9. reconcile       free    settle what the venue did with yesterday's orders
#  10. setups --list   free    the queue you actually read
#
# **A failing step does not abort the run.** A YouTube outage should not cost you the price
# refresh, and a bad roster marker should not cost you the whole night. Every step's status is
# recorded and the run's exit code reflects the worst of them, so a partial night is visible
# rather than passing as a good one.
#
# Costs are totalled from the two places they are actually reported — `cost_in_usd_ticks` in the
# xAI raw response, and the `[claude-code-backend] usage-equivalent cost:` lines. They were
# grepped away by hand once and the number was lost, which is exactly what this exists to stop.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# launchd hands over a near-empty PATH — uv and claude both live in ~/.local/bin, and `claude`
# not being found would look like an LLM failure rather than a PATH one.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ── when this is allowed to run ──────────────────────────────────────────────────
#
# **The trigger is "the laptop is genuinely awake", not a clock time.** launchd pokes this
# script every couple of minutes and the gate below decides; open the lid and the next tick
# runs it. Nothing is scheduled to wake the machine, so a day the laptop never opens is a day
# the cycle does not run. That is the intended trade.
#
# It replaced a 06:15 `StartCalendarInterval`, which fired during *DarkWake* — a maintenance
# wake with the lid shut — and on battery macOS went straight back to sleep. The job was not
# killed, it was frozen, thawing for a few seconds per wake. Measured 2026-08-01: the 06:15 run
# limped from 06:16 to 09:26 in 2-45 second slices and reported `ingest-roster (10354s)` for
# about four minutes of actual work, while `distill-roster`, which happened to start after the
# lid opened, took 149s. Same pipeline, same night — the only variable was consciousness.
#
# `caffeinate` cannot fix this and never could: `-s` is honoured **only on AC power**, and a
# closed lid is not "idle sleep" so `-i` does not cover it either (caffeinate(8)). It can stop
# a machine falling asleep mid-run; it cannot wake a sleeping one. So it moved from the plist
# to below the gate — wrapping a poll that exits in 50ms just pokes power management 720 times
# a day for nothing.
PAUSE_FILE="$REPO/data/nightly.pause"
NO_X_FILE="$REPO/data/nightly.no-x"
GATE_FILE="$REPO/data/nightly.gate"
STAMP_FILE="$REPO/data/nightly.last-run"

# Earliest start, local HHMM: after the US close and the overnight session, before the morning
# you would actually read the queue. Opening the lid at 05:00 should not spend the day's run on
# a corpus that has not finished moving.
NIGHTLY_EARLIEST="${NIGHTLY_EARLIEST:-0615}"
# On battery, below this, defer. An awake run is 8-16 minutes (509s/604s/956s across the last
# three, data/logs/nightly/), so this is a "do not start a job on a dying laptop" floor rather
# than a budget.
NIGHTLY_MIN_BATTERY="${NIGHTLY_MIN_BATTERY:-30}"

# How many transcripts `brain-extract` may process in one night.
#
# **The cap is the whole point of this variable.** `brain-extract` with no limit processes
# every un-extracted transcript it can find, and the backlog was 408 when this was added —
# one uncapped night would be ~$159 of Max allowance (measured mean $0.3909/call over 431
# real calls, data/brain-extract-overnight.log) and would almost certainly hit the usage cap,
# at which point every remaining call fails instantly and `--max-consecutive-failures` aborts
# the sweep. A normal day brings 4-8 new transcripts, so 12 keeps pace AND drains the backlog
# by a few a night, at roughly $4.70 of allowance. Raise it deliberately, not by default.
BRAIN_EXTRACT_LIMIT="${BRAIN_EXTRACT_LIMIT:-12}"

# ── whether the nightly pulls X at all ───────────────────────────────────────────
#
# **Off since 2026-08-18, and this is the switch to flip when that changes.** `ingest-x` is the
# only step billed in real dollars, and `scripts/probe_x_contribution.py` measured what the
# money bought: X is 2.6% of the corpus and yields ~1.4 zones a month that YouTube did not also
# find — about $13 each, with twelve of sixteen weekly builds showing none at all. That is a
# defensible price for a live book and a poor one for a paper account, which is the actual
# reason it is off rather than anything wrong with the ingest.
#
# A variable rather than `touch data/nightly.no-x`, deliberately. The file is the right shape
# for "stop spending, I'll think about it" — it is local, immediate, and survives your
# attention. It is the wrong shape for a decision: `data/` is gitignored, so the file records
# no reason and no date, and a wipe of `data/` silently resumes spending. Both switches remain
# and the file still wins where it applies.
#
# To turn it back on: set this to 1 (or run with `--with-x` for a single run). The step's own
# resume behaviour means re-enabling does NOT lose the intervening days — `ingest-x` picks up
# from the last capture, bounded by its own 7-day lookback, so a long pause resumes at a week.
NIGHTLY_WITH_X="${NIGHTLY_WITH_X:-0}"

mkdir -p "$REPO/data"

# ── flags ────────────────────────────────────────────────────────────────────────
#
# These exist because the habit is running this by hand, and the *file* switches below are the
# wrong shape for that: they persist, so `touch data/nightly.no-x` to skip X once means the
# NEXT unattended run silently skips it too, and the file's whole point is that it survives
# your attention. A flag lasts exactly one run, which is what "just this once" means.
#
# The files stay for the unattended case — launchd cannot be handed an argument.
#
# Tested against "1" rather than for non-emptiness, so NIGHTLY_FORCE=0 means what it reads as.
FORCE=0
[ "${NIGHTLY_FORCE:-0}" = "1" ] && FORCE=1
SKIP_STEPS=""
ONLY_STEPS=""

# Kept because the flag loop below consumes `$@`, and the caffeinate re-exec further down has
# to hand the SAME arguments to the second invocation. Losing them there is not a visible
# error: the re-executed script simply parses no flags, so `--force` vanishes and the run
# defers with "already ran today" as though nothing had been asked for.
declare -a ORIGINAL_ARGS=("$@")

ALL_STEPS="verify-roster ingest-roster ingest-x distill-roster brain-extract brain-index \
plaid-sync fetch-prices fetch-funding reconcile reconcile-perps setups fetch-tickers \
canon-drift backup digest"

usage() {
  cat <<'USAGE'
nightly.sh — the whole cycle: refresh the corpus, re-price, rebuild the queue.

  --force            run even if the time/battery/already-ran gate says no
  --with-x           run ingest-x for THIS run (it is OFF by default — real money)
  --no-x             skip ingest-x for THIS run
  --skip a,b         skip these steps
  --only a,b         run only these steps
  --list             print the steps in order and exit
  -h, --help         this

ingest-x is the only step that spends real dollars, and it is OFF by default. Set
NIGHTLY_WITH_X=1 in this script to re-enable it permanently; see the comment there for
what it was measured to be worth.

Examples
  ./scripts/nightly.sh --force               # the usual manual run: everything, free
  ./scripts/nightly.sh --force --with-x      # ...and pull X too, spending money
  ./scripts/nightly.sh --only setups         # just rebuild the queue
  ./scripts/nightly.sh --skip ingest-roster  # everything but the slow one

Persistent switches, for the unattended run (launchd takes no arguments):
  data/nightly.pause   stop everything
  data/nightly.no-x    stop ingest-x until removed (redundant while it is off by default)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1 ;;
    --with-x) NIGHTLY_WITH_X=1 ;;
    --no-x)  SKIP_STEPS="$SKIP_STEPS ingest-x" ;;
    --skip)  shift; SKIP_STEPS="$SKIP_STEPS ${1//,/ }" ;;
    --only)  shift; ONLY_STEPS="$ONLY_STEPS ${1//,/ }" ;;
    --list)  printf '%s\n' $ALL_STEPS; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 64 ;;
  esac
  shift
done

# Refuse a name that matches no step rather than silently running everything — `--only setup`
# for `setups` would otherwise look like it worked and quietly do nothing.
for want in $ONLY_STEPS $SKIP_STEPS; do
  case " $ALL_STEPS " in
    *" $want "*) ;;
    *) echo "unknown step: $want" >&2
       echo "steps: $ALL_STEPS" >&2
       exit 64 ;;
  esac
done

# Single gate for both `step` and the ingest-x block, so a step cannot be selectable one way
# and not the other.
should_run() {
  case " $SKIP_STEPS " in *" $1 "*) return 1 ;; esac
  [ -z "$ONLY_STEPS" ] && return 0
  case " $ONLY_STEPS " in *" $1 "*) return 0 ;; esac
  return 1
}

# One line, overwritten every poll. A file rather than a log line because this runs all day:
# appending would bury the night that mattered under 700 lines of "not yet", and printing
# nothing would leave no way to answer "why hasn't it gone?". `cat data/nightly.gate`.
defer() {
  printf '%s  deferred: %s\n' "$(date '+%Y-%m-%d %H:%M')" "$1" > "$GATE_FILE"
  exit 0
}

# ── stop switches ────────────────────────────────────────────────────────────────
#
# Three of them, because they answer different questions:
#
#   data/nightly.pause   stop everything      "not now"
#   data/nightly.no-x    stop only ingest-x   "keep the free work, stop spending"
#   XAI_MONTHLY_CAP      automatic backstop   "stop before I run out of credits"
#
# Files rather than a flag on the command, because the thing you want to stop runs while you
# are not at the keyboard. `touch data/nightly.pause` from anywhere kills the next run, and the
# file's existence is its own reminder that you did it — a launchctl bootout leaves nothing
# behind to explain the silence, and silence is indistinguishable from a quiet market.
# **`--force` does not override this one.** The rest of the gate is about timing, and forcing
# past it is a reasonable thing to want; pause is about spending, and the run it stops sends
# real money to xAI. A switch called "stop everything" that a convenience flag walks through is
# worse than no switch. Remove the file if you meant it.
[ -f "$PAUSE_FILE" ] && defer "paused — remove $PAUSE_FILE to resume"

if [ "$FORCE" -eq 0 ]; then
  # Base 10 forced: `date +%H%M` zero-pads, and 0615 is not a valid octal literal.
  [ "$((10#$(date +%H%M)))" -lt "$((10#$NIGHTLY_EARLIEST))" ] && defer "before $NIGHTLY_EARLIEST"

  # Written at start, not at finish, so a poll landing mid-run cannot launch a second one.
  [ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$(date +%F)" ] && defer "already ran today"

  # Absent on a machine with no lid, where the empty string correctly fails the "Yes" test.
  LID="$(ioreg -r -k AppleClamshellState -d 4 2>/dev/null \
    | sed -n 's/.*"AppleClamshellState" = \(.*\)/\1/p' | head -1)"
  BATT_PCT="$(pmset -g batt 2>/dev/null | grep -oE '[0-9]+%' | head -1 | tr -d '%')"

  # On AC we run regardless of the lid — that is clamshell-on-a-desk, and `caffeinate -s` is
  # honoured there, so the run will actually finish.
  if ! pmset -g batt 2>/dev/null | grep -q "'AC Power'"; then
    [ "$LID" = "Yes" ] && defer "lid closed on battery — macOS sleeps through the run"
    [ -n "$BATT_PCT" ] && [ "$BATT_PCT" -lt "$NIGHTLY_MIN_BATTERY" ] \
      && defer "battery ${BATT_PCT}% below ${NIGHTLY_MIN_BATTERY}%"
  fi
fi

# Gate passed. Hold the machine awake for the duration — re-exec rather than wrap in the plist
# so the 50ms polls stay out of power management entirely. `/bin/bash "$0"` rather than `"$0"`
# so this does not silently depend on the exec bit surviving a checkout.
if [ -z "${NIGHTLY_CAFFEINATED:-}" ]; then
  export NIGHTLY_CAFFEINATED=1
  exec /usr/bin/caffeinate -s -i -m /bin/bash "$0" ${ORIGINAL_ARGS[@]+"${ORIGINAL_ARGS[@]}"}
fi

# Not stamped under `--force`, so running one by hand does not silently eat the day's automatic
# run — the next poll would otherwise say "already ran today" and the night would be skipped by
# the act of testing it.
[ "$FORCE" -eq 0 ] && date +%F > "$STAMP_FILE"
rm -f "$GATE_FILE"

STAMP="$(date +%Y%m%d-%H%M)"
LOG_DIR="$REPO/data/logs/nightly"
LOG="$LOG_DIR/$STAMP.log"
mkdir -p "$LOG_DIR"

# Monthly ceiling on real money. Only ingest-x spends dollars; everything else bills against
# the Max subscription.
#
# **Set it HERE, not in `.env`.** This is read by bash before any Python runs, and nothing in
# this script sources `.env` — that file is loaded inside the commands by `core.env.load_env`,
# far too late to affect the check below. The plist exports only PATH and HOME. So a cap put
# in `.env` is silently ignored and the default applies, which is exactly the kind of
# not-wrong-just-inert config this repo keeps getting bitten by.
#
# **It is a trailing check, not a pre-authorisation.** Spend is recorded after a run, so the
# run that crosses the line still completes and the *next* one is skipped. Overshoot is
# bounded by one run — about $0.25 — which is not worth pre-estimating a call's cost to avoid.
#
# Raised 15 -> 20 on 2026-08-06, on a figure the same day's work then undercut. The $0.57/night
# that justified it came from the nightly-only history, and `ingestion.spend` went on to measure
# what that history had never seen: July $5.55 tracked against $7.44 real, August $3.07 against
# $7.18. So 20 is headroom over a known-low number, not a derived ceiling. Re-derive it from
# `scripts/nightly_report.py` once a full month of the reconciled ledger exists.
#
# **Still a floor, but a much closer one.** `ingest-x` writes the ledger itself now, so runs made
# by hand reach it — they were most of the gap above. What stays invisible is a call that times
# out: it bills at xAI and returns no response to read `cost_in_usd_ticks` from. See
# `ingestion/spend.py`, which owns this and explains why that trade is accepted.
# Exported so `digest` sees the same ceiling this script gates on. It was a plain shell
# variable, so the child process never inherited it and always fell back to its own
# hardcoded default — the two could not agree, and raising one here changed nothing there.
export XAI_MONTHLY_CAP="${XAI_MONTHLY_CAP:-20.00}"
MONTH="$(date +%Y-%m)"
# Asks `ingestion.spend` rather than reading a path, so the gate and the writer can never
# disagree about where the ledger lives — which they briefly did when it moved out of
# data/logs/nightly/, leaving the cap gating on a file nothing wrote to any more.
SPENT_THIS_MONTH=$(uv run python -c "
from ingestion import spend
print(f'{spend.total():.2f}')" 2>/dev/null || echo "0.00")

WORST=0
RUN_STARTED=$(date +%s)
declare -a STATUS_LINES
# Parallel to STATUS_LINES, but machine-readable: `name|status|seconds`, assembled into a JSON
# row at the end. The pretty lines above answer "what happened last night"; this answers "is
# distill getting slower", which no single run's log can. See scripts/nightly_report.py.
declare -a STEP_RECORDS
# Reasons the run is not clean that NO step status can carry. A step is marked from its exit
# code, and several commands report a failure while exiting 0 — `ingest-roster` aborting on a
# YouTube IP block is the one that exposed this. Others belong to no step at all (`claude`
# auth expiring, the xAI cap). Before this existed those raised WORST while every step stayed
# `ok`, so the run exited 1 and the morning mail could only say "none individually flagged".
# Keep it parallel to STATUS_LINES: the log gets the pretty line, this survives into
# history.jsonl and therefore into the mail.
declare -a REASONS

# Raise the run to at least WARN and say why, in both places.
flag() {
  STATUS_LINES+=("  WARN  $1")
  REASONS+=("$1")
  [ $WORST -lt 1 ] && WORST=1
  return 0
}

step() {
  local name="$1"; shift
  if ! should_run "$name"; then
    STATUS_LINES+=("  skip  $name — deselected")
    return 0
  fi
  local started
  started=$(date +%s)
  echo "" | tee -a "$LOG"
  echo "───── $name ─────" | tee -a "$LOG"
  "$@" >>"$LOG" 2>&1
  local rc=$?
  local secs=$(( $(date +%s) - started ))
  # verify-roster exits non-zero to flag a disagreement, which is information, not a reason to
  # skip the night's ingestion. Recorded as a warning so it still surfaces in the summary.
  if [ "$name" = "verify-roster" ] && [ $rc -ne 0 ]; then
    STATUS_LINES+=("  WARN  $name (${secs}s) — roster disagrees with reality, see log")
    STEP_RECORDS+=("$name|warn|$secs")
    [ $WORST -lt 1 ] && WORST=1
  elif [ $rc -ne 0 ]; then
    STATUS_LINES+=("  FAIL  $name (${secs}s) rc=$rc")
    STEP_RECORDS+=("$name|fail|$secs")
    WORST=2
  else
    STATUS_LINES+=("  ok    $name (${secs}s)")
    STEP_RECORDS+=("$name|ok|$secs")
  fi
  return 0
}

echo "tegan-trades nightly · $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"

step verify-roster  uv run verify-roster
step ingest-roster  uv run ingest-roster

# The only step that spends real money, so the only one with a way to skip it. Skipping it
# does NOT lose the days it would have covered — `ingest-x` resumes from the last capture, so
# a paused week is picked up on resume (up to its own 7-day lookback cap).
if ! should_run ingest-x; then
  STATUS_LINES+=("  skip  ingest-x — deselected")
# Naming the step explicitly is an explicit request, so `--only ingest-x` runs it without
# `--with-x`. Anything else — a bare nightly, a `--skip` of something unrelated — is the
# default path and stays free. The unattended launchd run can never reach this branch, which
# is the point: the step that spends money now requires someone to have typed something.
elif [ "$NIGHTLY_WITH_X" != "1" ] && [ -z "$ONLY_STEPS" ]; then
  STATUS_LINES+=("  skip  ingest-x — off by default (--with-x, or NIGHTLY_WITH_X=1)")
elif [ -f "$NO_X_FILE" ]; then
  STATUS_LINES+=("  skip  ingest-x — $NO_X_FILE exists")
elif awk -v s="$SPENT_THIS_MONTH" -v c="$XAI_MONTHLY_CAP" 'BEGIN{exit !(s>=c)}'; then
  flag "ingest-x skipped — \$$SPENT_THIS_MONTH spent this month, cap \$$XAI_MONTHLY_CAP"
else
  step ingest-x     uv run ingest-x
fi

step distill-roster uv run distill-roster --concurrency 3

# ── the brain: stances, then the vector index over them ──
#
# **This order is load-bearing, not alphabetical.** `brain-index` reads each transcript's
# stance file to populate the `assets` column it uses as a pre-filter (`index_cli._read_assets`),
# so a transcript indexed before its stance exists is stored with NO assets and is invisible
# to every asset-filtered query. Extraction first means a transcript ingested tonight gets its
# stances tonight and is indexed with them in the same run.
#
# Indexing is incremental — it re-embeds only what changed, keyed on the transcript, its
# sidecar AND its stance file, so a stance arriving later still forces a re-index of that one
# transcript. That is what makes it cheap enough to be here: a full pass measured 18,108
# chunks / 1,086s and grows with the corpus, while a quiet night is seconds.
step brain-extract  uv run brain-extract --limit "$BRAIN_EXTRACT_LIMIT"

# Free — local `fastembed` (BAAI/bge-small-en-v1.5), no API, no LLM, no network. The only
# cost is CPU, which is why it has no cap the way brain-extract does.
step brain-index    uv run brain-index

# Before `fetch-prices`, so a position bought today is warmed the same night it appears.
# Only touches accounts that have a Plaid token; a hand-kept file is left exactly alone, which
# is why this can fail without costing the run anything downstream.
step plaid-sync     uv run plaid-sync

# `--all-portfolios` rather than a list of names: this script cannot know about an account
# added to data/portfolios/ after it was written, and a hardcoded name would quietly stop
# warming the new one. `review` would then print it unpriced every night with nothing failing.
step fetch-prices   uv run fetch-prices --all-portfolios

# Ordered before `setups` deliberately — the queue reads this log to price each candidate's
# carry, so running it after would cost the queue a day of freshness for no reason.
#
# **A snapshot, not `--backfill`.** The backfill pulls *realised* settlements and would be the
# better series — complete hourly data instead of one sample a night — but neither venue
# honours the day window (see `funding_cli._backfill`): Hyperliquid returns its 500-row cap per
# symbol and Aster its 1000, so a nightly backfill would append ~39,000 mostly-duplicate rows
# every night to save nothing. Run `fetch-funding --backfill 30` by hand after an outage, or
# paginate Hyperliquid on `startTime` and revisit. The snapshot is ~1,180 rows a night.
#
# This is also the only way Lighter is ever captured — it serves no reconcilable history
# (§22), so a night this step misses is a night of Lighter coverage that cannot be recovered.
step fetch-funding  uv run fetch-funding

# Ordered before `setups` for the same reason `fetch-funding` is: the queue is worth more when
# what it reads is current. `placed` is written from the *submission* reply, so an order the
# venue killed at the open goes on reading as live — it burns its candidate through the
# duplicate guard and appears to hold budget it never spent (§40). Settling first means
# the queue is built against what actually happened. `setups --list` does not read the order
# log today, only `--execute` does; this is ordered for when that changes, and costs nothing.
#
# **Read-only.** `book` without `--cancel` cannot change anything at the venue, which is what
# makes it safe unattended. It exits 0 whether or not the venue killed anything, so a night
# that discovers three rejections is an `ok` line and the detail is in the log.
#
# **Both venues are settled, in two invocations.** `book` talks to one venue at a time —
# `cfg/execution.yaml` says `alpaca` — so this used to leave every Hyperliquid order reading
# `placed` forever. The comment here used to say a second step was pointless because that venue
# could not be asked about a candidate at all; that was wrong. It sends no `cloid`, but the order
# log has stored its oids since the log existed, and `query_order_by_oid` answers on those. When
# the second step was first run it settled six orders that had been unreadable every night —
# including two positions that had been filled and unrecorded for over a week.
step reconcile      uv run book --reconcile

# The perp side. Separate rather than a flag on the step above because a venue is a connection:
# one failing must not cost the other its settlement, and `step` records them independently.
step reconcile-perps uv run book --reconcile --venue hyperliquid

step setups         uv run setups --list

# ── config drift and durability, after the queue is built ──
#
# Ordered last on purpose: neither affects tonight's queue, and both should run even on a night
# an earlier step failed — which the `step` helper already guarantees.

# Free, network, no key. The canon registry decides whether a newly-mentioned coin resolves to
# anything at all, and it only goes stale in one direction — a coin that launched after the last
# refresh is unresolvable until this runs. Cheap enough that nightly beats reasoning about when.
step fetch-tickers  uv run fetch-tickers

# The REPORT half only. `--review` is an interactive curation loop and must never be automated —
# what this does is make the drift visible, because it currently surfaces only when someone
# thinks to run the command by hand. It found 25 unmapped labels the first time it was run
# unprompted, several of them ordinary tickers the roster discusses weekly.
step canon-drift    uv run distill-canon

# Off-machine copy. There is no Time Machine destination on this laptop and data/ is gitignored,
# so before this existed the corpus lived on exactly one disk. See scripts/backup.sh for what is
# copied and what is deliberately not.
step backup         ./scripts/backup.sh

# ── what it cost, from the two places cost is actually reported ──
CLAUDE_COST=$(grep -o 'usage-equivalent cost: \$[0-9.]*' "$LOG" \
  | sed 's/.*\$//' | awk '{s+=$1} END {printf "%.2f", s+0}')
CLAUDE_CALLS=$(grep -c 'usage-equivalent cost:' "$LOG" || true)
# Read from ingest-x's own reported lines — plural: it now calls xAI once per day in the
# window and prints a cost line for each, so this sums them. An earlier version summed
# data/raw/x/ by modification time and silently re-counted the same day's manual runs into the
# nightly total — it reported $2.56 for a run that spent $0.22. The command that spends the
# money reports the money.
#
# **Still a floor, and more visibly so now.** A day that times out bills server-side but
# prints nothing, because the cost is parsed from a response that never arrived. A partial
# run therefore under-reports, which matters because $XAI_MONTHLY_CAP is checked against it.
XAI_COST=$(grep -o '^\[ingest-x\] cost: \$[0-9.]*' "$LOG" \
  | sed 's/.*\$//' | awk '{s+=$1} END {printf "%.2f", s+0}')

# The brain's two layers, reported together because staleness in either one is invisible from
# the outside: `brain_search` answers just as confidently over a corpus it stopped indexing
# three weeks ago. Both were silently 197 transcripts behind when these steps were added.
BRAIN_EXTRACTED=$(grep -oE '^TOTAL: [0-9]+ extracted' "$LOG" | tail -1 | cut -d' ' -f2)
BRAIN_INDEXED=$(grep -oE '^[0-9]+ transcripts indexed' "$LOG" | tail -1 | cut -d' ' -f1)
# The circuit breaker means a usage cap was hit and the rest were never attempted. They cost
# nothing and are retryable, but a night that hits it has NOT kept up and should say so.
if grep -q 'CIRCUIT BREAKER TRIPPED' "$LOG"; then
  flag "brain-extract — circuit breaker tripped, see log"
fi

# ── a step that fails every single item still exits 0 ───────────────────────────
#
# `ingest-roster`, `distill-roster` and `brain-extract` all catch per-item failures, record them
# in their TOTAL line, and return 0. So `step` prints `ok`, the run exits 0, and the summary is
# indistinguishable from a night that had nothing to do.
#
# Not hypothetical. 2026-08-17 and 08-18 each reported fourteen `ok` steps and `exit 0` while
# EVERY LLM call failed on an expired OAuth token — 19 transcripts deep the first night, 28 the
# second, plus 12 stances a night. Nothing in the summary said so; the only tell was
# `claude $0.00 over 0 calls`, which reads exactly like a quiet night unless you already
# suspect. Two full days of distillation and stance extraction were lost in silence, and the
# corpus went on answering queries as though it were current.
#
# Read from the TOTAL line rather than the per-item `!` lines because TOTAL is the command's own
# summary — it survives a change to how individual failures are formatted, and there is exactly
# one of it per step.
total_failed() {
  grep -E "^TOTAL: [0-9]+ $1," "$LOG" | tail -1 | grep -oE '[0-9]+ failed' | cut -d' ' -f1
}

for pair in "ingest-roster:$(total_failed ingested)" \
            "distill-roster:$(total_failed distilled)" \
            "brain-extract:$(total_failed extracted)"; do
  failed="${pair##*:}"
  if [ "${failed:-0}" -gt 0 ]; then
    flag "${pair%%:*} — ${failed} item(s) failed, see log"
  fi
done

# Called out separately from the counts above because it is a different instruction. A few
# failed transcripts are a retry — the next night picks them up, since every LLM step is
# resume-safe. This is every call failing identically and continuing to fail until someone runs
# `claude /login` at a keyboard, which no amount of unattended retrying will accomplish. A night
# that hits this has not fallen behind, it has stopped.
if grep -q 'OAuth session expired' "$LOG"; then
  flag "claude auth expired — run \`claude /login\`, then re-run the LLM steps"
fi

CANDIDATES=$(grep -oE '^[0-9]+ candidates' "$LOG" | tail -1 | cut -d' ' -f1)
DROPPED=$(grep -oE '[0-9]+ theses dropped' "$LOG" | tail -1 | cut -d' ' -f1)

# Pulled out of the log for the same reason `FUNDING` is: `book --reconcile` exits 0 whether it
# settled nothing or discovered that the venue rejected every order last night, so the exit code
# cannot carry it. An order killed at the open means a candidate never traded and budget that
# looked committed never was — the one outcome here worth reading the next morning.
KILLED=$(grep -oE '[0-9]+ killed by the venue' "$LOG" \
  | cut -d' ' -f1 | awk '{s+=$1} END {print s+0}')

# The other half of the same step. `book --reconcile` now also records how filled trades ENDED,
# and a finished trade is the most interesting thing a night can produce — it is the only output
# here that is evidence rather than intention. Reported separately from KILLED because the two
# mean opposite things: an order the venue killed never traded, a close is the one that did.
# Summed, not `tail -1`: there are two reconcile steps and the last one would be the only one
# counted, silently dropping whichever venue ran first.
CLOSED=$(grep -oE '[0-9]+ close\(s\) recorded' "$LOG" \
  | cut -d' ' -f1 | awk '{s+=$1} END {print s+0}')

# Funding is reported separately from the step's exit code because it fails in a way the exit
# code cannot see: `fetch-funding` records what it *did* reach and returns 0, so losing one
# venue of three looks identical to a clean run. That matters more here than elsewhere —
# Hyperliquid and Aster can be backfilled afterwards, Lighter cannot (§22), so a silently
# skipped venue is silently unrecoverable data.
FUNDING=$(grep -oE '^[0-9]+ observations logged' "$LOG" | tail -1 | cut -d' ' -f1)
FUNDING_FAILED=$(grep -cE '^  ! (hyperliquid|lighter|aster)' "$LOG" || true)
if [ "${FUNDING_FAILED:-0}" -gt 0 ]; then
  flag "fetch-funding — ${FUNDING_FAILED} venue(s) unreachable, see log"
fi

# Read only. `ingest-x` writes the ledger itself now (`ingestion.spend`) — this script used to,
# which meant it recorded only the calls the nightly made and every manual run was invisible to
# the cap above. Re-reading here rather than adding $XAI_COST to the earlier figure so the
# number reported is the ledger's, not this script's idea of it.
SPENT_TOTAL=$(uv run python -c "
from ingestion import spend
print(f'{spend.total():.2f}')" 2>/dev/null || echo "$XAI_COST")

{
  echo ""
  echo "───── summary ─────"
  printf '%s\n' "${STATUS_LINES[@]}"
  echo ""
  echo "  xAI (real money):      \$${XAI_COST}  ·  \$${SPENT_TOTAL}/${XAI_MONTHLY_CAP} this month"
  echo "  claude (Max allowance): \$${CLAUDE_COST} over ${CLAUDE_CALLS} calls"
  echo "  brain:                 ${BRAIN_EXTRACTED:-0} extracted · ${BRAIN_INDEXED:-0} indexed"
  echo "  candidates:            ${CANDIDATES:-?}"
  echo "  orders killed:         ${KILLED:-0}"
  echo "  trades closed:         ${CLOSED:-0}"
  echo "  funding observations:  ${FUNDING:-0}"
  echo "  theses dropped:        ${DROPPED:-0}"
  echo "  exit:                  $WORST"
} | tee -a "$LOG"

# ── one machine-readable row per run ──
#
# The per-night log answers "what happened last night" and is rotated away after 30. This
# answers the questions that need history: which step is slowest, is distill drifting upward,
# how often does ingest-x fail, is the corpus still growing. Appended rather than rotated —
# it is ~400 bytes a night, so a decade costs about a megabyte, and the trend IS the value.
# Read it with `uv run python scripts/nightly_report.py`.
# One newline-joined argument rather than a second trailing array: two variable-length arrays
# in one argv cannot be told apart on the receiving side. Reasons are single-line by
# construction (they come straight from `flag`), so a newline is a safe separator.
REASONS_BLOB=""
if [ "${#REASONS[@]}" -gt 0 ]; then
  REASONS_BLOB=$(printf '%s\n' "${REASONS[@]}")
fi

uv run python - "$LOG_DIR/history.jsonl" "$WORST" "$RUN_STARTED" "$XAI_COST" "$CLAUDE_COST" \
  "$CLAUDE_CALLS" "$CANDIDATES" "$FUNDING" "$BRAIN_EXTRACTED" "$BRAIN_INDEXED" "$REASONS_BLOB" \
  "${STEP_RECORDS[@]}" <<'PY' 2>/dev/null || true
import json, sys, time
path, worst, started, xai, claude, calls, cands, funding, bx, bi, reasons, *steps = sys.argv[1:]

def num(v, cast=float, default=0):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default

row = {
    "run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "exit": num(worst, int),
    "duration_s": int(time.time()) - num(started, int),
    # A step that never ran (skipped on the monthly cap, or the script died early) is ABSENT
    # rather than zero — averaging a zero it never spent would quietly drag every trend down.
    "steps": [
        {"name": n, "status": s, "seconds": num(sec, int)}
        for n, s, sec in (rec.split("|") for rec in steps)
    ],
    # Why the run is not clean, when no step's own status can say — see `flag` above. Omitted
    # entirely when empty so a clean row keeps its old shape.
    **({"reasons": [r for r in reasons.split("\n") if r.strip()]} if reasons.strip() else {}),
    "cost": {"xai": num(xai), "claude": num(claude), "claude_calls": num(calls, int)},
    "output": {
        "candidates": num(cands, int),
        "funding": num(funding, int),
        "brain_extracted": num(bx, int),
        "brain_indexed": num(bi, int),
    },
}
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row) + "\n")
PY

# ── the digest: what CHANGED, as opposed to what happened ──
#
# **Deliberately not a `step`.** It has to run after the history row above is written, because
# that row is what it reads for the run-health line — as a step it would report the *previous*
# night's health beside tonight's queue. By this point `STEP_RECORDS` is already serialised, so
# a status recorded here could not reach the summary or the history row anyway.
#
# It IS in `ALL_STEPS` so `--only digest` and `--skip digest` work: that list is the selector,
# not the summary.
#
# Runs last, after `backup`, so nothing it does can cost the night's ore. Both `--vault` and
# `--email` warn rather than fail — a missing vault or an unset DIGEST_* setting is a surface
# lost, not a run lost. A degraded run still exits 0, so the `||` branch below catches only an
# unhandled crash; the survivable failures announce themselves in the digest's own body and
# subject line instead, which is where a person will actually meet them.
if should_run digest; then
  echo "" | tee -a "$LOG"
  echo "───── digest ─────" | tee -a "$LOG"
  uv run digest --vault --email >>"$LOG" 2>&1 || \
    echo "  WARN  digest — see log" | tee -a "$LOG"
fi

# One line per night somewhere it will actually be read. A nightly job that dies silently and a
# quiet market look identical, so the point of this file is that a *missing* line is the signal.
NOTE="$HOME/vault/Trading/Trade Logs/Nightly.md"
if [ -d "$(dirname "$NOTE")" ]; then
  [ -f "$NOTE" ] || echo "# Nightly runs" > "$NOTE"
  {
    printf '\n- **%s** · %s candidates · xAI $%s · claude $%s (%s calls)' \
      "$(date -u +%Y-%m-%d\ %H:%MZ)" "${CANDIDATES:-?}" "$XAI_COST" "$CLAUDE_COST" "$CLAUDE_CALLS"
    # Only when non-zero, like `exit` below. Zero killed is every ordinary night and printing it
    # would train the eye to skip the field that exists to be noticed.
    [ "${KILLED:-0}" -eq 0 ] || printf ' · **%s order(s) killed by the venue**' "$KILLED"
    # The per-trade detail goes to Closed Trades.md, written by `book` itself. This is only the
    # pointer, so a night that finished a trade is visible from the run line.
    [ "${CLOSED:-0}" -eq 0 ] || printf ' · **%s trade(s) closed**' "$CLOSED"
    [ "$WORST" -eq 0 ] || printf ' · **exit %s — see `%s`**' "$WORST" "${LOG/#$HOME/~}"
  } >> "$NOTE"
fi

# Keep 30 nights. The raw xAI responses in data/raw/x/ are ore and are NOT touched here.
# A read loop rather than `xargs -r` — that flag is a GNU extension and BSD xargs would run
# `rm` with no arguments on an empty list.
ls -1t "$LOG_DIR"/*.log 2>/dev/null | tail -n +31 | while read -r old; do rm -- "$old"; done

exit $WORST
