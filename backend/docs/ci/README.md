# backend/docs/ci — the proposed backend CI workflow

`backend/docs/ci/backend.yml.proposed` is a ready-to-apply GitHub Actions
workflow for `backend/**`, and `backend/scripts/ci_local.sh` runs the same
steps in the same order locally. This file records what the proposal is, the
evidence for it, the measured local proof, and the decisions Sam still owns.

## Why this exists

`.github/workflows/server.yml` triggers only on `server/**`, so no workflow
runs the `backend/` tests (status-report/basescanning.json). A backend pull
request's only green checks today are the size label, CodeRabbit-skip and
Aikido — nothing executes a single backend test.

Agents may not edit `.github/`, so the workflow ships as a reviewed proposal
instead of a commit.

## What the workflow runs

Two jobs:

- **`backend-checks`** — install, ruff check, ruff format check, import
  contracts, `make test` (with the four expired SD-96 gate test ids and the
  cold-restart file deselected by name; see "Known failures"), `make
  schemas`, `make validate`. Green on alpha as it stands; this is the job a
  merge signal would require.
- **`backend-hygiene-known-red`** — `make hygiene` on its own, with
  `continue-on-error: true`. The step is known red on alpha (see "Known
  failures"), so it lives outside the required signal while staying visible;
  the job name says what a failed annotation means.

## How to apply

Copy **one file**:

```sh
cp backend/docs/ci/backend.yml.proposed .github/workflows/backend.yml
```

That is the whole change. The workflow that activates is the one proved
below. The applying pull request itself changes `.github/workflows/backend.yml`,
which is inside the workflow's own `paths` filter, so the first run happens on
that PR before anything merges. Nothing else is touched: no `.github/` edits,
no `uv.lock` or dependency pins, no thresholds or gate windows, no secrets.

## How to run the same steps locally

```sh
bash backend/scripts/ci_local.sh               # fails fast: nonzero on the first failing gating step
bash backend/scripts/ci_local.sh --keep-going  # runs every step, still exits nonzero if any gating step failed
```

The script mirrors the workflow job for job: the gating steps of
`backend-checks` first, the hygiene job last. The job's `actions/checkout`
and `astral-sh/setup-uv` steps are environment provisioning with no local
equivalent. Run it from a checkout with uv installed.

The hygiene step runs through a soft wrapper that mirrors the job's
`continue-on-error: true`: the failure prints and the run still exits 0 when
only that step failed, exactly like the workflow. A gating-step failure fails
the run as it fails the job. Step timing uses a portable `now_ms` timer:
python3 milliseconds when python3 exists, whole-second timestamps otherwise —
GNU `date`'s `%N` is not available on macOS/BSD, where the team's shared Mac
runs (Codex P2 on PR #288).

## Measured proof run

Fresh clone of `SamGu-NRX/BaseScanning`, branch
`obv/burst11-bs-backend-ci-proposal` at the restructure head `5e51801a`
(`alpha` = `4bcbedba` plus this branch's commits), run with
`/bin/bash backend/scripts/ci_local.sh` on the packet's Debian 13 fleet
sandbox (8 cores, uv 0.13.1, Python 3.12; the uv cache was warm for this
run — the session's first sync had downloaded the ~2 GB set once, cold):

| Step | Command | Result | Wall time |
|---|---|---|---|
| Install dependencies (locked) | `uv sync --frozen --extra api` | ok (warm cache; the session's cold sync downloaded ~2 GB once) | 0.0s |
| Ruff check | `uv run --frozen ruff check .` | ok | 0.0s |
| Ruff format check | `uv run --frozen ruff format --check .` | ok | 0.0s |
| Import contracts | `uv run --frozen lint-imports` | ok (5 kept, 0 broken) | 0.2s |
| Tests | `make test` with the deselections | ok — **1429 passed, 19 skipped, 4 deselected**, 8 warnings (1425 alpha + the 4 new regression tests) | 550.8s (pytest 548.72s) |
| Schemas | `make schemas` | ok | 0.3s |
| Committed fixtures | `make validate` | ok — scenes=5 verdicts=16 labels=61 packets=36 | 2.6s |
| Repo hygiene | `make hygiene` | **FAILED — named known failure, non-blocking** (mirrors the job's `continue-on-error`) | 0.4s |

Start `11:01:58`, end `11:11:13` UTC. Overall script exit: **0** with the
closing line `gating steps green; the known-red hygiene step failed
non-blocking, as the mirrored job does on alpha` — the only failure is the
known-red hygiene step, which the mirrored job wraps in `continue-on-error`;
every gating step was green. The prior canonical run at the pre-restructure
head `528661c7` (hygiene as a required step, `--keep-going`, overall exit 1,
test step 527.3s / pytest 525.91s) is preserved in this file's history; its
per-step results match this run's.

Baseline checks that the deselections target exactly the pre-existing
failures, re-verified at `5e51801a` on untouched alpha state:

- `uv run --frozen --extra api pytest server/eval/tests/test_gates.py -q`
  without any deselection: **4 failed, 90 passed in 6.92s** — the four
  failures are exactly the ids listed below.
- `make hygiene`: fails with
  `server/orchestrator/tests/data/packets/synthetic-meter.tar: deny-list
  (synthetic-meter.tar)` — the tracked `.tar` the repo's own rules deny.
- `make test` on untouched alpha was measured at the prior head: 4 failed
  (the four SD-96 ids), 1425 passed, 19 skipped, 14 collection errors (all
  of `tests/test_capture_cold_restart.py`), 487.78s.

Regressions: `backend/tests/test_ci_proposal_portability.py` pins both PR
#288 fixes and the Greptile P2 test gap. Four exit-semantics tests run the
real `ci_local.sh` end to end with stubbed `uv` and `make` first on PATH —
each stub exits with a code read from an env var keyed by its arguments and
logs every call — and pin: an all-green run executes every step in the
workflow's order; a failed gating step stops the default run and skips the
later steps; `--keep-going` runs the later steps and still exits nonzero; a
hygiene-only failure exits zero and says so. Those four fail against a
deliberately broken copy of the script that swallows every failure and always
exits 0 — it still printed `all steps green` after a required check failed —
and pass on the real script (shown on PR #288). The first four tests
(hygiene job shape; `%N` in the script; no `now_ms`) fail on the unfixed
tree and pass after.

## Cache

- `astral-sh/setup-uv` with `enable-cache: true` caches `~/.cache/uv`, keyed on
  `backend/uv.lock` (`cache-dependency-glob: uv.lock`). Measured here:
  **2.1 GB** for `~/.cache/uv`, **2.1 GB** for `backend/.venv`.
- `cache-python: true` also caches the uv-managed CPython 3.12, so the runner
  does not re-download Python per run.
- First run on a cold runner pays the download once (~2 GB, dominated by the
  torch pin in the default dependency groups); later runs restore from cache.
- The hygiene job runs the same cache. Its sync skips the `api` extra (the
  hygiene script is stdlib-only), so it restores the same cache and adds no
  new download.

## Runner image

`ubuntu-24.04` — the same pinned image `server.yml` uses. Justification: with
the `api` extra the suite is pure CPU (no GPU, no Modal, no docker, no Swift),
so a hosted x64 Linux runner is the cheapest sufficient image; pinning the
version (rather than `ubuntu-latest`) keeps the glibc/Python surface stable,
which matches this repository's existing convention.

## Known failures on alpha (named, not hidden)

At `alpha` = `4bcbedba`, three kinds of failures pre-exist this proposal. The
required job deselects the test ones by name and the hygiene one runs outside
the required signal; deleting the relevant block returns everything to CI.

1. **Four expired SD-96 gate tests** — `server/eval/tests/test_gates.py`:
   - `test_committed_fixtures_are_scored_against_their_labels`
   - `test_offline_table_skips_the_engine_and_passes_the_unpatched_reference`
   - `test_route_over_the_bar_is_known_until_expiry`
   - `test_refresh_rewrites_pins_after_a_committed_edit`

   The SD-96 window closed `2026-10-04` in `backend/server/eval/gates.yaml`
   (`positive_pass`, known_failure "conservative miss from near-wall
   self-occlusion, SD-96", owner WS-09, until 2026-10-04). The window and the
   thresholds are Sam's decision; this proposal does not touch `gates.yaml`.

2. **14 collection errors in `tests/test_capture_cold_restart.py`** — the
   file's tests all error at collection on alpha
   (`scripts.replay_capture` no longer provides `load_receipt`) and are being
   repaired in a separate change. The proposed job passes
   `--ignore=tests/test_capture_cold_restart.py` with a comment; remove that
   line when the repair lands on alpha.

3. **Repo hygiene** — `make hygiene` fails on alpha:
   `server/orchestrator/tests/data/packets/synthetic-meter.tar: deny-list
   (synthetic-meter.tar)` — the hygiene script denies every tracked `.tar`,
   and alpha tracks that file. The step runs in the separate
   `backend-hygiene-known-red` job with `continue-on-error: true`; it turns
   solid green when alpha stops tracking the file or the deny-list changes —
   both outside this proposal — and the flag can then be deleted.

### The known-red hygiene step: both restructurings, exact diff

Codex P1 on this PR: do not ship a workflow with a permanently failing
required step. The failure itself cannot be fixed here: un-tracking the tar
or editing the deny-list is a `server/**`-side or history decision outside
this proposal. Two restructurings keep the default green:

**Option 1 — a separate, clearly named non-blocking job (shipped default).**
The step keeps running on every backend change, its failure stays visible in
the run graph and annotations, and the pull-request check stays green:

```diff
     jobs:
       backend-checks:
         name: Backend checks
         runs-on: ubuntu-24.04
         timeout-minutes: 30
         steps:
           ...
-          - name: Repo hygiene (make hygiene)
-            run: make hygiene
+
+      backend-hygiene-known-red:
+        name: Repo hygiene (known red on alpha)
+        continue-on-error: true
+        runs-on: ubuntu-24.04
+        timeout-minutes: 15
+        defaults:
+          run:
+            working-directory: backend
+        steps:
+          - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
+            with:
+              persist-credentials: false
+          - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
+            with:
+              working-directory: backend
+              enable-cache: true
+              cache-dependency-glob: uv.lock
+              cache-python: true
+              version: "0.13.1"
+          - name: Install dependencies (locked)
+            run: uv sync --frozen
+          - name: Repo hygiene (make hygiene)
+            run: make hygiene
```

Cost: a second runner pays a cache-restored sync for a step that fails in
about 0.3s. Benefit: the denial keeps executing on every backend change, so
the moment alpha stops tracking the file, the job turns solid green on its
own and someone notices.

**Option 2 — leave the step out of the workflow until it can pass** (the
reviewer's literal alternative). Delete the whole
`backend-hygiene-known-red` job block above. Greenest check list; the cost is
that the failing deny-list check runs nowhere until someone re-adds it, so
the regression stays invisible.

Either way `backend-checks` is green on alpha. Option 1 ships.

### The SD-96 decision (Sam's)

Two options, with the exact diff between them:

- **Option A — run them.** Delete the `PYTEST_ADDOPTS` env block from the
  `Tests (make test)` step in `backend.yml.proposed` (and the matching
  `PYTEST_ADDOPTS` line in `ci_local.sh`). The four tests run on every
  backend pull request and fail until SD-96 is decided; the job is red from
  day one and cannot gate merges; the failure set is exactly these four tests
  plus, until the in-flight repair lands, the 14 cold-restart collection
  errors:

  ```diff
       - name: Tests (make test)
   -     env:
   -       PYTEST_ADDOPTS: >-
   -         --deselect server/eval/tests/test_gates.py::test_committed_fixtures_are_scored_against_their_labels
   -         --deselect server/eval/tests/test_gates.py::test_offline_table_skips_the_engine_and_passes_the_unpatched_reference
   -         --deselect server/eval/tests/test_gates.py::test_route_over_the_bar_is_known_until_expiry
   -         --deselect server/eval/tests/test_gates.py::test_refresh_rewrites_pins_after_a_committed_edit
   -         --ignore=tests/test_capture_cold_restart.py
         run: make test
  ```

- **Option B — deselect those four test ids by name (as proposed).** The job
  is green; the other gate tests in `test_gates.py` still run (the
  deselection is by exact test id, so the blast radius is exactly four
  tests); restoring them later is deleting one env block, and the block's
  comment plus this README point at the SD-96 decision. This is the shipped
  default and the diff above is what Option A would apply on top of it.

**Recommendation: Option B.** A job that is red from day one trains everyone
to ignore it and can never gate a merge — it re-creates the current situation
(green checks that mean nothing) one level down. The deselection is auditable
(by name, with a comment pointing at the decision) and reversible in one
deletion; the cost is that exactly those four tests leave CI coverage until
restored, which is the honest price of an expired window Sam has not decided
yet. Reverse this call the moment SD-96 is decided: delete the block and the
tests come back.

## What the job deliberately excludes

- **GPU.** The job syncs `--extra api` only. `--all-extras` would install the
  GPU torch pin and change OWLv2 parity behavior (backend/Makefile comment);
  no GPU runner, no CUDA.
- **Modal.** No `modal` invocations: no `make weights` (Modal weights fetch),
  no `make e2e-dev` (Modal capture-api), no cloud state/storage backends. The
  in-suite journeys run no-cloud by the documented SD-73/SD-11 settings
  (`HSS_ORCH_STAGE_STUBS=1`, prewarm off).
- **Fixtures fetch.** No `make fixtures` — the store-backed families (ETH3D
  laser PLYs and friends) are unreachable from CI; the job relies on the
  committed fixtures (`make validate` covers those: scenes=5, verdicts=16,
  labels=61, packets=36).
- **Live e2e.** No `make e2e` / `e2e-local` (needs Modal pipeline outputs —
  the recon `points.ply` files are gitignored Modal outputs), no
  `validate-full`, no `criteria-golden` (needs the laser PLYs from
  `make fixtures`), no `make evals` (a stage that cannot run FAILS by design),
  no `reads-extra`/`lamar`. The iOS suite and web checks are other workflows'
  scope.

## Not verified here

- Actual execution on a GitHub-hosted runner (applying the workflow requires
  a `.github/` change — that is Sam's copy step). The workflow text, its pins
  and its inputs are verified against the actions' published `action.yml` at
  the pinned tags, and both pins are the exact commits `server.yml` already
  runs.
- Hosted-runner wall times (see the caveat under the measured table).
- macOS hardware: `ci_local.sh` portability is verified against a BSD-like
  `date` stub (a `%N`-less date, and a run with no `python3` on PATH) plus
  code inspection (no `%N`, no bash-4-only constructs, `set -euo pipefail`
  and arithmetic that work on the stock `/bin/bash` 3.2), not on a real Mac.
- The behavior of the four SD-96 tests after Sam's decision.
