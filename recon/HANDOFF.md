# Handoff: photos into a 3D model, and what we measured on the way

For the server team building photos, then a 3D model, then evaluation against the placement criteria. Every dataset number below came from real public data; the pose-prior settings add simulated pose error, and say so where they appear. Each points to the file that produced it:
- `evals/…` and `results/…` are under `experiments/evals/` on branch `t3/evals` (PR #12, SamGu-NRX/house-scanning-master);
- `recon/…` is this folder on `t3/recon`.

## 1. What was measured

**AR tracking drift.** Position error after walking, compared with the server's allowance of 0.16 ft per ft (`evals/drift.py`, `evals/modern_arkit.py`; METHODS.md sections 1 and 6).

| Phone, data | p90 at 3 / 10 / 20 / 30 ft | Allowance |
| --- | --- | --- |
| iPhone 6s (2018), ADVIO | 18.6 / 55.7 / 94.6 / 132.6 in | 5.8 / 19.2 / 38.4 / 57.6 in |
| iPhone 14 Pro Max, MARViN, trusted walks | – / 8.6 / 13.4 / 18.5 in | same |

- **ADVIO** is 2 to 3 times over the allowance, though its reference's own heading and position noise inflate that. ARKit read distances 5% to 17% short against a GPS-referenced truth.
- **ADVIO's reference** is mis-scaled about 20% in walks 20 and 21: its fix points come from a map. That explains a 345 m against 474 m path-length gap.
- **MARViN:** ARKit's scale is within 2% of the dataset's reference on 30 of 35 walks, and within a site the ARKit/reference ratio spreads 0.3% to 0.8% between walks (1 SD). That is disagreement between ARKit and the reference, not a bound on ARKit's error: the reference's local scale error could produce all of it. The reference's metric scale cannot be checked independently to 2%, and the phone has LiDAR.

**Learned depth against a laser scan.** ETH3D electro, walls within 6 m, 1–3 m spans; `evals/recon.py`, `evals/pose_priors.py`, METHODS.md sections 2 and 3.
- **A model's own scale is 4% to 12% off.** MoGe-2 reads long and Depth Anything 3 metric reads short: median 6 to 8 in, p90 about 20 in.
- **One photo plus one taped distance:** median 1.6 in. The p90 is 9.3 in on surfaces, 13.6 in on vertical surfaces and 25.3 in at depth edges.
- **Averaging photos placed with true poses makes it worse.** On one set of seed groups, with one tape, p90 rises from 5.2 in with 1 photo to 11.9 in with 8, because each photo carries its own scale error.
- **Best recipe: MoGe-2 rescaled per photo by SIFT features triangulated with the AR poses** (method b), then fused over 8 photos. Wall p90, with 95% intervals over seed groups and 5 pose-noise draws:
  - exact poses: 2.8 in [2.0, 4.6];
  - an assumed modern phone: 5.0 in [3.7, 6.9]. This is a synthetic setting, not a measured phone: ETH3D's true poses degraded by a simulated 2% scale error plus 1 cm and 0.1° of noise per photo (`evals/ar_poses.py`);
  - the 2018 phone's measured pose error: 10.8 in [8.6, 13.1].

  These assume perfect feature matching; with real matching, walls reach 6.3 in even with exact poses. Edges stay at 8 in or worse everywhere.
- **MapAnything is not a stable baseline.** Given poses, it fixed its scale on electro but not facade, and left 10% to 20% of pairs unpredicted. Its metric scale depends on input resolution: +17% on electro and −14% on facade at 392 px, and 10% to 33% short at 518 px in fp32.
- **Which photos to keep: the close ones.** Keeping only photos with the wall within 6 m cuts a single photo's p90 from 38.7 to 14.8 in on 3 to 10 m spans (METHODS.md section 4).
- **Evaluation floor:** depth rendered from the scan itself is off 0.2 to 0.8 in median. The visibility tolerance moves medians about 0.5 in and p90s up to 20% (`results/eth3d_visibility_sensitivity.md`).

**Coverage and occlusion** (METHODS.md sections 7 and 7b; `evals/coverage.py`, `evals/coverage_options.py`).
- **The app's coverage map claims unseen wall.** It claims 1.1 ft of a 19.3 ft wall that no photo saw, behind a scaffold's footings. The pre-registered bar was 0.5 ft. The cause is `CoverageMap.swift` lines 241–248 at beede15 (t3/ios-mvf): it checks range, angle and image bounds, not occlusion.
- **Rules about how the phone moved don't fix it.** Views up to 2 m apart or 45° apart change nothing. The patch goes only at 5 m or 60° apart, which gives up 6.0 or 8.5 ft of wall that was seen.
- **A depth test does fix it.** Against true depth it claims nothing unseen but misses 3.3 ft: pilasters 0.36 m proud read as occluders. LiDAR's roughly 5 m reach costs 8.9 ft on these photos.

**This worker, today** (`recon/results/eth3d_electro.md`, `make accept`). It fuses depth into a truncated signed distance field (TSDF) and tests coverage against it. On ETH3D electro:
- **Laser-as-LiDAR:** wall pair p90 1.8 in; 0.07 ft false-observed of 22.5 ft claimed (bar 0.5 ft: passes).
- **MoGe-2 photos-only:** wall pair p90 1.5 in; 0.00 ft false-observed of 19.9 ft claimed (passes).
- A wall sample counts as seen only where the depth puts a surface at it, and the acceptance truth credits no pixel without a laser return (`recon/results/eth3d_electro.md`).
- **Each laser point is scored from the nearest photo that sees it**, by the laser's own visibility. Before, the nearest photo scored it regardless of whether it was in frame or behind the scaffold. On the laser path, whose wall did not move, that fix alone took the pair p90 from 2.1 to 1.8 in.
- **Both paths fit the same wall.** The laser path fits a 31.9 ft stretch of it, and MoGe-2 a 20.5 ft stretch inside that one (`wall_ends_xz_m` in `recon/results/eth3d_electro.json`). Which MoGe-2 stretch wins is fragile. RANSAC splits MoGe-2's noisier surface into collinear pieces, and fitting the ground as a plane (tilted 0.4°) instead of taking its median height moved the pick from a 15.7 ft piece to the 20.4 ft one. The MoGe-2 row's drop from 2.8 to 1.5 in therefore mixes the scoring fix with a different stretch.
- **End to end:** at c6a8be3 the ADVIO replay and the app's Simulator bundle both reached a server result, `manual_review`. Neither saw much wall head-on, because the ADVIO camera looks along its path. They were not rerun after this round's geometry changes.

## 2. What to reuse

On `t3/evals`, from `experiments/evals`; data goes to `~/house-scanning-data/evals/`:

| Command | Does |
| --- | --- |
| `make recon-data` | ETH3D download (about 2.4 GB), checked by sha256, prepared |
| `make recon-models` | MoGe-2 and Depth Anything 3 on ETH3D (`models/run.py`) |
| `make pose-priors` | AR-like pose noise, MapAnything with poses, method (b) (`evals/triangulate.py`) |
| `make drift`, `make replay` | ADVIO drift; the ADVIO replay session in Measure Lab format |
| `make modern-arkit` | MARViN scale and position error |
| `make coverage`, `make coverage-options` | Coverage-visibility harness against the laser scan; the app's code at a pinned commit |
| `make field SESSION=… TRUTH=… MAP=…` | A field session against a tape survey, scored by PR #4's harness; templates and sheet in `field/` |
| `make field-dryrun` | That command end to end on a synthetic session |

In `recon/` on this branch:

| Command | Does |
| --- | --- |
| `make run BUNDLE=… OUT=…` | The worker on an app bundle or Measure Lab session: `model.glb`, `geometry.json`, `coverage.json`, `scene.json`, server result |
| `make accept` | The worker against ETH3D electro's laser scan |
| `make test` | Unit tests and lint, as CI runs them |

**Memory, on a shared 24 GB Mac with a 4 GB per-process limit:**
- MoGe-2 peaked at 4.07 GB on 24 MP photos. The worker feeds it at most 640 px, which peaks at 2.95 GB (`recon/models/moge_depth.py`).
- MapAnything fits only 2 and 4 views at 392 px with bf16 weights (4.05 GB).
- The worker itself peaks at 2.9 GB. `make accept` with MoGe-2's depth already cached peaked at 1.2 GB.

## 3. Pitfalls we hit

- **Sensor images are sideways.** Keyframe JPEGs are unrotated landscape sensor images, and the intrinsics match them. Turn the image upright for a depth model and turn the intrinsics with it (`recon/depth.py` `rotated_intrinsics`), then turn the depth back.
- **Pixel conventions differ by half a pixel.**
  - ARKit, Measure Lab and COLMAP put pixel (0, 0) at the image's top-left corner; OpenCV puts it at the first pixel's centre.
  - MoGe-2 assumes a centred principal point: the true one moved medians at most 0.1 in, but pass it anyway.
- **ARKit camera axes are +y up and looking along −z.** OpenCV's are +y down and looking along +z. Converting is `T @ diag(1, −1, −1, 1)`.
- **Scene poses are rounded.** `scene.json` rounds poses to 4 decimals, so their rotations are not orthonormal; snap them (`recon/capture.py`).
- **"Up" from the cameras was 10° off.** The mean camera up axis was 10° off on ETH3D facade; level on the ground plane under the cameras instead.
- **The ADVIO reference scale is wrong in places.** Use the GPS-referenced ARKit scales (`results/advio_drift.json`).
- **MARViN poses need converting.** Its quaternions are (w, x, y, z) in Unity's left-handed axes; handedness was chosen per walk by the steadiest heading offset.
- **One noise draw is not a result.** A single draw showed walls at 3.9 in; five draws put it at 5.0 [3.7, 6.9]. Bootstrap over seed groups and draws.
- **Caches go stale.** MoGe-2 depth is cached per bundle under `~/house-scanning-data/recon/work/`; delete it when the model or resolution changes.
- **Marching cubes finds false surfaces.** A TSDF (truncated signed distance field) leaves unobserved voxels at +1, which puts a false surface one truncation distance behind every real one. Drop vertices on half-observed edges (`recon/fusion.py`, tested).
- **Scikit-image flips normals.** Its marching-cubes normals point into the surface whichever `gradient_direction` is set; flip them.
- **Tapped planes aren't flat walls.** Pilasters and bins read as occluders against a tapped plane. Sample the wall's real face where it is flat and full height (`face_offsets`).
- **Measure Lab numbers things itself.** It assigns P1, M1 and R1 in order and lets no one name them. PR #4's example map uses lowercase ids and won't match a real session.

## 4. Recommendations for the 3D path

- **When the phone has LiDAR, use its depth and the ARKit mesh.**
  - Keep confidence of medium or better.
  - Its roughly 5 m reach means capture must stand within about 4 m of the wall.
  - The worker reads per-keyframe `depth: {file, confidenceFile, w, h}`: Float32 meters and UInt8 confidence, row-major, in the keyframe's unrotated orientation, as Measure Lab writes them. S3 is adding the app's fields; match them at `recon/capture.py`.
  - A keyframe saved without depth gets no depth rather than MoGe-2's: it adds nothing to the model, and what only it saw stays unobserved. Mixing in MoGe-2 would make the whole wall carry MoGe-2's error bar (`recon/depth.py`, `depth_maps`).
- **Without LiDAR:** MoGe-2 at 640 px or less, rescaled per photo by triangulation with the AR poses, fused into a TSDF.
  - Expect wall p90 of about 5 in over 1–3 m spans at a simulated 2% pose scale error (not a measured phone): more at the 2018 phone's measured error, about 2 to 3 in with exact poses.
  - Never trust a model's own scale.
- **Coverage must be a depth test against the reconstruction.** "Unseen is not clear": the server passes a check only over area reported observed.
- **What to test first:**
  1. A real field session with a tape survey (`make field`), for the phone's own pose error.
  2. A LiDAR bundle from the app once S3 exports depth.
  3. Why MoGe-2's wall splits into collinear stretches, and which one the worker should pick (a 0.4° ground change moved it).
  4. The ground band. It is still untested, because ETH3D's photographers stood too far away for a 65° view of the ground.
  5. Stepped walls, which the 6 cm line fit handles poorly.

## 5. Interfaces

- **Scan bundle (S3's `shareableScan`, t3/ios-mvf):** a zip of `scene.json` and the keyframe JPEGs it names.
  - The scene frame is ARKit's gravity world in feet, with the ground at the wall at y = 0.
  - Keyframe `pose` is column-major camera-to-world with the translation in feet; `intrinsics` are in pixels of the unrotated image.
  - Contract: `ios/HouseScan/Contract/ScanContract.swift` and `ios/HouseScan/Runtime/ScanEngine+Export.swift`.
  - The Simulator bundle's wall comes from the replay autopilot: it lies 4 ft from any reconstructed surface.
- **Scene contract:** `server/schemas/scene.schema.json` and "What settles each check" in `server/README.md` on t3/server.
  - `coverage.observed` entries are `wall`, `ground` (with `out_ft`), `facing` and `overhead`.
  - `facing` and `overheads` carry measured gaps and clearances. A facing gap is measured from the reconstructed wall's front, so in front of a pilaster it starts at the pilaster's face.
  - The reconstructed wall carries `source`: `mesh` when LiDAR built it, `plane` from photos alone. It carries no `plus_minus_ft`, so the server applies that source's default (0.5 or 0.75 ft) plus 0.16 ft per foot walked from the meter. An explicit bound would switch that drift off. `facing` and `overheads` entries carry the depth source's error, 0.5 ft for LiDAR and 0.56 ft for photos.
- **Known limit for the server team: ground is sampled only 10 ft out** (`GROUND_MAX_M`, `recon/coverage.py`). The server asks for ground out to D + r + e, which for the public pool rule is 1.83 + 10 + e, at least 12.3 ft. A pool check therefore stays UNSURE even when the photos show ground past 10 ft. That errs toward UNSURE, never toward a pass. The reach should come from the server's request, not from a constant in the worker; the server team owns that request.
- **Servers:**
  - public demo https://house-scanning-server.vercel.app, public rules only: `POST /v1/placements`, `POST /v1/placements/site-plan.svg`, bare `scene.json` under 4.5 MB;
  - a private, key-protected deployment with the real rules: ask Sam for access and its auth scheme.

## 6. Known defects

These defects remain after the review-debt repairs. They can let the server settle a check on evidence the worker does not have. The saved ETH3D results above predate those repairs; dataset acceptance has not been rerun.

- **One view can count as two for free space.** `recon/coverage.py:189-193, 208-246`: `_slab` treats a voxel as observed when its fused weight is above zero, and any single frame gives it weight. Repro: a capture in which one frame alone sees the space in front of a wall cell reports a facing clearance there. Wall and ground cells need two positions 0.25 m apart for the same claim. The server at 903d86f can settle a check on that clearance. A fix needs visibility kept per frame for free space, with the same two-position test as the wall and ground.
- **The overhead strip is narrower than a battery.** `recon/coverage.py:42, 236-245`: overhead space is sampled only 0.10 to 0.56 m out from the wall front. The worker now samples the rear edge and the full existing 0.56 m depth, including its outer edge. A 0.7 m eave over a 3 ft deep battery still lies beyond that assumed depth. A fix needs the footprint of the battery the chosen rule places, passed from the server's rules or request.
- **The photos-only clearance error is not calibrated.** `recon/pipeline.py:29-35`: facing gaps and overhead clearances from photos carry ±0.56 ft. That comes from the 95% interval of the evals' surface pair-error p90 over 1 to 3 m spans, not from any measurement of where the fused volume puts a boundary. The evals put surface p90 at 5.64 in [4.63, 6.66] and edges at 9.65 in [4.93, 15.01]. Repro: a photos-only overhead clearance measured at 7.10 ft passes the 6.5 ft headroom rule, since 7.10 − 0.56 = 6.54 > 6.5. A fix needs the clearance estimator's own measured uncertainty, or a gate that returns the check unresolved until that exists.

The worker now replaces the target wall's prior measurements, stops clearance at any occupied sampled voxel, and refuses LiDAR without a confidence map. Ground coverage follows the fitted slope, and only whole cells within the fitted wall extent are exported. The regressions are in `tests/test_review_debt.py`; they do not validate the remaining clearance policies above.

## 7. Data

Nothing is in git: the datasets are non-commercial and measure accuracy only. Local copies live under `~/house-scanning-data/`:
- `evals/{advio,eth3d,marvin}` for the datasets, `evals/hf-cache` for checkpoints;
- `replays/` for the ADVIO replay, `reports/sim/` for the app's Simulator bundles, `recon/` for worker outputs.

Download your own. URLs, sizes and sha256 are in `experiments/evals/METHODS.md`, "Datasets" (t3/evals). ARKitScenes, for a LiDAR test, ships depth, confidence and poses together only in its `raw` subset, about 0.3 to 0.6 GB per scene. Its pose convention is not documented.
