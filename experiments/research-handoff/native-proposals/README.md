# Native patch proposals

Both patches target exact commit [`a39d0a503412d688dd151f628cdeebc00f8221b6`](https://github.com/SamGu-NRX/house-scanning/commit/a39d0a503412d688dd151f628cdeebc00f8221b6). They are review artifacts, not changes applied to this branch's app. Patch labels are repository-relative; hashes are recorded in [patch-identities.json](patch-identities.json).

| Patch | Target | Status |
| --- | --- | --- |
| [scan-engine-task-guards.patch](scan-engine-task-guards.patch) | `ios/HouseScan/Runtime/ScanEngine.swift` | Narrow native host cases executed; full iOS integration untested |
| [ground-replay-helper-unrun.patch](ground-replay-helper-unrun.patch) | `ios/HouseScanKit/Sources/HouseScanKit/Coverage/CoverageMap.swift` | Prepared only; **UNRUN and unintegrated** |

The ScanEngine patch refuses obsolete queued keyframe/close-up work before storage starts, and rechecks generation/phase after the thumbnail await before updating current UI/counts. It moves the existing per-generation pending-save decrement into a Task-exit `defer`, so success, failure and early returns drain the captured generation's entry. It preserves this baseline's store epoch and dictionary-based pending counts.

One Swift 6 host build and one run exercised nine paired cases: **18 observations matched**. Four baseline defect cases reproduced obsolete storage calls or stale post-thumbnail updates; all nine patched cases met their assertions, including five existing-behavior/drain controls. The executable used eight source-bound current/patched method bodies and four explicit completion-signaling overlays, with controlled storage, UI and framework dependencies. This is actual-method host evidence, not a full app/store/JPEG/OCR test. It does not fix producer-frame world provenance, already-running fixed-name still writes, durable capture inventory, export/response correlation or same-generation retake identity. No old scalar-counter patch should be transplanted over the current dictionary design.

The ground helper proposes synchronous recomputation from a caller-qualified camera ledger in original acceptance order, including observations whose original delta was zero. It retains latent skip flags and marked-end coordinates, discards photographic row state, replays existing geometry methods, and advances revision from the prior map. Unchanged samples and meter-y-only updates preserve current evidence/revision. Changed horizontal geometry, nonfinite heights or a nonzero residual cell shift are refused without mutation.

**The ground helper is not a complete geometry fix.** Ordinary anchor refinement can leave a residual shift and prevent its use. Carrying that residual into freshly projected evidence, or dropping it while retaining rounded skip cells, would mix coordinate histories. Caller ledger admission, world/session provenance, refusal handling, coherent UI publication and export snapshot integration remain unresolved. This helper does not implement them. It also loses evidence across a marked end. `observe` skips cells past a marked end, so replaying cameras recorded before the end was marked drops what they saw beyond it, and `clearEnd` replays nothing to bring it back. A fix needs each observation's admission-time ends in the caller's ledger, or a replay when an end is cleared; the patch does neither ([review](https://github.com/SamGu-NRX/BaseScanning/pull/18#discussion_r4111600380)). Alpha's `CoverageMap.replayObservedCameras` also loses regular wall and ground sightings past a marked end. When clearing a limit end, Alpha's `clearEnd` calls `replayPastLimits()` to rebuild past-limit ground state, but it does not replay those regular sightings to restore them. Nine source-prepared controls cover fresh-map equivalence, useful zero-delta observations, order dependence, latent skips/bounds and revision collisions; none was compiled or run. It makes no physical-accuracy, occlusion or phone-performance claim.

To check applicability later, use a separate checkout already at the exact baseline and inspect the output of `git rev-parse HEAD` first. These commands only check whether the patches apply; they do not apply them:

```sh
git rev-parse HEAD
# Expected: a39d0a503412d688dd151f628cdeebc00f8221b6
git apply --check /path/to/native-proposals/scan-engine-task-guards.patch
git apply --check /path/to/native-proposals/ground-replay-helper-unrun.patch
```

Applicability against another commit requires a fresh source review. No apply check, application, app build or test rerun was performed while packaging this handoff. The export check establishes byte identity and relative labels only.
