#!/bin/bash
# Bring the corpus down from the mirror `scripts/backup.sh` writes.
#
# The inverse of that script, and it inherits every decision made there — the rclone remote, the
# Drive-API-not-the-mount reasoning, and the exclusions. Read backup.sh's header first; this file
# only documents where the two directions genuinely differ.
#
# **Why a pull exists.** `data/` is gitignored, so a second machine clones the repo and has an
# empty corpus. `setups`, `review` and `digest` all read down into it and would run against
# nothing. The backup was built to survive one laptop dying; this makes the same mirror the way
# a second laptop catches up.
#
# **`--scheduled` is how the laptop runs it unattended** — the gate launchd polls against, with
# `scripts/com.tseitz.tegan-trades.data-pull.plist`. The nightly on the droplet runs this script
# as its own step 2 with no flags, which is the unconditional form; `--scheduled` adds the
# earliest-hour, once-a-day and lid checks, skips the transfer when the mirror has not moved, and
# turns a failure into a notification instead of a line in a log nobody opens.
#
# **`--update` by default, and it is the whole safety story.** Both directions are `copy`, which
# overwrites the destination when a file differs. Pulling therefore *can* replace local ore with
# an older remote copy — the exact failure the backup direction cannot have, because there the
# remote is only ever behind. `--update` makes rclone skip any file that is newer locally, so a
# pull onto a machine that has already run tonight is a no-op rather than a rollback. `--force`
# drops the guard for the one case that wants it: a corpus you believe is damaged.
#
# **`cfg/` is backed up but deliberately not pulled.** It is tracked in git, so `git pull` is
# already its sync path and it will be the newer copy. Restoring it from Drive would silently
# revert a committed watchlist edit, and nothing downstream would report it. Fetch it from the
# mirror by hand only when recovering a machine that has no clone.
#
# **The per-night `.log` files are excluded; `logs/nightly/history.jsonl` is not.** nightly.sh
# prunes to the newest 30 logs but the mirror is `copy`, so the remote keeps every log ever
# written. Pulling them restores files this machine deliberately deleted, and the next run prunes
# them again — churn on every pull, forever. The history ledger beside them is append-only and the
# digest reads it, so it stays.
#
# **`data/brain/index.db` is not in the mirror, and should not be.** It is a single 114MB SQLite
# index that changes every night, so syncing it would upload the whole file daily to protect the
# one artifact that costs nothing to recreate. On a fresh machine run `brain-index` once (~40
# minutes, free, no LLM); the nightly keeps it current after that because indexing is incremental.
# Until that runs, `brain` and the MCP server return nothing on a machine that just pulled.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# Same default and same override as backup.sh, so a second destination stays a one-variable change
# and the two directions can never be pointed at different places by accident.
DEST="${TEGAN_BACKUP_DEST:-gdrive:Coding/tegan-trades}"

UPDATE_FLAG="--update"
DRY_RUN=""
SCHEDULED=0
for arg in "$@"; do
  case "$arg" in
    --force)     UPDATE_FLAG="" ;;
    --dry-run)   DRY_RUN="--dry-run" ;;
    --scheduled) SCHEDULED=1 ;;
    *) echo "usage: $0 [--force] [--dry-run] [--scheduled]" >&2; exit 2 ;;
  esac
done

# ── --scheduled: the gate launchd polls against ──────────────────────────────────
#
# **This polls; it does not schedule.** Same shape and same reasoning as nightly.sh's gate —
# read that one first, including the DarkWake measurement that forced it. A
# `StartCalendarInterval` fires with the lid shut and the job then freezes and thaws for hours.
#
# The one structural difference: this gate ends in a network call, so it cannot run every 120s
# the way the nightly's can. The plist polls at 1800s and the cheap local checks below run
# first, so the manifest is only fetched on a poll that would otherwise go.
GATE_FILE="$REPO/data/data-pull.gate"
STAMP_FILE="$REPO/data/data-pull.last-run"
# Epoch seconds of the first poll that found the mirror unmoved. Cleared the moment it moves, so
# on a healthy cycle it never survives a morning. See the escalation below for what it is for.
UNCHANGED_FILE="$REPO/data/data-pull.unchanged-since"

