base: alpha
title: [obvious-fleet] Backend docs: measured alpha target sweep + real-socket journey
draft: true
---
fleet-packet: burst11-bs-alpha-target-sweep

**Docs change:** `obv/burst11-bs-alpha-target-sweep` @ `f7b61071` (one commit: `backend/docs/STATUS.md` rewritten + `PR.md` draft) · **Base:** `alpha` @ `4bcbedba7ffd3d8c4d7f725395e33ed5a9d27d6c` · **Human author:** Sam Gu (sgu07966@gmail.com)

This `obv-pr/…` carrier holds only this description and the captures under `evidence/`; the docs commit itself lives on `obv/burst11-bs-alpha-target-sweep`. Nothing merged, no workflows touched.

## What this is

`backend/docs/STATUS.md` rewritten from a measured run of **every documented backend command** on pure `alpha` (fresh clone, no credentials, nothing deployed), plus a **real-socket device journey** through the capture API — each command and each journey step screen-recorded with `asciinema` and rendered to PNG with `agg` (no retyped output). Document rows carry documented status, measured result, evidence file, measured-at and observer.

## Headline results (all from the recordings)

- **Green, measured:** `make setup` (9 s), `uv sync --frozen --extra api`, `make lint` (5 import contracts kept), `make schemas`, `make validate` (5 scenes / 16 verdicts / 61 labels / 36 packets), `make orchestrator-e2e` (**176 passed**), `make result-golden`, `make fixtures ONLY=small` (**119 items, zero missing**), `scripts/capture_flow_e2e.py` (`ok: true`), `scripts/replay_capture.py --receipt` (`ok: true`, terminal `manual_review`).
- **Known failures, reproduced and confirmed pre-existing on pure alpha:** `make test` 1425 passed / 19 skipped / **4 failed** / **14 errors** — the 4 are the single expired SD-96 window in `server/eval/gates.yaml` (`gt-laser-gravel/C/footprint3in`, route 9.1834 ft > 9.0, `until: 2026-10-04`), the 14 are `tests/test_capture_cold_restart.py` setup importing `load_receipt`, undefined on alpha's `scripts/replay_capture.py`. `make gates-offline` 134 pass / 1 fail / 1 warn / 28 skip (same SD-96 row). `make hygiene` exits 2 on the `synthetic-meter.tar` deny-list hit (since `f0df5cd9`). `make evals` and `make criteria-golden` exit 2 with `CriteriaInputError: occluders_missing` (`server/criteria/run.py:78`) — tier-`all` laser/occluder fixtures not authorized; the small tier fetches clean.
- **New findings this run:** `make e2e-local`'s `--local` flag is dead code — `server/eval/e2e.py:33` never imports the in-process API, so the target runs no journey at all; `make reads-gate` fails on an undocumented precondition (`STORE` unset, breadth cache absent, `scripts/reads_gate.py`).

## The device journey over a real socket (first time over real HTTP)

uvicorn `server.api.app:create_app --factory` on `127.0.0.1:8641` with the documented dev settings (`STATE_BACKEND=memory`, local volume storage, static device+reviewer tokens, HMAC secret, `HSS_INGEST_HOOKS=auto` + `HSS_ORCH_STAGE_STUBS=1` + `HSS_ORCH_PREWARM=0`), driven by a separate `httpx` client per step: create **201 uploading** → register **200, 11 files, signed upload URLs** → **11/11 signed PUTs 200** → commit **200, committed=11, no notFound/mismatch** → finalize **202, `runId=run_8c8de965f367c707`** → events **200, 31 events, terminal `verdict_ready`, validate stage carrying the finalize runId** → status **200, `manual_review`** → result **200, device-masked `outcome.kind=manual_review`, `recommendedPlacement=null`, top-level runId matches** → retried create **200, same captureId** (stored create body replayed by design, `server/api/routes/captures.py:66`). Both anomalies seen on first pass were investigated and are not API defects (stored-body replay; top-level `runId` per `server/api/routes/result.py:83`).

## Why this is a draft

The docs commit itself passes everything that checks docs, but the package it reports on does not all pass: every red gate (`make test`, `make ci`, `make gates-offline`) hangs on the single expired SD-96 window, and the maintainer's decision there — re-extend with a fresh reason, or close the 9.18 ft route as a real miss — is follow-up 1 in the rewritten document. Draft until that call is made.

## What did not get verified (honest gaps)

- `make ios-kit` not re-run this sweep (sandbox has no `zip` binary; last verified 2026-10-15 with a `zip` shim).
- `/docs` page not visually captured (no Playwright browser at run time); the OpenAPI document behind it is covered by `make schemas`.
- `make weights`, `make e2e-dev`, lamar targets, deploy targets, `make gates-update` — cloud/paid/dataset-download/mutating (never deploy). GPU breadth tracks and live credentials absent.
- Swift/iOS side: no Swift toolchain.

## Document changes

- `backend/docs/STATUS.md` — rewritten: sweep table (command × documented status × measured result × evidence), not-run table with reasons, real-socket journey section, refreshed failures and 9 follow-ups (SD-96 first). No code changed.

## Evidence

- **This branch:** `evidence/` — the per-command and per-journey-step asciinema casts (`*.cast`), PNG renders (`*.png`), raw logs and `summary.txt` under `evidence/logs/`.
- **Obvious project:** `sweep11-evidence.zip` (file `fl_OZ03NG7E`), same contents archived.

🔗 [Obvious Project](https://app.obvious.ai/p/products-Uu1863v1) · 🧵 [Obvious Thread](https://app.obvious.ai/p/products-Uu1863v1?thread=th_M4i2Agjr) · 🔀 [Obvious PR Artifact](https://app.obvious.ai/link/pr-artifact/backend-user-run-every-documented-backend-command-task:todo_o6zXzaOb)
