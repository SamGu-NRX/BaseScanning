---
cursor:
  subagentId: "bc-523191a1-af98-5e0a-a32c-f2416a3650b1"
---

# A6 handoff: real phone frames + real ARKit (LaMAR LIN), tight-prior COLMAP

## 1. One-paragraph summary

Path A6 re-ran A1 (COLMAP with ARKit priors) and A2 (π³, DA3-Large) on real iPad/iPhone-class ARKit frames (LaMAR LIN: outdoor Zürich street, 1440×1920 `capturedImage`, real ARKit poses and intrinsics, laser truth).

- **Headline:** COLMAP with ARKit position priors at σ 3 cm and no robust loss gives:
  - 1.3 / 4.6 in (median / p90) on 0.5–2 m distances, which passes;
  - 3.8 / 11.3 in on 15–26 ft distances, which fails on ARKit scale.
- **With corrected scale:** 3.0 / 7.4 in, and 2.4–2.8 in median on the no-depth sessions.
- **Real ARKit baselines (43 sessions, 15–26 ft apart):** 3.6 / 12.6 in; 7.4 / 18.9 in on sessions without scene depth.
- **Verdict:** conditional go. Short distances pass; long distances need a scale reference. π³ and DA3 trail A1 on phone frames.
- **Report:** `internal/breadth/A6-iphone-quality.md`.
- **Code:** `internal/breadth/a6-iphone-quality/` (tables in `results/`). Not in the repo.

## 2. Environment that worked

- **Machine:** 4 vCPU CPU VM, Ubuntu 24.04, Python 3.12, 15 GB RAM (about 6–7 GB free; other tenants), no GPU.
- **Setup script:** `bash internal/breadth/a6-iphone-quality/setup.sh` (about 10 min). It installs:
  - `~/a2/venv` via `uv`, with torch 2.14.0+cpu, numpy 1.26.4 (`numpy<2` pinned for the A2 adapters), pycolmap 4.2.0 (BSD) and open3d;
  - A2 code copied to `~/a2/code` (`a2ff/common.py` hard-codes `~/a2`);
  - repos Pi3 @ `9fa3ddb` (BSD-2) and Depth-Anything-3 @ `3d835ec` (Apache-2.0), installed `--no-deps -e`;
  - weights `yyfz233/Pi3` and `depth-anything/DA3-LARGE-1.1` into `HF_HOME=~/a2/hf` (5.2 GB).
- **Install fixes:**
  - DA3 import failed with `No module named moviepy`. Fix: `~/.local/bin/uv pip install "moviepy==1.0.3"` (now in `setup.sh`).
  - `uv` lives in `~/.local/bin`; it is not on PATH inside tmux sessions.
  - `remotezip` (for range reads from the LaMAR zips) is installed with `pip3 install --user --break-system-packages remotezip`. Use `/usr/bin/python3`, not the venv.
- **Disk:** `~/lamar` 6.1 GB (3 raw sessions, GT model, laser clouds).
- **Download speed** from cvg-data.inf.ethz.ch was about 27 MB/s.

## 3. Reproduce the headline result

Everything below runs from `internal/breadth/a6-iphone-quality/`; `S` is the store root.

```bash
source ~/a2/venv/bin/activate; mkdir -p ~/lamar && cd ~/lamar
curl -sL https://cvg-data.inf.ethz.ch/lamar/INFO | grep -i licen        # expect CC BY-SA 4.0
curl -sL https://cvg-data.inf.ethz.ch/lamar/raw/LIN/metadata_phone.json > metadata_phone.json   # has_depth per session
```

**Step 1: fetch metadata.** The two fetchers are VM-only (`~/lamar/fetch_meta.py`, `~/lamar/fetch_par.py`), not in the store; recreate them from this description if missing.

- `fetch_meta.py` does a remotezip extract of `trajectories.txt`, `images.txt` and `sensors.txt` for every `raw/LIN/sessions/ios_*.zip` into `~/lamar/raw/<s>/`, plus `LIN/sparse/{cameras,frames,rigs,images,points3D}.bin` and `LIN/pose_covariances.txt` from `colmap/LIN.zip` into `~/lamar/colmap/`. About 3 min.
- Then dump the GT poses. This writes `gt_ios.json` (13,398 frames) and `gt_navvis.json` (3,972), about 10 s. The dump loads `pycolmap.Reconstruction("colmap/LIN/sparse")` and writes `name -> {R, t, cam}` from `cam_from_world()` for `ios_*` and `navvis_*` images. See `lamar_io.load_gt`.

**Step 2: raw 10 fps frames for the three test sessions** (about 40 s total; sizes are the zip sizes).

