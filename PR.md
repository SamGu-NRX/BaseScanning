# Docs: measured backend status for alpha `4bcbedba` (full target sweep + real-socket journey)

**Branch:** `obv/burst11-bs-alpha-target-sweep` · **Base:** `alpha` @ `4bcbedba7ffd3d8c4d7f725395e33ed5a9d27d6c` · **Human author:** Sam Gu (sgu07966@gmail.com)

## What this is

One commit, one file: `backend/docs/STATUS.md` rewritten from a measured run of
**every documented backend command** on pure `alpha` (fresh clone, no
credentials, nothing deployed), plus a **real-socket device journey** through
the capture API — each command and each journey step screen-recorded with
`asciinema` and rendered to PNG with `agg` (no retyped output). The document's
rows carry documented status, measured result, evidence file, measured-at and
observer.

## Headline results (all from the recordings)

- **Green, measured:** `setup`, `lint`, `schemas`, `validate` (5 scenes / 16 verdicts / 61 labels / 36 packets), `orchestrator-e2e` (**176 passed**), `result-golden`, `fixtures ONLY=small` (**119 items, zero missing**), `capture_flow_e2e` (`ok: true`), `replay_capture --receipt` (`ok: true`, terminal `manual_review`).
- **Real socket:** the full device journey — create **201 uploading** → register **11 files** → **11/11 signed PUTs** → commit **committed=11, no notFound/mismatch** → finalize **202, `runId=run_8c8de965f367c707`** → events **31, terminal `verdict_ready`, validate-for-run** → status **`manual_review`** → result **200, masked device `manual_review`, runId matches** → retried create **200, same captureId**. This is the first time the journey is proven over real HTTP (previous evidence was in-process TestClient).
- **Known failures, reproduced and confirmed pre-existing on pure alpha:** `make test` 1425 passed / 19 skipped / **4 failed** (the single expired SD-96 window in `server/eval/gates.yaml`, `gt-laser-gravel/C/footprint3in`, `until: 2026-10-04`) / **14 errors** (`tests/test_capture_cold_restart.py` setup: `scripts/capture_cold_restart.py` imports `load_receipt`, undefined on alpha's `scripts/replay_capture.py`); `make gates-offline` 134/1/1/28 (same SD-96 row); `make hygiene` deny-list hit on `synthetic-meter.tar` (since `f0df5cd9`); `make evals` + `make criteria-golden` blocked on tier-`all` laser/occluder fixtures (`occluders_missing`, `server/criteria/run.py:78`).
- **New findings this run:** `make e2e-local`'s `--local` flag is dead code — `server/eval/e2e.py:33` never imports the in-process API, so the target runs no journey at all; `make reads-gate` fails on an undocumented precondition (`STORE` unset, breadth cache absent); the small fixture tier fetches 119/119 clean (the 2026-10-15 "laser items missing" applies to the tier-`all` items it no longer attempts).
- **Deliberately not run:** `make weights` / `make e2e-dev` / lamar (cloud, paid or dataset downloads — prohibited), `make ios-kit` (no `zip` binary; verified 2026-10-15 with a shim), deploy targets, `make gates-update` (mutates pinned gate data).

## Document changes

- `backend/docs/STATUS.md` — rewritten with the sweep table (command × documented status × measured result × evidence), the not-run table with reasons, the new real-socket journey section, refreshed failures/follow-ups (9 items, SD-96 first). No code changed.

## Risk

Docs-only commit; no executable change. The status numbers it asserts were all
reproduced this session from a fresh clone; older conflicting numbers from
2026-10-15 runs are kept and labeled.

## Evidence

Recordings and PNGs (17 target phases + 10 journey steps) plus raw logs and the
summary are published with this PR description as `sweep11-evidence.zip`.

🔗 [Obvious Project](https://app.obvious.ai/p/products-Uu1863v1) · 🧵 [Obvious Thread](https://app.obvious.ai/p/products-Uu1863v1?thread=th_M4i2Agjr) · 🔀 [Obvious PR Artifact](https://app.obvious.ai/link/pr-artifact/backend-user-run-every-documented-backend-command-task:todo_o6zXzaOb)
