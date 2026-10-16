---
cursor:
  subagentId: "bc-4da717fb-7eba-55dd-869e-6335aa978667"
---

# A3: metric monocular depth + fusion (Phase 1 breadth)

## 1. Verdict

**No-go as a standalone reconstruction under ARKit-like poses. Conditional go as the per-frame depth layer (LiDAR phones, or single-frame measurement with triangulated scale), and go as the occlusion check for coverage.**

- **The 2–6 in bar** comes from C1's error sweep. Only simulated-LiDAR depth reaches it at p90. With true poses the fused 1–3 m pair error is 0.7 / 2.7 in (median / p90). A single frame reaches 0.9 / 3.4 in, which does not depend on the poses.
- **Image-only depth with true poses** and per-image triangulated scale gets the median into the bar: Metric3D v2 ViT-S 1.7 in, UniDepth V2 ViT-S 1.5 in. Its p90 does not: 6.0 in and 9.4 in on all surfaces, 9.5 in and 14.7 in on walls, 43 in at edges.
- **Fusing across ARKit-drifted poses breaks the result**, even with LiDAR depth: 1.9 / 9.8 in and 3.2 / 18.1 in on two drift draws. Image-only fusion under drift gives 4.5–9.3 / 17–35 in. So no depth model plus drifted AR poses meets the bar for multi-view geometry.
- **Occlusion.** A depth test using predicted depth fixes PR #12's coverage over-claim just as well as true depth does: 0.0 ft false-observed, 3.4–3.9 ft missed, against 3.35 ft missed with true depth.

Per model:

| Model | Verdict | Why |
| --- | --- | --- |
| Metric3D v2 ViT-S | **go** (best overall, BSD-2 code) | Best image-only fused p90 (6.0 in with true poses + tri); 2.9 s/img CPU |
| UniDepth V2 ViT-S | no-go for product (CC BY-NC) | Best median (1.5 in), p90 9.4 in; non-commercial weights |
| MoGe-2 ViT-S | second choice | Fused tri 3.0 / 11.9 in; raw scale +9% (worst raw); MIT |
| MoGe-2 ViT-L | not scored | Inference didn't finish on electro under CPU contention (38 of 45 views); 7 s/img alone |
| Depth Pro | needs GPU | 30 s/img, 9.3 GB RSS on CPU; not scored; Apple licence unverified |
| MoGe-3 | needs GPU | Needs FlexGEMM/Triton (CUDA) |

## 2. Inputs (real vs synthetic) and what was produced

- **Real inputs:**
  - ETH3D electro (45 DSLR photos) and facade (76 DSLR photos), with laser scans as truth.
  - Tanks and Temples Barn (137 frames, with poses registered to the laser).
  - Two YouTube walkarounds: yt-0gotY (151 frames) and yt-4h8k (148 frames).
- **Synthetic inputs:**
  - ARKit-like poses: packetgen `apply_drift` with `lidar_modern` and `nonlidar_modern` (the latter is an assumed profile), seeds 0 and 1.
  - Simulated ARKit feature points (`feat`): ETH3D SfM points with the session scale and 2% noise.
  - Simulated LiDAR (`lidar`): the laser scan rendered as depth, limited to 0.3–5 m, block-averaged to 256 px, with 1 cm + 0.5% noise. It covers 41% of electro pixels. Because it comes from the truth, the LiDAR rows are optimistic.
- **Scale modes** per image:
  - `raw`: the model's output as is.
  - `feat`: scaled to the simulated feature points.
  - `tri`: scaled to SIFT points triangulated with the pose mode's poses.
  - `lidar`: scaled to LiDAR, and LiDAR replaces mono depth up to 5 m.
  - `oracle`: scaled to the laser depth (a diagnostic).
