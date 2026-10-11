# BaseScanning integration and evaluation plan

## End goal and what is known

Connect a permitted BaseScanning capture to Hunter's real local ingest and executable processing pipeline, present the correct associated result, and preserve recoverable work through failures. Finish the useful client and evidence experience, including error/retry/accessibility behavior. Local success is not an electrical-safety or reconstruction-quality conclusion.

At the audited heads, placement FastAPI handles synthetic requests with an input digest, while Hunter capture create/replay/version/result handlers run separately. Forty-two contract/storage-ID tests passed. Those Hunter checks used NullHooks and prohibited storage, so upload/finalize/worker execution remains unproved. The iOS candidate #216 has advanced to `263827efaa5d600ecd7ed0443b6e7dd05fbef807` but still selects unavailable setup or a DEBUG fixture, not a service connection. Canonical Hunter source is `huntertcarver/house-scanning-server` at `d43d657bcc52898858d372fb5b23afe05c241a98`; private intake changes are not silently canonical. BaseScanning #228 at `a08c5662c5e3` has strict-validation/identity/inspection defects to repair first.

## One harness and explicit outputs

The foundation owner implements `tools/hunter-local-ingest/` and repairs `tools/capture-evidence-pack/`. Reuse existing contract, uploader and orchestrator tests before introducing another runner. Hunter's `server/api/tests/test_ingest.py` covers real signed PUT/commit/finalize handlers in temporary storage; `tests/test_e2e_upload.py` substitutes transport and ends before reconstruction. `server/eval/replay.py` already emits capture/run/packet/event/result receipts. Use these entry points and current #216's HouseScanKit producer/uploader tests, replacing the fixture-HTTP boundary where actual local integration is claimed. `scripts/e2e_dev.py` allows only the live Modal dev stack and is not an authorized local verification command. A single documented command accepts a candidate ref or adapter, immutable fixture inputs, a run directory and seed. Each run gets its own output directory; replay never rewrites its evidence.

Outputs are `run-manifest.json`, `checks.jsonl`, `summary.json` and relevant packet/result/server/client logs with synthetic-only data. The manifest pins client/server refs, fixture digests, runtime/dependencies, hardware, seed, actual hooks/stages and commands. Every check has an ID, exercised boundary, expected observation, actual observation, evidence reference and one outcome: PASS, FAIL, BLOCKED or NOT_RUN. Summary reports those counts by ingest, client recovery, processing, identity and UI. A missing check cannot become a pass. Exclude credentials and real home data; redaction failures block the run.

The harness first characterizes current code and then detects deliberate corruption of its own decisive assertions. Independent expected state comes from protocol fixtures or a small hand-computed state trace, not the candidate's own output. Exact UUIDs/times may vary where the protocol allows them; identity and state transitions cannot.

Required scenarios include:

- Actual create/register/PUT/commit/finalize/events/result using local registered Hunter handlers and isolated temporary storage. Record NullHooks/stub stages separately from real worker paths.
- Same request replay, changed-payload conflict, expired upload URL, missing/corrupt bytes, duplicate/out-of-order commit, cancel/consent withdrawal, response-body abort, delayed/no result, wrong capture/run/result and process restart.
- Capture field/schema/provenance validation, packet/source/result association and independent expected-identity checking. The service currently lacks a processed-input digest; report that limitation instead of inventing one.
- Mounted/native client recovery where the host supports it: pending upload, stale progress, rejected result, retry/relaunch and cleanup. Swift/device checks unavailable on the host remain NOT_RUN, not a mocked pass.
- Inspector rendering after both loads, visible full result identity, keyboard/mobile/reduced-motion/axe checks and deterministic pack/reader refusals.
- Actual worker/orchestrator stages over permitted synthetic or explicitly supplied media. Inventory absent model/runtime prerequisites without downloading weights or invoking paid/cloud jobs. A schema-valid manual_review result is not geometry ground truth.

