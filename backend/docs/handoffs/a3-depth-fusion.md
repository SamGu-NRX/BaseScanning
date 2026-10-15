---
cursor:
  subagentId: "bc-4da717fb-7eba-55dd-869e-6335aa978667"
---

# A3 metric depth fusion: builder handoff notes

## 1. One-paragraph summary
Path `A3-depth-fusion` computes per-image metric depth (Metric3D v2 ViT-S, MoGe-2 ViT-S, UniDepth V2 ViT-S) at 1024 px wide, scales it per image, and TSDF-fuses it along camera poses. It was scored against ETH3D laser scans with PR #12's alignment-free pair metric and PR #12's electro coverage truth.

Headline numbers (electro, points within 6 m, 1–3 m pairs, inches median / p90):
- Metric3D ViT-S, true poses, scale `tri`: fused 1.7 / 6.0; walls p90 9.5; edges p90 44.
- Simulated LiDAR depth, true poses: 0.7 / 2.7.
- Under packetgen ARKit drift, fusion breaks: 4.5–9.3 / 17–35 image-only, and 1.9–3.2 / 10–18 with simulated LiDAR.
- Single photo with one taped distance: 2.2 / 7.9 regardless of poses.

Verdicts:
- No-go as a standalone reconstruction.
- Go as the predicted-depth seen/hidden test. On the 19.3 ft electro wall at 0.10 m relief it gives 0.0 ft false-observed and 3.35–3.9 ft missed. The laser gives 0.0 / 3.35 and today's app gives 1.1 ft false-observed.
- Conditional go as a per-frame second opinion.

Locations:
- Report: `internal/breadth/A3-depth-fusion.md`.
- Code and small results: `internal/breadth/a3-depth-fusion/` (`results/tables.md`, `results/eval/*.json`, `results/occlusion/electro_tri.json`, `results/houses/*`).
- Shared-scorer outputs: `internal/eval-harness/results/eth3d-facade/A3-*`.
- Nothing was pushed to any repo.

## 2. Environment that worked
- Machine: Ubuntu 24.04, 4 vCPU, 15 GB RAM, no GPU, Python 3.12 venv at `~/a3/venv`.
- System packages: `sudo apt-get install -y p7zip-full python3.12-venv libegl1 libgl1 libgomp1`.
- `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` (torch 2.14.0+cpu).
- `pip install open3d opencv-python-headless scipy pillow huggingface_hub timm einops pycolmap trimesh plyfile matplotlib h5py imageio pandas tabulate termcolor tqdm mmengine`. This installs open3d 0.20.0 and pycolmap 4.2.0.
- MoGe and Depth Pro dependencies conflict. MoGe main is v3: it requires numpy>=2 and pulls FlexGEMM, which is CUDA-only. depth-pro pins numpy<2. The fix is to install without dependencies:
  `pip install --no-deps git+https://github.com/apple/ml-depth-pro.git git+https://github.com/microsoft/MoGe.git "git+https://github.com/EasternJournalist/utils3d-moge.git@62f09d58509485564e24d5d9f6aac9ee9ebc0c37" "git+https://github.com/EasternJournalist/pipeline.git@1c511390d90226c00c101f34b84df26a0f8789b4"`
  then `pip install "numpy>=2,<2.4" pillow_heif click`. `from moge.model.v2 import MoGeModel` works on CPU; v3 does not.
- UniDepth: `git clone --depth 1 https://github.com/lpiccinelli-eth/UniDepth.git && pip install --no-deps -e UniDepth`. It imports wandb, so make a stub with `mkdir -p ~/a3/stubs/wandb && echo "Image = None" > ~/a3/stubs/wandb/__init__.py` and run with `PYTHONPATH=~/a3/stubs`.
- Metric3D: `git clone --depth 1 https://github.com/YvanYin/Metric3D.git && (cd Metric3D && git apply internal/breadth/a3-depth-fusion/vm-setup/metric3d-cpu.patch)`. The patch falls back from mmcv to mmengine and changes the RAFT decoder from cuda to cpu. Load it through `hubconf.metric3d_vit_small`.
- Weights (auto-download from Hugging Face or the torch hub on first run):

