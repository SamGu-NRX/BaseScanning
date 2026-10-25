# Local capture API — evidence

Produced 2026-10-16 on a fresh clone of `alpha` at `4bcbedba`, branch
`obv/burst11-bs-local-api-serve`, head `993c3627`. Everything here was
captured against the real stack: the launcher script under uvicorn over a
TCP socket, no TestClient, no drain_finalize.

| File | What it shows |
|---|---|
| `local-api-terminal.png` | Frame from the `make local-api` recording: the launcher banner — base URL, session-file path, the stand-in stage list with `placement` real, and the tokens-never-on-stdout note. |
| `local-api.cast` | Full asciinema recording of `make local-api` from a clean `runs/`: dependency-free startup, health wait, banner; stopped with Ctrl-C (Error 130 = SIGINT at the end of the recording is the operator stop). |
| `smoke-terminal.png` / `smoke.cast` | `make local-api-smoke`: the committed packet journey over real HTTP — `6 passed in 6.49s`. |
| `review-page.png` | Playwright screenshot of `GET /v1/captures/{id}/review` with the reviewer bearer from `session.json`, after the capture reached a terminal status. Renders the dev stub review page — locally the compose stage is a stand-in (`HSS_ORCH_STAGE_STUBS=1`), which is the documented dev configuration; the machine-readable outcome is the result envelope below. |
| `result-page.png` | Playwright screenshot of `GET /v1/captures/{id}/result` with the app bearer: the device-facing envelope — `status: manual_review`, `outcome.kind: manual_review`, "Review required before a placement is shown.", `reviewUrl`, `runId`. |
| `live-journey-receipt.json` | Sanitized receipt of the recorded live journey (create → signed uploads → commit → finalize 202 → inline orchestrator run → terminal `manual_review`). Tokens redacted; they are per-run local secrets. |

Reproduction: `make local-api` in one terminal, `make local-api-smoke` in
another (both from `backend/`); the driver for the screenshots reused the
committed smoke-test helpers against the live port.
