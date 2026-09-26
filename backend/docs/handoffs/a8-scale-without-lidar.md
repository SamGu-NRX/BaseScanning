---
cursor:
  subagentId: "bc-3d8b8a80-8af0-5c5e-85bb-bebf6e1707f2"
---

# A8 scale without LiDAR: builder handoff

## 1. One-paragraph summary
Path `A8-scale-without-lidar`. It measured how accurately metric scale can be set without LiDAR, using known-size references (doors, windows, brick courses, siding boards, a notional meter base), a homeowner tape, ARKit's own scale and learned metric depth (MoGe-2, Metric3D), scored against laser truth (ETH3D facade and electro) and the laser-registered Tanks and Temples barn cameras.

- **Tape: ruled out.** The tape recommendation (0.4 / 0.9 % median / p90 scale error) is dead: customers have no tools.
- **What carries into production:**
  1. Measuring a reference inside the scale-free SfM model is precise. Recon vs laser for the same pixels was −0.27 % mean, SD 0.35 % (n = 17). View-to-view SD was 0.12–0.34 % for crisp repeated edges and 1.35 % for a door outline.
  2. ARKit scale works as a gate and fallback, not a source: non-LiDAR p90 is 5.7 % (modern, simulated) and 17.8 % (2018 profile, simulated from real ADVIO).
  3. Door and footprint references only work as consistency checks. Feature identity dominates: the barn door outline read 86.8 in against an 80 in leaf, +8.5 %.
  4. Learned depth is a coarse prior only: scene-level error −12.5 % to +20.3 %.
  5. The evaluation method (§3).
- **Weak references with no homeowner action:** 1.7 / 4.7 % median / p90 scale error, 5.4 / 14.9 in at 26 ft; only 54 % of houses within 6 in.
- **Verdict: conditional go** for the gate and consistency layer; no-go for any single weak source as primary scale.
- **Report:** `internal/breadth/A8-scale-without-lidar.md`. Code and results: `internal/breadth/a8-scale/`. Nothing is in the repo yet.

## 2. Environment that worked
- Machine: 4 vCPU, 15 GB RAM, no GPU, Ubuntu with Python 3.12.3.
- Setup:
  - `sudo apt-get update && sudo apt-get install -y python3.12-venv p7zip-full`. The venv failed until `python3.12-venv` was installed, and apt needed `update` first.
  - `python3 -m venv ~/a8/venv`
  - `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`
  - `pip install -r internal/eval-harness/requirements.txt` (pins `pycolmap==4.2.0`)
  - `pip install git+https://github.com/microsoft/MoGe.git timm huggingface_hub plyfile open3d`
  - Metric3D: `git clone --depth 1 https://github.com/YvanYin/Metric3D.git ~/a8/Metric3D && pip install mmcv-lite`
- Metric3D CPU patch: `rg -l 'device="cuda"' ~/a8/Metric3D/mono | xargs sed -i 's/device="cuda"/device="cpu"/g'`. Without it, inference dies with "Torch not compiled with CUDA enabled".
- Weights:
  - MoGe-2 `Ruicheng/moge-2-vits-normal` (Hugging Face; code MIT, DINOv2 parts Apache-2.0).
  - Metric3D `metric_depth_vit_small_800k.pth` (Hugging Face JUGGHM/Metric3D, 143 MB; code BSD-2, weights licence not verified).
  - COLMAP / pycolmap: BSD-3.
- Data:
  - ETH3D facade + electro `dslr_scan_eval`, `dslr_occlusion`, `dslr_undistorted` from `https://www.eth3d.net/data/<scene>_<kind>.7z`: about 1.5 GB for the facade alone, downloaded in about 2 min. ETH3D is CC BY-NC-SA.
  - Barn frames: `internal/data/houses/tt-barn/frames` (137 frames).
- Disk used: under 10 GB. Peak RAM was about 3 GB after the SVD fix (§5).

## 3. Reproduce the headline result
Run from a local copy (`cp -r internal/breadth/a8-scale ~/a8/code`). Running directly on the store FUSE mount once gave a stale-import error. Paths are hard-coded to `~/a8/data`, `~/a8/work` and the store root in `common.py`.

1. Facade GT for the scorer (about 4 min):
   `cd internal/eval-harness && python eth3d/build_gt.py --data ~/a8/data/eth3d/facade --out ~/a8/data/eval-harness/facade-gt`
2. Scale-free models:
   - ETH3D: untar `internal/breadth/a1-classical/results/<scene>/sparse/incremental.model-light.tar.gz` into `~/a8/work/<scene>/inc`.
   - Barn: `python sfm_barn.py`, 15.5 min (extract 68 s, exhaustive match 765 s, mapper 98 s). Expect `models_registered [137]` and about 45.7 k points.
