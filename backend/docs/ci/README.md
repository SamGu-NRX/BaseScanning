# backend/docs/ci — the proposed backend CI job

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

## How to apply

Copy **one file**:

```sh
cp backend/docs/ci/backend.yml.proposed .github/workflows/backend.yml
```

That is the whole change. The workflow that activates is the one proved below.
The applying pull request itself changes `.github/workflows/backend.yml`,
which is inside the workflow's own `paths` filter, so the first run happens on
that PR before anything merges. Nothing else is touched: no `.github/` edits,
no `uv.lock` or dependency pins, no thresholds or gate windows, no secrets.

## How to run the same steps locally

```sh
bash backend/scripts/ci_local.sh               # fails fast: nonzero on the first failing step
bash backend/scripts/ci_local.sh --keep-going  # runs every step, still exits nonzero if any failed
```

The script mirrors the job step for step; the job's `actions/checkout` and
`astral-sh/setup-uv` steps are environment provisioning with no local
equivalent. Run it from a checkout with uv installed.

## Measured proof run

Fresh clone of `SamGu-NRX/BaseScanning`, checked out at `alpha` =
`4bcbedba` (the planning-time head), branch
`obv/burst11-bs-backend-ci-proposal`, run with
`bash backend/scripts/ci_local.sh --keep-going`:

The same clone first ran `make test` on **untouched alpha** (`4bcbedba`) as a
baseline: **4 failed** (exactly the four SD-96 gate tests named below),
1425 passed, 19 skipped, **14 collection errors** (all of
`tests/test_capture_cold_restart.py`) in 487.78s — establishing that the
deselections target precisely the pre-existing failures.

Canonical proof run, per step (start `08:05:13`, end `08:14:13`):

| Job step | Command | Result | Wall time |
|---|---|---|---|
| Install dependencies (locked) | `uv sync --frozen --extra api` | ok | 0.0s (warm cache; cold sync downloads ~2 GB once) |
| Ruff check | `uv run --frozen ruff check .` | ok | <0.1s |
| Ruff format check | `uv run --frozen ruff format --check .` | ok | <0.1s |
| Import contracts | `uv run --frozen lint-imports` | ok (5 kept, 0 broken) | 0.1s |
| Tests | `make test` with the deselections | ok — **1425 passed, 19 skipped, 4 deselected**, 8 warnings | 527.3s (pytest 525.91s) |
| Schemas | `make schemas` | ok | 0.3s |
| Repo hygiene | `make hygiene` | **FAILED — named known failure** (see below) | 0.3s |
| Committed fixtures | `make validate` | ok — scenes=5 verdicts=16 labels=61 packets=36 | 2.1s |

Overall script exit: **1**, from the hygiene step only — the `--keep-going`
mode runs every step and exits nonzero when any failed; it does not hide
anything. The test-step result is the point: with the four SD-96 ids
deselected and the cold-restart file ignored, the suite is exactly the alpha
baseline minus the named pre-existing failures (same 1425 passed / 19 skipped;
the 4 failures and 14 errors are gone). An earlier `ci_local.sh` pass on the
same clone recorded the test step at 576.2s (the sandbox is shared with
concurrent fleet threads, so timings vary between runs).

Sandbox: the packet's Debian 13 fleet sandbox (8 cores), uv 0.13.1,
Python 3.12 (`.python-version`), `.venv` 2.1 GB after sync. On a hosted
`ubuntu-24.04` runner the first run additionally downloads the
dependency set once (uv cache 2.1 GB here), after which `setup-uv`'s cache
restores it.

## Cache

- `astral-sh/setup-uv` with `enable-cache: true` caches `~/.cache/uv`, keyed on
  `backend/uv.lock` (`cache-dependency-glob: uv.lock`). Measured here:
  **2.1 GB** for `~/.cache/uv`, **2.1 GB** for `backend/.venv`.
- `cache-python: true` also caches the uv-managed CPython 3.12, so the runner
  does not re-download Python per run.
- First run on a cold runner pays the download once (~2 GB, dominated by the
  torch pin in the default dependency groups); later runs restore from cache.

## Runner image

`ubuntu-24.04` — the same pinned image `server.yml` uses. Justification: with
the `api` extra the suite is pure CPU (no GPU, no Modal, no docker, no Swift),
so a hosted x64 Linux runner is the cheapest sufficient image; pinning the
version (rather than `ubuntu-latest`) keeps the glibc/Python surface stable,
which matches this repository's existing convention.

## Known failures on alpha (named, not hidden)

At `alpha` = `4bcbedba`, three kinds of failures pre-exist this proposal. The
job deselects or names them so its signal stays usable; deleting the relevant
block returns them to CI.

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
   (synthetic-meter.tar)` — the tracked file sits in the repo's own
   `DENY_NAMES`. The job keeps the step and names the state in a comment; it
   goes green when alpha stops tracking that file or the deny-list changes —
   both outside this proposal.

### The SD-96 decision (Sam's)

Two options, with their exact effect on the proposed job:

- **Option A — run them.** Delete the `PYTEST_ADDOPTS` env block from the
  `Tests (make test)` step in `backend.yml.proposed` (and the matching line in
  `ci_local.sh`). The four tests run on every backend pull request and fail
  until SD-96 is decided; the job is red from day one and cannot gate merges;
  the failure set is exactly these four tests plus, until the in-flight repair
  lands, the 14 cold-restart collection errors.
- **Option B — deselect those four test ids by name (as proposed).** The job
  is green except the named hygiene step; the other gate tests in
  `test_gates.py` still run (the deselection is by exact test id, so the
  blast radius is exactly four tests); restoring them later is deleting one
  env block, and the block's comment plus this README point at the SD-96
  decision.

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

- Actual execution on a GitHub-hosted runner (applying the workflow requires a
  `.github/` change — that is Sam's copy step). The workflow text, its pins
  and its inputs are verified against the actions' published `action.yml` at
  the pinned tags, and both pins are the exact commits `server.yml` already
  runs.
- Hosted-runner wall times (see the caveat under the measured table).
- The behavior of the four SD-96 tests after Sam's decision.