# How long the mirror may sit unmoved before that becomes a reported failure rather than a quiet
# defer. 36h, not 24: one cycle is a day, and a droplet running a few hours late on a single
# morning must not page you. Anything past a day and a half is a dead pipeline, not a late one.
MIRROR_STALE_AFTER="${MIRROR_STALE_AFTER:-129600}"

# Local HHMM, but the constraint it encodes is UTC: the droplet's cron fires at 11:00 UTC and
# `backup.sh` is its second-to-last step. Across 2026-09-04..16 the run ended between 11:57Z
# and 13:09Z and the duration is trending up (43min mid-month, 65min on the 16th). 0700 PDT is
# 14:00Z — about an hour of margin, two once the clocks go back. 0630 would leave twenty
# minutes, which the trend eats. Raise it, don't lower it.
PULL_EARLIEST="${PULL_EARLIEST:-0700}"

mkdir -p "$REPO/data"

# One line, overwritten every poll — a log would bury the day that mattered under "not yet".
# `cat data/data-pull.gate`.
defer() {
  printf '%s  deferred: %s\n' "$(date '+%Y-%m-%d %H:%M')" "$1" > "$GATE_FILE"
  exit 0
}

# **A failed pull has to interrupt someone.** The rclone OAuth token expired on 2026-09-17 and
# nothing noticed for two days — `review` kept printing verdicts against a corpus that had
# stopped moving, and its `written N days ago` line is the only tell. A gate file would have
# gone unread for the same two days. This is a LaunchAgent in the GUI session, so osascript
# reaches the notification centre; on the droplet (no osascript) it degrades to the stderr line
# that was already there.
fail() {
  echo "data-pull: $1" >&2
  printf '%s  FAILED: %s\n' "$(date '+%Y-%m-%d %H:%M')" "$1" > "$GATE_FILE"
  if [ "$SCHEDULED" -eq 1 ] && command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"$1\" with title \"tegan-trades: data-pull failed\"" \
      >/dev/null 2>&1
  fi
  exit 1
}

