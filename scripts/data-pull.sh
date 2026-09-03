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
for arg in "$@"; do
  case "$arg" in
    --force)   UPDATE_FLAG="" ;;
    --dry-run) DRY_RUN="--dry-run" ;;
    *) echo "usage: $0 [--force] [--dry-run]" >&2; exit 2 ;;
  esac
done

if ! command -v rclone >/dev/null 2>&1; then
  echo "data-pull: rclone not installed — brew install rclone" >&2
  exit 1
fi

REMOTE="${DEST%%:*}"
if [ "$REMOTE" != "$DEST" ] && ! rclone listremotes 2>/dev/null | grep -qx "$REMOTE:"; then
  echo "data-pull: rclone remote '$REMOTE:' not configured (HOME=$HOME)" >&2
  echo "           rclone config create $REMOTE drive scope=drive" >&2
  exit 1
fi

# Who wrote this snapshot and when. Printed before the transfer rather than after, because the
# answer decides whether you want the transfer at all: a manifest naming *this* host means the
# mirror is your own last run and a pull will move nothing.
echo "[data-pull] source snapshot:"
rclone cat "$DEST/MANIFEST.txt" 2>/dev/null | sed 's/^/  /' || echo "  (no manifest — mirror may be empty)"
echo "[data-pull] this host: $(hostname)"
echo

# --checksum for the same reason backup.sh uses it: part of the mirror predates rclone and carries
# modtimes Drive assigned itself, which read as changed forever under the default comparison.
rclone copy "$DEST/data/" data/ \
  --checksum \
  $UPDATE_FLAG $DRY_RUN \
  --transfers 8 --checkers 16 \
  --exclude '.DS_Store' \
  --exclude 'logs/nightly/*.log' \
  --stats-one-line --stats 10s \
  || { echo "data-pull: rclone copy from $DEST/data/ failed" >&2; exit 1; }

if [ -n "$DRY_RUN" ]; then
  echo "[data-pull] dry run — nothing was written"
  exit 0
fi

echo "[data-pull] pulled into data/"
if [ ! -f data/brain/index.db ]; then
  echo "[data-pull] note: data/brain/index.db absent — run 'uv run brain-index' (~40min, free)"
fi
