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
- **Scenarios.** Five observation capabilities (frozen rigs, per-rig focal
  lengths chosen so each intended blind spot is real, with margin — tests
  assert the visibility patterns): `one-view`, `two-view`, `two-view-pan-only`,
  `two-view-top-blind`, `two-view-end-blind` (true right end at 16 ft, aimed
  left of it).
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
- **A5 — finite grid.** "Supported" means agreed across the 2625 frozen grid
  worlds only. A count of 1 is a statement about this grid, not a proof of
  global identifiability; the two ~2 ft grid steps are well above the 3 px
  quantization at these ranges.
- **A6 — one wall, no clutter.** The scene contains exactly one wall and the
  landmark set; detection, matching, and occlusion are out of scope.

## Results (results/table.md, results/run.json)

| fact | one-view | two-view | pan-only | top-blind | end-blind |
|---|---|---|---|---|---|
| orientation | ✅ | ✅ | ✅ | ✅ | ✅ |
| distance | ✅ | ✅ | ✅ | ✅ | ✅ |
| extent s0 | ✅ | ✅ | ✅ | ✅ | ✅ |
| extent s1 | ✅ | ✅ | ✅ | ✅ | UNKNOWN (3) |
| height | ✅ | ✅ | ✅ | UNKNOWN (3) | ✅ |

- **One view is enough.** With metric poses (A1) and ground landmarks (A3),
  each ground-point pixel ray intersects the known ground plane at exactly one
  3D point: orientation, distance, and both ends are pinned by a single frame
  that contains them; one sight of any top corner pins height on the
  ground-pinned wall plane. The binding constraint is **frame coverage**, not
  multi-view geometry.
- **The two real failure modes are blind spots.** Height stays UNKNOWN only
  when no top corner is ever in frame (top-blind); the right end stays UNKNOWN
  only when it is never in frame (end-blind, compatible with s1 ∈ {12, 14, 16}).
- **Every action that moves a frame settles the blind spot — panning included.**
  In top-blind, `tilt_pair`, `stereo_step`, `end_approach`, and even `pan_pair`
  each settle height, because each brings a top corner into some frame. The
  control (`reobserve`) settles nothing anywhere, and `pan_pair` does not
  settle the end-blind right end (its panned frame sees the end from only one
  center and non-visibility still bounds s1 to the same set). The practical
  rule this study hands to capture-coverage: the export supports a wall fact
  iff some exported keyframe's frame contains the corresponding landmark — ends
  and interior marks need ground coverage, the top corners need top coverage.

## Reproduce

    cd experiments/nonlidar-observability
    uv run --project . python run.py            # verify manifest, run, write results/
    uv run --project . python run.py --replay results
    uv run --project . pytest tests -q

The manifest is frozen: `run.py` errors on drift instead of silently updating.
Replay recomputes every scenario from the manifest and compares observation
hashes — a mismatch exits nonzero and is reported, never papered over.
