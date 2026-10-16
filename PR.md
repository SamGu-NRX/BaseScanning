# backend CI: a ready-to-apply workflow proposal, proved locally

Human author: Sam Gu (sgu07966@gmail.com)

## What this adds

- **`backend/docs/ci/backend.yml.proposed`** — a GitHub Actions workflow for
  `backend/**`. Apply it by copying this one file to
  `.github/workflows/backend.yml`; that is the whole change. It ships as a
  proposal because agents may not edit `.github/`.
- **`backend/scripts/ci_local.sh`** — the same steps in the same order,
  nonzero on the first failure (`--keep-going` runs every step and still exits
  nonzero if any failed).
- **`backend/docs/ci/README.md`** — the measured proof run, cache sizes,
  runner-image justification, deliberate exclusions, and the SD-96 decision
  with a recommendation.

Nothing else changes: no `.github/` edits, no `uv.lock` or dependency pins, no
thresholds or gate windows, no secrets. Nothing activates until the copy step.

## Why

`.github/workflows/server.yml` triggers only on `server/**`
(status-report/basescanning.json), so `backend/**` runs zero workflows today —
a backend pull request's only green checks are the size label, CodeRabbit-skip
and Aikido. This gives backend PRs a real test signal.

## Proof — fresh clone at `alpha` = `4bcbedba`, this branch's head `528661c7`

Baseline `make test` on **untouched alpha**: **4 failed** (exactly the four
SD-96 gate tests named below), 1425 passed, 19 skipped, **14 collection
errors** (all of `tests/test_capture_cold_restart.py`), 487.78s.

Canonical `ci_local.sh --keep-going` run:

| Step | Result | Wall time |
|---|---|---|
| `uv sync --frozen --extra api` | ok | 0.0s warm (cold ~2 GB once) |
| `ruff check` / `ruff format --check` | ok | <0.1s each |
| `lint-imports` | ok (5 kept, 0 broken) | 0.1s |
| `make test` (with the two documented removals) | ok — **1425 passed, 19 skipped, 4 deselected**, 8 warnings | 527.3s |
| `make schemas` | ok | 0.3s |
| `make hygiene` | **FAILED — named known failure below** | 0.3s |
| `make validate` | ok — scenes=5 verdicts=16 labels=61 packets=36 | 2.1s |

The test step is exactly the alpha baseline minus the named pre-existing
failures: same 1425/19; the 4 failures and 14 errors are gone. Overall script
exit was 1, from the hygiene step only — the mode does not hide anything.
Measured on the packet's Debian 13 fleet sandbox (8 cores, uv 0.13.1,
Python 3.12); hosted-runner times will differ.

## Known failures on alpha (named, not hidden)

1. **Four expired SD-96 gate tests**
   (`test_committed_fixtures_are_scored_against_their_labels`,
   `test_offline_table_skips_the_engine_and_passes_the_unpatched_reference`,
   `test_route_over_the_bar_is_known_until_expiry`,
   `test_refresh_rewrites_pins_after_a_committed_edit`). The SD-96 window
   closed 2026-10-04 in `backend/server/eval/gates.yaml` — untouched here.
   The job deselects these four exact test ids via a commented
   `PYTEST_ADDOPTS` block; deleting the block returns them to CI. Sam's
   decision between run-them (red from day one) and deselect-them (proposed):
   README "The SD-96 decision (Sam's)" prices both.
2. **14 collection errors in `tests/test_capture_cold_restart.py`**
   (`scripts.replay_capture` no longer provides `load_receipt`; repair in
   flight elsewhere). The job passes `--ignore` for this one file with a
   comment; remove the line when the repair lands.
3. **`make hygiene`** fails on alpha:
   `server/orchestrator/tests/data/packets/synthetic-meter.tar: deny-list
   (synthetic-meter.tar)` — the tracked file sits in the repo's own
   `DENY_NAMES`. The step is kept and the state named in a comment; it goes
   green when alpha stops tracking that file or the deny-list changes — both
   outside this proposal.

## Deliberate exclusions and runner

- `--extra api` only: the suite is pure CPU. No GPU, no Modal (`make weights`,
  `e2e-dev`), no fixtures fetch (`make fixtures` — store-backed families are
  unreachable from CI), no live e2e / `validate-full` / `criteria-golden` /
  `make evals`. Reasons in README "What the job deliberately excludes".
- Runner `ubuntu-24.04`, pinned to match `server.yml`. Actions SHA-pinned to
  `actions/checkout` v7.0.1 and `astral-sh/setup-uv` v10.2.0 — the exact
  commits `server.yml` already runs on every change; both tags verified
  against each action's published `action.yml` (cache and working-directory
  inputs confirmed there). `setup-uv` caches `~/.cache/uv` keyed on
  `backend/uv.lock` (measured 2.1 GB) plus the uv-managed CPython.

## Risk and rollback

Additive — two files and docs; nothing runs until Sam's copy step. Revert is
deleting `.github/workflows/backend.yml`.

## Review pointers

- The SD-96 decision (the one open product call):
  `backend/docs/ci/README.md` → "The SD-96 decision (Sam's)".
- What was verified vs not: README → "Not verified here" (no hosted-runner
  execution yet — applying the workflow is the copy step, and its first run
  happens on the applying PR itself).

Visual evidence: not applicable — backend-only proposal, no rendered UI.

🔗 [Obvious Project](https://app.obvious.ai/p/products-Uu1863v1) · 🧵 [Obvious Thread](https://app.obvious.ai/p/products-Uu1863v1?thread=th_OVvBmCP9) · 🔀 [Obvious PR Artifact](https://app.obvious.ai/link/pr-artifact/backend-a-ready-to-apply-ci-job-proposal-proved-by-task:todo_JXJRUnTo)
