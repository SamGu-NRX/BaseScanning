# Backend status

Honest state of `backend/`. This file merges two earlier documents that had not
met: the burst2 inventory written after the move into the monorepo (PR #248,
checked 2026-10-15 on the port commit `2d0911f8` plus this branch's SSR fix),
and the capture-chain status written for the integration-gate task
(burst5-20261015-bs-integration-gate) on `alpha`. Both halves ran in fresh
clones, from `backend/`, with `uv sync --frozen` and the `api` extra unless
noted. Failures are listed, not summarized away. Where the two runs disagree,
the newer evidence wins and the older number is kept, labeled: the capture-chain
run is the newest, and where it saw an SSR-hygiene failure this branch's fix
(`f4a8536c`) supersedes it, because the merged tree carries the fix.

## What the capture path proves

The device's own journey — `POST /v1/captures` → register → signed `PUT` →
commit → finalize → poll events → read result — runs end to end over the
committed `mini-arkit` packet fixture, in-process, with no cloud service:

- `tests/test_capture_fixture_flow.py` — persisted bytes, stored hashes, commit
  counts, the `awaiting_files` path when a file never arrives.
- `scripts/capture_flow_e2e.py` — the same journey as one command, leaving a
  JSON receipt (`captureId`, statuses, file counts, error codes). Ran clean
  this session: exit 0.
- `scripts/replay_capture.py` — re-sends a saved receipt to a fresh app
  instance and asserts the idempotency invariants (retried create returns the
  stored capture; retried finalize returns the same deterministic runId;
  committed hashes equal the receipt's). Ran clean this session: `ok=true`,
  terminal `manual_review`.
- `tests/test_integration_gate.py` (the capture chain) — the gate: the committed
  packet through create, upload, commit, finalize, **the configured local hook**
  (`HSS_INGEST_HOOKS=auto` + `HSS_ORCH_STAGE_STUBS=1`, the documented SD-73 dev
  setting; prewarm off via the documented SD-11 knob so the gate stays
  no-cloud), event polling, and result retrieval. It asserts a useful terminal
  result: the runId equals `run_id_for_capture(captureId, packet bytes)`, all
  eleven stages reach done, exactly one terminal `verdict_ready` with that
  runId and kind, and `GET /result` serves `outcome.kind == manual_review` with
  the finalize runId and a masked verdict for the device principal. Every other
  journey test on this chain runs with hooks `null` and stops at
  `processing`; this file is the only proof of the last leg.

The wire contract the iOS uploader reads (`CaptureAPI.swift` keys, error-body
shapes, signed-URL capabilities) is exercised at the HTTP boundary by these
tests; the Swift decode side has never been executed (no Swift toolchain in the
sandbox).

## What the deterministic orchestrator proves

With stage stubs, the real coordinator, state store, storage, events and
outcome table run inline: a finalized packet produces a run, eleven stage
records reaching done, exactly one terminal event, and the `result/result.json`
the result route serves. Stub verdicts are always `manual_review` — that is the
deterministic path the gate asserts, not a hope. What this proves is the
*wiring* (finalize handoff → `start_run` → coordinator → outcomes → API), not
vision or geometry quality; that is the evaluator's domain below.

Suite: `server/orchestrator/tests` with `STATE_BACKEND=memory
STORAGE_BACKEND=local HSS_ORCH_STAGE_STUBS=1` — **153 passed** in both runs
(capture chain and burst2 inventory).

## What passed, whole-package

Checked 2026-10-15 on the port commit plus the SSR fix (burst2 inventory,
Debian 13, Python 3.12, `uv sync --frozen` with the `api` extra); nothing was
deployed and no external credentials were set.

| Check | Result |
| --- | --- |
| `uv sync --frozen` | clean install, no resolver complaints |
| `make lint` (ruff check, ruff format, import-linter) | all pass, 459 files, 5 architecture contracts kept |
| `make schemas` | pass, exported schemas match committed copies |
| `make hygiene` | pass (3093 tracked files) |
| `make validate` | pass, 5 scenes, 16 verdicts, 61 labels, 36 packets |
| `make test` | 1205 passed, 17 skipped, 4 failed (see gates below) |
| `make gates-offline` | pass 134, fail 1, warn 1, skip 28 |
| `make ios-kit` | pass (with a `zip` shim; see ios-kit below) |
| orchestrator suite with stub env | 153 passed |

The 17 skipped tests are the `live` marker (real OpenAI, xAI, GCS or R2 calls); no keys were set.

The short version from that run: the port is in good shape. One install ran
clean, lint, schemas, hygiene and fixture validation all pass, and 1205 of 1209
tests pass. The four failures share one cause that has nothing to do with the
move: a known-failure window in the gate config expired on 2026-10-04. One real
path bug from the move is fixed in this PR.

## Components

### Contracts (`server/contracts`)

Pydantic models for the capture packet, result and API envelope, plus the JSON Schema exporter the iOS kit and the validator both read.

- Tests: `server/contracts/tests` runs green inside `make test`.
- End to end: `make schemas` confirms the exported schemas match the committed copies in `server/contracts/schemas/` and `ios-kit/schemas/`. `make validate` proves the schema still accepts all 36 committed packet fixtures.
- Nothing stubbed. The import-linter contract that keeps this package dependency-free passes.

### Server API (`server/api`)

The FastAPI capture API that takes a packet and returns a result.

- Tests: `server/api/tests` runs green inside `make test` on the burst2 run.
- End to end: the burst2 inventory recorded this as not verified; the capture-chain run since proved the device journey in process — create, upload, commit, finalize, event polling and result retrieval over the committed `mini-arkit` fixture, terminal `manual_review` (see "What the capture path proves"). A live deployment run still needs `CAPTURE_API_URL` and deployment is out of scope for both checks (never deploy). `make e2e` runs and correctly SKIPs the pipeline rows ("no ReplayRun timings"), scoring everything else from committed fixtures.
- One wording wart: `server/eval/e2e.py` prints "the in-process API is not importable" whenever `CAPTURE_API_URL` is unset, whether or not the API would import. Cosmetic; see follow-up 5.

### Eval (`server/eval`, `eval/`)

The gate table: thresholds in `server/eval/gates.yaml`, the scorer in `server/eval/gates.py`, producers and the e2e driver. `eval/` is the breadth snapshot of scoring harness and schema builders.

- Tests: `server/eval/tests` runs green except the four failures below. The offline gate run against committed fixtures produces a full table (see summary above).
- End to end: `make gates-offline` works on committed fixtures alone. `make e2e` degrades gracefully: geometry rows SKIP without the laser PLYs, verdict and safety rows score from committed evidence.
- Broken, and it is not the port: the SD-96 known-failure window in `server/eval/gates.yaml` (`positive_pass` entry for `eth3d-facade/gt-laser-gravel/C/footprint3in`, `until: "2026-10-04"`) has expired, so `server/eval/gates.py:_apply_known_failure` now leaves that row at FAIL. That single row is the entire content of all four test failures (`test_gates.py::test_committed_fixtures_are_scored_against_their_labels`, `::test_offline_table_skips_the_engine_and_passes_the_unpatched_reference`, `::test_route_over_the_bar_is_known_until_expiry`, `::test_refresh_rewrites_pins_after_a_committed_edit`) and the one FAIL in `make gates-offline`. The gate engine is doing exactly what it says: after `until`, the miss is a FAIL again. Someone who owns SD-96 has to decide whether to re-extend the window or treat the 9.18 ft route as a real miss. Follow-up 1.

### Scene (`scene/`, `server/scene`)

`scene/` holds the B1/B2 breadth snapshots (criteria objects, equipment close-up reading). `server/scene` holds the pipeline's scene build stages.

- Tests: no committed tests under `scene/`; the pipeline stages are exercised through `server/tests` (for example `test_box_house.py`, which chains dense, coverage and scene publishing) and those pass.
- End to end: the B1/B2 object models need the `objects` extra (torch, transformers, open_clip), which needs weights. Not installed, not run; see follow-up 4.

### Recon (`recon/`, `server/poses`, `server/dense`, `server/scale`)

`recon/` holds the A1-A5 breadth snapshots (SfM/MVS, feed-forward multi-view, metric depth, splatting, world models). The pipeline's pose, dense and scale stages live under `server/`.

- Tests: `server/poses/tests`, `server/dense` (via `server/tests`, Open3D TSDF included through the dev group) and `server/scale/tests` all pass inside `make test`.
- End to end: the actual A-track reconstructions need GPU weights and the ETH3D laser scans. Not run. See follow-up 4.

### Criteria (`criteria/`, `server/criteria`)

`criteria/` holds the C1/C2/C3 breadth snapshots and the spec with thresholds (`criteria/spec/criteria.yaml`). `server/criteria` is the verdict engine: C1 rules, C2 volumetric fit, C3 optional VLM judge.

- Tests: `server/criteria/tests` runs green inside `make test`.
- End to end: blocked on fixture data. `python -m server.criteria.run` on the committed `data/fixtures/scenes/eth3d-facade/gt-laser/scene.json` raises `CriteriaInputError: occluders_missing` (`server/criteria/run.py:78`), because the scene references a laser PLY that `data/fixtures/scenes/` does not carry. `make fixtures ONLY=small` fetches 108 items fine, but the laser items (`eth3d-*-laser`, `eth3d-*-occluders`, plus `eth3d-facade-colmap-*`) fail with "missing" from the fixture store, so `make criteria-golden`'s three scene runs could not execute here. This is a fixture-store access problem, not a path bug from the move. Follow-up 2.
- C3 stays off by default without API keys, which matches the config; the 17 skipped live tests cover it.

### Packet (`packet/`, `server/packet`)

The capture packet schema and `packetgen`, the synthetic packet generator, plus the pipeline's packet validation stage.

- Tests: `packet/tests` and `server/packet/tests` run green inside `make test` (the fuzz test in there is a good sign).
- End to end: `python -m packetgen --help` and all its subcommands import and run; `make validate` passes all 36 packet fixtures, including the three pose-free and 2018-ARKit edge cases.

### ios-kit (`ios-kit/`)

The zip the iOS team installs: schemas, example packets, packetgen, prompts.

- Tests: `ios-kit/tests` passes (18 tests) but is not in pytest's `testpaths`; run it with `uv run pytest ios-kit/tests`. That split is inherited from the source repo, not introduced by the move. Follow-up 3.
- End to end: `make ios-kit` ran through staging, schema consistency (`cmp` of the two schema copies) and packing, producing a 121 KB zip with 78 entries. One sandbox caveat: the image has no `zip` binary and no root to install one, so the burst2 run used a small `zipfile`-based shim on PATH. Every other step of the target ran unmodified; the only unverified part is real `zip` byte-for-byte behavior, which nothing in the target depends on.

## Current evaluator results

- `make gates-offline` (capture-chain run, this session): **pass 134, fail 1, known_fail 0,
  warn 1, skip 28.** The one FAIL is
  `positive/eth3d-facade/gt-laser-gravel/C/footprint3in` — "route 9.1834 ft
  exceeds 9.0". Its known-failure window (`until: "2026-10-04"`) in
  `server/eval/gates.yaml` has expired, so the gate correctly reports FAIL
  again. Extending a placement-criteria window is a WS-09 policy call, so it is
  recorded here, not patched. `make ci` therefore exits 1 on both runs for
  this reason alone.
- Full suite `make test` results: see "Failures, kept visible" below.
- `make schemas` passes; `make validate` passes (5 scenes, 16 verdicts,
  61 labels, 36 packets); `make lint` passes (ruff check, ruff format,
  import-linter: 5 contracts kept, 0 broken).

## Fixed in this PR

- `scripts/ssr_overlap_check.py` defaulted its root to `git rev-parse --show-toplevel`. In the source repo the toplevel held `data/hygiene/ssr_shingles.sha256`; in the monorepo the toplevel is BaseScanning root, one level above the package, so the script crashed with FileNotFoundError and `tests/test_ssr_overlap_check.py::test_repo_has_no_ssr_shingle_overlap` failed. The script now derives its root from its own location, and a new test (`test_default_root_resolves_inside_the_moved_package`) pins that the default root contains both the script and the hash list. Verified the test fails against the old script and passes with the fix. This is the one place the capture-chain run and the burst2 inventory disagreed: the capture chain, whose tree predated the fix, recorded the SSR-hygiene failure as pre-existing; on this branch and in the merged tree the test passes.
- I checked the other path arithmetic that the move could have broken: `server/eval/gates.py`, `server/eval/eth3d/scene_from_recon.py`, `server/objects/*`, `server/orchestrator/stubs/_common.py` and the test `ROOT` constants all count parents from depths that are unchanged by the move, and `chain_hybrid.py` probes vendor locations with fallbacks that resolve. No other edits needed.

## What remains blocked

- **Laser/occluder fixtures.** `make fixtures` cannot fetch the
  `eth3d-*-laser`, `eth3d-*-occluders` or `colmap-*` items from the fixture
  store, so `server.criteria.run` on the golden laser scenes raises
  `occluders_missing` and `make criteria-golden` is not executable. The
  criteria unit tests pass; the golden-scene engine run does not exist here.
- **GPU weights.** The breadth tracks (scene/ B1 object detection, B2 close-up
  reads, recon/ A1–A5) need the `objects`/`gpu` extras and model weights;
  not installed, not run. Every real stage behind the stubs is unverified.
- **Live credentials.** No Modal token, no GCS/R2 storage, no
  `CAPTURE_API_URL` — so prewarm, remote spawn, cloud storage backends and any
  deployed-stack check are unexercised (the standing rule is also: never
  deploy). The evaluator's `runtime/e2e` row SKIPs for exactly this reason.
- **The `/v1/placements` contract mismatch.** The iOS placement client
  (`ios/HouseScanKit/.../PlacementHTTPClient.swift`) posts `scene.json` to
  `POST /v1/placements` and expects a synchronous answer whose
  `stats.input_sha256` matches the submitted bytes. The ported backend serves
  the async `/v1/captures` journey and has no `/v1/placements` route at all.
  Until one side moves, the app's placement client and this backend cannot
  talk to each other; the capture path above is the backend's own contract,
  proven without that endpoint.
- **Swift.** No Xcode/Swift in this sandbox: no iOS build, no
  `swift test` on HouseScanKit, no decode round-trip. The server side of every
  key the Swift clients read is asserted; the client side is read, not run.

## Failures, kept visible

| Check | Result | Failure and why |
| --- | --- | --- |
| `make test` (burst2 run, with this PR's SSR fix) | 1205 passed, 17 skipped, 4 failed | the four eval-gate failures from the expired SD-96 known-failure window only; the SSR-hygiene test passes with the fix |
| `make test` (capture-chain run, tree without the fix) | see below | 1 SSR-hygiene failure + eval-gate failures from the expired known-failure window; identical on the base branch, none touch this change |
| `make gates-offline` (both runs) | pass 134, fail 1, warn 1, skip 28 | the expired window above; exit 1 |
| `tests server/api/tests` (capture-chain run) | 203 passed, 1 failed | the SSR-hygiene pre-existing failure — superseded by this PR's fix, see "Fixed in this PR" |
| `make criteria-golden` | not executable | laser/occluder fixtures cannot be fetched |
| iOS suites | not run | no Swift toolchain |
| `make lint` | pass | — |
| `make schemas` | pass | — |
| `make validate` | pass | — |
| orchestrator stub suite | 153 passed | — |
| runner receipt | exit 0 | — |
| replay receipt | `ok=true`, terminal `manual_review` | — |
| integration gate test | 1 passed | — |

Final full-suite numbers were appended to each source document after its run;
the failure classification was done before any code changed in either.

## Follow-ups, priority order

1. **Decide on the expired SD-96 known failure** (`server/eval/gates.yaml`, the `gt-laser-gravel/C/footprint3in` entry, `until: 2026-10-04`). Until someone with the WS-09 hat either re-extends the window with a fresh reason or closes the miss as real, `make test`, `make ci`, `make gates-offline` and `make e2e` all exit 1. This blocks every CI run of the package, so it goes first.
2. **Restore access to the ETH3D laser and occluder fixtures** (`scripts/fetch_fixtures.py` items `eth3d-*-laser`, `eth3d-*-occluders`, `eth3d-facade-colmap-*`). Without them the criteria engine cannot run on the golden scenes and `make criteria-golden` cannot be verified anywhere fresh. If the store moved, update the manifest; if it needs credentials, document that in `data/README.md`.
3. **Fold the orphan suites into `testpaths`** (`ios-kit/tests`, `scripts/lamar/tests`, `scripts/meter_models/tests`, `scripts/real_house/tests`). All 32 of their tests pass, but `make test` never runs them, so a regression there ships silently. The `testpaths` line is inherited from the source repo, so nothing broke in the move; it is just worth fixing now.
4. **Verify the breadth tracks on a machine with weights and a GPU.** `scene/` B1/B2 and `recon/` A1-A5 were import-checked only through the pipeline stages they share. Running the OWLv2 parity test and one A1 reconstruction end to end would close the biggest honest gap in this inventory.
5. **Fix the e2e skip message** (`server/eval/e2e.py`, around line 29). It claims the in-process API is "not importable" even when the real reason is that `CAPTURE_API_URL` is unset. A one-line wording change; worth it because the current text sent me down a wrong path during this check.
6. **Wire the SSR overlap check into `make hygiene`.** Its own docstring says WS-00 should do this; right now only the test suite runs it.
7. **Settle the `/v1/placements` contract mismatch** (see "What remains blocked"). One side has to move: the iOS placement client or the backend's async capture journey.

## CI proposal for the PR body

The port deliberately excluded `.github/` (7 workflow files). To get the package under CI in the monorepo, a `backend-ci` job on a `push`/`pull_request` filter over `backend/**` running `make setup`, `make ci` and the orchestrator stub suite would cover everything verified above except the GPU-dependent breadth tracks and the live-marker tests. Python 3.12 with `uv sync --frozen` and the `api` extra matches the Makefile's own assumptions.
