---
cursor:
  subagentId: "bc-daa6b400-6fd7-514e-837d-95d1771fa424"
---

# A1 classical (COLMAP): builder handoff notes

`S` = `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`, `C` = `S/internal/breadth/a1-classical`, `H` = `S/internal/eval-harness`. All work ran on a 4 vCPU / 15 GB / no-GPU Ubuntu 24.04 cloud VM.

## 1. One-paragraph summary
Path A1: classical SfM (pycolmap 4.2 incremental mapper) on real ETH3D photos, in three pose modes: none, ARKit-like, and ground truth. ARKit noise was synthetic (packet-generator drift profiles applied to GT poses). A1 also tried OpenMVS dense, GLOMAP and online walkaround videos.

Headline: on the ETH3D `facade`, **incremental SfM with ARKit positions as soft priors** (σ 0.3 m) scored through the shared scorer (`H`, `score-cloud --metric`):

| Profile | Key distances scored | \|err\| median / p90 / max | Scale error |
|---|---|---|---|
| LiDAR | 31 of 50 | 0.84 / 2.06 / 2.66 in | +0.29 % |
| non-LiDAR | 30 of 50 | 0.84 / 2.90 / 5.20 in | −1.17 % |

Verdict: **conditional go**. It passes the 2–6 in bar on observed openings, heights and gaps, with two conditions:
- ARKit's session scale bias remains after SfM (LiDAR p90 of about 12 in at 26 ft), so it needs a scale reference;
- edge-on walls, corners and the ground are missing (honestly).

Report: `S/internal/breadth/A1-classical.md`. Code: `C/*.py`, `C/*.sh`. Small results: `C/results/`. Scorer outputs: `H/results/eth3d-facade/A1-colmap/<run>_<sparse|dense>/`. Figures: `S/media/colmap-facade/`. Nothing is in the `house-scanning-server` repo yet.

## 2. Environment that worked
- Ubuntu 24.04, Python 3.12.3, gcc 13.3, cmake 3.28. `bash C/setup.sh` rebuilds everything into `~/a1` in about 15 min (apt 3 min, downloads about 1 min at about 25 MB/s, OpenMVS build 5 min at `-j3`, evaluator under 1 min).
- pip (venv `~/a1/venv`): `pycolmap==4.2.0` (CPU wheel, BSD-3; includes GLOMAP as `pycolmap.global_mapping`), numpy 2.5.3, scipy 1.18.1, open3d 0.20.0, opencv-python-headless 5.0.0.93, plyfile 1.1.5, matplotlib 3.11.2, plus `pip install -r H/requirements.txt` for the scorer.
- `pycolmap.has_cuda` is False, so COLMAP dense stereo (`patch_match_stereo`) is unavailable (CUDA-only).
- OpenMVS **v2.3.0** only (AGPL-3.0), built with gcc. The build fixes:
  - `/usr/bin/c++` is clang-18 and fails to link, so force `CC=gcc CXX=g++`;
  - develop and v2.4 need vcpkg-only dependencies (PoseLib, TinyEXIF), so don't use them;
  - v2.3 aborts at runtime with `*** buffer overflow detected ***` in `Util::formatTime` (glibc fortify). `setup.sh` patches `libs/Common/Util.h` lines 601–628 (`128` → `128-len`) before building.
- ETH3D multi-view evaluator (github ETH3D/multi-view-evaluation): `sed -i s/-std=c++11/-std=c++17/` is needed for PCL 1.14.
- Disk: about 3 GB of ETH3D archives, about 1 GB per dense workspace. RAM peak about 3 GB (OpenMVS). No GPU used.

## 3. Reproduce the headline result
```bash
bash C/setup.sh                                  # ~15 min, once
. ~/a1/venv/bin/activate && cd C
python prepare.py facade                         # 32 s: image symlinks, gt_model/, merged 1 cm scan
python sfm.py facade features                    # 110 s SIFT + 156 s exhaustive matching (76 imgs @ 2048 px)
python sfm.py facade gt                          # 16 s, needed by arkit (gravity alignment uses GT points)
python sfm.py facade arkit nonlidar_modern 0     # ~2 min: writes sparse/arkit_nonlidar_modern_s0_{fixed,prior}
python sfm.py facade arkit lidar_modern 0
# the shared scorer needs the laser GT scene on the VM (65 s):
cd ~/a1/dl && curl -sSLO https://www.eth3d.net/data/facade_dslr_occlusion.7z && curl -sSLO https://www.eth3d.net/data/facade_dslr_undistorted.7z
cd ~/a1/eth3d && 7z x -y ~/a1/dl/facade_dslr_occlusion.7z && 7z x -y ~/a1/dl/facade_dslr_undistorted.7z 'facade/dslr_calibration_undistorted/*'
cd H && python eth3d/build_gt.py --data ~/a1/eth3d/facade --out ~/a1/facade-gt
cd C && python hscore.py arkit_nonlidar_modern_s0_prior sparse    # ~1 min
python summarize.py                              # table of every scored run
```
Expected, from `sparse/arkit_nonlidar_modern_s0_prior.json`: registered 76, points3D about 62,434, mean reprojection about 0.32 px. Expected from `H/results/eth3d-facade/A1-colmap/arkit_nonlidar_modern_s0_prior_sparse/geomscore.md`: "Key distances (30 scored): median 0.84 in, p90 2.9 in, max 5.2 in".

