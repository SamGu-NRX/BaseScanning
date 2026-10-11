# BaseScanning real-backend evaluation and integration plan

## End goal and source boundary

Evaluate Hunter's actual CPU backend, connect the BaseScanning client through its API, and preserve recoverable capture work with truthful result/error states. Run GPU-dependent arms only as named stubs. A CPU run or schema-valid result is not phone, GPU, geometry-quality or electrical-safety proof.

Sam authorized the private exact mirror `SamGu-NRX/house-scanning-server`: main is `d43d657bcc52898858d372fb5b23afe05c241a98`, with the original 41 branches and no initial edits. The fleet token can clone this mirror in a separate task-owned private checkout. Never write to Hunter's original repository or collaborator branches. Backend edits, tests, raw logs and source/spec/schema artifacts stay on `fleet/*` task branches or the mirror's `alpha`; none may be copied into BaseScanning or another public repo. Public client code talks to the API using its published client contract, not a vendored backend.

The public BaseScanning `server/` is a separate placement solver. Its synthetic HTTP/input-digest checks are not Hunter integration. Historical 42 contract/storage-ID tests used NullHooks and did not establish processing; the mirror now allows rerunning that suite and real CPU stages. Report exact current commands/counts instead of importing historical success.

The only recorded real Hunter run took 662.8 seconds, ended needs_more_photos and produced no preview. Private Oct 4 CPU work used held patches, stubbed objects, no VLM and sparse fallback; it is historical evidence, not a fleet result. Reproduce current-head issues before applying new mirror fixes: parent/child hydration, asset/rules publication, finalized still storage paths, reused region evidence, forced storage selection and raw internal codes in homeowner messages. Do not copy the old held patch set or decide unanswered amendment semantics without the lead's contract decision.

## Shared harness and outputs

Two complementary foundations have one API boundary. The private mirror owner runs the real backend suite and CPU-stage evaluations, produces a safe local-start command and capability/contract results, and makes bounded backend fixes only in that mirror. The public foundation builds `tools/hunter-local-ingest/`, the client runner and shared adapter interface; it may launch a task-owned checkout of the mirror or connect to its local HTTP endpoint, but cannot copy private implementation, schemas/specs or raw backend traces into public output.

At 12:06Z Apps recorded the PM-authorized writer release for #228 from th_XSOmhByi. The evidence is observed zdgu account-wide execution refusal since 09:05:32Z with starter credits zero, not expiry alone or a fabricated per-thread idle receipt. Only the designated evidence-pack repair may write its existing branch/files, and launch requires exact head a08c5662c5e3b870fe62985a157e694ccacde44a; a moved head stops launch. Never resume the old thread. The independent harness still has no evidence-pack ownership. Existing #216/#230/#232/#229 writers need separate handoff before overlapping edits.

One runner accepts candidate ref/adapter, immutable fixtures, seed, backend ref/endpoint and a fresh result directory. Every check identifies its actual transport: REAL_MIRROR_CPU, FAKE_HUNTER fault control, or GPU_STUB. Fakes remain useful for faults that real execution cannot deterministically induce, but cannot replace successful real API/CPU checks. Private internal expectations/tests stay in the mirror. Public expected traces derive from published packet 0.4/HouseScanKit behavior, not copied private schema definitions.

Each run emits `run-manifest.json`, `checks.jsonl`, `summary.json` and appropriate evidence. Pin client/backend/fixture refs, runtime/hardware, seed, commands and actual stage mode. Checks report ID, oracle basis, expected/actual observation, evidence and PASS, FAIL, BLOCKED or NOT_RUN. Public receipts contain sanitized counts, synthetic public fixture identity and API observations only; raw private logs/source/spec/rules and internal fixture payloads stay private. Replay never overwrites artifacts. Credential or private-source leakage blocks publication.

Required evaluations include:

