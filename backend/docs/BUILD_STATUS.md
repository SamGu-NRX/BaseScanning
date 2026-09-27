# Build status

Snapshot for agents taking over. Updated UTC: 2026-09-27T05:33:50Z. `main` is `e0589bb` (PR 96). GitHub PR states below were checked at 05:33 UTC; refresh them before claiming work. Cursor agents are active. This is an assisting status refresh, not a coordinator takeover.

Live health was rechecked without authentication at 05:33:46 UTC: HTTP 200, version `c786f76cb7af`, storage `gcs`, state `memory`. That image is a revision of draft PR 91, not a merged-main release. A synthetic missing capture returns `404 capture_not_found`. M1's recorded end-to-end proof belongs to image `41a0880`; no new M1 or M2 live proof is asserted. See the seven-field status block in [INTEGRATION.md](INTEGRATION.md).

## Program

- Build plan: `docs/build/server-pipeline-build-plan.md`. Brief index: `docs/build/README.md`. Seam decisions override briefs; measured reports take precedence over prose.
- The active coordinator routes validators and the integrator. Claim work in a draft PR or an owning-PR comment. Do not push another agent's branch; target it with an assist PR.
- Every assist PR requires an independent validator PASS at its head, green CI, and resolved review threads. SD-80 permits only its specified conflict-merge carry-over. Author tests and a GitHub merged state are not independent validation evidence.
- No secrets or key values, real meter numbers, Base SSR prompt text, or files over 5 MB. No force-push, direct main push, or `terraform apply`.
- One environment only (SD-77): the existing GCP/Modal dev stack. External vision defaults off (SD-67). Provider live gates remain separate from offline correctness tests.
- Hunter's spend headroom, capture day, label approval, Modal-limit decisions, and documented product defaults stay with Hunter. Do not raise limits or apply infrastructure.
- Refresh integration/status after relevant changes and at least hourly overnight. No new milestone is claimed, so no milestone Discord note is due.

## Workstreams

| Workstream | Current state and remaining work |
| --- | --- |
| WS-01 ingest API | PRs 34/59/65/72/76/78 merged. Deletion/background-finalize race fix 87 is open, with green CI and author tests; independent head validation remains required. |
| WS-02 packet validation | PR 37 and diagnostic/test-isolation follow-up 85 merged. An independent post-merge review of 85 passed its targeted packet checks; this does not recertify large-fixture timing. |
| WS-03 orchestrator | PRs 29/57/67/71/77/79 merged. Metadata handoff 74 is open. Draft 91 (`82313f3`) adds remote result collection; an earlier revision `c786f76` is live. Lifecycle, tar transport, heartbeat, and persistence findings were posted against `75fc260`; later heads need revalidation. SD-81's remote/live rows remain required before M2. |
| WS-04 pose backbone | PR 32 (`7398db4`) is open. Validate the current head against SD-88; older measured pose gates do not alone validate later publication, registration-count, and tooling changes. |
| WS-05 scale layer | PR 35 merged. Source ancestry does not establish a new live scale-gate result. |
| WS-06 dense scene | PRs 31/94/96 merged. Open3D dependency 93 (`e70de7c`) and draft performance/clocks 95 (`4e67085`) are open. The component doc's `--extra dense` command is invalid until that dependency exists. PR 96 has two evidence findings: cross-region ground votes remain with WS-06; duplicate-frame casting is claimed by assist 98. Frozen-cloud scores are not an image-to-scene proof; remaining L4 caps still need measurements. |
| WS-07 objects | PR 28 and quota fallback 75 merged. The focused review on 75 reports dropped Grok fallback views/instances; no verified fix was found in this snapshot. |
| WS-08 meter/panel reads | PRs 30/52 merged. The coordinated capped live-read gate is separate. Early artifact ownership 71 merged; metadata 74 remains open. |
| WS-09 criteria | PRs 39/81/83 merged. Inferred-flag order dependence found after 81 is fixed by open assist 97. C3/judge photos and wall-view targeting 82 (`b68adad`) remain open. |
| WS-10 result and AR | PRs 42/48/53/68/69 merged. Copy parity and bare early meter identity are on main. |
| WS-11 eval and regression | PR 33 merged, including replay contract fix 84. Contract repair and offline checks do not establish a new live capture-to-verdict run. |
| WS-12 deploy/infra | PR 36 and follow-ups 88/90 merged. The live image now comes from draft 91. No assist agent should apply Terraform. |
| WS-13 real-house data | PRs 25/46/63 merged. Draft labels for five new houses still await Hunter. Real meter values stay out of assist work. |
| WS-14 iOS kit | PRs 43/54/55/60 merged. Contract patterns, prompt gaps, and optional-token replay guidance are on main. |

