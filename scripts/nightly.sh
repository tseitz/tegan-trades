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
#   6. setups --list   free    the queue you actually read
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
mkdir -p "$LOG_DIR"

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
step ingest-x       uv run ingest-x
step distill-roster uv run distill-roster --concurrency 3
step fetch-prices   uv run fetch-prices
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

{
  echo ""
  echo "───── summary ─────"
  printf '%s\n' "${STATUS_LINES[@]}"
  echo ""
  echo "  xAI (real money):      \$${XAI_COST}"
  echo "  claude (Max allowance): \$${CLAUDE_COST} over ${CLAUDE_CALLS} calls"
  echo "  candidates:            ${CANDIDATES:-?}"
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