- **Produced:**
  - Per-image depth maps at 1024 px.
  - TSDF meshes: Open3D VoxelBlockGrid, 4 cm voxels, truncation 4 voxels, depth cut at 12 m, vertices kept at weight ≥ 1.
  - Coloured house meshes with renders.
  - A Python port of the app's CoverageMap, which reproduces PR #12's Swift results exactly (19.3 / 1.1 / 0.0 ft; true depth 14.6 / 0.0 / 3.35 ft).

## 3. Metrics against ground truth

**PR #12's pair metric** was run on the fused mesh. Every view is used as a seed, and the mesh is raycast from the seed's estimated pose. The table gives median / p90 in inches for points within 6 m, on predicted points only. The "predicted" column is the share of pairs the asset has surface for. Full table: `a3-depth-fusion/results/tables.md`.

| electro, true poses | predicted | fused 1–3 m | fused 3–10 m | walls 1–3 m | edges 1–3 m | single photo 1–3 m | single + 1 tape |
| --- | --- | --- | --- | --- | --- | --- | --- |
| laser scan as depth (floor) | 93% | 0.7 / 1.4 | 1.4 / 2.4 | 0.3 / 0.9 | 0.9 / 22.7 | 0.7 / 1.6 | 0.4 / 1.3 |
| Metric3D ViT-S, lidar (sim.) | 94% | **0.7 / 2.7** | 1.3 / 4.0 | 0.5 / 4.0 | 0.5 / 8.1 | 0.9 / 3.4 | 1.3 / 4.9 |
| Metric3D ViT-S, tri | 82% | **1.7 / 6.0** | 3.7 / 11.5 | 2.0 / 9.5 | 10.4 / 43.8 | 2.3 / 8.6 | 2.2 / 7.9 |
| UniDepth ViT-S, tri | 82% | 1.5 / 9.4 | 2.6 / 14.3 | 1.5 / 14.7 | 12.2 / 42.9 | 1.9 / 10.9 | 1.9 / 7.7 |
| MoGe-2 ViT-S, tri | 80% | 3.0 / 11.9 | 5.9 / 15.1 | 3.0 / 15.0 | 15.6 / 42.9 | 3.1 / 11.8 | 3.2 / 12.0 |
| Metric3D ViT-S, feat | 80% | 4.1 / 16.9 | 8.2 / 28.3 | 3.8 / 19.4 | 13.1 / 43.3 | 4.2 / 17.9 | 2.2 / 7.9 |
| Metric3D ViT-S, raw | 74% | 5.2 / 16.7 | 9.9 / 25.6 | 5.5 / 20.5 | 16.8 / 46.4 | 5.1 / 16.9 | 2.2 / 7.9 |
| Metric3D ViT-S, oracle scale | 85% | 1.3 / 4.2 | 2.5 / 8.6 | 1.3 / 8.2 | 10.5 / 53.5 | 1.8 / 6.5 | – |

With drifted poses (`<profile>@<seed>`), on electro (Metric3D ViT-S; UniDepth behaves the same):

| poses | scale | predicted | fused 1–3 m | fused 3–10 m | single + 1 tape |
| --- | --- | --- | --- | --- | --- |
| lidar_modern@0 | lidar (sim.) | 85% | 1.9 / 9.8 | 4.1 / 24.0 (taped) | 1.3 / 4.9 |
| lidar_modern@1 | lidar (sim.) | 64% | 3.2 / 18.1 | – | 1.3 / 4.9 |
| lidar_modern@0 / @1 | tri | 71% / 58% | 4.5 / 17.1 · 5.8 / 21.1 | 7.5 / 23.2 · 11.0 / 28.3 | 2.2 / 7.6 |
| nonlidar_modern@0 / @1 | tri | 56% / 52% | 7.4 / 27.2 · 9.3 / 31.9 | 11.6 / 33.8 · 14.9 / 45.3 | 2.2 / 7.6 |
| lidar_modern@0 / nonlidar_modern@0 | feat | 68% / 71% | 5.8 / 20.5 · 7.6 / 22.1 | 11.0 / 29.6 · 12.5 / 33.1 | 2.2 / 7.9 |

