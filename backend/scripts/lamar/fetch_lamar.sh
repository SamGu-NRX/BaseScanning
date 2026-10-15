#!/usr/bin/env bash
# Fetch LaMAR LIN for the real-phone gate. Data directory should be ~/lamar:
# A6's lamar_io.py reads that path. Raw data stays out of git.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: scripts/lamar/fetch_lamar.sh <data-dir>" >&2
  exit 2
fi

DATA=$1
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
mkdir -p "$DATA"

INFO=$(curl -fsSL "https://cvg-data.inf.ethz.ch/lamar/INFO")
echo "$INFO" | grep -i licen
printf '%s\n' "$INFO" | grep -iq "CC BY-SA 4.0"

uv run python scripts/lamar/fetch_meta.py --data "$DATA"
uv run python scripts/lamar/fetch_frames.py --data "$DATA"

A6=${HSS_A6_ROOT:-}
if [[ -z "$A6" && -n "${FIXTURE_SOURCE:-}" ]]; then
  A6="${FIXTURE_SOURCE}/breadth/a6-iphone-quality"
fi

LAMAR_HOME=$(cd "$HOME/lamar" 2>/dev/null && pwd || true)
DATA_ABS=$(cd "$DATA" && pwd)
if [[ -n "$A6" && -f "$A6/build_laser.py" && -n "$LAMAR_HOME" && "$DATA_ABS" == "$LAMAR_HOME" ]]; then
  (cd "$A6" && uv run --project "$ROOT" python build_laser.py)
elif [[ -n "$A6" && -f "$A6/build_laser.py" ]]; then
  echo "build_laser.py reads ~/lamar via lamar_io.py; pass ~/lamar to run it" >&2
fi

if [[ -n "$A6" && -f "$A6/results/segments.json" ]]; then
  cp "$A6/results/segments.json" "$DATA/segments.json"
fi

uv run python scripts/lamar/fetch_meta.py --data "$DATA" --dump-only --write-manifest data/fixtures/manifest.json
echo "gt_ios.json frames: $(uv run python -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$DATA/gt_ios.json")"
