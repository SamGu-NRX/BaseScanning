#!/usr/bin/env bash
# Copy breadth work from the Cursor Project store onto breadth/<path-id> branches and push them.
#
# Usage: scripts/sync_breadth.sh [--no-push] <path-id>... | --all
#   Path ids: a1-classical a2-feedforward a3-depth-fusion a5-world-models
#             a6-iphone-quality a7-iphone-degradation a8-scale-without-lidar
#             a9-no-tape-scale a10-sparse-photo-flow
#             b1-detection b2-equipment-reading b3-cheap-gate
#             c1-rules-3d c2-volumetric c3-vlm e2e-criteria-on-recon
# Each branch starts from origin/breadth/<path-id> if it exists, otherwise origin/main, and is
# built in a temporary worktree so the current checkout is untouched. The store folder is mirrored
# into the package folder under backend/ and the path's one-page result file is copied to
# <folder>/RESULTS.md. Git operations (fetch, worktrees, push) still run from the repository toplevel.
# Before any commit or push, files that contain real meter numbers, Base SSR prompt text (or a
# reconstruction/digest of it), API keys, or anything over 5 MB are removed from the tree.
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
    a5-world-models) echo "a5-world-models recon/a5-world-models A5-" ;;
    a6-iphone-quality) echo "a6-iphone-quality recon/a6-iphone-quality A6-" ;;
    a7-iphone-degradation) echo "a7-iphone-degradation recon/a7-iphone-degradation A7-" ;;
    a8-scale-without-lidar) echo "a8-scale recon/a8-scale-without-lidar A8-" ;;
    a9-no-tape-scale) echo "a9-no-tape recon/a9-no-tape-scale A9-" ;;
    a10-sparse-photo-flow)
      if [[ -d "$BREADTH/a10-sparse-photo-flow" ]]; then
        echo "a10-sparse-photo-flow recon/a10-sparse-photo-flow A10-"
      else
        echo "a10-sparse-photo recon/a10-sparse-photo-flow A10-"
      fi
      ;;
    b1-detection) echo "b1-detection scene/b1-detection B1-" ;;
    b2-equipment-reading) echo "b2 scene/b2-equipment-reading B2-" ;;
    b3-cheap-gate) echo "b3-cheap-gate scene/b3-cheap-gate B3-" ;;
    c1-rules-3d) echo "c1-rules-3d criteria/c1-rules-3d C1-" ;;
    c2-volumetric) echo "c2-volumetric criteria/c2-volumetric C2-" ;;
    c3-vlm) echo "c3-vlm criteria/c3-vlm C3-" ;;
    e2e-criteria-on-recon) echo "e2e-criteria-on-recon criteria/e2e-criteria-on-recon E2E-" ;;
  esac
}

PUSH=1
ids=()
for arg in "$@"; do
  case "$arg" in
    --no-push) PUSH=0 ;;
    --all) ids=(a1-classical a2-feedforward a3-depth-fusion a5-world-models a6-iphone-quality a7-iphone-degradation a8-scale-without-lidar a9-no-tape-scale a10-sparse-photo-flow b1-detection b2-equipment-reading b3-cheap-gate c1-rules-3d c2-volumetric c3-vlm e2e-criteria-on-recon) ;;
    *) ids+=("$arg") ;;
  esac