Findings:

- **Scale:**
  - Raw metric scale is +9% for MoGe-2, +13% for UniDepth and +0.5% for Metric3D on electro.
  - `tri` with true poses brings the fused scale error within ±3%.
  - On Barn (real, laser-registered poses), Metric3D's depth-derived global scale is **−10.1%**, and the per-frame spread (p10 to p90) runs from −8% to +12%.
- **Fusion adds little over a single photo.** With a consistent scale it trims the p90 (oracle scale: from 6.5 to 4.2 in). It costs 10–20 points of pair coverage through TSDF holes and floaters, and edges stay above 40 in p90 in every image-only row.
- **Surface accuracy / completeness** (share within 10 cm):
  - Metric3D tri, true poses: 52% / 67%.
  - lidar_modern@0 tri: 33% / 48%.
  - Laser floor: 100% / 100%.
- **Facade (10 m standoff).** MoGe-2 tri gives a fused scale error of +38% and 27.5 / 71 in, while the single photo with one tape gives 1.3 / 7.7 in. Per-image triangulated scale plus the TSDF fails when the photos are far from the wall. This was not debugged in time.
- **Shared scorer** (`score-cloud --metric`, eth3d-facade, results in `internal/eval-harness/results/eth3d-facade/A3-*`):
  - Laser-as-depth floor through my fusion: 7 key distances scored, 43 missing, median 3.8 in, anchor off by 0.77 m.
  - MoGe-2 tri: 6 scored, 45 missing, median 9.1 / p90 14.6 in; 17% within 2 in, 33% within 6 in.
  - Because even the floor loses most key distances, these numbers measure the 12 m depth cut and the extractor's fit on my meshes, not depth quality. They are **not** a fair headline.
  - The visibility oracle crashed on these clouds, so they were run with `--no-visibility`. As a result there are no coverage-honesty or §3.1 numbers from the harness for A3.

## 4. Occlusion / coverage (electro wall, 19.3 ft)

This is PR #12's truth with the app's rule ported to Python, true poses and `tri` depth. Relief means how much nearer than the wall surface still counts as wall.