| Weights | Licence |
| --- | --- |
| `Ruicheng/moge-2-vits-normal` / `-vitl-normal` | MIT; DINOv2 parts Apache-2.0 |
| `lpiccinelli/unidepth-v2-vits14` | CC BY-NC 4.0, not for product |
| Metric3D `metric3d_vit_small` | Code BSD-2; weights licence unverified |
| `apple/DepthPro` (1.9 GB) | Apple custom licence, unverified |

- Full setup script: `internal/breadth/a3-depth-fusion/vm-setup/` plus the handoff `internal/handoffs/A3-depth-fusion.md`.
- ETH3D download: about 3.5 GB at ~50 MB/s, 7z extract about 1 min per scene.
- PR #12 eval code: `gh repo clone SamGu-NRX/house-scanning hs` then `git fetch origin t3/evals && git checkout FETCH_HEAD`. It is used read-only via `common.py`.
- CPU seconds per image at 1024 px, run alone:

| Model | s / image | Peak memory |
| --- | --- | --- |
| UniDepth ViT-S | 1.5 | – |
| MoGe-2 ViT-S | 2–3.4 | <2 GB |
| Metric3D ViT-S | 2.9 | – |
| MoGe-2 ViT-L | 7 | 3.5 GB |
| Depth Pro | 30 | 9.3 GB RSS |

- MoGe-3 did not install (needs CUDA).

## 3. Reproduce the headline result
All commands run from `internal/breadth/a3-depth-fusion` with `A3_ROOT=~/a3` (the default). ETH3D must be extracted in `~/a3/eth3d/<scene>`, with the symlink `~/a3/data/evals/eth3d`.
1. `bash vm-setup/run_prepare.sh`: runs PR #12 `evals.recon.prepare` and writes `images_1024/`, `visibility_1024_tol4/`, `normals.npy`, `subsets.json` and `scan_points_10mm.npy`. Takes 150 s on electro and 300 s on facade.
2. `PYTHONPATH=~/a3/stubs python infer.py --scene electro --model metric3d2-vits`: writes `~/a3/work/pred/electro/metric3d2-vits/<view>.npz` with depth, valid, K and seconds. About 2 min alone. It resumes per view.
3. `python evaluate.py --scene electro --models scan,metric3d2-vits,unidepth2-vits,moge2-vits --poses gt --scales raw,feat,tri,oracle`. Each run takes 70–230 s. Results go to `~/a3/work/eval/electro/<tag>.json` and meshes to `~/a3/work/fused/electro/<tag>.ply`. It prints a line per run; compare with:
   `metric3d2-vits__gt__tri__v4: FUSED cov 82% raw1-3 1.7/6.0 tape1-3 2.1/8.4 tape3-10 4.3/17.5 | SINGLE cov 91% raw1-3 2.3/8.6 tape1-3 2.2/7.9 | acc10 0.52 comp10 0.67`
   `scan__gt__raw__v4: FUSED cov 93% raw1-3 0.7/1.4 ...` (the evaluation floor)
4. Drift: `--poses lidar_modern@0,nonlidar_modern@0 --scales tri,feat`, and `--scales lidar`. Expected: `metric3d2-vits__lidar_modern-0__lidar__v4: FUSED cov 85% raw1-3 1.9/9.8`.
5. Occlusion: `python occlusion.py --models metric3d2-vits,moge2-vits --scale tri`, about 10 min.
   - The port must first reproduce the app baseline: `app today ... claimed 19.29 false_observed 1.12 missed 0.0`.
   - It must also reproduce the laser row: `laser scan (PR #12 true depth) 14.63 / 0.0 / 3.35`.
   - Then expect `metric3d2-vits per-image depth (tri) 14.63 / 0.0 / 3.35`.
6. Tables: `python report.py electro facade > results/tables.md`.

