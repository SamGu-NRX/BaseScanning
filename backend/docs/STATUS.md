# Backend status

Honest state of `backend/` on the capture-replay chain, written for the
integration-gate task (burst5-20261015-bs-integration-gate). Every command below
was run this session in a fresh clone, from `backend/`, with
`uv run --frozen --extra api` unless noted. Failures are listed, not summarized
away. The earlier inventory (burst2, PR #248) recorded the same baseline
failures on its branch; nothing here contradicts it.

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
- `tests/test_integration_gate.py` (this branch) — the gate: the committed
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
STORAGE_BACKEND=local HSS_ORCH_STAGE_STUBS=1` — **153 passed** this session.

## Current evaluator results

- `make gates-offline` (this session): **pass 134, fail 1, known_fail 0,
  warn 1, skip 28.** The one FAIL is
  `positive/eth3d-facade/gt-laser-gravel/C/footprint3in` — "route 9.1834 ft
  exceeds 9.0". Its known-failure window (`until: "2026-10-04"`) in
  `server/eval/gates.yaml` has expired, so the gate correctly reports FAIL
  again. Extending a placement-criteria window is a WS-09 policy call, so it is
  recorded here, not patched. `make ci` therefore exits 1 on this branch for
  this reason alone.
- Full suite `make test` results: see "Failures, kept visible" below.
- `make schemas` passes; `make validate` passes (5 scenes, 16 verdicts,
  61 labels, 36 packets); `make lint` passes (ruff check, ruff format,
  import-linter: 5 contracts kept, 0 broken).

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
| `make test` (full suite) | see below | 1 SSR-hygiene failure + eval-gate failures from the expired known-failure window; identical on the base branch, none touch this change |
| `make gates-offline` | pass 134, fail 1, warn 1, skip 28 | the expired window above; exit 1 |
| `make criteria-golden` | not executable | laser/occluder fixtures cannot be fetched |
| iOS suites | not run | no Swift toolchain |
| `make lint` | pass | — |
| `make schemas` | pass | — |
| `make validate` | pass | — |
| orchestrator stub suite | 153 passed | — |
| `tests server/api/tests` | 203 passed, 1 failed | the SSR-hygiene pre-existing failure |
| runner receipt | exit 0 | — |
| replay receipt | `ok=true`, terminal `manual_review` | — |
| integration gate test | 1 passed | — |

Final full-suite numbers are appended below after the run; the failure
classification was done before any code changed.
