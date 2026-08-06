#!/bin/bash
# Mirror the irreplaceable half of data/ off this laptop.
#
# **Why this exists at all:** `data/` is gitignored and was on exactly one disk with no Time
# Machine destination configured and no cloud sync covering ~/code. Most of it is rebuildable
# given time and allowance. `data/transcripts/x/` is not — X posts get deleted and accounts go
# private, so a verbatim capture is the only copy that will ever exist (see
# `ingestion/x_search.py`'s module docstring). `data/funding/` is the same shape: both venues
# serve bounded history and Lighter serves none, so a night not captured is gone (§22).
#
# **Why rclone over the Drive API, and not rsync into ~/Library/CloudStorage.** The mounted
# Google Drive folder is a File Provider domain, and TCC gates it on the *responsible* process.
# Under launchd that is `/bin/bash`, a platform binary macOS refuses to even prompt for, so the
# grant your terminal holds does not apply. Measured 2026-08-06 with the same script run both
# ways — and the denial is partial, which is what makes it dangerous:
#
#     operation                     launchd            terminal
#     stat the directory            ok                 ok
#     list the directory            DENIED             ok
#     glob data/*/*.json            ok, returns 0      returns 2
#     read a pre-existing file      DENIED             ok
#     create a new file             ok                 ok
#
# So a launchd process may *write* there but may not enumerate the tree or read anything it did
# not create — and `glob` reports success while returning nothing rather than raising. rsync has
# to enumerate the destination to compute deltas, which is exactly the denied operation; it
# failed with `open: Operation not permitted` on the first unattended night (2026-08-06).
#
# The API path has none of this: rclone authenticates to Drive directly over the network and
# never touches the mount, so no privacy grant is load-bearing. That row-three result is also
# why `data/` itself must stay on local disk — every reader in the pipeline globs, and on that
# mount they would all find nothing and exit 0.
#
# **`copy`, not `sync`.** rclone's `sync` deletes at the destination to match the source; `copy`
# is additive. A backup that faithfully reproduces a corrupted or half-deleted source is not a
# backup, and the corpus only grows, so a file that vanishes locally stays here until someone
# removes it on purpose.
#
# **`--checksum`, not the default size+modtime.** The destination already holds ~95MB uploaded
# by the Drive app during the rsync era, whose `modifiedTime` Drive assigned itself and does not
# match the local mtime. On the default comparison every one of those files reads as changed and
# re-uploads. Comparing Drive's stored MD5 against the local hash skips them correctly.
#
# **What is skipped, and why it is safe to skip:**
#   brain/       96MB of derived embedding index. `brain-index` rebuilds it from transcripts
#                for free in ~40 minutes. Backing it up would double the transfer to protect
#                the one thing that costs nothing to recreate.
#   *.bak        one-off migration leftovers.
#   scratch/     throwaway.
#
# Everything else is kept even where it is cheap, because "cheap to refetch" assumes the API
# still serves that window, and price/funding history is exactly where that assumption fails.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# An rclone remote:path, not a filesystem path. Overridable so a second destination needs no
# edit — any rclone backend works here, including a plain local one (`/Volumes/disk/tegan`).
DEST="${TEGAN_BACKUP_DEST:-gdrive:Coding/tegan-trades}"

# Homebrew's bin is on the nightly's PATH already; named explicitly so a failure here reads as
# "rclone is missing" rather than surfacing three lines later as an empty transfer.
if ! command -v rclone >/dev/null 2>&1; then
  echo "backup: rclone not installed — brew install rclone" >&2
  exit 1
fi

# The remote's name, before the colon. A local path has no colon and skips this check.
REMOTE="${DEST%%:*}"
if [ "$REMOTE" != "$DEST" ] && ! rclone listremotes 2>/dev/null | grep -qx "$REMOTE:"; then
  # launchd's environment is the usual reason this fires without the config having changed:
  # rclone reads ~/.config/rclone/rclone.conf, so a plist that does not export HOME finds no
  # remotes at all and every backup silently becomes a no-op against an unconfigured name.
  echo "backup: rclone remote '$REMOTE:' not configured (HOME=$HOME)" >&2
  echo "        rclone config create $REMOTE drive scope=drive" >&2
  exit 1
fi

# --transfers/--checkers above the defaults of 4/8: the corpus is thousands of small JSON files,
# where throughput is bounded by round-trips rather than bandwidth.
rclone copy data/ "$DEST/data/" \
  --checksum \
  --transfers 8 --checkers 16 \
  --exclude 'brain/**' \
  --exclude 'scratch/**' \
  --exclude '*.bak' \
  --exclude '*.pre-contentid.bak/**' \
  --exclude '.DS_Store' \
  || { echo "backup: rclone copy of data/ failed" >&2; exit 1; }

# The config is small and versioned in git, but a restore needs the watchlist and the canon
# registry to mean anything — keeping them beside the corpus makes this self-contained.
rclone copy cfg/ "$DEST/cfg/" --checksum || true

# A manifest, so "did the backup work" is answerable without trusting rclone's exit code. Counts
# rather than a checksum: the point is to catch a mirror that silently stopped growing, which is
# the way a scheduled backup actually fails.
#
# `rclone size` reads the destination rather than the source, so it is the one line here that
# would catch a transfer that reported success and moved nothing.
MANIFEST="$(mktemp)"
trap 'rm -f "$MANIFEST"' EXIT
{
  echo "backed_up_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host: $(hostname)"
  echo "transcripts: $(find data/transcripts -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "theses:      $(find data/theses -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "stances:     $(find data/stances -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "funding:     $(find data/funding -type f 2>/dev/null | wc -l | tr -d ' ')"
  echo "decisions:   $(wc -l < data/setups/decisions.jsonl 2>/dev/null | tr -d ' ') rows"
  echo "remote:      $(rclone size "$DEST/data" 2>/dev/null | tr '\n' ' ')"
} > "$MANIFEST"

rclone copyto "$MANIFEST" "$DEST/MANIFEST.txt" || true

echo "[backup] mirrored to $DEST"
cat "$MANIFEST"
