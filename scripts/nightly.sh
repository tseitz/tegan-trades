#!/bin/bash
# The nightly cycle: refresh the corpus, re-price, and rebuild the setups queue.
#
# Ordered so the free checks run before anything is spent, and so a step that fails cannot
# silently poison the next one:
#
#   1. verify-roster   free    catches a channel whose marker no longer matches reality
#   2. ingest-roster   free    new YouTube transcripts
#   3. ingest-x        $$      xAI — the ONLY step that spends real money
#   4. distill-roster  Max     LLM extraction, subscription-billed
#   5. fetch-prices    free
#   6. fetch-funding   free    what holding a position costs — must precede setups
#   7. setups --list   free    the queue you actually read
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

mkdir -p "$REPO/data"

# Tested against "1" rather than for non-emptiness, so NIGHTLY_FORCE=0 means what it reads as.
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
[ "${NIGHTLY_FORCE:-0}" = "1" ] && FORCE=1

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
  exec /usr/bin/caffeinate -s -i -m /bin/bash "$0" "$@"
fi

# Not stamped under `--force`, so running one by hand does not silently eat the day's automatic
# run — the next poll would otherwise say "already ran today" and the night would be skipped by
# the act of testing it.
[ "$FORCE" -eq 0 ] && date +%F > "$STAMP_FILE"
rm -f "$GATE_FILE"

STAMP="$(date +%Y%m%d-%H%M)"
LOG_DIR="$REPO/data/logs/nightly"
LOG="$LOG_DIR/$STAMP.log"
SPEND="$LOG_DIR/spend.json"
mkdir -p "$LOG_DIR"

# Monthly ceiling on real money. Only ingest-x spends dollars; everything else bills against
# the Max subscription. Default is deliberately loose relative to the ~$7/mo the 11-handle
# digest actually costs, so it is a runaway backstop rather than a budget you fight.
#
# **It is a trailing check, not a pre-authorisation.** Spend is recorded after a run, so the
# run that crosses the line still completes and the *next* one is skipped. Overshoot is
# bounded by one run — about $0.25 — which is not worth pre-estimating a call's cost to avoid.
XAI_MONTHLY_CAP="${XAI_MONTHLY_CAP:-15.00}"
MONTH="$(date +%Y-%m)"
SPENT_THIS_MONTH=$(uv run python - "$SPEND" "$MONTH" <<'PY' 2>/dev/null || echo "0.00"
import json, sys
try:
    print(f'{json.load(open(sys.argv[1])).get(sys.argv[2], 0.0):.2f}')
except Exception:
    print("0.00")
PY
)

WORST=0
declare -a STATUS_LINES

step() {
  local name="$1"; shift
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
    [ $WORST -lt 1 ] && WORST=1
  elif [ $rc -ne 0 ]; then
    STATUS_LINES+=("  FAIL  $name (${secs}s) rc=$rc")
    WORST=2
  else
    STATUS_LINES+=("  ok    $name (${secs}s)")
  fi
  return 0
}

echo "tegan-trades nightly · $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"

step verify-roster  uv run verify-roster
step ingest-roster  uv run ingest-roster

# The only step that spends real money, so the only one with a way to skip it. Skipping it
# does NOT lose the days it would have covered — `ingest-x` resumes from the last capture, so
# a paused week is picked up on resume (up to its own 7-day lookback cap).
if [ -f "$NO_X_FILE" ]; then
  STATUS_LINES+=("  skip  ingest-x — $NO_X_FILE exists")
elif awk -v s="$SPENT_THIS_MONTH" -v c="$XAI_MONTHLY_CAP" 'BEGIN{exit !(s>=c)}'; then
  STATUS_LINES+=("  skip  ingest-x — \$$SPENT_THIS_MONTH spent this month, cap \$$XAI_MONTHLY_CAP")
  [ $WORST -lt 1 ] && WORST=1
else
  step ingest-x     uv run ingest-x
fi

step distill-roster uv run distill-roster --concurrency 3
step fetch-prices   uv run fetch-prices

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
# duplicate guard and appears to hold budget it never spent (§33, §40). Settling first means
# the queue is built against what actually happened. `setups --list` does not read the order
# log today, only `--execute` does; this is ordered for when that changes, and costs nothing.
#
# **Read-only.** `book` without `--cancel` cannot change anything at the venue, which is what
# makes it safe unattended. It exits 0 whether or not the venue killed anything, so a night
# that discovers three rejections is an `ok` line and the detail is in the log.
#
# **It settles Alpaca's half, not the book.** `book` talks to one venue — `cfg/execution.yaml`
# says `alpaca` — so the Hyperliquid testnet orders stay `placed` regardless. That is not a
# scheduling gap to close by adding a second invocation: §33 is why. `wire.order_requests`
# sends no `cloid`, so Hyperliquid cannot be asked what became of a given candidate at all, and
# a second step would faithfully report "nothing came back" every night.
step reconcile      uv run book --reconcile