## Assist queue

| PR | Snapshot head | State / dependency |
| --- | --- | --- |
| [74](https://github.com/huntertcarver/house-scanning-server/pull/74) — registered image metadata | `f2532c8` | Ready, green CI. Dependency 71 merged; independent current-head PASS remains required. |
| [87](https://github.com/huntertcarver/house-scanning-server/pull/87) — prevent deleted-capture resurrection | `151fbad` | Ready, green CI. Independent current-head PASS remains required. |
| [97](https://github.com/huntertcarver/house-scanning-server/pull/97) — clearance evidence independent of object order | `97514d4` | Ready, green CI. Regression fails before the fix; independent current-head PASS remains required. |
| [98](https://github.com/huntertcarver/house-scanning-server/pull/98) — require distinct frames for 2D casts | `e257aee` | Draft, finishing CI. Both real-emitter duplicate-frame regressions fail before the fix. Does not fix the ground-vote finding. |

Assist PRs 71/73/78/80/84/86/89/92 have merged (84 into 33). This records repository state, not an assertion that every independent validation report was published on GitHub.

## Integration seams still requiring evidence

- M1: historical no-token run on `41a0880` returned a schema-valid `manual_review` result in 59 s with stub stages. A new image needs its own end-to-end measurement.
- M2: remote Modal execution/result collection, stage handoff through result composition, and coordinated live/model gates remain unverified. Health 200, artifact tests, and stub-stage results do not prove M2.
- Remote lifecycle: [PR 91 attempt/run-state findings](https://github.com/huntertcarver/house-scanning-server/pull/91#issuecomment-5852861684) and [tar/heartbeat/persistence findings](https://github.com/huntertcarver/house-scanning-server/pull/91#issuecomment-5852885750). The owner retains fixes; re-run the supplied probes on each revised head before claiming closure.
- Scene evidence: [PR 96 findings](https://github.com/huntertcarver/house-scanning-server/pull/96#issuecomment-5852990907). Material votes currently apply to bands the voting cameras did not see. WS-06 owns per-region visibility; assist 98 owns only the distinct-frame casting gate.
- Criteria: [PR 81 order-dependence finding](https://github.com/huntertcarver/house-scanning-server/pull/81#issuecomment-5852941532) changes `pass` to `manual_review` when identical objects are reordered. Assist 97 fixes it; do not treat author checks as its independent gate.
- Deletion race: accepted background finalize can recreate deleted state/start a run. [PR 72 reproduction](https://github.com/huntertcarver/house-scanning-server/pull/72#issuecomment-5852443813); assist 87 is the fix.
- Object fallback: [PR 75 finding](https://github.com/huntertcarver/house-scanning-server/pull/75#issuecomment-5852531110) shows lost frame/instance evidence under full or partial quota failure. Coordinate any fix with WS-07.
- Shared-file merges must preserve all targets, jobs, extras, and dependencies. Recheck CI and the exact-head validation rule after each merge.

Open PRs at snapshot: 32, 74, 82, 87, 91 (draft), 93, 95 (draft), 97, 98 (draft). This documentation PR will be additional. PR descriptions and exact head states override this list when they move.