```bash
for s in ios_2022-06-17_10.43.29 ios_2022-02-10_14.47.24 ios_2021-06-02_11.12.32; do
  curl -sSL -o raw/$s.zip https://cvg-data.inf.ethz.ch/lamar/raw/LIN/sessions/$s.zip   # 0.5-1.3 GB each
  (cd raw && unzip -q -o $s.zip "$s/raw_data/images/*" && rm $s.zip); done
```

**Step 3: run the pipeline.**

```bash
cd $S/internal/breadth/a6-iphone-quality
python arkit_scale.py          # 8 s; SUMMARY pair_abs_err_in_med 3.58, p90 12.56; session_scale_abs_med 1.17 %
python arkit_scale_split.py    # nodepth dt<60s: med 7.39, p90 18.9
python segments.py             # results/segments.json (5 segments, about 35 m each); then add 'frames' = keyframes ∪ gt_views (done in the stored json)
python build_laser.py          # fetches navvis_depth PNGs (12 threads), about 12 min; 06-17 s0 LiDAR vs laser 1.65 / 5.87 cm
bash run_a1_all.sh             # A1 modes fixed prior_s03 gt noprior prior on 4 segments; about 60 min (s0 is run separately: python a1_lamar.py 2022-06-17_10.43.29_s0 prior fixed noprior prior_s10 prior_s03 gt)
bash run_a2_all.sh             # pi3, da3KT, da3img, 2 windows × 5 segments; about 40 min
python rescore.py > results/rescored_table.md   # about 50 s; a1|prior_s03|<=10m|arkit patches 3.8 / 11.3 (12.4)
python per_segment.py > results/per_segment_table.md
```

Expected A1 log line for `06-17 s0 prior_s03`: `registered 99, scale_vs_gt_pct -0.40, rigid_rms_to_gt_m 0.088`.

## 4. Parameters that matter

- **ARKit prior σ** (`PosePrior.position_covariance`). This is the decisive knob.
  - σ 0.03 m without robust loss wins: 6–20 cm camera RMS, no bending on 5 of 5 segments.
  - σ 0.10 m without robust loss bent: 0.77 m RMS, p90 101 in.
  - σ 0.30 m with robust loss (the A1 ETH3D recipe) bent on `06-17 s0` (1.48 m RMS, −5.3 % scale, p90 76 in).
  - Not tried: σ 0.01 m, or rotation priors.
- **Matching:** spatial matching from the ARKit positions (`max_num_neighbors` 40, `max_distance` 20 m, `ignore_z` False) plus sequential matching (`overlap` 10). This was 2–3× cheaper than exhaustive; exhaustive was not re-tried.
- **Features:** SIFT, 8192 features, `max_image_size` 1920, PINHOLE per image with the ARKit per-frame intrinsics, `ba_refine_focal/principal/extra` all False. fx varies per frame (1570–1610 px, autofocus), so never share one camera across frames.
- **Keyframes:** a keyframe every 0.5 m or 15° of ARKit motion (packet rule), plus all GT views, giving about 0.3 m spacing and 87–205 frames per 35 m.
- **A2 input size:** 378×504 portrait (3:4, multiple of 14). DA3's `process_res=504 upper_bound_resize` keeps it exactly.
- **Scorer:**
  - points ≤ 10 m from the camera;
  - 25 cm laser patches with ≥ 3 points each;
  - outliers flagged by a Sim3 fit per 4 m cell with a 0.30 m cutoff;
  - laser voxel 2 cm, NavVis depth stride 5, render at 0.25× with a 1 px splat;
  - edge filter: 3×3 depth spread < 5 %.

## 5. Dead ends

- **Global-Sim3 outlier filter.** It marked 75–97 % of the points of a bent model as outliers and made that model look good. Use local cells.
- **Soft or robust priors, and image-only SfM + Sim3 to ARKit,** on forward street walks: they bend or fold (−46 % and −19 % scale on the February segments).
- **ADVIO** (iPhone 6s, non-LiDAR ARKit). Its inertial GT is noisier than the phones: ARKit vs Tango agree at 8–15 in, while each disagrees with the GT by 13–33 in. It can't score inches.
- **The Read tool can't open VM images** here; inspect frames by metrics instead.
- **`pgrep -f <pattern> | xargs kill`** killed the calling shell because the pattern matched the command itself. Kill by explicit PID.
- **Per-file remotezip extraction** runs at about 1.4 files/s; 12 threads with one `RemoteZip` each reach about 15 files/s (`fetch_par.py`).
- **`a1_lamar.py` result JSON** was overwritten when it was run with a subset of modes. It is fixed now (merge), but the per-point arrays in `~/lamar/arrays/` are the source for all tables.

## 6. Data quirks

