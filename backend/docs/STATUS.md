# Backend status

Honest state of `backend/`, measured on **pure `alpha` at `4bcbedba7ffd3d8c4d7f725395e33ed5a9d27d6c`**.

This document merges three runs, newest wins: (1) the burst2 inventory after the
monorepo move (PR #248, port commit `2d0911f8` + SSR fix `f4a8536c`,
2026-10-15), (2) the capture-chain status on `alpha` (2026-10-15), and (3) the
**sweep 2026-10-16** below: a fresh clone of pure `alpha`, every documented
backend command measured and screen-recorded, plus a real-socket device journey.
Failures are listed, not summarized away. Where runs disagree, the 2026-10-16
sweep is the newest evidence; older numbers are kept and labeled.

- **Measured at:** 2026-10-16, 07:27–08:05 UTC (single session, one fresh clone at `/home/user/work/burst11-bs-alpha-target-sweep`).
- **Observer:** Obvious agent (Autobuild), no human output retyped. Debian 13, Python 3.12.15, uv 0.13.1, `uv sync --frozen --extra api`. No credentials set, no cloud services, nothing deployed (standing rule: never deploy). Every command ran under `asciinema rec`, rendered to PNG with `agg`; exit codes, wall times and first errors come from the recordings and logs, not memory.

## Every documented backend command, measured (sweep 2026-10-16)

