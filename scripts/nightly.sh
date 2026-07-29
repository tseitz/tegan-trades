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

STAMP="$(date +%Y%m%d-%H%M)"
LOG_DIR="$REPO/data/logs/nightly"
LOG="$LOG_DIR/$STAMP.log"
SPEND="$LOG_DIR/spend.json"
mkdir -p "$LOG_DIR"

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
PAUSE_FILE="$REPO/data/nightly.pause"
NO_X_FILE="$REPO/data/nightly.no-x"

if [ -f "$PAUSE_FILE" ]; then
  echo "paused — $PAUSE_FILE exists. Remove it to resume." | tee -a "$LOG"
  exit 0
fi

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
    [ "$WORST" -eq 0 ] || printf ' · **exit %s — see `%s`**' "$WORST" "${LOG/#$HOME/~}"
  } >> "$NOTE"
fi

# Keep 30 nights. The raw xAI responses in data/raw/x/ are ore and are NOT touched here.
# A read loop rather than `xargs -r` — that flag is a GNU extension and BSD xargs would run
# `rm` with no arguments on an empty list.
ls -1t "$LOG_DIR"/*.log 2>/dev/null | tail -n +31 | while read -r old; do rm -- "$old"; done

exit $WORST