step setups         uv run setups --list

# ── what it cost, from the two places cost is actually reported ──
CLAUDE_COST=$(grep -o 'usage-equivalent cost: \$[0-9.]*' "$LOG" \
  | sed 's/.*\$//' | awk '{s+=$1} END {printf "%.2f", s+0}')
CLAUDE_CALLS=$(grep -c 'usage-equivalent cost:' "$LOG" || true)
# Read from ingest-x's own reported line. An earlier version summed data/raw/x/ by modification
# time and silently re-counted the same day's manual runs into the nightly total — it reported
# $2.56 for a run that spent $0.22. The command that spends the money reports the money.
XAI_COST=$(grep -o '^\[ingest-x\] cost: \$[0-9.]*' "$LOG" \
  | sed 's/.*\$//' | awk '{s+=$1} END {printf "%.2f", s+0}')

CANDIDATES=$(grep -oE '^[0-9]+ candidates' "$LOG" | tail -1 | cut -d' ' -f1)
DROPPED=$(grep -oE '[0-9]+ theses dropped' "$LOG" | tail -1 | cut -d' ' -f1)

# Pulled out of the log for the same reason `FUNDING` is: `book --reconcile` exits 0 whether it
# settled nothing or discovered that the venue rejected every order last night, so the exit code
# cannot carry it. An order killed at the open means a candidate never traded and budget that
# looked committed never was — the one outcome here worth reading the next morning.
KILLED=$(grep -oE '[0-9]+ killed by the venue' "$LOG" | tail -1 | cut -d' ' -f1)

# Funding is reported separately from the step's exit code because it fails in a way the exit
# code cannot see: `fetch-funding` records what it *did* reach and returns 0, so losing one
# venue of three looks identical to a clean run. That matters more here than elsewhere —
# Hyperliquid and Aster can be backfilled afterwards, Lighter cannot (§22), so a silently
# skipped venue is silently unrecoverable data.
FUNDING=$(grep -oE '^[0-9]+ observations logged' "$LOG" | tail -1 | cut -d' ' -f1)
FUNDING_FAILED=$(grep -cE '^  ! (hyperliquid|lighter|aster)' "$LOG" || true)
if [ "${FUNDING_FAILED:-0}" -gt 0 ]; then
  STATUS_LINES+=("  WARN  fetch-funding — ${FUNDING_FAILED} venue(s) unreachable, see log")
  [ $WORST -lt 1 ] && WORST=1
fi

# Accumulate real spend into its own small file rather than re-deriving it from the logs — the
# logs rotate at 30 nights, which would silently reset the cap partway through a long month.
uv run python - "$SPEND" "$MONTH" "$XAI_COST" <<'PY' 2>/dev/null || true
import json, sys
path, month, amount = sys.argv[1], sys.argv[2], float(sys.argv[3])
try:
    data = json.load(open(path))
except Exception:
    data = {}
data[month] = round(data.get(month, 0.0) + amount, 4)
json.dump(data, open(path, "w"), indent=2, sort_keys=True)
PY
SPENT_TOTAL=$(uv run python - "$SPEND" "$MONTH" <<'PY' 2>/dev/null || echo "$XAI_COST"
import json, sys
try:
    print(f'{json.load(open(sys.argv[1])).get(sys.argv[2], 0.0):.2f}')
except Exception:
    print("0.00")
PY
)

{
  echo ""
  echo "───── summary ─────"
  printf '%s\n' "${STATUS_LINES[@]}"
  echo ""
  echo "  xAI (real money):      \$${XAI_COST}  ·  \$${SPENT_TOTAL}/${XAI_MONTHLY_CAP} this month"
  echo "  claude (Max allowance): \$${CLAUDE_COST} over ${CLAUDE_CALLS} calls"
  echo "  candidates:            ${CANDIDATES:-?}"
  echo "  orders killed:         ${KILLED:-0}"
  echo "  funding observations:  ${FUNDING:-0}"
  echo "  theses dropped:        ${DROPPED:-0}"
  echo "  exit:                  $WORST"
} | tee -a "$LOG"

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
    [ "$WORST" -eq 0 ] || printf ' · **exit %s — see `%s`**' "$WORST" "${LOG/#$HOME/~}"
  } >> "$NOTE"
fi

# Keep 30 nights. The raw xAI responses in data/raw/x/ are ore and are NOT touched here.
# A read loop rather than `xargs -r` — that flag is a GNU extension and BSD xargs would run
# `rm` with no arguments on an empty list.
ls -1t "$LOG_DIR"/*.log 2>/dev/null | tail -n +31 | while read -r old; do rm -- "$old"; done

exit $WORST