Use deterministic protocol/state permutations where they exercise distinct failures, with a fixed reviewed seed corpus. Hundreds of checks are useful only when each has a distinct assertion. Avoid multiplying screenshots or seeds merely to hit a count. Measure latency, memory, request count and retry work on the same host after establishing a baseline; no invented performance bar.

## Rounds and distinct directions

1. Foundation: repair #228, build the shared harness, run baseline and negative controls, and publish the executable local ingest/processing capability inventory. The accepted harness becomes the sole scoring interface.
2. Parallel direction A: a minimal direct client adapter with bounded retry and explicit state transitions, using the existing uploader/HTTP contracts. Own `tools/hunter-rounds/directions/direct/` only.
3. Parallel direction B: a resumable client adapter that retains an upload journal across restart and reconciles with server state. Own `tools/hunter-rounds/directions/resumable/` only. Compare concrete retained-work benefits and complexity, not two names for the same implementation.
4. Both run the same accepted harness and immutable scenario set against actual local handlers. Neither modifies canonical Hunter, shared harness, production app files, workflows or another direction's files. Both report limitations and failure traces, including whether real stages ran.
5. The lead reconciles using a council with independent correctness, client integration, product/UX and failure-recovery views. Reject any apparent improvement caused by skipped checks, weaker oracles, hidden retries or fake successful endpoints. Prefer the simplest correct mechanism; graft complementary ideas only when they improve a measured gap.
6. A single integration owner ports the selected mechanisms into reconciled BaseScanning client files after writer handoff, joins the existing iOS capture contract and runs actual local capture-to-result flow. It finishes meaningful failure/recovery and presentation polish. It does not parallel-write retired #216/#230/#232/#229 branches.
7. Repeat one targeted parallel round against remaining measured failures, then rerun integrated acceptance and polish checks. Update this plan in place with the new gap and selected direction, not a new plan.

## Selection, finish and stop rule

Safety and identity violations disqualify a candidate. Among eligible candidates, compare required checks passed, blocked real-stage coverage, recoverable work, user-visible truthful state and measured resource cost. Do not blend missing real execution into a synthetic success score. A slower candidate may win when it prevents data loss; state that tradeoff.

The bar is met when the achievable actual-local capture-to-result journey passes its required protocol/identity/recovery checks, no known data-loss or false-success issue remains, the supported client's user journey and inspector polish pass, and every unsupported model/device/provider gate is explicit. Processing quality requires its own independent known-answer evidence before any quality claim.

Stop when the bar is met or two consecutive comparable rounds fail to improve the agreed dimensions. Comparable means the same required scenarios, oracle definitions and runtime conditions, with no new skipped/blocked checks hidden. If external prerequisites block the bar, preserve working local integration and state the exact missing condition rather than spending rounds on the same unexecutable arm.

## Branch and publication boundary

The requested integration branch is `alpha` from main. Never touch the existing `beta`, which belongs to a collaborator, or merge into main. At 11:12Z the lead applied the PM's corrected read-only criterion: #228's current head has only label/security/draft-review checks, and deployments queries for both its branch and exact SHA return none. No production environment/build was observed. Alpha was created at main `c813182a808c957ccc981fa56490c6c29d3c1560`; this records the observed branch evidence, not blanket hosting-configuration assurance. Inspect new candidate-head checks/deployments before accepting integration; a production deployment stops the work and goes to the PM. Never invoke deployment commands. Task branches remain drafts. Integration requires substantive accept, caretaker pass and an authorized no-fast-forward merge; this lead does not merge on the user's behalf. Never merge into main or a production base, force-push, alter workflows/billing, use production data or expose secrets.

Prefer Obvious sandboxes for heavy compute. On Sam's Mac, every build/test/dev-server/simulation batch needs the long-run admission lease plus heavy.lock; GUI/simulator work needs the GUI lease/lock, and T3 app previews need the browser lease and tab cleanup. Read `~/.long-run/bin/SKILL-ADDITION.md`, reap only `fleet-apps` at each wake, and treat exit 75 as wait/shrink, not permission to proceed. One heavy job runs at a time.
