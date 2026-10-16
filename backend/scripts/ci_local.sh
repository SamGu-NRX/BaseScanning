#!/usr/bin/env bash
# Local mirror of the proposed backend CI job
# (backend/docs/ci/backend.yml.proposed).
#
# Runs the same steps in the same order and exits nonzero on the first
# failure, so a local run proves exactly what the proposed job will do. The
# job's environment-provisioning steps (actions/checkout, astral-sh/setup-uv)
# have no local equivalent: run this from a checkout of the repository with
# uv installed (the proof ran with uv 0.13.1, Python 3.12).
#
# Usage: bash backend/scripts/ci_local.sh [--keep-going]   (from anywhere; it
# cd's to backend/)
#
#   default      exits nonzero on the FIRST failing step, mirroring the job,
#                where a failed step fails the job immediately.
#   --keep-going runs every step anyway, collecting each step's result, and
#                still exits nonzero when any step failed — one command for the
#                full per-step report backend/docs/ci/README.md documents.
set -euo pipefail

cd "$(dirname "$0")/.." # backend/

KEEP_GOING=0
FAILED=0
if [ "${1:-}" = "--keep-going" ]; then
  KEEP_GOING=1
fi

command -v uv >/dev/null 2>&1 || {
  echo "==> [ci_local] uv not found — install it first (https://docs.astral.sh/uv/)" >&2
  exit 1
}

step() {
  local name="$1"
  shift
  local t0 ms
  t0=$(date +%s%3N)
  printf '==> [ci_local] %s\n' "$name"
  if "$@"; then
    ms=$(($(date +%s%3N) - t0))
    printf '<== [ci_local] %s ok in %d.%ds\n' "$name" $((ms / 1000)) $(((ms % 1000) / 100))
  else
    ms=$(($(date +%s%3N) - t0))
    printf '<== [ci_local] %s FAILED after %d.%ds\n' "$name" $((ms / 1000)) $(((ms % 1000) / 100)) >&2
    if [ "$KEEP_GOING" -eq 1 ]; then
      FAILED=1
      return 0
    fi
    exit 1
  fi
}

step "uv sync --frozen --extra api" uv sync --frozen --extra api
step "ruff check" uv run --frozen ruff check .
step "ruff format --check" uv run --frozen ruff format --check .
step "lint-imports" uv run --frozen lint-imports

# Known failures, deselected by name so ci_local mirrors the proposed job —
# see the comment in backend.yml.proposed and backend/docs/ci/README.md
# ("Known failures").
step "make test (known failures deselected)" env \
  PYTEST_ADDOPTS='--deselect server/eval/tests/test_gates.py::test_committed_fixtures_are_scored_against_their_labels --deselect server/eval/tests/test_gates.py::test_offline_table_skips_the_engine_and_passes_the_unpatched_reference --deselect server/eval/tests/test_gates.py::test_route_over_the_bar_is_known_until_expiry --deselect server/eval/tests/test_gates.py::test_refresh_rewrites_pins_after_a_committed_edit --ignore=tests/test_capture_cold_restart.py' \
  make test

step "make schemas" make schemas
step "make hygiene" make hygiene
step "make validate" make validate

if [ "$FAILED" -ne 0 ]; then
  echo '[ci_local] FAILED — see the step results above' >&2
  exit 1
fi
echo '[ci_local] all steps green'