| Option | Claimed ft | False-observed ft | Missed ft |
| --- | --- | --- | --- |
| App today (frustum only) | 19.3 | **1.1** (fails) | 0.0 |
| Depth test, laser depth, 0.10 m relief (PR #12) | 14.6 | 0.0 | 3.35 |
| Depth test, laser, 0.4 m relief | 18.1 | 0.07 | 0.0 |
| Depth test, per-image Metric3D / MoGe-2 / UniDepth, 0.10 m | 14.6 / 14.6 / 14.1 | **0.0** | 3.35 / 3.4 / 3.9 |
| same, 0.4 m relief | 19.3 / 18.8 / 18.2 | 1.1 / 0.7 / 0.6 (fail) | 0 / 0 / 0.5 |
| Depth test, fused TSDF mesh (Metric3D / UniDepth), 0.4 m | 17.65 | **0.07** | 0.46 |

- At the strict 0.10 m relief, predicted depth is as good an occluder test as LiDAR.
- Allowing pilaster relief with single-image depth lets the footings back in. The fused mesh does not.
- All of this was run with true poses.

## 5. Runtime and cost per house (4 vCPU, no GPU)

- **Depth, seconds per image measured alone:** Metric3D ViT-S 2.9, UniDepth ViT-S 1.5, MoGe-2 ViT-S 2–3.4, MoGe-2 ViT-L 7, Depth Pro 30 (9.3 GB).
- **Other steps:**
  - TSDF of 45–137 frames: 1–3 s.
  - `tri` scale: SIFT plus triangulation, about 1–2 min for 45 views.
  - Evaluation: 1–3 min per run.
- **Per house, 60–140 frames, small model:** about 3–7 min of depth, plus SfM if there are no AR poses. SfM took 5.3 min for Barn's 137 frames at 1024 px (exhaustive matching dominates).
- **Depth Pro** would take about 30–70 min per house on CPU; the other models are CPU-feasible.
- **Online houses** (robustness only):

| House | SfM result | Per-frame scale spread (p10 to p90) | Fused mesh agrees with each frame's own depth (within 5%) |
| --- | --- | --- | --- |
| Barn | 137/137 frames registered | −8% to +12% | 70% |
| yt-0gotY (640×480) | Split into 3 fragments (47/21/11 frames) | −16% to +20% | 73% |
| yt-4h8k | Split into 6 fragments (largest 44 frames) | −13% to +14% | 67% |

Renders for these houses are under `results/houses/*/`.

## 6. Failure modes

1. **Pose drift dominates fusion.** Centre errors of 0.3–0.6 m (lidar_modern) or 0.6–1.2 m (nonlidar_modern) over a 50 m path create double surfaces and TSDF cancellation, and pair coverage falls to 52–71%. With the same depth on true poses, coverage is 80–94%.
2. **Per-image scale noise.** Raw metric scale is off by 5–13%, and `feat` scale leaves ±10% per frame. Without triangulation or LiDAR, fused surfaces disagree by more than the TSDF truncation.
3. **Edges** stay at 38–60 in p90 in every image-only row, from flying pixels and smoothing at depth discontinuities. Clearance edges (frames, equipment) are exactly the weak spot.
4. **Standoff.** Beyond about 6–8 m (facade), triangulated scale and fusion break (+38% scale error).
5. **Video without AR poses.** SfM fragments on YouTube walkarounds (3–6 models), so a whole-house loop is not guaranteed.

## 7. Licence

| Model | Licence | Commercial use |
| --- | --- | --- |
| MoGe-2 | Code MIT; DINOv2 parts Apache-2.0 | Yes |
| Metric3D v2 | Code BSD-2 | Yes for the code; weights licence to verify |
| UniDepth V2 | CC BY-NC 4.0 | No |
| Depth Pro | Apple custom licence | Unverified |

All data used is evaluation-only: ETH3D is CC BY-NC-SA, Tanks and Temples CC BY 4.0, and the YouTube walkarounds are internal use only.

## 8. Recommendation for the depth phase / going deeper

- **Use Metric3D v2 (or MoGe-2 for licence safety) per frame**, scaled to triangulated or LiDAR points. Measure within a frame or a small group of nearby frames, not on a whole-house TSDF. Single photo plus one tape: 2.2 / 7.9 in. LiDAR frame: 0.9 / 3.4 in.
- **Fuse only after pose refinement.** Bundle-adjust the AR poses on SIFT or learned matches first (A1's `arkit` prior mode), then TSDF. With true poses the fused median is already inside the bar.
- **Use predicted-depth occlusion now** for the coverage map at 0.10 m relief, or 0.4 m with the fused mesh.
- **Next experiments:**
  - LiDAR fusion with refined poses.
  - A multi-view consistency filter instead of plain TSDF, to fix edges.
  - A GPU run of MoGe-2 ViT-L / Depth Pro / MoGe-3.
  - Fixing the depth cut and harness fit so `score-cloud` scores the key distances; the floor currently loses 43 of 50.
  - Debugging the facade `tri` failure.

Code: `internal/breadth/a3-depth-fusion/`:
- `infer.py`: per-image depth.
- `poses.py`: pose modes and scale helpers.
- `fuse.py`: scaling and TSDF fusion.
- `evaluate.py`: pair metric, surface accuracy and completeness.
- `occlusion.py`: the coverage-rule port and depth tests.
- `harness.py`: export to `score-cloud`.
- `houses.py`: SfM, depth and fusion on online houses.
- `report.py`: result tables.
- `vm-setup/`: setup and job scripts.

Small results are under `results/`: eval JSONs, occlusion, houses with renders, logs, and `tables.md`. Large meshes and depth maps are only on the VM, under `~/a3/work`.