if [ "$SCHEDULED" -eq 1 ]; then
  # Base 10 forced: `date +%H%M` zero-pads and 0700 is not a valid octal literal.
  [ "$((10#$(date +%H%M)))" -lt "$((10#$PULL_EARLIEST))" ] && defer "before $PULL_EARLIEST"

  # Written before the transfer, not after, so a poll landing mid-pull cannot start a second
  # one — and so a failure notifies once rather than every half hour until you fix it.
  [ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$(date +%F)" ] && defer "already pulled today"

  # Only the lid matters here, and only on battery: a closed-lid machine sleeps through the
  # transfer. No battery floor — this is a four-minute download, not the nightly's hour.
  if [ "$(uname -s)" = "Darwin" ] \
     && ! pmset -g batt 2>/dev/null | grep -q "'AC Power'"; then
    LID="$(ioreg -r -k AppleClamshellState -d 4 2>/dev/null \
      | sed -n 's/.*"AppleClamshellState" = \(.*\)/\1/p' | head -1)"
    [ "$LID" = "Yes" ] && defer "lid closed on battery"
  fi
fi

if ! command -v rclone >/dev/null 2>&1; then
  fail "rclone not installed — brew install rclone"
fi

REMOTE="${DEST%%:*}"
if [ "$REMOTE" != "$DEST" ] && ! rclone listremotes 2>/dev/null | grep -qx "$REMOTE:"; then
  echo "           rclone config create $REMOTE drive scope=drive" >&2
  fail "rclone remote '$REMOTE:' not configured (HOME=$HOME)"
fi

# Who wrote this snapshot and when. Printed before the transfer rather than after, because the
# answer decides whether you want the transfer at all: a manifest naming *this* host means the
# mirror is your own last run and a pull will move nothing.
#
# Fetched into a variable rather than piped straight to the terminal because `--scheduled`
# decides on its contents, and a second `rclone cat` would be a second chance for the two reads
# to disagree. stderr is kept rather than dropped: "the mirror is empty" and "your OAuth token
# expired" are the same silence otherwise, and it was the second one both times.
if MANIFEST="$(rclone cat "$DEST/MANIFEST.txt" 2>&1)"; then
  MANIFEST_ERR=""
else
  MANIFEST_ERR="$(printf '%s' "$MANIFEST" | tail -1)"
  MANIFEST=""
fi
echo "[data-pull] source snapshot:"
if [ -n "$MANIFEST" ]; then printf '%s\n' "$MANIFEST" | sed 's/^/  /'
else echo "  (no manifest — mirror may be empty or unreachable: ${MANIFEST_ERR:-no error reported})"; fi
echo "[data-pull] this host: $(hostname)"
echo

if [ "$SCHEDULED" -eq 1 ]; then
  # An unreadable manifest is a failure, not an empty mirror. This is the call that surfaces a
  # dead OAuth token, and treating it as "nothing new" is precisely how the last outage stayed
  # invisible — the transfer below would have failed anyway, one step later and just as quietly.
  REMOTE_AT="$(printf '%s\n' "$MANIFEST" | awk '/^backed_up_at:/{print $2}')"
  [ -n "$REMOTE_AT" ] \
    || fail "cannot read the mirror manifest — ${MANIFEST_ERR:-no backed_up_at line}. Try: rclone config reconnect $REMOTE:"

  # Nothing new to fetch. Deliberately does NOT stamp: the droplet may simply be running late,
  # and the next poll should still catch it rather than writing the day off at 07:00.
  #
  # **But "unchanged" cannot stay quiet forever, and this is the subtle one.**
  # `oracle/freshness.py` spells out the trap this check walks into: `backup.sh` writes the
  # manifest *after* the rclone copy it exits on, so a night the droplet's backup fails leaves
  # `backed_up_at` frozen at yesterday. Compared against `.last-pull` that reads as "nothing new,
  # all is well" — a guard derived from the pipeline whose failure it is meant to detect, failing
  # closed to healthy. So a mirror that has not moved in over a cycle escalates to the
  # notification rather than deferring into silence. That module measures local price-cache mtime
  # for the same reason and remains the honest staleness signal; this is only the scheduler
  # noticing it has nothing to do.
  if [ "$REMOTE_AT" = "$(cat "$REPO/data/.last-pull" 2>/dev/null)" ]; then
    [ -f "$UNCHANGED_FILE" ] || date +%s > "$UNCHANGED_FILE"
    UNCHANGED_FOR=$(( $(date +%s) - $(cat "$UNCHANGED_FILE") ))
    if [ "$UNCHANGED_FOR" -gt "$MIRROR_STALE_AFTER" ]; then
      # Stamped before failing so this notifies once and then holds until tomorrow, rather than
      # every half hour for as long as the droplet stays down.
      date +%F > "$STAMP_FILE"
      fail "mirror has not moved in $(( UNCHANGED_FOR / 3600 ))h (still $REMOTE_AT) — the droplet's nightly or its backup step is failing"
    fi
    defer "mirror unchanged since $REMOTE_AT"
  fi
  rm -f "$UNCHANGED_FILE"

  date +%F > "$STAMP_FILE"
fi

# The files both machines APPEND to are held out of the bulk copy and reconciled below instead.
# `rclone copy` replaces the destination wholesale, which is right for ore and silently lossy for
# an append-only log: whoever copies second wins and the other machine's rows are gone.
#
# The patterns come from `oracle.mirror` rather than being written out here, because the two
# drifting apart is the one failure with no symptom — the bulk copy would replace the file, the
# reconcile would then merge the remote copy with itself, and every test in the repo would still
# pass. Read that module's docstring before touching this. It also explains why the patterns
# carry no `data/` prefix: the copy is rooted at `data/`, so a prefixed pattern matches nothing
# and excludes nothing, failing the same quiet way.
#
# Built with a while-read loop rather than `mapfile`: macOS ships bash 3.2, where `mapfile` does
# not exist. The shebang is `#!/bin/bash`, so this runs under 3.2 on the laptop and under 5.x on
# the droplet, and only the older dialect is safe to use.
EXCLUDES_RAW="$(uv run python -m oracle.mirror excludes)" \
  || fail "could not read the exclude list from oracle.mirror"
HELD_OUT=()
while IFS= read -r held_line; do
  [ -n "$held_line" ] && HELD_OUT+=("$held_line")
done <<< "$EXCLUDES_RAW"
if [ "${#HELD_OUT[@]}" -eq 0 ]; then
  fail "the exclude list came back empty — refusing to overwrite append-only logs"
fi

# --checksum for the same reason backup.sh uses it: part of the mirror predates rclone and carries
# modtimes Drive assigned itself, which read as changed forever under the default comparison.
rclone copy "$DEST/data/" data/ \
  --checksum \
  $UPDATE_FLAG $DRY_RUN \
  --transfers 8 --checkers 16 \
  --exclude '.DS_Store' \
  --exclude 'logs/nightly/*.log' \
  "${HELD_OUT[@]}" \
  --stats-one-line --stats 10s \
  || fail "rclone copy from $DEST/data/ failed"

if [ -n "$DRY_RUN" ]; then
  echo "[data-pull] dry run — nothing was written"
  exit 0
fi

# `--force` means "this corpus is damaged, take the mirror's copy". A merge can never repair a
# corrupt local file — it would splice the corruption in and then push the result back up — so
# the flag keeps its stated meaning by taking the remote copy wholesale for these three too.
if [ -z "$UPDATE_FLAG" ]; then
  echo "[data-pull] --force: taking the mirror's copy of the append-only files wholesale"
  uv run python -m oracle.mirror synced | while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    rclone copyto "$DEST/data/$rel" "data/$rel" --checksum \
      || echo "data-pull: warning: could not restore data/$rel" >&2
  done
else
  # `--root` is passed explicitly and is NOT optional here. Without it `oracle.mirror` derives
  # the data root from its own file location, which is the checkout the *module* lives in — not
  # the one this script resolved from `${BASH_SOURCE[0]}` and cd'd into. The two are the same in
  # production and differ under any second checkout, where the reconcile would then quietly
  # rewrite the wrong machine's corpus.
  #
  # Exits non-zero on failure rather than warning: this script has no `set -e`, so a warning
  # would be swallowed and the pull would go on to print success over a merge that never ran.
  uv run python -m oracle.mirror reconcile --dest "$DEST" --root "$REPO/data" \
    || fail "reconciling the append-only files failed — local rows are untouched"
fi

# Written only after everything above succeeded, so a failed pull cannot leave a receipt claiming
# this machine is current. `oracle.freshness` is the primary staleness signal and needs no
# network; this receipt answers the narrower question of which mirror snapshot was last taken.
#
# Taken from the manifest read at the top rather than a fresh one: that is the snapshot this run
# actually copied, so a droplet push landing mid-transfer cannot have us claim ore we never got.
BACKED_UP_AT="$(printf '%s\n' "$MANIFEST" | awk '/^backed_up_at:/{print $2}')"
if [ -n "$BACKED_UP_AT" ]; then
  printf '%s\n' "$BACKED_UP_AT" > data/.last-pull
fi

# Success clears whatever the last defer or failure left behind, so the file only ever describes
# a live condition. A stale `FAILED:` line sitting next to a healthy corpus is its own bug report.
rm -f "$GATE_FILE"

echo "[data-pull] pulled into data/"
if [ ! -f data/brain/index.db ]; then
  echo "[data-pull] note: data/brain/index.db absent — run 'uv run brain-index' (~40min, free)"
fi
