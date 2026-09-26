#!/usr/bin/env bash
# Copy breadth work from the Cursor Project store onto breadth/<path-id> branches and push them.
#
# Usage: scripts/sync_breadth.sh [--no-push] <path-id>... | --all
#   Path ids: a1-classical a2-feedforward a3-depth-fusion b1-detection b2-equipment-reading
#             c1-rules-3d c2-volumetric c3-vlm
#
# Each branch starts from origin/breadth/<path-id> if it exists, otherwise origin/main, and is
# built in a temporary worktree so the current checkout is untouched. The store folder is mirrored
# into the repo folder and the path's one-page result file is copied to <folder>/RESULTS.md.
set -euo pipefail

STORE_ID="bc-5bc79926-909c-4cfb-aa62-24b3a44735e2"
if [[ -z "${STORE:-}" ]]; then
  for candidate in \
    "$HOME/Library/Application Support/Cursor/AgentStores/cursor_agent_stores/$STORE_ID/files" \
    "/cursor/stores/$STORE_ID"; do
    if [[ -d "$candidate/internal" ]]; then STORE="$candidate"; break; fi
  done
fi
if [[ -z "${STORE:-}" || ! -d "$STORE/internal/breadth" ]]; then
  echo "error: store not found; set STORE=/path/to/store" >&2
  exit 1
fi
BREADTH="$STORE/internal/breadth"

path_spec() {  # path-id -> "store-folder repo-folder result-file-prefix"
  case "$1" in
    a1-classical) echo "a1-classical recon/a1-classical A1-" ;;
    a2-feedforward) echo "a2-feedforward-code recon/a2-feedforward A2-" ;;
    a3-depth-fusion) echo "a3-depth-fusion recon/a3-depth-fusion A3-" ;;
    b1-detection) echo "b1-detection scene/b1-detection B1-" ;;
    b2-equipment-reading) echo "b2 scene/b2-equipment-reading B2-" ;;
    c1-rules-3d) echo "c1-rules-3d criteria/c1-rules-3d C1-" ;;
    c2-volumetric) echo "c2-volumetric criteria/c2-volumetric C2-" ;;
    c3-vlm) echo "c3-vlm criteria/c3-vlm C3-" ;;
  esac
}

PUSH=1
ids=()
for arg in "$@"; do
  case "$arg" in
    --no-push) PUSH=0 ;;
    --all) ids=(a1-classical a2-feedforward a3-depth-fusion b1-detection b2-equipment-reading c1-rules-3d c2-volumetric c3-vlm) ;;
    *) ids+=("$arg") ;;
  esac
done
if [[ ${#ids[@]} -eq 0 ]]; then
  sed -n '2,6p' "$0"
  exit 1
fi

# Photos, model outputs and Base-derived prompt text stay out of git.
RSYNC_OPTS=(-a --delete --max-size=5m
  --exclude=__pycache__ --exclude=.venv --exclude=venv --exclude='*.egg-info'
  --exclude=.pytest_cache --exclude=.ruff_cache --exclude=.DS_Store --exclude='.env*'
  --exclude='*.jpg' --exclude='*.jpeg' --exclude='*.heic' --exclude='*.mp4' --exclude='*.mov'
  --exclude='*.npz' --exclude='*.npy' --exclude='*.ply' --exclude='*.bin' --exclude='*.pt'
  --exclude='*.pth' --exclude='*.safetensors' --exclude='*.ckpt' --exclude='*.onnx'
  --exclude=pred-snapshot --exclude=vm-copies --exclude=weights
  --exclude='ssr_prompts_reconstructed.py' --exclude='SSR_Prompts.md')

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO"
git fetch -q origin

for id in "${ids[@]}"; do
  spec="$(path_spec "$id")"
  if [[ -z "$spec" ]]; then echo "error: unknown path id $id" >&2; exit 1; fi
  read -r src dst prefix <<<"$spec"
  if [[ ! -d "$BREADTH/$src" ]]; then echo "skip $id: $BREADTH/$src missing"; continue; fi

  branch="breadth/$id"
  base="origin/main"
  git show-ref -q --verify "refs/remotes/origin/$branch" && base="origin/$branch"

  wt="$(mktemp -d)/wt"
  git worktree add -q -B "$branch" "$wt" "$base"
  mkdir -p "$wt/$dst"
  rsync "${RSYNC_OPTS[@]}" --exclude=README.md --exclude=RESULTS.md "$BREADTH/$src/" "$wt/$dst/"
  if [[ -f "$BREADTH/$src/README.md" ]]; then cp "$BREADTH/$src/README.md" "$wt/$dst/README.md"; fi

  shopt -s nullglob
  lower="$(printf '%s' "$prefix" | tr '[:upper:]' '[:lower:]')"
  results=("$BREADTH/$prefix"*.md "$BREADTH/$lower"*.md)
  shopt -u nullglob
  if [[ ${#results[@]} -gt 0 ]]; then
    cp "${results[0]}" "$wt/$dst/RESULTS.md"
    result_note="result file $(basename "${results[0]}")"
  else
    result_note="no result file yet"
  fi

  git -C "$wt" add -A
  if git -C "$wt" diff --cached --quiet; then
    echo "$id: no changes ($result_note)"
  else
    git -C "$wt" commit -q -m "Sync $id breadth work from the Project store ($result_note)"
    echo "$id: committed $(git -C "$wt" rev-parse --short HEAD) on $branch ($result_note)"
    if [[ $PUSH -eq 1 ]]; then git -C "$wt" push -q -u origin "$branch"; echo "$id: pushed $branch"; fi
  fi
  git worktree remove --force "$wt"
done