Other entry points:
- `bash run_scene.sh <scene> sparse dense score`: all modes, OpenMVS, and extras.
- `python scale_study.py facade incremental --order tour`: 50-seed scale error, 20 s.
- `python render_media.py`: the figures, 1 min.
- `bash collect.sh`: copies small results into `C/results`.

## 4. Parameters that matter
- **Intrinsics fixed** in every BA (`ba_refine_focal_length/principal_point/extra_params = False`). The packet provides K. Refining it was not tested.
- **Prior σ 0.3 m** (`pp.position_covariance = I·0.09`) with `use_robust_loss_on_prior_position = True`. Not swept. Camera-shape error with priors was 1.7 cm RMS, the same as the no-prior run, so the priors fix the frame and scale without distorting shape.
- SIFT `max_num_features = 8192` at `max_image_size = 2048`. The harness baseline used 1600 px; both registered 76/76.
- Exhaustive matching: 2,850 pairs, 156 s for 76 images. For production, use spatial matching from the ARKit positions (untested).
- OpenMVS: `--resolution-level 1` (1024 px depth maps), `--max-threads 4`, default geometric-consistency pass. That took 22.6 min contended for 76 images, and roughly doubles the time over the first pass alone.
- Scorer input: voxelize to 2 cm (the laser GT resolution) and crop to 20 m from the cameras (`hscore.py`). At 1 cm and uncropped, the dense cloud was 229 MB and the scorer stalled.
- `--metric` for every mode except no-pose. Without it the scorer uses a similarity alignment and hides scale error.

## 5. Dead ends
- **Fixed ARKit poses** (triangulate only): 17–48 cm camera error, reprojection 1.6 px; 4.8 in (LiDAR) and 12 in (non-LiDAR) median; most walls lost. Always re-run BA with priors.
- **GLOMAP** (`global_mapping`, CPU; GPU flags must be set off): 0.34 px reprojection but folded positions. Camera RMS was 4.0 m on facade and 2.5 m on electro; it worked only on delivery_area (0.4 cm). Low reprojection error is not a health check; compare against priors.
- **OpenMVS dense as the measurement cloud**: wall-face completeness within 10 cm rose from 48 % to 63 %, but key distances got worse (GT poses: 21 scored, p90 677 in). Two causes:
  - window glass yields virtual depth 0.3–1 m behind the wall, and the lower wall band is patchy;
  - the harness extractor then splits w1 and invents corners.
- **Online videos, image-only**: every one fragmented into 4 models (33/151, 74/150, 43/148 frames in the largest). `yt-4h8kyjnPo9s` crashed OpenMVS with a corrupted allocation of about 262 TB at levels 0 and 1.
- **Shared scorer bug**: `score-cloud` crashed on the dense fixed-LiDAR cloud (`AttributeError: 'float' object has no attribute 'min'` at `best_fp = fp.min(1)`).
- **Quirks**:
  - the "9,955 features > 8192" count is not a bug: SIFT adds extra orientations per keypoint (direct `ext.sift.max_num_features = …` assignment works);
  - Open3D writes double-precision PLY, which the ETH3D evaluator silently scores as all zeros, so write float32 with plyfile;
  - the store FUSE mount sometimes serves the previous version of a just-edited file for a few seconds, so `sleep 3` before running;
  - `pycolmap.triangulate_points` logs "Global bundle adjustment" but keeps the poses fixed (verified to 1e-12 m).

