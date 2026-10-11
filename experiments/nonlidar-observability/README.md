# nonlidar-observability

Without LiDAR, which wall facts do the exported observations support at all?

The recon worker's scene export binds every keyframe to a metric pose, per-frame
intrinsics, and pixel dimensions (`scene.schema.json`: `keyframes[].pose` is a
column-major cam-to-world 16 with translation in feet, `intrinsics` is
`[fx, fy, cx, cy]`, `w`/`h` the sensor image size). This study asks what those
observations can determine about a single wall — orientation, distance, extent
(both ends), height — using a finite camera model over a finite world grid, with
no question of accuracy anywhere in it: a fact is **supported** when every world
on the grid consistent with the observations agrees on its value, and **UNKNOWN**
when the observations fit two or more distinct values.

## Method

- **Model.** Pinhole camera per keyframe, bound to the raw export fields
  (`nonlidar_observability/projection.py`; `Camera.from_keyframe` reads pose,
  intrinsics, `w`, `h` exactly as the schema defines them, and the reading is
  cross-checked against `recon/recon/capture.py`'s own `column_major` in tests).
- **Worlds.** A wall = `(orientation θ, distance d, ends s0/s1, height h)`.
  Landmarks: the two ground ends, two ground marks at fixed offsets past the
  left end, and the two top corners. The outward side and baseline direction
  follow the scene schema's convention exactly (`worlds.py`).
- **Equality.** Two worlds are indistinguishable iff their canonical
  observation maps — landmark → keyframe → quantized pixel — hash identically.
- **Grid.** 5·5·5·7·3 = 2625 worlds (frozen in `manifest.json`). Nominal world:
  θ=0, d=20 ft, s∈[-10, 10] ft, h=9 ft.
- **Scenarios.** Six observation capabilities (frozen rigs, per-rig focal
  lengths chosen so each intended blind spot is real, with margin — tests
  assert the visibility patterns): `one-view`, `two-view`, `two-view-pan-only`,
  `two-view-top-blind`, `one-view-tops-only` (one frame aimed above the wall:
  only the two top corners ever register), and `two-view-end-blind` (true right
  end at 16 ft, aimed left of it).
- **Family probe.** A grid count of 1 is not identification on its own: a
  continuous family of worlds can pass through the true one while the coarse
  grid holds no second point of it. Whenever the grid leaves one compatible
  world, the engine walks that family (`identifiability.py::probe_family`): a
  damped Gauss-Newton refit over the five continuous facts from a perturbed
  start, then continuation along the chord to travel it. A member counts only
  if its observation hash equals the true one EXACTLY; facts the farthest
  member moved are downgraded to UNKNOWN with the pair recorded. A supported
  fact is one the grid agrees on AND the walk could not move.
- **Actions.** `stereo_step` (two new positions framing the wall),
  `tilt_pair` (pitch up, tight frame on the top), `end_approach` (walk right,
  frame the end low), `pan_pair` (yaw every existing keyframe in place), and
  `reobserve` (the zero-information control: duplicate every base keyframe from
  the same pose).

## Assumptions

- **A1 — metric, trusted poses.** Keyframe poses are metric (feet) and taken at
  face value from the export. Every result below is conditional on this; with
  up-to-scale poses the scale-free world is not identified and this study does
  not apply.
- **A2 — square pixels, fx=fy** in all rigs; the schema carries fx and fy
  separately and the model reads both.
- **A3 — known ground.** Landmarks live on the plane y=0 and the wall rises
  from it; the scene frame's gravity axis is exact.
- **A4 — closed-world observation.** A landmark inside a frustum always
  registers at its projected pixel (3-decimal quantization); outside, it never
  does. Nothing occludes, nothing is missed. Non-visibility of a landmark is
  therefore itself evidence (this is why the end-blind rig's non-visible right
  end still bounds s1 from below).
- **A5 — finite grid, family-checked.** "Supported" means agreed across the
  2625 frozen grid worlds, and — when the grid leaves a single compatible
  world — unchanged by the continuous family walk (see Method). Residual grid
  relativity: a fact the probe could not move on the sampled family is
  reported supported, which is evidence, not a proof of global
  identifiability.
- **A6 — one wall, no clutter.** The scene contains exactly one wall and the
  landmark set; detection, matching, and occlusion are out of scope.

## Results (results/table.md, results/run.json)

| fact | one-view | two-view | pan-only | top-blind | tops-only | end-blind |
|---|---|---|---|---|---|---|
| orientation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| distance | ✅ | ✅ | ✅ | ✅ | UNKNOWN (family) | ✅ |
| extent s0 | ✅ | ✅ | ✅ | ✅ | UNKNOWN (family) | ✅ |
| extent s1 | ✅ | ✅ | ✅ | ✅ | UNKNOWN (family) | UNKNOWN (3) |
| height | ✅ | ✅ | ✅ | UNKNOWN (3) | UNKNOWN (family) | ✅ |

- **One view is enough — when the ground is in frame.** With metric poses (A1)
  and ground landmarks (A3), each ground-point pixel ray intersects the known
  ground plane at exactly one 3D point: orientation, distance, and both ends
  are pinned by a single frame that contains them; one sight of any top corner
  pins height on the ground-pinned wall plane. The binding constraint is
  **frame coverage**, not multi-view geometry.
- **The first real failure mode is a blind spot.** Height stays UNKNOWN only
  when no top corner is ever in frame (top-blind); the right end stays UNKNOWN
  only when it is never in frame (end-blind, compatible with s1 ∈ {12, 14, 16}).
- **The second failure mode is a continuous family.** Aim a single frame above
  the wall (tops-only): only the two top corners register, and the grid —
  2625 worlds — agrees on exactly one world. That is a grid artifact. The
  family walk refits a materially different wall (face 23.9 ft out, ends
  ±12.8 ft, top 10.1 ft) whose observation hash is byte-identical: both top
  corners sit on fixed rays from the camera center, and the equal-height
  constraint is scale-homogeneous, so the wall scales about the camera center
  with orientation exactly fixed. Orientation alone stays supported from this
  rig — the one fact the scaling cannot turn.
- **Every action that adds a camera center settles the family; panning cannot.**
  For the four family facts, `tilt_pair`, `stereo_step`, and `end_approach`
  each pin every fact: a second center makes the corner rays cross in 3D.
  `pan_pair` yaws in place — same center, same rays, family intact — and the
  `reobserve` control settles nothing anywhere. In top-blind and end-blind the
  movers that bring the missing landmark into frame settle it (see
  `results/table.md` for the per-cell detail); `pan_pair` does not settle the
  end-blind right end. The practical rule this study hands to capture
  coverage: the export supports a wall fact iff some exported keyframe's frame
  contains the corresponding landmark — ends and interior marks need ground
  coverage, the top corners need top coverage — and a single center seeing
  only corners determines orientation and nothing else.

## Reproduce

    cd experiments/nonlidar-observability
    uv run --project . python run.py            # verify manifest, run, write results/
    uv run --project . python run.py --replay results
    uv run --project . pytest tests -q

The manifest is frozen: `run.py` errors on drift instead of silently updating.
Replay recomputes every scenario from the manifest and compares observation
hashes — a mismatch exits nonzero and is reported, never papered over.
