#!/usr/bin/env bash
# Local mirror of the proposed backend CI workflow
# (backend/docs/ci/backend.yml.proposed).
#
# Runs the steps of both jobs in the same order: the gating steps of
# backend-checks first, then the known-red hygiene job. The job's
# environment-provisioning steps (actions/checkout, astral-sh/setup-uv) have
# no local equivalent: run this from a checkout of the repository with uv
# installed (the proof ran with uv 0.13.1, Python 3.12).
#
# Usage: bash backend/scripts/ci_local.sh [--keep-going]   (from anywhere; it
# cd's to backend/)
#
#   default      exits nonzero on the FIRST failing gating step, mirroring
#                the job, where a failed step fails the job immediately.
#   --keep-going runs every step anyway, collecting each step's result, and
#                still exits nonzero when any gating step failed — one
#                command for the full per-step report backend/docs/ci/README.md
#                documents.
#
# make hygiene is known red on alpha, so the proposed workflow runs it in a
# separate job with continue-on-error: true (backend-hygiene-known-red). The
# local mirror does the same with step_soft: a failure there is printed and
# remembered, but the run still exits 0 when only that step failed, exactly
# like the job it mirrors. Nothing is hidden; the step line says FAILED.
set -euo pipefail

cd "$(dirname "$0")/.." # backend/

KEEP_GOING=0
FAILED=0
SOFT_FAILED=0
if [ "${1:-}" = "--keep-going" ]; then
  KEEP_GOING=1
fi

command -v uv >/dev/null 2>&1 || {
  echo "==> [ci_local] uv not found — install it first (https://docs.astral.sh/uv/)" >&2
  exit 1
}

# Millisecond wall clock. GNU date's %N is not portable (macOS/BSD date
# prints a literal 'N' and the arithmetic below would abort the script —
# Codex P2 on PR #288), so the time comes from python3 when it exists and
# falls back to whole-second timestamps: every POSIX date has %s, and the
# value stays in milliseconds.
now_ms() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import time; print(int(time.time() * 1000))'
  else
    date +%s000
  fi
}

step() {
  local name="$1"
  shift
  local t0 ms
  t0=$(now_ms)
  printf '==> [ci_local] %s\n' "$name"
  if "$@"; then
    ms=$(($(now_ms) - t0))
    printf '<== [ci_local] %s ok in %d.%ds\n' "$name" $((ms / 1000)) $(((ms % 1000) / 100))
  else
    ms=$(($(now_ms) - t0))
    printf '<== [ci_local] %s FAILED after %d.%ds\n' "$name" $((ms / 1000)) $(((ms % 1000) / 100)) >&2
    if [ "$KEEP_GOING" -eq 1 ]; then
      FAILED=1
      return 0
    fi
    exit 1
  fi
}

# Mirrors a step that runs with continue-on-error: true in
# backend.yml.proposed: a failure is reported and stays visible but cannot
# fail the run.
step_soft() {
  local name="$1"
  shift
  local t0 ms
  t0=$(now_ms)
  printf '==> [ci_local] %s\n' "$name"
  if "$@"; then
    ms=$(($(now_ms) - t0))
    printf '<== [ci_local] %s ok in %d.%ds\n' "$name" $((ms / 1000)) $(((ms % 1000) / 100))
  else
    ms=$(($(now_ms) - t0))
    printf '<== [ci_local] %s FAILED (non-blocking, mirrors the job) after %d.%ds\n' "$name" $((ms / 1000)) $(((ms % 1000) / 100)) >&2
    SOFT_FAILED=1
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
step "make validate" make validate

# The hygiene job runs last, as in the workflow: known red on alpha, and
# non-blocking for the run, like its continue-on-error job.
step_soft "make hygiene (known red on alpha, non-blocking)" make hygiene

if [ "$FAILED" -ne 0 ]; then
  echo '[ci_local] FAILED — see the step results above' >&2
  exit 1
fi
if [ "$SOFT_FAILED" -ne 0 ]; then
  echo '[ci_local] gating steps green; the known-red hygiene step failed non-blocking, as the mirrored job does on alpha (backend/docs/ci/README.md)'
  exit 0
fi
echo '[ci_local] all steps green'