- **Devices.** The paper says "iPad Pro", the table says iPhone/iPad; all have LiDAR hardware. There is no device id per session.
- **Depth recording.** The `has_depth: false` sessions (6 on 2021-06-02, 2 on 2022-02-10) are the only non-LiDAR proxy. They are confounded by date and app version.
- **Pose conventions.**
  - `trajectories.txt` is sensor-to-world (Kapture format, reversed), with the camera already in OpenCV axes. A relative-rotation check against GT matches to 0.23° as-is; flipping y/z gives 40°. Quaternion order is `qw, qx, qy, qz`, then `tx, ty, tz`.
  - The GT world is z-up (z range −8…+6 m) and is not the ARKit world.
- **Intrinsics.** `sensors.txt` has 10 fields: `id, name, camera, PINHOLE, w, h, fx, fy, cx, cy`. `w, h` are at index 4.
- **Timestamps.** Image names in the GT COLMAP subset (about 1 per metre) are raw timestamps, so they join `raw_data/images/<ts>.jpg` exactly.
- **Laser coverage.** `navvis_depth` is rendered from the laser mesh, in mm (uint16 PNG), and is aligned to the GT frame through the NavVis GT poses. Dynamic objects are removed, so pedestrians and cars in phone frames map to the facade behind them; they are handled as outliers. Laser covers 79–98 % of phone pixels.
- **GT pose σ:** 1.0 / 2.2 cm (median / p90) from `pose_covariances.txt`, using the last three diagonal entries as translation.
- **Segments that folded or split:**
  - `2022-02-10_14.47.24_s0`: 67 of 99 frames registered in 2 models in every SfM mode (prior_s03, prior, noprior). Fixed-ARKit triangulation registered all 99.
  - `2022-02-10_14.47.24_s0` and `_s1`: noprior folded (−45.7 % and −18.8 % long-pair scale).
  - `2022-06-17_10.43.29_s0`: prior (σ 0.3 m robust) and prior_s10 bent (p90 76–101 in). prior_s03 was fine.
  - DA3 images-only `2022-02-10_s0/w0`: +45 % scale.
  - Low texture and light on February: 9–13 k sparse points per segment vs 36–46 k in June.

## 7. Integration notes for the pipeline

- **Inputs** (per the packet spec):
  - keyframe JPEGs at 1920×1440;
  - per-frame `intrinsics` [fx, fy, cx, cy] (not a session constant);
  - per-frame `pose` (camera-to-world);
  - `epoch` (treat each epoch as a separate prior group; resets were not tested).
- **Pose stage:** `incremental_mapping` with `use_prior_position=True`, `use_robust_loss_on_prior_position=False`, σ 0.03 m, intrinsics fixed.
  - Fallback 1: if the largest model has under 90 % of frames, or its Sim3 RMS to ARKit is over 0.25 m, triangulate with the fixed ARKit poses (`pycolmap.triangulate_points`).
  - Fallback 2: surface the error code `sfm_split` / `sfm_bent` in `provenance`.
- **Scene emission:** emit `keyframes[]` with `pose_source: "sfm"` in ARKit metres, and `scale_source: "arkit_poses"` unless a reference exists (then `scale_reference` or `tap_distance` plus `scale_reference_ids`).
- **Uncertainty (`plus_minus_m` / measured `plus_minus_ft`):** carry at least ±(0.5 in + 2.5 % of the length) for ARKit-scaled distances. This is the p90 scale on no-depth sessions.
- **Degrade:** long distances (> 2 m) scaled only by ARKit on non-LiDAR devices go to `unsure` with `unsure_cause: margin` when the margin is under that bound.
- **Regression gate:** the 5 LaMAR segments + `rescore.py`. Gate values (ARKit scale, ≤ 10 m, patches):
  - openings p90 ≤ 4.6 in;
  - 15–26 ft p90 ≤ 12.4 in on the worst segment;
  - floor (`gt` mode) 1.7 / 5.4 in.

## 8. Open risks

- **No true non-LiDAR iPhone data with laser truth.** Cheapest fix: one iOS-app capture on a non-Pro iPhone along a wall measured with a tape or laser distance meter, run through `a1_lamar.py`-style scoring.
- **Tracking resets/epochs, night sessions, and loops longer than 35 m** were not tested. Cheapest: run `segments.py` with `length=100` on a night session (e.g. `ios_2022-06-19_23.08.45`, session RMS 1.7 m).
- **KD-level scoring** (planes and openings) is not available for LaMAR; point pairs carry a floor of about 5 in p90. Fit laser planes for 2–3 facade spans.
- **A2 was only tested with 16-view windows** and a Sim3 over 16 cameras (per-window scale −2.7 to +3.9 %). Test longer windows scaled from the whole-segment ARKit track.

## 9. What you would do differently

- Tune prior σ on real ARKit first; the ETH3D-simulated drift pushed the recipe the wrong way.
- Use local outlier cells from the start, and save per-point arrays from the first run.
- Build the laser KD scene for 2 LaMAR spans before scoring many modes.
- Skip ADVIO; its GT can't resolve inches.