3. Truth scale on load (`common.load_scene`):
   - facade: `s_true` 2.8027 m/unit, Sim(3) RMS 1.7 cm;
   - electro: 0.6908 m/unit, RMS 0.3 cm;
   - barn: 4.0712 m/unit, RMS 1.8 cm.
4. Reference segments, 5–25 s per scene: `python measure.py {facade,electro,barn} [--qa]`, then `python stats.py`.
   - Expect: `Recon vs laser, same pixels (ETH3D, 17 measurements): mean -0.27 %, SD 0.35 %`, siding `6.72 in (6.70–6.76)`, barn door `86.80 in`, facade course `2.98 in`.
5. Learned depth, about 1 min per scene per model: `python mdepth.py <scene> {moge2-vits,metric3d-vits} --n 16`.
   - Expect scene-level errors: MoGe-2 electro +16.8, facade −12.5, barn +3.5 %; Metric3D electro +6.8, facade +20.3, barn −9.1 %.
6. Monte Carlo, about 2 s: `python combine.py`. Writes `results/combine.md`; weak refs with no action: `1.7 / 4.7 / 6.0 %`.
7. Shared scorer: `python score_scaled.py oracle 0 arkit_2018_med -7.0 ...`, 1–4 min per run, then `python scorer_summary.py`.
   - Expect the median over key distances > 3 m to be 5.8 in at oracle scale, 13.9 in at −7 %, 27.8 in at −17.5 %.

## 4. Parameters that matter
- **Edge snap** (`refine.snap_edge`):
  - search radius R = 12–20 px (20 for rough first clicks, ×1.5 for transferred views); 40 samples over the middle 70 % of the edge; RANSAC line tolerance 1.5 px.
  - Drop measurements where the snap inlier fraction is below 0.5 on either edge (7 of 41 dropped).
- **Plane for ray intersection:**
  - RANSAC threshold 0.4 % of median depth.
  - Occlusion: keep the front-most point per 8 px cell, then points within 25 % of the 25th-percentile depth.
  - Subsample to 20 k points.
  - Band: 25 % of the segment length laterally, inset 8 % at the ends. "Wide" mode for textureless faces (doors, glass): 60 % lateral, extended 40 %.
- **Length definition:** use the 3D perpendicular distance between the two back-projected edge lines (`common.line_separation`), not the image-normal endpoint distance. The image-normal distance tilts under perspective and adds about 1 %.
- **Repeat counting** (`measure.count_repeats`): 8× upsampled profile; the first autocorrelation peak above 60 % of the maximum, refined on the highest multiple that fits in the profile. Works at 32 px or more per unit and fails at about 7 px.
- **Gross-view rejection:** views more than 25 % off the object's median are flagged gross.
- **Combination** (`combine.py`): inverse-variance in log scale; 3σ gating; the conflict test compares precise sources (declared SD ≤ 1.5 %) at 3 joint SDs.
  - Declared SDs: ARKit 3 % (modern) / 10 % (2018); depth 12 %; door 3.5 %; meter from a house photo 3 %; meter close-up 1 %.
  - 2 SD covered only 89–93 % of houses, so emit 95 % bounds as 2.5 × SD.

## 5. Dead ends
- **`np.linalg.svd(Q - c)` with default `full_matrices=True`** on 40 k laser points allocates an N×N matrix and was OOM-killed at 14 GB. Always pass `full_matrices=False`.
- **Plane fit on all projected SfM points (no occlusion):**
  - Symptom: barn door 5.4 m tall, depth 16 m instead of 6.4 m. On a 360° loop the far walls project "through" the building.
  - Fix: per-image track points when the model has tracks, plus the per-cell z-buffer.
  - I first blamed the dataset units; they are fine (MoGe-2 and the track points agree at 6.3–6.4 m).
- **Local per-endpoint planes for tape segments:** too few points on flat door edges (9 points, 4.8 % error). A single band plane works.
- **Automatic brick-course counting from house-distance photos:** right in 1 of 4 views; it locks onto 6-course colour bands or harmonics.
- **Roll-up garage door:** the two views disagreed by more than 25 % (degenerate snap on panel lines). Doors transferred to oblique views often snap to the jamb instead of the leaf.
- **A1 "model-light" tarballs have no 2D tracks:** `mdepth.py` found 0 points until it fell back to a z-buffered projection of all points.
- **`Barn.ply` via `fetch_gt.sh`:** Google Drive returned a 2 kB HTML "Quota exceeded" page saved as `.ply`. Check `file Barn.ply`.
- **Read tool vs `/tmp`:** freshly written images read as "File not found" once; retrying works.
- **ARKit gate alone can't catch misread references:** a 10 % ARKit bound lets 5–10 % errors through, so a 3σ gate is useless against them.

