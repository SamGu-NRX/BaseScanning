# BaseScanning contract evaluation and integration plan

## End goal and current boundary

Evaluate how much of Hunter's capture-processing contract works, prepare the BaseScanning client to use it, and connect the real service when Hunter grants access. Preserve recoverable capture work and truthful result/error states. Neither a fake-server pass nor schema-valid output establishes reconstruction quality or electrical safety.

Hunter's real backend is the private `huntertcarver/house-scanning-server` repository. The fleet token reaches Sam's repositories only. Agents must not clone Hunter's code, copy it from local worktrees, exports or another account, or publish private source, schemas, specs, tests or held patches in this public repository. Work only from contracts already published in Sam's repo and explicitly releasable test descriptions/receipts. Missing information is a blocked contract question, not permission to reconstruct private code.

The public `server/` folder is a separate placement solver written by Sam's agents, not Hunter's 11-stage pipeline. Its synthetic HTTP/input-digest checks do not count as Hunter integration. Historical evidence includes 42 contract/storage-ID tests passing with NullHooks; agents do not have that private suite and must not copy it. Their own runs must report the original suite NOT_RUN until access is granted, preserving historical counts separately.

The only recorded real Hunter run took 662.8 seconds, ended needs_more_photos and produced no preview. A separate private Oct 4 CPU attempt used held patches, stubbed objects, no VLM and sparse fallback; it is not fleet, GPU, phone or accuracy proof. Unanswered stage issues include parent/child packet hydration, asset/rules publication, finalized still storage paths, reused region evidence, forced storage selection and internal failure codes shown to homeowners. Evaluate their client-visible consequences using published facts and labeled fakes; do not deliver private fixes.

## Shared harness and outputs

The foundation owner builds `tools/hunter-local-ingest/` without touching #228's branch or `tools/capture-evidence-pack/`. Original writer release remains unproved; #228 is held until an explicit stopped/idle receipt and ownership handoff. The independent harness may read its current public artifacts and report known defects, not repair them.

Use the public packet 0.4 material from BaseScanning PR #22/Packet README, public HouseScanKit types/uploader tests, and other published client contracts. Record exact public path/ref and the field or behavior each check derives from. If the result or amendment contract exists only in Hunter's private spec, mark that case BLOCKED and ask the lead; never invent a new enum, schema field or child-packet interpretation.

One runner accepts candidate ref/adapter, immutable fixtures, seed and a fresh output directory. It uses a faithful independently written local contract fake, prominently labeled FAKE_HUNTER. The fake must expose request/response/state traces and explicit fault injection; it must not claim to be Hunter's registered API or run his worker. Mock semantics derive from public contracts and hand-computed traces, not copied private handlers or candidate predictions. A stricter fake without a contract basis is no more trustworthy than a lenient one.

Each run emits `run-manifest.json`, `checks.jsonl`, `summary.json` and synthetic-only evidence. Record source/fixture digests, runtime/hardware, seed, command, transport identity and actual stage mode. Every check has an ID, expected/actual observation, public oracle basis, evidence reference and PASS, FAIL, BLOCKED or NOT_RUN. Summaries separate public-contract fake conformance, real public placement checks, client recovery, unrun private-suite/service/GPU checks and UI. Replay does not overwrite prior artifacts.

Required checks cover:

