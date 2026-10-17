#!/usr/bin/env bash
# Fails if `.env` would be committed as plaintext. Stands in for `detect-private-key` on `.env`,
# which that hook skips because it reads the decrypted working copy.
set -euo pipefail
if ! git check-attr filter -- .env | grep -q 'filter: git-crypt$'; then
  echo ".env is not git-crypt filtered — it would be committed as plaintext" >&2
  exit 1
fi