## 6. Data quirks
- The store's `internal/data/houses/eth3d-*/calibration/images.txt` has its POINTS2D lines stripped. The harness `read_cameras` assumes 2 lines per image and silently returns half the cameras (38 of 76). Read `~/a8/data/eth3d/<scene>/dslr_calibration_undistorted/` instead.
- ETH3D GT intrinsics are at 6201 px; store frames are 2048 px. Scale K by 2048 / 6201, with pixel-centre handling in `common.eth3d_gt_cams`.
- Barn:
  - Undistorted images are 1958×1085, not the 1920×1080 source; annotate on `~/a8/work/barn/undistorted/images`.
  - T&T `Barn_trans.txt` × log centres is metric; the Sim(3) scale is 4.07.
  - Barn truth here is camera-based (our model × `s_true`), not laser points.
- The facade brick is European (2.98 in per course); electro doors are European steel doors. Only the barn has US-standard objects: door outline 86.8 in, siding 6.72 in, roll-up door about 70 × 120 in.
- None of the scenes has a real meter; the meter-base term is modelled (flange 6.95 in; cover 6.29–6.42 in).
- Shared scorer on the sparse facade cloud: key-distance counts change between scaled runs (31 vs 35). Maxima of 88–170 in are phantom corners from the extractor, not scale.

## 7. Integration notes for the pipeline
- **Inputs:**
  - the SfM model with per-image tracks (points actually observed in each image) and cameras in model units;
  - the ARKit Sim(3) scale and its profile (non-LiDAR);
  - reference detections as rough edges or boxes (door leaf, meter base, window);
  - optionally per-frame learned depth.
- **Outputs,** extending `docs/scene-and-verdict-format.md`:
  - `scale_source` (`arkit_poses` or `scale_reference`) and `scale_reference_ids`;
  - a proposed `scale` block: `{estimates:[{kind, value_m_per_unit, rel_sd, residual_pct, used}], rel_bound_95, status: verified|single_reference|unverified, conflict}`.
- **Every measurement:** `plus_minus_ft = shape_bound + rel_bound_95 × distance_from_reference_ft`. Bounds combine linearly (contract §3), and C5 margin handles unsure.
- **Error codes to surface:**
  - `scale_reference_edge_not_found` (snap inliers below 0.5);
  - `scale_reference_views_disagree` (spread over 25 %);
  - `scale_sources_conflict` (precise sources over 3 joint SDs apart);
  - `scale_plane_fit_failed` (under 8 visible points).
- **Degrade gracefully:** with no usable reference, fall back to ARKit scale with an 18 % bound (2018 profile) or about 7 % (modern) and let margins go unsure. Never let learned depth or a single unconfirmed object produce `verified`. Never let a bound over 6 % support a reject on the 16–26 ft reach rules.
- **Door leaf vs outline:** detect the leaf (inside the stop), not the frame or trim; the outline is +4–9 %.

## 8. Open risks
- **ARKit numbers are simulated** (packet-generator drift profiles). The cheapest check: 3 real non-Pro iPhone walks along a taped wall, comparing ARKit scale with the tape.
- **Meter-base reference untested:** no meter in laser truth. Photograph one meter close-up next to a ruler and measure it through the pipeline.
- **Door-leaf detection rate unknown** (the 30 % outline confusion is assumed). Run the VLM/OWLv2 plus snap on B1's door set and hand-check leaf vs frame.
- **Endpoint precision (6 mm) came from DSLR frames at 2048 px.** Phone frames (4032 px but noisier, with rolling shutter) may differ; rerun `measure.py` on a real packet.
- **Learned-depth scale checked on 3 scenes only;** its ±20 % may be worse on dark siding or close-ups.

## 9. What you would do differently
- Use per-image tracks and occlusion from the start. The occlusion bug cost about 40 min and a false alarm about dataset units.
- Measure between back-projected lines in 3D from day one, not between image-normal endpoints.
- Get one real iPhone capture with ARKit poses before modelling homeowner behaviour.
- Treat the shared scorer as confirmation above 2 % only. On sparse clouds its extractor noise hides smaller scale errors, so use direct laser-segment checks below that.
- Skip automatic repeat counting unless there are 20 px or more per unit.
