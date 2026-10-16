#!/usr/bin/env bash
# Re-copy the foundation folders from the Cursor Project store into this repo.
#
# Usage: scripts/sync_from_store.sh [--here]
#   Default: creates branch sync/store-<UTC timestamp> from origin/main, then copies.
#   --here:  copies onto the current branch (used for the initial import).
#
# STORE defaults to Hunter's Mac path, then the cloud-agent mount. Override with STORE=/path.
# Nothing is committed; review with `git status` and commit yourself.
set -euo pipefail

STORE_ID="bc-5bc79926-909c-4cfb-aa62-24b3a44735e2"
if [[ -z "${STORE:-}" ]]; then
  for candidate in \
    "$HOME/Library/Application Support/Cursor/AgentStores/cursor_agent_stores/$STORE_ID/files" \
    "/cursor/stores/$STORE_ID"; do
    if [[ -d "$candidate/internal" ]]; then STORE="$candidate"; break; fi
  done
fi
if [[ -z "${STORE:-}" || ! -d "$STORE/internal" ]]; then
  echo "error: store not found; set STORE=/path/to/store" >&2
  exit 1
fi

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO"

if [[ "${1:-}" != "--here" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "error: working tree is dirty; commit or stash first" >&2
    exit 1
  fi
  branch="sync/store-$(date -u +%Y%m%d-%H%M)"
  git fetch -q origin main
  git checkout -q -b "$branch" origin/main
  echo "on new branch $branch"
fi

# Base-provided prompt material stays out of git (see private/ in .gitignore).
PRIVATE_FILES=(SSR_Prompts.md ssr-quick-reference-notion.md)

RSYNC_OPTS=(-a --delete --max-size=5m
  --exclude=__pycache__ --exclude=.venv --exclude=venv --exclude='*.egg-info'
  --exclude=.pytest_cache --exclude=.ruff_cache --exclude=.DS_Store)

mirror() {  # mirror <store-relative-src-dir> <repo-dst-dir> [extra rsync args]
  local src="$STORE/$1" dst="$REPO/$2"
  shift 2
  if [[ ! -d "$src" ]]; then echo "skip: $src missing"; return; fi
  mkdir -p "$dst"
  rsync "${RSYNC_OPTS[@]}" "$@" "$src/" "$dst/"
  echo "synced $src -> ${dst#"$REPO"/}"
}

copy_file() {  # copy_file <store-relative-src> <repo-dst>
  local src="$STORE/$1" dst="$REPO/$2"
  if [[ ! -f "$src" ]]; then echo "skip: $src missing"; return; fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "copied $1 -> $2"
}

mirror internal/packet-generator packet
mirror internal/eval-harness eval
mirror internal/criteria criteria/spec "${PRIVATE_FILES[@]/#/--exclude=}"
mirror internal/data/scripts data/scripts
copy_file internal/data/manifest.json data/manifest.json

copy_file docs/capture-packet-spec.md docs/capture-packet-spec.md
copy_file docs/criteria-spec.md docs/criteria-spec.md
copy_file docs/scene-and-verdict-format.md docs/scene-and-verdict-format.md

for f in "${PRIVATE_FILES[@]}"; do
  copy_file "internal/criteria/$f" "private/criteria/$f"
done

echo
echo "skipped for size (>5 MB):"
find "$STORE/internal/packet-generator" "$STORE/internal/eval-harness" \
  "$STORE/internal/criteria" "$STORE/internal/data/scripts" -type f -size +5M \
  -not -path '*/__pycache__/*' | sed "s|^$STORE/|  |"
echo
git status --short
