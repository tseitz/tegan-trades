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
# **No `--delete`, deliberately.** A backup that faithfully reproduces a corrupted or
# half-deleted source is not a backup. The corpus only grows, so additive is the correct
# semantics — a file that vanishes locally stays here until someone removes it on purpose.
#
# **What is skipped, and why it is safe to skip:**
#   brain/       96MB of derived embedding index. `brain-index` rebuilds it from transcripts
#                for free in ~40 minutes. Backing it up would quadruple the transfer to
#                protect the one thing that costs nothing to recreate.
#   *.bak        one-off migration leftovers.
#   scratch/     throwaway.
#
# Everything else is kept even where it is cheap, because "cheap to refetch" assumes the API
# still serves that window, and price/funding history is exactly where that assumption fails.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# Overridable so a second destination (external disk) needs no edit.
DEST="${TEGAN_BACKUP_DEST:-/Users/tseitz/Library/CloudStorage/GoogleDrive-tdseitz10@gmail.com/My Drive/Coding/tegan-trades}"

if [ ! -d "$(dirname "$DEST")" ]; then
  echo "backup: destination parent missing — is Google Drive mounted? ($DEST)" >&2
  exit 1
fi

mkdir -p "$DEST" || exit 1

# --no-perms/owner/group: Google Drive's filesystem does not carry POSIX metadata, and without
# these rsync sees every file as changed and re-uploads the whole corpus every single night.
# Verified: the second run of an unchanged tree transfers 0 B.
rsync -a --no-perms --no-owner --no-group \
  --exclude 'brain/' \
  --exclude 'scratch/' \
  --exclude '*.bak' \
  --exclude '*.pre-contentid.bak' \
  --exclude '.DS_Store' \
  data/ "$DEST/data/" || { echo "backup: rsync failed" >&2; exit 1; }

# The config is small and versioned in git, but a restore needs the watchlist and the canon
# registry to mean anything — keeping them beside the corpus makes this self-contained.
rsync -a --no-perms --no-owner --no-group cfg/ "$DEST/cfg/" || true

# A manifest, so "did the backup work" is answerable without trusting rsync's exit code. Counts
# rather than a checksum: the point is to catch a mirror that silently stopped growing, which
# is the way a scheduled backup actually fails.
{
  echo "backed_up_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host: $(hostname)"
  echo "transcripts: $(find data/transcripts -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "theses:      $(find data/theses -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "stances:     $(find data/stances -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
  echo "funding:     $(find data/funding -type f 2>/dev/null | wc -l | tr -d ' ')"
  echo "decisions:   $(wc -l < data/setups/decisions.jsonl 2>/dev/null | tr -d ' ') rows"
  echo "size:        $(du -sh "$DEST/data" 2>/dev/null | cut -f1)"
} > "$DEST/MANIFEST.txt"

echo "[backup] mirrored to ${DEST#"$HOME"/}"
cat "$DEST/MANIFEST.txt"
