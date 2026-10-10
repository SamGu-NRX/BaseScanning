# reconstruction-repeatability

Which input transformations must preserve Hunter's reconstruction (a stable wrong wall is a
transformation that deterministically changes it), and which manipulations of run history does the
depth cache actually neutralize (a history-dependent wall is one that depends on what ran before,
not on declared inputs)?

Ten cases run the real `pipeline.run` on a synthetic four-frame room with the MoGe-2 model stubbed
by an analytic deterministic renderer -- no weights, no GPU, no network, no live model -- and
`rescale` replaced by an identity pass (deterministic in depths and poses, holds no history). Any
repeatability this packet reports is the pipeline's own; the model is not evaluated.

Everything runs offline and deterministically. The frozen snapshot is PR #220's head
(`c404045002439bf27ed7906fcba061a07097f046` on `obv/basescanning-004`); `manifest.json` records the
date, base commit, and SHA-256 of every recon source file the experiment reads.

## Running

```bash
uv run --project experiments/reconstruction-repeatability pytest experiments/reconstruction-repeatability/tests -q
uv run --project experiments/reconstruction-repeatability python experiments/reconstruction-repeatability/run.py
```

`run.py` re-runs every case from the manifest and writes
`results/reconstruction-repeatability.json` and `results/reconstruction-repeatability.md`. On a
second invocation it replays: re-executing every case in a scratch directory and diffing the fresh
record against the committed one, proving the committed conclusions survive a rerun. The replay
refuses to run if `manifest.json` has changed since the record was committed. The committed record
is the contract; the replay is the proof.

## Cases

| # | name | class | transformation | graded against |
|---|------|-------|----------------|----------------|
| 1 | baseline-repeat | identity | two fresh work directories, same bundle | byte-identical outputs |
| 2 | warm-cache | identity | second run, same work directory (cache hit) | byte-identical outputs, 0 model calls |
| 3 | frames-reordered | identity | scene.json keyframes reversed | wall moves <= 0.005 ft (float summation order) |
| 4 | frame-id-duplicate | refusal | a keyframe record repeated | capture refuses before any reconstruction |
| 5 | image-reencoded | identity | JPEGs decoded and re-encoded: same pixels, different bytes | byte-identical outputs |
| 6 | intrinsics-shift | sensitivity | fx/fy +4 px, cx/cy +2 px | measured, not graded (key must change) |
| 7 | unrelated-history | identity | another capture runs through the same work directory in between | byte-identical outputs |
| 8 | stale-cache-pose-edit | guarded | camera moves 0.05 m after a first run, then rerun | equals a fresh run of the edited bundle |
| 9 | interrupted-run | guarded | model dies after the first depth map, then rerun | equals the uninterrupted result |
| 10 | same-size-cache-edit | guarded | one cached map overwritten with a copy shifted 0.05 m, key.json untouched | equals the baseline within 0.0005 ft |

Identity cases demand byte-identical outputs because the stub, the pipeline, and the cache path are
all deterministic; frames-reordered allows 1.5 mm of wall movement for float non-associativity in
the fusion loop's ordered summation; guarded cases demand the guarded run equal its reference
within 0.15 mm. Case 10's expectation is deliberately guarded: whatever it measures, it measures.

## Layout

- `manifest.json` -- the frozen snapshot and the ten cases with their graded expectations
- `bundles.py` -- the synthetic four-frame room, its re-encoders, poisoners, and the analytic
  depth-model stub
- `compare.py` -- output collection and comparison (byte equality, largest numeric diff, wall metric)
- `run.py` -- the case runner and the replay against the committed record
- `tests/` -- pytest-level hand witnesses on the same code paths (`test_keying.py`: the cache key
  covers image bytes, poses, and intrinsics but not the phone's marks; stale maps are deleted on
  key mismatch; a same-size edit is consumed silently. `test_identities.py`: the pipeline-level
  identities and the history-dependent wall)
- `results/` -- the committed record, written by `run.py` once the cases run

No recon repair belongs in this packet: a failing case is recorded as a finding, not fixed.