done
if [[ ${#ids[@]} -eq 0 ]]; then
  sed -n '2,9p' "$0"
  exit 1
fi

# Photos, model outputs, meter-number labels, and Base-derived prompt text stay out of git.
RSYNC_OPTS=(-a --delete --max-size=5m
  --exclude=__pycache__ --exclude=.venv --exclude=venv --exclude='*.egg-info'
  --exclude=.pytest_cache --exclude=.ruff_cache --exclude=.DS_Store --exclude='.env*'
  --exclude='*.jpg' --exclude='*.jpeg' --exclude='*.heic' --exclude='*.mp4' --exclude='*.mov'
  --exclude='*.npz' --exclude='*.npy' --exclude='*.ply' --exclude='*.bin' --exclude='*.pt'
  --exclude='*.pth' --exclude='*.safetensors' --exclude='*.ckpt' --exclude='*.onnx'
  --exclude='*.pem' --exclude='*credentials*' --exclude='*.conflict.*'
  --exclude=pred-snapshot --exclude=vm-copies --exclude=weights
  --exclude=raw/meter --exclude=meter_numbers_hand.csv --exclude=hand_numbers_raw.txt
  --exclude=ssr_prompts.py --exclude=ssr_prompts_reconstructed.py --exclude=SSR_Prompts.md
  --exclude='*ssr*digest*' --exclude='*prompt*digest*')

# Before the port into the BaseScanning monorepo the git toplevel was this package, so the
# breadth folders mirrored at the toplevel root. The toplevel is now the monorepo root one
# level above backend/: git operations (fetch, worktrees, push) still run at the toplevel,
# but every mirrored file anchors under the package root, matching sync_from_store.sh.
# pwd -P keeps PACKAGE comparable to REPO across symlinked parent directories.
PACKAGE="$(cd "$(dirname "$0")/.." && pwd -P)"
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
if [[ "$PACKAGE" == "$REPO" ]]; then
  PACKAGE_REL=""
elif [[ "$PACKAGE/" == "$REPO/"* ]]; then
  PACKAGE_REL="${PACKAGE#"$REPO"/}"
else
  echo "error: package root $PACKAGE is not inside repository $REPO" >&2
  exit 1
fi
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
  dst_rel="$dst"
  if [[ -n "$PACKAGE_REL" ]]; then dst_rel="$PACKAGE_REL/$dst"; fi
  mkdir -p "$wt/$dst_rel"
  rsync "${RSYNC_OPTS[@]}" --exclude=README.md --exclude=RESULTS.md "$BREADTH/$src/" "$wt/$dst_rel/"
  if [[ -f "$BREADTH/$src/README.md" ]]; then cp "$BREADTH/$src/README.md" "$wt/$dst_rel/README.md"; fi

  shopt -s nullglob
  lower="$(printf '%s' "$prefix" | tr '[:upper:]' '[:lower:]')"
  results=("$BREADTH/$prefix"*.md "$BREADTH/$lower"*.md)
  shopt -u nullglob
  if [[ ${#results[@]} -gt 0 ]]; then
    cp "${results[0]}" "$wt/$dst_rel/RESULTS.md"
    result_note="result file $(basename "${results[0]}")"
  else
    result_note="no result file yet"
  fi

  git -C "$wt" add -A
  # Scan the whole diff against main, including files already on the branch. rsync
  # --exclude does not delete those, so drop them here before any commit or push.
  excluded_file="$(mktemp)"
  if ! python3 - "$wt" "$BREADTH" "$excluded_file" <<'PY'
import csv, re, subprocess, sys
from pathlib import Path

wt, breadth, out_path = sys.argv[1:]
diff = subprocess.run(
    ["git", "-C", wt, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "origin/main"],
    check=True, capture_output=True, text=True,
)
paths = [line for line in diff.stdout.splitlines() if line.strip()]

def forbidden_name(rel: str) -> str | None:
    name = Path(rel).name
    low = rel.replace("\\", "/").lower()
    if "/raw/meter/" in f"/{low}/" or low.endswith("/raw/meter") or "/raw/meter/" in f"/{low}":
        return "meter raw reads"
    if name == "meter_numbers_hand.csv":
        return "hand-labeled meter numbers"
    if name == "hand_numbers_raw.txt":
        return "hand-labeled meter numbers"
    if name in {"ssr_prompts.py", "ssr_prompts_reconstructed.py", "SSR_Prompts.md"}:
        return "SSR prompt text"
    folded = name.lower()
    if "digest" in folded and ("ssr" in folded or "prompt" in folded):
        return "SSR prompt digest"
    if name.startswith(".env") or name.endswith(".pem") or "credentials" in folded:
        return "credentials"
    if ".conflict." in name:
        return "sync conflict copy"
    return None

numbers: list[str] = []
hand = Path(breadth) / "b2" / "labels" / "meter_numbers_hand.csv"
if hand.is_file():
    with hand.open(newline="") as handle:
        for row in csv.DictReader(handle):
            value = (row.get("number") or "").strip()
            if len(value) >= 6:
                numbers.append(value)
numbers = sorted(set(numbers), key=len, reverse=True)
number_re = re.compile(r"(?<!\d)(" + "|".join(re.escape(n) for n in numbers) + r")(?!\d)") if numbers else None

needles: list[str] = []
prefixes: dict[str, list[str]] = {}
prompt_path = Path(breadth).parent / "criteria" / "SSR_Prompts.md"
if prompt_path.is_file():
    prompt = prompt_path.read_text(errors="ignore")
    for offset in range(0, max(0, len(prompt) - 80), 400):
        chunk = prompt[offset:offset + 80]
        if len(chunk.strip()) >= 60:
            needles.append(chunk)
            prefixes.setdefault(chunk[:12], []).append(chunk)

key_re = re.compile(
    r"(?:sk-ant-[A-Za-z0-9_\-]{20,}|sk-[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}"
    r"|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
text_suffixes = {".py", ".md", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml", ".toml", ".log", ".sh"}
max_bytes = 5 * 1024 * 1024
reasons: dict[str, str] = {}

def text_of(path: Path) -> str:
    if path.suffix.lower() not in text_suffixes:
        return ""
    if path.stat().st_size > max_bytes:
        return ""
    return path.read_text(errors="ignore")

for rel in paths:
    path = Path(wt) / rel
    if not path.is_file():
        continue
    why = forbidden_name(rel)
    if why:
        reasons[rel] = why
        continue
    if path.stat().st_size > max_bytes:
        reasons[rel] = "file over 5 MB"
        continue
    text = text_of(path)
    if not text:
        continue
    if key_re.search(text):
        reasons[rel] = "API key or token"
        continue
    if number_re is not None and number_re.search(text):
        reasons[rel] = "real meter number"
        continue
    if prefixes:
        for index in range(0, max(0, len(text) - 12)):
            hits = prefixes.get(text[index:index + 12])
            if not hits:
                continue
            window = text[index:index + 80]
            if any(chunk == window or chunk in text[index:index + 80] for chunk in hits):
                reasons[rel] = "SSR prompt text"
                break

lines = [f"{path}\t{why}" for path, why in sorted(reasons.items())]
Path(out_path).write_text(("\n".join(lines) + "\n") if lines else "")
print(f"scanned {len(paths)} files, excluded {len(reasons)}", file=sys.stderr)
PY
  then
    echo "$id: safety scan failed; not committing or pushing" >&2
    rm -f "$excluded_file"
    git worktree remove --force "$wt"
    continue
  fi

  if [[ -s "$excluded_file" ]]; then
    while IFS=$'\t' read -r rel why; do
      [[ -z "$rel" ]] && continue
      echo "$id: excluded for safety ($why): $rel"
      git -C "$wt" rm -r -f -q --ignore-unmatch -- "$rel" || true
    done < "$excluded_file"
    git -C "$wt" add -A
    if ! python3 - "$wt" "$BREADTH" "$excluded_file" <<'PY'
import pathlib, subprocess, sys
wt, _breadth, out_path = sys.argv[1:]
# Reuse the same scanner by execing the block above is not possible; a second
# pass just confirms no forbidden names remain in the staged diff.
diff = subprocess.run(
    ["git", "-C", wt, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "origin/main"],
    check=True, capture_output=True, text=True,
)
bad = []
for rel in diff.stdout.splitlines():
    name = pathlib.PurePosixPath(rel).name
    low = rel.lower()
    if (
        "/raw/meter/" in f"/{low}"
        or name in {"meter_numbers_hand.csv", "hand_numbers_raw.txt", "ssr_prompts.py", "ssr_prompts_reconstructed.py", "SSR_Prompts.md"}
        or ( "digest" in name.lower() and ("ssr" in name.lower() or "prompt" in name.lower()))
        or name.startswith(".env")
    ):
        bad.append(rel)
pathlib.Path(out_path).write_text("\n".join(bad))
PY
    then
      echo "$id: safety rescan failed; not committing or pushing" >&2
      rm -f "$excluded_file"
      git worktree remove --force "$wt"
      continue
    fi
    if [[ -s "$excluded_file" ]]; then
      echo "$id: forbidden paths still staged; not committing or pushing" >&2
      cat "$excluded_file" >&2
      rm -f "$excluded_file"
      git worktree remove --force "$wt"
      continue
    fi
  fi
  rm -f "$excluded_file"

  if git -C "$wt" diff --cached --quiet; then
    echo "$id: no changes ($result_note)"
  else
    git -C "$wt" commit -q -m "Sync $id breadth work from the Project store ($result_note)"
    echo "$id: committed $(git -C "$wt" rev-parse --short HEAD) on $branch ($result_note)"
    if [[ $PUSH -eq 1 ]]; then
      git -C "$wt" push -q -u origin "$branch"
      echo "$id: pushed $branch"
    fi
  fi
  git worktree remove --force "$wt"
done
