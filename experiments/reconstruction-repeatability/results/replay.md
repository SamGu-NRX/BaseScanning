# Reconstruction repeatability

Stable wrong walls versus history-dependent walls in `recon`, evaluated at `c40404500243` (obv/basescanning-004).

- **The depth model was not exercised.** MoGe-2 is stubbed (analytic maps, deterministic in the frame id); no weights, no GPU, no network. `recon.depth.rescale` is an identity pass. The real `moge_cache` key path, the cache files and the full pipeline are the actual code.
- Evaluates Hunter's recon at PR #220's snapshot; nothing here evaluates the server branch, where recon is absent.

| Case | Class | Check | Verdict | Observed |
| --- | --- | --- | --- | --- |
| baseline-repeat | identity | outputs_identical | pass | outputs byte-identical |
| warm-cache | identity | outputs_identical | pass | outputs byte-identical |
| frames-reordered | identity | wall_within | pass | max diff 0.0 ft |
| frame-id-duplicate | refusal | refusal | refused | refused as DuplicateFrameId |
| image-reencoded | identity | json_identical | pass | max diff 0.0 ft |
| intrinsics-shift | sensitivity | measured | measured | max diff inf ft (measured, not graded) |
| unrelated-history | identity | outputs_identical | pass | outputs byte-identical |
| stale-cache-pose-edit | guarded | outputs_identical | pass | outputs byte-identical |
| interrupted-run | guarded | outputs_identical | pass | outputs byte-identical |
| same-size-cache-edit | guarded | wall_within | fail | max diff 0.5 ft; stable when repeated: True |

## Replay

Replayed against the committed records (036962b05ca5): 10 of 10 cases agree exactly.

| Case | Agreement |
| --- | --- |
| baseline-repeat | agree |
| warm-cache | agree |
| frames-reordered | agree |
| frame-id-duplicate | agree |
| image-reencoded | agree |
| intrinsics-shift | agree |
| unrelated-history | agree |
| stale-cache-pose-edit | agree |
| interrupted-run | agree |
| same-size-cache-edit | agree |