- Published create/register/upload/commit/finalize/events/result behavior through a real local HTTP fake, with repeated/changed idempotency, expiry, missing/corrupt bytes, interrupted bodies, delayed/no/wrong-run responses and restart where the public contract decides the answer.
- Client packet/schema/provenance and local capture/result association. No service processed-input digest is available; matching local IDs is not processed-byte proof.
- Direct and resumable client recovery after rejection, ambiguous acknowledgement, restart and corrupt/denied local journal storage. Ambiguous outcomes remain visibly recoverable, not blindly resent.
- Public Swift uploader conformance to the HTTP fake where supported. DEBUG fixture behavior remains explicit. Device/ARKit worlds, clocks, auth/privacy and live send gates remain NOT_RUN or BLOCKED, not synthetic passes.
- Homeowner states before camera start when sending is unavailable, needs-more-photos without a preview, proposed placement rather than fits, retained legacy selection without silent fallback, and readable failures without internal codes.
- GPU and model stages represented by named stubs only. Empty object lists do not prove clearance. No model downloads, external processing, cloud spend, real homes, private rules or collaborator writes.
- Independent planted mutations showing decisive assertions fail. Seed expansion must test distinct failures, not pad a check count. Baseline latency/memory/request/retry measurements concern the local fake/client only and cannot become Hunter service estimates.

## Rounds and ownership

1. Foundation: inventory published contracts and missing private dependencies, build the frozen fake/runner and independent negative controls, execute client conformance and recovery baseline, and document exact capability/permission gaps. Acceptance concerns this usable harness, not real Hunter E2E.
2. Direction A owns `tools/hunter-rounds/directions/direct/`, a minimal direct client using existing published uploader semantics and bounded retries.
3. Direction B owns `tools/hunter-rounds/directions/resumable/`, a durable journal that reconciles only facts the published contract exposes. Extra machinery must show a measured retained-work benefit.
4. Both run the same accepted fake, scenario set, seed corpus and oracles. Shared harness, production app, collaborator/private source and other directions remain read-only. Report fake versus blocked real execution separately.
5. The lead council evaluates correctness, client integration, failure recovery and homeowner UX. Disqualify weaker oracles, skipped checks, hidden retries and fabricated service proof. Prefer the simplest correct mechanism, grafting ideas only to close a specific measured gap.
6. One integration owner ports the selected adapter into reconciled public client code after writer handoff and finishes native/local conformance and presentation polish. Keep separate typed Hunter results; do not coerce them into legacy placement fields. The live service switch remains disabled until Hunter access plus the agreed auth/privacy/clock/deployment contracts exist. Existing #216/#230/#232/#229 branches are not parallel writers.
7. Run a targeted second round against remaining executable gaps. The actual Hunter integration/reconstruction stage is an access-gated later step, not another fake success to fill the gap. Update this plan in place.

## Finish and stop rule

Data loss, privacy leakage, false saved/ready/clear status or incorrect identity disqualifies a candidate. Compare executable conformance checks, retained work, honest UX and local resource cost; keep blocked real evidence separate.

The current bar is a usable frozen public-contract harness, robust client adapters and a polished truthful local/native flow with no known data-loss or false-success issue, plus an explicit list of unanswered Hunter contracts. It does not declare the real backend connected. The real-service bar requires granted access, authorized execution, the original suite/current service runs and independent result/geometry evidence.

Stop when the current bar is met or two comparable rounds do not improve agreed executable metrics. Keep scenarios/oracles/runtime fixed and report unchanged blocked real arms. Do not repeatedly spend on unavailable GPU/private access or bypass the permission gate. The longer-term service connection stays open on Sam's list.

## Branch and resource rules

`alpha` starts at main `c813182a808c957ccc981fa56490c6c29d3c1560`. Recent #228 branch/head deployments were empty and no production check appeared under the PM's branch-creation criterion. Recheck candidate checks/deployments before integration; production evidence stops the work. Existing `beta` is Aiden's, and Hunter/Aiden branches, private patches, TestFlight and main remain untouched.

Task branches stay drafts. Integration needs substantive accept, caretaker pass and an authorized no-fast-forward merge; this lead leaves merges to the user. Never deploy, force-push, alter workflows/billing, use production data/credentials or copy collaborator material. Prefer Obvious sandboxes. On Sam's Mac use the long-run admission lease and heavy/gui/browser lock as appropriate; reap only `fleet-apps`, close previews, and wait or shrink on exit 75. One heavy job runs at a time.