## 4. Parameters that matter
- **Scale mode is the biggest lever.** Fused 1–3 m p90 for Metric3D: raw 16.7 → feat 16.9 → tri 6.0 → oracle 4.2 → lidar 2.7.
  - Use `tri`: per-image median of z_triangulated / z_predicted over SIFT matches with the 6 nearest same-facing views, 2 px reprojection, 2° ray angle, needing ≥20 points.
  - `feat` (ARKit feature points plus the session scale) leaves about ±10% per frame.
- **Flying-pixel filter:** drop a pixel when its 5×5 depth range exceeds 5% of its depth. Keep it; without it edges get worse.
- **TSDF:** Open3D tensor VoxelBlockGrid.
  - Voxel 0.04 m, `trunc_voxel_multiplier` 4, `depth_trunc` 12 m, `weight_threshold` 1.0.
  - `block_resolution` 8 with `block_count` 60000. `block_resolution` 16 with 200000 blocks was OOM-killed at 16 GB.
  - I started a sweep (trunc 8, min weight 2, voxel 0.08, voxel 0.02) but killed it for CPU and it produced no results; the settings are untuned.
- **Occlusion test:**
  - Relief (`hide_abs`) 0.10 m and `HIDE_REL` 0.04 are PR #12 defaults. Keep them for per-image depth.
  - At 0.4 m relief, per-image depth fails: 0.6–1.1 ft false-observed. The fused mesh at 0.4 m passes with 0.07 ft.
  - Depth buffer: 1024 px wide, 5×5 minimum filter, and no depth counts as hidden (unseen means unsure).
- **App CoverageConfig defaults** (HouseScanKit `beede15`):

| Setting | Value |
| --- | --- |
| cellWidth | 0.1524 m |
| wallBandHeight | 1.9812 m |
| groundBandDepth | 1.2 m |
| maxDistance | 6 m |
| maxAngleFromNormal | 65° |
| imageMargin | 0.03 |
| rowsPerBand | 3 |
| coveringBaseline | 0.25 m |

- **Simulated LiDAR:** 0.3–5 m, block median to 256 px wide, noise 1 cm + 0.5%. It covered 41% of electro pixels.

## 5. Dead ends
- **Legacy `o3d.pipelines.integration.ScalableTSDFVolume` in Open3D 0.20** returns 0-vertex meshes for float-metre depth, even though the point cloud built from the same RGBD is fine. Use `o3d.t.geometry.VoxelBlockGrid`.
- **VoxelBlockGrid `extract_triangle_mesh` defaults to `weight_threshold=3`**, which gives an almost empty mesh for a few views. Pass 1.0.
- **Plain TSDF across drifted poses** makes double surfaces: pair coverage falls from 80–94% to 52–71%. Per-image scale does not fix pose error.
- **Fusion adds little over a single frame** with image-only depth: p90 goes from 8.6 to 6.0 in and costs 10–20 points of coverage.
- **Facade** (cameras about 10 m from the wall) with MoGe-2 `tri`: fused scale error +38%, 27.5 / 71 in, while the single photo plus one tape gives 1.3 / 7.7. Not debugged. Suspects: the 12 m depth cut plus floaters, and triangulation over distant views.
- **Harness `score-cloud` on these meshes:**
  - The laser-as-depth floor scores only 7 of 50 key distances, with the anchor off by 0.77 m.
  - The visibility oracle crashed, so it was run with `--no-visibility`.
  - `eth3d/build_gt.py` failed once on a schema change while the harness was being edited (17:00 UTC); a rerun worked.
- **PR #12's tape rule** (a reference pair with a missing end fails every pair it scales) turns p90 into "fail" for any asset with more than about 5% holes. I added a `| predicted` cohort and report coverage separately.
- **Running 4 or more CPU jobs at once** (inference, SfM, eval, GT build) gave load 12–14 and MoGe-L at 160–330 s per image. Timings from those runs are useless.

## 6. Data quirks
- **ETH3D:**
  - The `_dslr_depth` archives match the *distorted* images; don't use them. Render depth from the scan (`recon.Scene.oracle_depth`).
  - COLMAP pixel centres are at 0.5 and OpenCV's at 0; `View.scaled_K` handles this. The harness camera JSON wants intrinsics with (0, 0) at the top-left corner, so pass cx + 0.5.
  - The scan and occluder splats have to be concatenated to act as blockers.
  - The electro wall has pilasters 0.36 m proud, which is why PR #12's 3.35 ft missed exists.
  - The facade ground is only ever seen at 81–84° incidence.
