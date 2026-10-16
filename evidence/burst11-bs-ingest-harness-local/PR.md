# burst11-land-bs-ingest-harness-realapi

Human author: Sam Gu (sgu07966@gmail.com)

## Objective

Run the `hunter-local-ingest` harness in `REAL_API` mode against the repo's backend on
`127.0.0.1:8000` in four configurations (baseline with the fault round, static auth +
`--api-token-env` token, the restart control, and the committed mini-arkit packet), fix what the
real-transport runs exposed in the harness, and commit the run receipts as evidence.

## Load-bearing assumption

The packet assumes running the harness against the real backend surfaces harness-side gaps (not
backend contract defects) and that fixing them keeps the harness's published-contract posture. The
runs confirmed the assumption with one nuance: every FAIL/BLOCKED the runs produced was classified
below — none is a backend contract defect; two are honest labels of local-configuration limits.

## What the runs exposed and what changed

1. **Finalize 422 — the synthetic fixture had no keyframes.** The app's ARKit packets carry
   `keyframes/*.jpg` entries in Packet04; the synthetic ARKit fixture did not, so the backend
   rejected finalize (`422 schema`) and every downstream check blocked. Classified: harness fixture
   gap. `fixturegen.py` now emits three seeded synthetic keyframe image files with matching
   `application/jpeg`-family digests and Packet04 keyframe records; the plan loader validates that
   keyframe entries reference declared files with the `keyframe` role.
2. **The loader only accepted the synthetic family.** `plan.py` now carries a family registry:
   `synthetic` (seeded, nonce-derived ids) and the committed `mini-arkit` packet (fixture-determined
   digests, null seed). `hunter-gen-miniarkit` (new console script) rebuilds the committed packet's
   plan from `backend/data/fixtures/packets/mini-arkit/` and binds its file digests. The receipt
   identity check (`receipts.py`) and the export schema (`export.py`: content-type enum, optional
   seed, generator allow-list) are family-aware.
3. **Comma in device text.** The committed fixture's device model `iPhone15,4` failed the bounded
   device-text charset; the charset now admits commas (real deviceModel usage).

## Failing-first evidence

The new loader/fixture tests were written before the implementation landed. Against the stashed old
code the new test module fails at collection (`gen_miniarkit` does not exist); mid-implementation it
fails on the committed device text (`plan.create_request.device.model: not synthetic device text`);
with the full change it passes. The batch/PUT arithmetic updates in `test_lifecycle.py` and
`test_packaging.py` are the direct arithmetic of the fixture growing from 5 to 8 files.

## The four REAL_API runs plus selfcheck (all receipts committed under `evidence/burst11-bs-ingest-harness-local/receipts/`)

| Run | Configuration | Verdict | Counts by transport | Non-PASS labels |
|---|---|---|---|---|
| selfcheck | FAKE_HUNTER self-check (recorded GIF) | PASS | FAKE_HUNTER 26 PASS; GPU_STUB 3 NOT_RUN | gpu-stub.* NOT_RUN |
| realapi-none | seed-1 plan, no auth, fault round | PASS | REAL_API 13 PASS; FAKE_HUNTER 12 PASS; GPU_STUB 3 NOT_RUN | restart-recovery NOT_RUN (unconfigured), gpu-stub.* NOT_RUN |
| realapi-static | seed-1 plan, static auth + HUNTER_API_TOKEN | PASS | REAL_API 13 PASS; GPU_STUB 3 NOT_RUN | restart-recovery NOT_RUN (unconfigured), gpu-stub.* NOT_RUN |
| realapi-restart | seed-1, no auth, faults + restart control | FAIL | REAL_API 14 (13 PASS, 1 FAIL); FAKE_HUNTER 12 PASS; GPU_STUB 3 NOT_RUN | control.restart-recovery FAIL, gpu-stub.* NOT_RUN |
| realapi-miniarkit | committed mini-arkit plan, static auth + restart control | FAIL | REAL_API 14 (13 PASS, 1 FAIL); GPU_STUB 3 NOT_RUN | control.restart-recovery FAIL, gpu-stub.* NOT_RUN |

Classification of every FAIL/BLOCKED across all runs:

- `control.restart-recovery` FAIL (runs 4–5): after the restart the capture reads are honest
  `404 capture_not_found` — the local dev backend keeps state in memory (`STATE_BACKEND=memory` is
  the only local option; `modal_dict` needs Modal, `postgres` is not built, WS-12). The check
  correctly reports that this configuration does not recover captures across restarts. Not a
  contract defect; a configuration limit the receipt now records.
- `auth_fault_proxy_not_authorized` BLOCKED (run 3, first attempt): by-design harness behavior —
  the FAKE_HUNTER fault proxy never receives the credential, so authenticated fault coverage is
  honestly labeled BLOCKED and the static-auth run is executed without the fault round.
- One run (realapi-static 075649Z) hit `lifecycle.register` 409 because an earlier attempt had
  finalized the same packet id on the still-running memory-state server; reruns against a stateful
  server require a fresh server (receipt kept as an operator-sequencing example, not part of the
  final five).
- The mini-arkit run's app-shaped journey completed against the real backend: finalize `202`
  `processing` (runId, eta 180) → events long-poll → result `manual_review` with preview URL,
  matching the known stubbed-stage behavior for this packet.

## Test battery

- `pytest tests/`: **390 passed** (was 377 before this change).
- `ruff check` and `ruff format --check`: clean on the package and tests.
- `tests/test_plans.py` is new: seeded keyframe coverage, ARKit-without-keyframes loader refusal,
  mini-arkit load + digest binding.
- No backend files changed; the backend suite is untouched.

## Notes

- `tests/contracts/` (JS-syntax contract checks) does not exist in this harness; the contract
  surface is `tests/test_contract.py`, which passes.
- No `make local-api` target exists on alpha. The receipts record only the redacted client flags
  (`<path>`, `<argv>`); the backend server-start command is deliberately not part of any receipt.
  A secret-free local server start for reproducing the REAL_API runs:

  ```sh
  cd backend
  uv sync --locked --extra api
  # Auth-off dev server, local storage: every request is the device principal `demo`.
  SMP_ENVIRONMENT=dev uv run uvicorn server.api.app:create_app --factory --port 8000
  # For the static-auth runs only: HSS_AUTH_MODE=static CAPTURE_API_TOKENS='<label>:<token(>=32 chars)>'
  # and run the harness with --api-token-env (the token is read from HUNTER_API_TOKEN).
  ```

  `SMP_ENVIRONMENT=dev` is what selects `HSS_AUTH_MODE=none`; `STORAGE_BACKEND` defaults to
  `local`, whose in-memory state is ephemeral across restarts — which is exactly what the
  restart-recovery run records as FAIL.

🔗 [Obvious Project](https://app.obvious.ai/p/products-Uu1863v1) · 🧵 [Obvious Thread](https://app.obvious.ai/p/products-Uu1863v1?thread=th_uKOVRjtY) · 🔀 [Obvious PR Artifact](https://app.obvious.ai/link/pr-artifact/run-the-app-shaped-ingest-harness-in-realapi-mode-task:todo_2YMYcAxt)
