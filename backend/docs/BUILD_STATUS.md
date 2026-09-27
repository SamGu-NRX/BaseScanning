# Build status

Snapshot for agents taking over. Updated UTC: 2026-09-27T03:31:17Z. `main` is `dc5cc3a` (PR 63). M1 is live on image `41a0880` (PR 56): a no-token end-to-end run returned a schema-valid result. That image is behind this `main`. This file has no store links. Open-PR notes below were checked with `gh pr list` at the same time; older checklist wording is corrected where `main` has already moved.

## Program

- Build plan: `docs/build/server-pipeline-build-plan.md`. Brief index: `docs/build/README.md`. Seam decisions: `docs/build/decisions.md`.
- PR babysitting: the coordinator checks open PRs, sends validators, and routes review comments to builders.
- SSR prompt text was removed from the repo (PR 40, merged). Do not copy it back. Briefs name the forbidden filenames and do not quote that wording.
- Collaborators: SamGu-NRX has accepted. ShreySuri and AidenJohnston were invited with write and were still pending when this snapshot was taken.
- Cloud agents reach this private repo with the SMP GitHub App (repo-scoped, one-hour tokens). They do not use any other credential.
- GCP dev is the only environment (SD-77): one live stack, billing, capture and fixtures buckets with deletion lifecycle, Secret Manager, keyless logins, CI limited to `main`. WS-12 is dropping unapplied Terraform so the config matches that stack. No `terraform apply` from an assisting agent.
- Live model gates are approved on a capped run and are waiting on the WS-08 and WS-07 live paths. `VISION_EXTERNAL_CALLS` defaults off (PR 50, merged).
- Hunter's account items (spend headroom, a later capture day, label approval, Modal limit) stay with Hunter. Do not raise limits or apply infrastructure.
- Integration and handoff docs are on `main` (PR 58). They are refreshed after a merge that changes the deployed API, and at least hourly overnight, on `hygiene/status-<time>`.
- Product calls still open: rule set C for Texas, whether the homeowner sees reject, photo-fact auto-pass, 39.5 in battery height, whether photos may go to OpenAI or xAI, calipers for Aclara / L+G / Sensus meters, unconfirmed meter number forces review, photo retention default 30 days. README defaults apply until those are decided.

## Builders

- WS-01 ingest API — PR 34 merged (`93638bf`). PR 59 merged (`8421c8b`): dev with `HSS_AUTH_MODE` unset is auth mode `none`. That commit is not in the live image `41a0880`. See `docs/INTEGRATION.md`.
- WS-02 packet validation — PR 37, branch `ws-02/packet-validation`, open. Last recorded round failed (crash on malformed inputs, purpose list, CI not running those tests). Builder was fixing.
- WS-03 orchestrator — PR 29 merged (`6f5ff86`). Stage stubs run in-process. Remote Modal stage execution is a follow-up before M2 (SD-81). Follow-up PR 57 merged (`bcc1baf`) and is not in the live image.
- WS-04 pose backbone — PR 32, branch `ws-04/pose-backbone`, open. Diagnosis: poses themselves were fine; the scorer artifact and fallback rule were wrong. Builder was applying that fix list.
- WS-05 scale layer — PR 35 merged (`0a7878b`). On `main`, not in the live image `41a0880`.
- WS-06 dense scene — PR 31, branch `ws-06/dense-scene`, open (draft). Fixture scores matched the facade reference. Runtime proof on Modal was the remaining step.
- WS-07 objects — PR 28, branch `ws-07/object-understanding`, open. A round-2 validator was running.
- WS-08 meter and panel reads — PR 30 merged. Follow-up PR 52 merged (`27561f6`, live reads gate, key-name hygiene).
- WS-09 criteria engine — PR 39, branch `ws-09/criteria-engine`, open. Round 2 still had a safety regression (inferred objects clearing checks), a delivery crash, lint, and a gravel-pass miss.
- WS-10 result and AR — PR 42 merged. Follow-up PR 48 merged. Copy-parity PR 53 merged (`87b7c60`) and is not in the live image.
- WS-11 eval and regression — PR 33, branch `ws-11/replay-and-gates`, open. Round 2 failed (replay client incomplete, a missing laser PLY passed silently, stubs left in other packages).
- WS-12 deploy and infra — PR 36, branch `ws-12/infra-terraform`, open (draft). One environment, deploy only from `main`. The live image is `41a0880`, which is `main` at PR 56.
- WS-13 real house and data — PR 25 merged. Wording fix PR 46 merged. PR 63 merged (`dc5cc3a`, modern ARKit tracks) and is not in the live image. Twelve houses are packaged. Draft labels for five new houses wait on Hunter.
- WS-14 iOS kit — PR 43 merged (`55e0a41`). PR 54 merged (`321e482`, id patterns). PR 55 merged (`e941928`, prompt gaps and auth-off replay). PR 60 merged (`6fb87b9`): the kit doc says `CAPTURE_API_TOKEN` is optional.

## Integration and quality

- End-to-end integration — M1 passed on the live URL (image `41a0880`, GCS, auth off): schema-valid `ResultResponse`, capture `cap_01M3GEDBNXKEZ5AWD6BR3GYRHW`, run `run_9b0b07f08045853f`, `manual_review`, 59 s, stub stages. Finalize currently takes about 60 s to respond; a fix is coming. PR 44 (`integration/e2e-dev`) stays open as a draft. The M2 dry run found stage handoff breaks. Live URL detail is the Current status block in `docs/INTEGRATION.md`.
- Shared-file conflicts: every merge that touches `Makefile`, `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, or `server/tests/test_skeleton.py` conflicts the other PRs. SD-80 is the carry-over rule. Do not mix feature work into a conflict-merge commit.
- Golden scene fix PR 51 merged. WS-00 security contracts PR 47 merged. Vision-calls-off PR 50 merged. PR 56 merged (`41a0880`, reads-gate ignore and on-only vision wording).
- WS-00 fixtures PR 41 merged. Contracts PR 26 and skeleton PR 38 merged.

## Open pull requests at snapshot time

28, 31 (draft), 32, 33, 36 (draft), 37, 39, 44 (draft), 65, 67. PR 65 (WS-01 auth pin) and PR 67 (WS-03 hint de-dup) are with validators. No `assist/` PR is open. No `assist/` PR is open, and none targets a workstream branch. Confirm with `gh pr list` before acting. PR descriptions override this list when they disagree.