- Real local create/register/PUT/commit/finalize/events/result against the mirror, local temporary storage and supported CPU stages. Keep NullHooks and stub-only returns distinct from actual processing.
- Same/changed idempotency, expiry, missing/corrupt bytes, interrupted bodies, delay/no result, wrong capture/run/result, restart and cancellation where the contract decides the outcome. Unanswered amendment/auth/privacy/clock semantics become explicit lead questions, not invented fields or enums.
- Public Swift uploader/API association and local/native recovery where supported. The backend has no processed-input digest; local IDs cannot establish processed-byte equality. Separate typed Hunter results from legacy PlacementResult.
- Homeowner states before camera start if sending is unavailable, needs-more-photos without preview, proposed rather than fits, no silent legacy fallback, retained work after rejection, and failures without raw internal codes. Preserve existing machine-code site.reasons in the API; human copy belongs to a small client/presenter allowlist, with generic fallback for unknown/untrusted values. Do not add a guessed human_message schema field or expose raw stage IDs/errors/paths. Actions come from the authoritative result status and explicitly requested views, not inferred diagnoses from codes.
- GPU/object/VLM/dense arms with explicit named stubs or known CPU fallback. Empty objects do not prove clearance; sparse fallback is not dense success. No model downloads, cloud processing, real-home uploads, production rules/credentials or spending.
- Current private contract/storage/orchestrator tests and a small meaningful synthetic CPU end-to-end corpus, plus independent negative controls that make the decisive assertions fail. Expand deterministic state/fixture cases for distinct defects rather than padding counts.
- Measured CPU/client latency, memory, request/retry work and flakiness on fixed runtime conditions. Historical 662.8 seconds is not a target or current measurement. Block unavailable dependencies honestly instead of silently selecting a fake service.

## Rounds and ownership

1. Private foundation: rerun current mirror tests, establish actual local CPU ingest/processing, inventory stage failures, and fix reproduced implementation defects only in a `fleet/*` mirror branch. Keep policy/contract forks with the lead. Public foundation runs real API conformance plus labeled fault controls and freezes one client adapter/scoring interface.
2. Direction A owns `tools/hunter-rounds/directions/direct/`, a minimal direct API client using published uploader semantics and bounded retries.
3. Direction B owns `tools/hunter-rounds/directions/resumable/`, a durable journal reconciling only server facts the contract exposes. Extra machinery must demonstrate retained-work benefit.
4. Both use the same accepted backend/harness refs, scenarios/seeds/oracles and runtime conditions. Compare actual CPU/API results separately from fake fault controls and GPU stubs. Shared harness, other directions and collaborator/private source remain outside each public adapter's ownership.
5. The lead council evaluates correctness, backend/client integration, recovery and homeowner UX. Disqualify weakened oracles, hidden retries, skipped real checks and fabricated quality. Prefer the simplest correct mechanism; graft ideas only against a specific measured gap.
6. One integration owner ports selected client behavior into reconciled public app code and joins it to the accepted private mirror endpoint. Finish local/native recovery and presentation polish. Live sending to an external service remains disabled until the agreed auth/privacy/clock/deployment contracts and explicit execution permission exist.
7. Target the remaining measured gaps in a second parallel round, then rerun integrated CPU/client acceptance and polish. Update this single plan in place; keep private backend details in its private companion plan.

## Finish and stop rule

Data loss, privacy/private-source leakage, false saved/ready/clear status or incorrect identity disqualifies a candidate. Compare executable correctness, recoverable work, honest UX and measured CPU/resource cost; keep blocked GPU/phone/external arms separate.

The achievable bar is real local CPU/backend API execution, trustworthy client capture-to-result association and recovery, no known blocking false-success/data-loss issue, and a polished supported local/native journey. Processing quality requires independent known-answer geometry evidence before claims. Original collaborator/GPU/physical-phone/deployment gates remain explicit, not counted as passes.

Stop at the bar or after two comparable rounds do not improve agreed executable metrics. Hold scenarios/oracles/runtime fixed. Do not spend rounds repeatedly on unavailable GPU/external access or lower the bar to a fake result.

## Branch and resource rules

Integration uses alpha from the definitive latest product version, not automatically main. Sourced history identifies the combined #191-to-#216 photo-processing app candidate as current. Live ancestry shows current main c813182a is its ancestor with 219 candidate-only and zero main-only commits. At 11:47Z the unused public alpha fast-forwarded, force false, to #216's codex/photo-processing-candidate-20261004 head 263827efaa5d600ecd7ed0443b6e7dd05fbef807, without discarded commits or a merge. This is its definitive app source, not isolated fleet study branches. The private mirror's alpha was created from its main d43d657bcc52898858d372fb5b23afe05c241a98. Never force-update a diverged integration ref. Existing beta is Aiden's, and Hunter's original repo, TestFlight and production branches remain untouched.

Recent agent-head/deployment evidence permits branch creation under the PM's criterion. Inspect exact mirror triggers before creating its alpha; never execute deploy commands. Candidate production deployment evidence stops work and goes to the PM. Task PRs stay draft; integration needs substantive accept, caretaker pass and an authorized no-fast-forward merge. This lead leaves merges to the user.

Prefer Obvious sandboxes. On Sam's Mac use the long-run lease plus heavy/gui/browser lock, reap only fleet-apps and close previews. Exit 75 means wait/shrink; keep individual processes below the recorded 4 GiB safety bound and stop on OOM. Never force-push, change deployment/billing/workflows, spend on services, expose credentials or copy private material into public repos.
