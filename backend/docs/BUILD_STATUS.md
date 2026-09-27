# Build status

Snapshot for agents taking over. Updated UTC: 2026-09-27T04:36:07Z. `main` is `cc6aa9c` (PR 83). GitHub PR states below were checked at that time; refresh them before claiming work. Cursor agents are active. This is an assisting status refresh, not a coordinator takeover.

Live health was rechecked without authentication at 04:34 UTC: HTTP 200, version `b25cd7e365a8`, storage `gcs`, state `memory`. That image identifies main at PR 44 and is behind this snapshot. M1's recorded end-to-end proof belongs to the earlier image `41a0880`; no M2 live proof is asserted. See the seven-field status block in [INTEGRATION.md](INTEGRATION.md).

## Program

- Build plan: `docs/build/server-pipeline-build-plan.md`. Brief index: `docs/build/README.md`. Seam decisions: `docs/build/decisions.md`. Decisions override briefs; measured reports take precedence over prose.
- The active coordinator routes validators and the integrator. Claim work in a draft PR or an owning-PR comment. Do not push another agent's branch; target it with an assist PR.
- Every assist PR requires an independent validator PASS at its head, green CI, and resolved review threads. SD-80 permits only its specified conflict-merge carry-over. Author tests and a GitHub merged state are not independent validation evidence.
- Keep secrets, key values, real meter numbers, Base SSR prompt text, and files over 5 MB out of assist work. No force-push, direct main push, or `terraform apply`.
- One environment only (SD-77): the existing GCP/Modal dev stack. External vision defaults off (SD-67). Model-provider live gates are still separate from offline correctness tests.
- Hunter's spend headroom, later capture day, label approval, and Modal-limit decisions stay with Hunter. Do not raise limits or apply infrastructure.
- Integration and status documentation should be refreshed after relevant merges and at least hourly overnight. No new milestone is claimed by this snapshot, so no milestone Discord note is due.
- Product calls remain as documented in README: rule set C for Texas, homeowner reject visibility, photo-fact auto-pass, battery height, model-provider photo egress, meter calipers, unconfirmed identity, and retention. Do not silently change those defaults.

## Workstreams

| Workstream | Current state and remaining work |
| --- | --- |
| WS-01 ingest API | PR 34 and auth follow-ups 59/65 merged. Background finalize 72 and transient-storage retry 76 merged. Amendment state reset 78 merged at 04:29 UTC. The deletion/background-finalize race reported on PR 72 remains open in the evidence reviewed for this snapshot. |
| WS-02 packet validation | PR 37 merged. Follow-up PR 85 (`e0a8b93`) is open: malformed stream-cell diagnostics, still-id diagnostics, and test-environment isolation. |
| WS-03 orchestrator | PRs 29/57/67 merged. Artifact publication fixes 77/79 merged. Early-read artifact ownership fix 71 is open; metadata handoff fix 74 depends on it. Remote Modal execution/result collection remains required before M2 (SD-81). |
| WS-04 pose backbone | PR 32 (`3997bd8`) is open. Its body separates prior measured pose gates from the latest publication, registration-count, and tooling fixes. Validate the current head; older measurements alone do not validate the new mechanics. |
| WS-05 scale layer | PR 35 merged. The snapshot main contains it; the current live image also postdates that merge. This is a source ancestry statement, not a new live scale-gate result. |
| WS-06 dense scene | PR 31 (`4e2798c`) is open and has merge conflicts. Its body records correctness evidence and deferred L4 timing work; frozen-cloud scoring is not an image-to-scene proof. Resolve conflicts under the applicable merge gate. |
| WS-07 objects | PR 28 and quota fallback 75 merged. A focused review on PR 75 reports dropped Grok fallback views/instances; it remains a follow-up, not a verified fix. |
| WS-08 meter/panel reads | PR 30 and live-read gate 52 merged. Live gate still needs the coordinated capped run. Early handoff defects are tracked by 71/74. |
| WS-09 criteria | PR 39 merged. PR 81 (`9021c53`) is open with conflicts: inferred-geometry guard, ground strip, and profile-A C2. PR 82 (`de13e1a`) is open: C3/judge photos and narrower wall requests. Delivery C2-off agreement report 83 merged. |
| WS-10 result and AR | PRs 42/48/53/68/69 merged. Copy parity and bare early meter identity are on main. |
| WS-11 eval and regression | PR 33 (`d8c2e43`) is open. It replaces the criteria stub adapter and repairs its test import boundary. Replay contract defects are claimed by child PR 84; the broader geometry/scoring gates remain with WS-11. |
| WS-12 deploy/infra | PR 36 (`bc69292`) is open, no longer draft. Read current commits and validation records: its body still contains older bootstrap/deployment notes. No assist agent should apply Terraform. |
| WS-13 real-house data | PRs 25/46/63 merged. Modern ARKit tracks are on main. Draft labels for five new houses still await Hunter. |
| WS-14 iOS kit | PRs 43/54/55/60 merged. Contract patterns, prompt gaps, and optional-token replay guidance are on main. |

## Assist queue

| PR | Snapshot head | State / dependency |
| --- | --- | --- |
| [71](https://github.com/huntertcarver/house-scanning-server/pull/71) — preserve early-read artifacts | `c2c586f` | Open, ready. Requires the current-head independent gate. |
| [74](https://github.com/huntertcarver/house-scanning-server/pull/74) — registered image metadata | `6875b22` | Draft; land 71 first, then validate the combined early-worker path. |
| [84](https://github.com/huntertcarver/house-scanning-server/pull/84) — replay API contract | `ab9145d` | Draft into `ws-11/replay-and-gates`; author is reconciling the owner's adapter-test repair and finishing CI. |

Assist PRs 73 (bounded e2e uploads), 78 (amendments), and 80 (integration status) have merged. This records repository state, not an assertion that every independent validation report was published on GitHub.

## Integration seams still requiring evidence

- M1: recorded no-token run on `41a0880` returned a schema-valid `manual_review` result in 59 s with stub stages. Integration PR 44 is now merged; upload contract repair 73 is also merged. A newer deployment needs its own end-to-end measurement.
- M2: remote Modal stage execution and result collection (SD-81), actual stage handoff through result composition, and the coordinated live/model gates remain unverified here. Do not infer M2 from health 200, artifact publisher tests, or a stub-stage result.
- Deletion race: an accepted background finalize can recreate deleted state and start a run. The deterministic reproduction is on [PR 72](https://github.com/huntertcarver/house-scanning-server/pull/72#issuecomment-5852443813); amendment fix 78 does not address it.
- Object fallback: [PR 75 finding](https://github.com/huntertcarver/house-scanning-server/pull/75#issuecomment-5852531110) shows lost frame/instance evidence under full or partial quota failure. Coordinate any fix with WS-07.
- Replay: [PR 33 finding](https://github.com/huntertcarver/house-scanning-server/pull/33#issuecomment-5852552949) covers the commit route, confidence-file omissions, and verdict URL. PR 84 owns that fix scope.
- Shared-file merges must preserve all workstream targets, jobs, extras, and dependencies. Recheck CI and the exact-head validation rule after each merge.

Open PRs at snapshot: 31, 32, 33, 36, 71, 74 (draft), 81, 82, 84 (draft into 33), 85. This documentation PR will be additional. PR descriptions and exact head states override this list when they move.