- **Packetgen drift** runs in capture order = DSC number, with resets disabled. The electro "path" is 50 m, and `lidar_modern@0` centre error is 0.36 m median and 0.70 m max.
- **Tanks and Temples Barn:** the store has 137 frames (every 3rd). The log index i maps to `%06d.jpg` of i+1, and GT metric = `Barn_trans.txt @ log pose`. Scale from Metric3D depth is **−10.1%** against the laser-registered poses; the per-frame p10 to p90 spread is −8% to +12%.
- **YouTube houses (pycolmap exhaustive matching, SIMPLE_RADIAL):** SfM fragments. yt-0gotY split into 47/21/11 frames, yt-4h8k into 44/38/33/22/16/6.

## 7. Integration notes for the pipeline
- **Inputs:**
  - Per keyframe: RGB, intrinsics K (OpenCV), camera-to-world pose (convert from the packet's ARKit axes with `diag(1,-1,-1)`).
  - Sparse points for scale: triangulated from matches, or ARKit raw feature points.
  - LiDAR depth when present (it replaces mono depth up to 5 m).
- **Seen/hidden test output:** per keyframe × coverage sample, hidden when predicted depth < sample depth − max(0.10 m, 4% × depth), or when there is no depth. Use this to drop sightings before `CoverageMap.record`, the same form as PR #12's `hidden` rows (see `occlusion.depth_hidden`).
  - Feed the result into `coverage` / `coverage_evidence` per `docs/scene-and-verdict-format.md` §2.6 and §3.1. A probe counts as well observed only if it is not hidden in at least 2 views, with incidence ≤ 75°.
- **Second opinion:** a per-frame metric distance, reported with its scale source (`tri` / `lidar` / `feat`) and the per-frame scale spread. Never report a fused whole-house length under AR poses.
- **Error codes to surface:**
  - `scale_unavailable`: fewer than 20 triangulated points in a frame.
  - `depth_missing`: pixels with no depth.
  - `pose_inconsistent`: fused surface agrees with a frame's own depth within 5% on less than about 70% of its pixels (Barn 70%, YouTube 67–73%).
  - `standoff_exceeded`: distance > 6 m.
- **Degrade gracefully:** where there is no depth, mark the probe hidden or unsure; never mark it seen.

## 8. Open risks
- Everything was run with DSLR photos or 640–1080p video. There is no real iPhone capture and no real ARKit LiDAR; the LiDAR rows are simulated from the laser scan. Cheapest check: one real LiDAR-iPhone packet on a wall with a tape.
- The occlusion result rests on one wall (electro, 19.3 ft), true poses and a 0.10 m relief tuned by PR #12. Check: rerun `occlusion.py` with `--scale feat` and with drifted poses, plus a second wall with a bush.
- Non-Pro iPhones (no LiDAR, `nonlidar_modern` is an assumed profile): `tri` scale depends on pose quality. Under drift the fused p90 is 27–32 in. Check: bundle-adjust the AR poses (A1 prior mode) before fusion and rerun `evaluate.py --poses nonlidar_modern@0`.
- Metric3D weights licence and Depth Pro licence are unverified.
- MoGe-2 ViT-L and Depth Pro were never scored, and there is no delivery_area data.
- The facade +38% failure is not understood.

## 9. What you would do differently
- Run one CPU-heavy job at a time. Contention cost hours and ruined timings.
- Get the harness `score-cloud` floor (laser-as-depth) scoring the key distances before scoring any model: check `depth_trunc`, vertex density and anchor.
- Put a pose refinement (bundle adjustment on the AR poses) in front of fusion from the start, instead of fusing raw drifted poses.
- Replace plain TSDF with a multi-view-consistency filter for edges.
- Test the seen/hidden test under drifted poses and `feat` scale, not only with true poses and `tri`.
