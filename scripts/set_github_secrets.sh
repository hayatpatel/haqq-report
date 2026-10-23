#!/usr/bin/env bash
# Push secrets from a local .env file into this repo's GitHub Actions secrets.
#
#   cp .env.example .env      # fill in real values locally (never commit .env)
#   gh auth login             # needs admin on the repo
#   ./scripts/set_github_secrets.sh [path/to/.env]
#
# Only names listed in .env.example are uploaded, and empty values are skipped.
# Values are piped to `gh secret set` over stdin: never echoed, never put on a command line.
set -euo pipefail

ENV_FILE="${1:-.env}"
REPO="${GITHUB_REPOSITORY:-hayatpatel/haqq-report}"
here="$(cd "$(dirname "$0")/.." && pwd)"

[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE found" >&2; exit 1; }
command -v gh >/dev/null || { echo "Install the GitHub CLI (gh) first" >&2; exit 1; }

allowed="$(grep -E '^[A-Z][A-Z0-9_]*=' "$here/.env.example" | cut -d= -f1)"
set_count=0; skip_count=0

while IFS= read -r name; do
  # GITHUB_* names are reserved by GitHub Actions and cannot be set as secrets.
  case "$name" in GITHUB_*) continue ;; esac
  value="$(grep -E "^${name}=" "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
  if [ -z "$value" ]; then
    echo "skip  $name (empty)"; skip_count=$((skip_count+1)); continue
  fi
  printf '%s' "$value" | gh secret set "$name" --repo "$REPO" >/dev/null
  echo "set   $name"; set_count=$((set_count+1))
done <<< "$allowed"

echo "done: $set_count set, $skip_count skipped"
