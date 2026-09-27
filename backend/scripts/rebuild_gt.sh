#!/usr/bin/env bash
# Download ETH3D archives and rebuild laser PLYs next to the committed scenes.
# Scene JSON in data/fixtures stays as committed when walls, chain, objects or
# coverage bands differ (the committed files carry the w2-window fix).
# PLY bytes are copied into .fixtures/ and into
# $FIXTURE_SOURCE/eval-harness/results/<site>/gt-laser/ when that directory
# exists. FIXTURE_SOURCE defaults to the store internal/ path (step 14).
# Set FIXTURE_SOURCE to a private directory to avoid writing the shared store.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PY=${PY:-python}
CACHE=${FIXTURE_CACHE:-.fixtures}
ETH="$CACHE/eth3d"
OUT="$CACHE/gt"
REBUILD="$CACHE/gt-rebuild"

if ! command -v 7z >/dev/null 2>&1; then
  echo "7z missing. Install it with: apt-get install p7zip-full   or   brew install p7zip" >&2
  exit 4
fi

mkdir -p "$ETH/archives" "$OUT" "$REBUILD"

download() {
  local scene=$1 kind=$2
  local url="https://www.eth3d.net/data/${scene}_${kind}.7z"
  local dest="$ETH/archives/${scene}_${kind}.7z"
  local code=0
  curl -fL -C - --retry 3 -o "$dest" "$url" || code=$?
  if [[ $code -ne 0 ]]; then
    # curl uses 22 for HTTP 4xx/5xx.
    echo "ETH3D download failed ($code) $url" >&2
    exit 5
  fi
  if ! 7z x -y -o"$ETH" "$dest"; then
    echo "7z extract failed for $dest (disk full? apt-get install p7zip-full)" >&2
    exit 4
  fi
}

for scene in facade electro delivery_area; do
  download "$scene" dslr_scan_eval
  download "$scene" dslr_occlusion
  download "$scene" dslr_undistorted
done

"$PY" -m server.eval.eth3d.build_gt --data "$ETH/facade" --out "$REBUILD/eth3d-facade"
"$PY" -m server.eval.eth3d.build_gt_c2 \
  --seed data/fixtures/scenes/eth3d-electro/seed.json \
  --data "$ETH/electro" --site eth3d-electro --out "$REBUILD/eth3d-electro"
"$PY" -m server.eval.eth3d.build_gt_c2 \
  --seed data/fixtures/scenes/eth3d-delivery/seed.json \
  --data "$ETH/delivery_area" --site eth3d-delivery --out "$REBUILD/eth3d-delivery" \
  --ground-material g1=drive

export FIXTURE_CACHE="$CACHE"
"$PY" - <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

from server.common.config import default_fixture_source

root = Path(".").resolve()
cache = Path(os.environ.get("FIXTURE_CACHE", ".fixtures"))
rebuild = cache / "gt-rebuild"
out = cache / "gt"
store = os.environ.get("FIXTURE_SOURCE") or default_fixture_source()
keys = ("walls", "chain", "objects")
notes = []
for site in ("eth3d-facade", "eth3d-electro", "eth3d-delivery"):
    committed = root / "data" / "fixtures" / "scenes" / site / "gt-laser" / "scene.json"
    rebuilt = rebuild / site / "scene.json"
    left = json.loads(committed.read_text())
    right = json.loads(rebuilt.read_text())
    for document in (left, right):
        runtime = document.get("provenance", {}).get("runtime_s")
        if isinstance(runtime, dict):
            runtime.pop("runtime_s", None)
        document.get("provenance", {}).pop("runtime_s", None)
    changed = []
    for key in keys:
        if left.get(key) != right.get(key):
            changed.append(key)
    left_bands = (left.get("coverage") or {}).get("bands")
    right_bands = (right.get("coverage") or {}).get("bands")
    if left_bands != right_bands:
        changed.append("coverage.bands")
    dest = out / site / "gt-laser"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("laser.ply", "occluders.ply"):
        shutil.copyfile(rebuild / site / name, dest / name)
    note = f"{site}: differ={changed or 'none (runtime_s ignored)'}; kept committed scene.json; copied PLYs to {dest}"
    notes.append(note)
    (dest / "scene-diff.txt").write_text(note + "\n")
    print(note)
    if store and not store.startswith("gs://"):
        store_dir = Path(store) / "eval-harness" / "results" / site / "gt-laser"
        if store_dir.is_dir():
            for name in ("laser.ply", "occluders.ply"):
                shutil.copyfile(dest / name, store_dir / name)
            print(f"copied PLYs into {store_dir}")
page = root / "docs" / "components" / "ws-00-skeleton-and-contracts.md"
marker = "<!-- gt-diff -->"
block = marker + "\n" + "\n".join(notes) + "\n"
if page.is_file() and marker in page.read_text():
    text = page.read_text()
    start = text.index(marker)
    end = text.find("\n## ", start + len(marker))
    if end < 0:
        end = len(text)
    page.write_text(text[:start] + block + text[end:])
else:
    print("component page has no gt-diff marker; diff is under .fixtures/gt/<site>/gt-laser/scene-diff.txt", file=sys.stderr)
PY