| Command | Documented status | Measured result | Evidence |
| --- | --- | --- | --- |
| `make setup` | works | exit 0, 9 s | `01-setup.cast/.png` |
| `uv sync --frozen --extra api` | works | exit 0, 1 s | `02-sync-api.cast/.png` |
| `make lint` (ruff check, ruff format, import-linter) | works | exit 0, 1 s; 5 contracts kept | `03-lint.cast/.png` |
| `make schemas` | works | exit 0, 1 s | `04-schemas.cast/.png` |
| `make hygiene` | known FAIL (deny-list) | exit 2 (make Error 1), 1 s — `server/orchestrator/tests/data/packets/synthetic-meter.tar: deny-list`; pre-existing on pure `alpha` since `f0df5cd9` | `05-hygiene.cast/.png` |
| `make validate` | works | exit 0, 5 s — `ok scenes=5 verdicts=16 labels=61 packets=36` | `06-validate.cast/.png` |
| `make test` | 4 known gate failures | exit 2 (make Error 1), 827 s — **1425 passed, 19 skipped, 4 failed, 14 errors**. The 4 failures are exactly the SD-96 gate tests (`server/eval/tests/test_gates.py`); the 14 errors are `tests/test_capture_cold_restart.py` at setup (`:37` imports `scripts.capture_cold_restart`, which imports `load_receipt` from `scripts.replay_capture` — undefined on alpha's copy), identical on pure `alpha` | `07-test.cast/.png`, `07-test.log` |
| `make gates-offline` | 134 pass / 1 fail / 1 warn / 28 skip | same table reproduced: **pass 134, fail 1, warn 1, skip 28**; exit 2 (make Error 1). The one FAIL: `positive/eth3d-facade/gt-laser-gravel/C/footprint3in` — "route 9.1834 ft exceeds 9.0", expired SD-96 window (`until: 2026-10-04`, `server/eval/gates.yaml`) | `08-gates-offline.cast/.png`, `08-gates-offline.log` |
| `make orchestrator-e2e` | works | exit 0, 15 s — **176 passed** (was 153 in the burst2 run; suite has grown) | `09-orchestrator-e2e.cast/.png` |
| `make result-golden` | works | exit 0, 2 s — 3 composes + schema validate, `ok synthetic photo reject` | `10-result-golden.cast/.png` |
| `make evals` | blocked on laser fixtures | exit 2, 2 s — `CriteriaInputError: occluders_missing` at `server/criteria/run.py:78` via `server/eval/suite.py:144` | `11-evals.cast/.png` |
| `make e2e-local` | SKIPs pipeline rows by design; exits 2 ("a missing API is not a PASS") | exit 2, 0 s, SKIP rows as documented. New observation: the `--local` flag is **dead** — `server/eval/e2e.py:33` folds it into the same `CAPTURE_API_URL`-unset branch and never imports the in-process API; `make e2e-local` runs no journey at all, only scores the committed fixture rows | `12-e2e-local.cast/.png`, `12-e2e-local.log` |
| `make fixtures ONLY=small` | works | exit 0, <1 s — **119 items ok** (up from 108 in the 2026-10-15 run), zero missing items. The small tier does not carry the laser/occluder items at all (they are tier-`all`); no missing-item failures this run — the 2026-10-15 "laser items fail with missing" line applied to a run that attempted them | `13-fixtures-small.cast/.png`, `13-fixtures-small.log` |
| `make criteria-golden` | blocked on laser fixtures | exit 2 (make Error 1), 117 s — pytest leg **118 passed, 1 skipped**; engine leg dies on `occluders_missing` (`server/criteria/run.py:78`), same fixture cause as `make evals` | `14-criteria-golden.cast/.png`, `14-criteria-golden.log` |
| `make reads-gate` | not previously measured | exit 2 (make Error 1), <1 s — `STORE is unset and the breadth cache was not found` (`scripts/reads_gate.py`). Needs a cached breadth-read store passed via `STORE`; no network involved. New row: the offline gates document never mentioned this precondition | `15-reads-gate.cast/.png` |
| `python scripts/capture_flow_e2e.py` | works | exit 0, 2 s — `ok: true`, receipt `/tmp/capture-flow-e2e-qz4bwp1z/receipt.json` | `16-capture-flow-e2e.cast/.png` |
| `python scripts/replay_capture.py --receipt <r>` | works | exit 0, ~1 s — `ok: true`, terminal `manual_review`, replay receipt written. (A bare invocation without `--receipt` is argparse-rejected — that first failure was the observer's invocation, not the repo's) | `17-replay-capture.cast/.png` |

Wall times are from the recordings; `make` wrappers report `Error 1` while the
make process itself exits 2, which is why the exit column says 2.

### Documented commands deliberately not run (sweep)

| Command | Reason |
| --- | --- |
| `make weights` | runs `modal run` (Modal + HuggingFace weight download to a Volume) — paid/cloud, prohibited |
| `make e2e-dev` | hard-requires a live Modal deployment (`CAPTURE_API_URL` on `*.modal.run`) — networked deploy, prohibited |
| `scripts/lamar` targets | need the LaMAR five-segment dataset download — prohibited; the gates table SKIPs lamar rows on this machine anyway |
| `make ios-kit` | sandbox has no `zip` binary; packet excludes the produced zip; schema-copy steps are covered by `make schemas` + `make validate` (2026-10-15 run verified the full target with a `zip` shim) |
| `make ci`, deploy targets, `make gates-update` | `ci` re-runs already-measured targets and exits 1 on SD-96 alone; deploy targets deploy; `gates-update` rewrites pinned gate hashes (mutates gate data) |

## The device journey over a real socket (new evidence 2026-10-16)

The 2026-10-15 runs proved the journey in-process (TestClient). This sweep
additionally drove it **over a real uvicorn HTTP socket**: `uvicorn
server.api.app:create_app --factory` on `127.0.0.1:8641` with the documented
dev settings — `STATE_BACKEND=memory`, `STORAGE_BACKEND=volume` (local volume
root), static tokens (`CAPTURE_API_TOKENS` device+reviewer),
`UPLOAD_URL_HMAC_SECRET` set, `HSS_INGEST_HOOKS=auto` +
`HSS_ORCH_STAGE_STUBS=1` + `HSS_ORCH_PREWARM=0`, `VISION_EXTERNAL_CALLS=off`.
Every request below is a real HTTP call from a separate `httpx` client; each
step has its own recording.

| Step | Result | Evidence |
| --- | --- | --- |
| boot + `GET /v1/healthz` | 200, uvicorn up on 127.0.0.1:8641 | `j-boot` (server log), `j-create.cast` context |
| `POST /v1/captures` (create projection, `Idempotency-Key: packetId`) | **201**, `status=uploading`, `captureId=cap_01M51VG6YG57X7M31K8PQAPGRY` | `j-create.cast/.png` |
| `POST /v1/captures/{id}/files` (register 11 sealed files) | **200**, 11 targets, signed upload URLs on the same socket | `j-register.cast/.png` |
| signed `PUT` × 11 | **11/11 HTTP 200** | `j-upload.cast/.png` |
| `POST /v1/captures/{id}/files:commit` | **200**, `committed=11`, `notFound=[]`, `mismatch=[]` | `j-commit.cast/.png` |
| `POST /v1/captures/{id}/finalize` (raw `packet.json`, `X-Packet-Sha256`) | **202**, `runId=run_8c8de965f367c707`, `status=processing`, `missing=[]` | `j-finalize.cast/.png` |
| `GET /v1/captures/{id}/events` (poll) | **200**, 31 events, terminal `verdict_ready`, a `validate` stage event carrying the finalize runId, `status=manual_review` | `j-events.cast/.png` |
| `GET /v1/captures/{id}` | **200**, `status=manual_review`, `filesCommitted=11` | `j-status.cast/.png` |
| `GET /v1/captures/{id}/result` (device principal) | **200**, `outcome.kind=manual_review` ("Review required before a placement is shown.", `recommendedPlacement=null`), top-level `runId == run_8c8de965f367c707` | `j-result.cast/.png` |
| retried `POST /v1/captures` (same key+body) | **200**, same `captureId` — idempotent replay of the stored create body (`server/api/routes/captures.py:66`), so the replayed body's `status` reads `uploading` by design | `j-recreate.cast/.png` |

Two anomalies seen during the first pass were investigated and are **not** API
defects: the re-create's `uploading` status is the stored create body replayed
verbatim (above), and `runId` is top-level on the result document
(`server/api/routes/result.py:83`), not inside `outcome`. The `/docs` page was
not visually captured (no Playwright browser in the sandbox at run time); the
OpenAPI document behind it is covered by `make schemas`.

## Components (unchanged conclusions, refreshed numbers)

- **Contracts (`server/contracts`)** — `make schemas` and `make validate` pass (5 scenes, 16 verdicts, 61 labels, 36 packets). Nothing stubbed.
- **Server API (`server/api`)** — in-process journey green (receipt + replay + orchestrator gate), and now proven over a real socket (table above). A deployed-stack run still needs `CAPTURE_API_URL` and is out of scope (never deploy).
- **Eval (`server/eval`)** — gate table reproduces exactly: 134 pass / 1 fail (expired SD-96 window) / 1 warn / 28 skip. All four test failures are that one row.
- **Criteria (`server/criteria`)** — unit tests pass; golden-scene engine runs blocked on tier-`all` laser/occluder fixtures (`occluders_missing`, `run.py:78`).
- **Packet, scene, recon** — unchanged from 2026-10-15: packet fixtures all validate; breadth tracks still need weights/GPU (not run).
- **ios-kit** — not re-run this sweep (no `zip` binary); last verified 2026-10-15 with the `zip` shim.

## Failures, kept visible (sweep 2026-10-16)

| Check | Result | Why |
| --- | --- | --- |
| `make test` | 1425 passed, 19 skipped, 4 failed, 14 errors | 4 failed = SD-96 expired-window gate tests; 14 errors = `tests/test_capture_cold_restart.py` setup import of `load_receipt` missing from alpha's `scripts/replay_capture.py`; both identical on pure `alpha`, pre-existing |
| `make gates-offline` / gate row | pass 134, fail 1, warn 1, skip 28 | the one FAIL is the expired SD-96 known-failure window (`gt-laser-gravel/C/footprint3in`, route 9.1834 ft > 9.0) |
| `make hygiene` | exit 2 | deny-list hit on `server/orchestrator/tests/data/packets/synthetic-meter.tar`; pre-existing since `f0df5cd9` |
| `make evals`, `make criteria-golden` | exit 2 | `occluders_missing` — tier-`all` laser/occluder fixtures not fetched (not in the authorized small tier) |
| `make e2e-local` | exit 2 | by design; and its `--local` flag is dead code (`e2e.py:33`) |
| `make reads-gate` | exit 2 | breadth cache absent; `STORE` unset (undocumented precondition, see follow-up 8) |
| Swift / iOS | not run | no Swift toolchain |
| GPU breadth tracks, live credentials, deployments | not run | weights and credentials absent; never deploy |

## What remains blocked

Unchanged from 2026-10-15: laser/occluder fixture access (tier `all`), GPU
weights for the breadth tracks, live credentials (Modal/GCS/R2/`CAPTURE_API_URL`),
the `/v1/placements` contract mismatch with the iOS placement client, and all
Swift-side verification. New this sweep: the cold-restart import break in
`scripts/capture_cold_restart.py` (follow-up 9) and the undocumented
`reads-gate` `STORE` precondition (follow-up 8).

## Follow-ups, priority order

1. **Decide the expired SD-96 known failure** (`server/eval/gates.yaml`, `gt-laser-gravel/C/footprint3in`, `until: 2026-10-04`). Re-extend with a fresh reason or close the miss as real. Blocks green `make test`/`make ci`/`make gates-offline`.
2. **Restore/authorize tier-`all` fixture access** (`eth3d-*-laser`, `eth3d-*-occluders`, `eth3d-facade-colmap-*`) so `make evals`/`make criteria-golden` can run; the small tier (119 items) fetches clean, the engine run needs the laser PLYs.
3. **Fix `scripts/capture_cold_restart.py`'s import** of `load_receipt` (missing from alpha's `scripts/replay_capture.py`) — 14 test errors at setup.
4. **Fold the orphan suites into `testpaths`** (`ios-kit/tests`, `scripts/lamar/tests`, `scripts/meter_models/tests`, `scripts/real_house/tests`).
5. **Fix the `e2e-local` skip path**: correct the misleading message (`server/eval/e2e.py`, "not importable" fires whenever `CAPTURE_API_URL` is unset) and either implement the `--local` in-process journey or remove the flag.
6. **Document `reads-gate`'s `STORE` precondition** (or default it) in the gates docs/README.
7. **Verify the breadth tracks on a machine with weights and a GPU.**
8. **Wire the SSR overlap check into `make hygiene`** (its docstring says WS-00 should).
9. **Settle the `/v1/placements` contract mismatch** (iOS placement client vs the async capture journey).

## CI proposal for the PR body

Unchanged: a `backend-ci` job on `backend/**` running `make setup`, `make ci`
and the orchestrator stub suite on Python 3.12 with `uv sync --frozen` and the
`api` extra covers everything verified above except the GPU-dependent breadth
tracks and the live-marker tests — and stays red on SD-96 until follow-up 1 is
decided.