## 6. Data quirks
- Facade frames in `S/internal/data/houses/eth3d-facade/frames` are 2048×1364 (from 6201×4130), with calibration pre-scaled. Electro and delivery are resized by `prepare.py` to 2048 px wide.
- ETH3D world: +z is up (about 1.3° off). The harness scene frame (meter-anchored, y up, w1 at z = 0) comes from `scene.json frame.from_source` (4×4, **column-major**).
- The harness `laser.ply` is cropped to the courtyard wing. For whole-scene per-point error, use the merged scan (`WORK/facade/gt_scan_1cm.ply`, ETH3D frame, 14.2 M points).
- The meter wall w1 is the courtyard wing, not the main facade most photos face. w2 is only seen edge-on (about 78°), and the ground only at 81–84°. So corners, the door, the w2/w3 lengths, facing and overheads are unobservable in this dataset.
- The meter position is invented, and the opening "truth" comes from PR #13 hand clicks ray-cast onto laser walls (±0.27–1.29 ft), not laser-exact.
- The ARKit simulation orders poses by DSC filename, which implies a 161 m walk (104 m with the nearest-neighbour "tour" order), inflating drift. The non-LiDAR profile is assumed, not measured.
- Electro and delivery_area have no shared-scorer GT scene here; they gave pose evidence only.

## 7. Integration notes for the pipeline
- Inputs: keyframe JPEGs, per-frame intrinsics (fx, fy, cx, cy at the image size), ARKit camera-to-world poses (column-major, ARKit axes; convert with `evalharness.frame`), a tracking epoch or reset index per frame, gravity, and meter tap pixels.
- Stage:
  1. SIFT features;
  2. matching (spatial from ARKit, plus sequential);
  3. write a `PosePrior` per image into the database (`corr_data_id = image.data_id`, CARTESIAN, position in the ARKit world, covariance σ²·I);
  4. `incremental_mapping` with `use_prior_position = True` and fixed intrinsics;
  5. take the largest model.
  Handle each ARKit epoch as its own prior group, or drop resets; A1 forced no resets.
- Outputs: a metric point cloud plus cameras in the ARKit world. Then `evalharness.extract` (or `score-cloud`) makes the scene v0:
  - `provenance.producer.path = "A1-colmap"`, `scale_source = "arkit_poses"` (or `tap_distance` with `scale_reference_ids` once a scale reference exists);
  - `keyframes[].pose_source = "sfm_scaled_by_arkit"`;
  - coverage from real observation rays; §3.1 coverage evidence comes from `evalharness.coverage.query_region` on that scene.
- Error codes to surface:
  - registered frames below about 80 % of the input, or more than one model (fragmentation);
  - camera-vs-ARKit RMS above about 0.3 m after the fit (bad SfM or bad priors);
  - Sim(3) scale versus ARKit off by more than about 3 %;
  - too few points on the meter wall (under about 60 in the 0.1–2.5 m band).
- Degrade: if SfM fails, fall back to fixed ARKit poses, but mark every distance unsure (they are 5–12 in off). Never report missing walls as measured.
- Declare bounds: scale term = 2σ of the profile × distance from the meter (for example 4 % for non-LiDAR), plus about 1 in local.

## 8. Open risks
- All ARKit noise is simulated. Cheapest check: 2–3 real iPhone captures of a wall with a tape-measured reference; run the prior mode and compare against the tape.
- Scale bias dominates at more than about 3 m from the meter. Cheapest check: add a tap-to-tap known distance (or LiDAR depth on Pro devices) and rerun `scale_study.py` with a scale reference.
- Phone images (rolling shutter, HDR, motion blur, 12 MP wide lens, lower texture on siding) are untested. DSLR undistorted images are a best case.
- Non-Pro iPhones have no LiDAR, and the non-LiDAR profile (2.4 % median scale error) fails the bar beyond about 2 m unless there is a scale reference.
- Fragmentation on real walkarounds: the priors should glue the fragments but this is untested (the videos have no ARKit). Cheapest check: one real packet walkaround at about 2 fps.
- Tracking resets: forced off in A1. Real packets need per-epoch priors; untested.
- Runtime with spatial matching and more frames (150–300 keyframes): untested. Exhaustive matching scales quadratically.

## 9. What you would do differently
- Implement ARKit-guided spatial matching from the start instead of exhaustive matching (faster, and fewer wrong loop closures).
- Score only through the shared scorer from day one; I built a separate scorer first and it was wasted.
- Skip OpenMVS: AGPL, a fortify patch, 20+ min on CPU, and it hurts extraction. Use GPU COLMAP stereo or learned depth only for coverage.
- Test real phone captures with a tape reference before any more synthetic-pose sweeps.
- Sweep the prior σ and try gravity priors (`PosePrior.gravity`) early.
