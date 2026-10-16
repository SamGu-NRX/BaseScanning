---
cursor:
  subagentId: "bc-daa6b400-6fd7-514e-837d-95d1771fa424"
---

# A1: classical reconstruction (COLMAP / GLOMAP + OpenMVS), breadth result

## 1. Verdict

**Go (conditional).** Incremental COLMAP SfM with the phone's ARKit positions used as **soft pose priors** meets the 2–6 in bar. On the facade's observed wall, all 31 scored key distances fall within 2.7 in (LiDAR drift profile) and within 5.2 in (non-LiDAR profile). Two conditions apply:

- **Scale.** Metric scale comes from ARKit. Its per-session scale bias (0.7–1.0 % median, 2–4 % p90 with LiDAR) gives about 3 in median and 10–12 in p90 on distances 26 ft from the meter. That fails the bar at long range unless a scale reference is added.
- **Coverage.** The walls seen edge-on (w2, w3), the ground strip and the door are not recovered, so 19 of 50 key distances are missing.

**No-go** for these, each measured:

- trusting fixed ARKit poses (median 4.8–12 in);
- GLOMAP's global mapper (wrong poses on 2 of 3 scenes);
- OpenMVS dense as the measurement cloud (it improves surface completeness but breaks the chain extraction; AGPL; about 20 min of CPU per house);
- image-only SfM on online walkaround videos (every one fragments into 4 models).

## 2. Inputs (real vs synthetic) and outputs

| Input | Real / synthetic | Use |
|---|---|---|
| ETH3D `facade` (76 DSLR images at 2048 px, laser scan) | **real** images and laser | primary accuracy test, the only site with a shared-scorer GT scene |
| ETH3D `electro` (45 images), `delivery_area` (44 images) | **real** | pose accuracy, ETH3D F1 (dense did not finish in time) |
| ARKit poses | **synthetic noise on real GT poses**: packet-generator drift profiles `lidar_modern` and `nonlidar_modern` (assumed, not measured), seed 0 for the SfM runs; 50 seeds × 3 profiles for the scale study | pose mode 2 |
| Online walkarounds `yt-0gotY_syWWU` (640×480), `yt-4h8kyjnPo9s` (1280×720), `yt-5-M-7f35Tyg` (360×480) | **real**, no GT | robustness only |

Pose modes, all with known pinhole intrinsics held fixed:

1. No poses: `incremental` (COLMAP) and `global` (GLOMAP inside COLMAP 4.2). Scale-free, so scored shape-only with a similarity alignment.
2. ARKit-like poses, in two variants:
   - `fixed`: triangulate with the noisy poses;
   - `prior`: incremental SfM with ARKit positions as priors (σ 0.3 m, robust loss). Metric; scored with a rigid alignment, so its scale error stays in every number.
3. Ground-truth poses (`gt`): triangulate only; the upper bound.

Dense: OpenMVS v2.3 `DensifyPointCloud` on CPU with 1024 px depth maps, run for facade `gt`, `incremental` and `arkit_lidar_fixed`. I stopped `arkit_lidar_prior` at the wrap-up (about 60 % done). COLMAP's patch-match stereo is CUDA-only.

## 3. Metrics against ground truth

### 3.1 Headline: shared geometry scorer (`evalharness.cli score-cloud`), ETH3D facade, sparse clouds

Inputs to the scorer were our cloud plus our COLMAP cameras. It runs the reference extractor with perfect GT meter taps and opening clicks, so these errors are reconstruction error only. The full rows (also dense, completeness and coverage) are in `a1-classical/results/summary.md`; raw scorer output is under `internal/eval-harness/results/eth3d-facade/A1-colmap/<run>_<sparse|dense>/`.

| Mode | KDs scored / missing | \|err\| median / p90 / max (in) | ≤2 / ≤4 / ≤6 in | Calibration | Scale err | Anchor (ICP) |
|---|---|---|---|---|---|---|
| **2b ARKit prior, LiDAR** | 31 / 19 | **0.84 / 2.06 / 2.66** | 87 / 100 / 100 % | 100 % | +0.29 % | 7 cm |
| **2b ARKit prior, non-LiDAR** | 30 / 20 | **0.84 / 2.90 / 5.20** | 87 / 93 / 100 % | 100 % | −1.17 % | 7 cm |
| 1 no poses, incremental (shape only) | 36 / 14 | 0.60 / 24.9 / 172 | 69 / 81 / 86 % | 83 % | similarity-aligned | 9 cm |
| 3 GT poses | 36 / 14 | 1.55 / 24.4 / 172 | 61 / 75 / 83 % | 89 % | 0 | 10 cm |
| 2a fixed ARKit, LiDAR | 18 / 32 | 4.77 / 25.7 / 46.8 | 22 / 33 / 56 % | 72 % | +0.36 % | 29 cm |
| 2a fixed ARKit, non-LiDAR | 6 / 44 | 12.1 / 28.1 / 32.3 | 17 / 33 / 50 % | 50 % | −1.31 % | 40 cm |
| 1 no poses, GLOMAP | 19 / 31 | 19.9 / 40.3 / 46.9 | 0 / 5 / 10 % | 37 % | similarity-aligned | 86 cm |

**Against the 2–6 in bar.** Only the ARKit-prior mode is inside the bar on every scored distance: meter height, window edges, sills, heads, widths and the gaps between openings, all on w1.

The large tails in the GT-pose and no-pose rows come from the door on w3. That wall is seen almost edge-on, the extractor places its edges 1–4 m off, and the scorer grades those elements "grazing". On truly observed elements only (the scorer's §3.1 coverage rule), no-pose incremental gets 0.67 / 7.0 in (median / p90) and GT poses 1.76 / 7.6 in. In the prior runs the door simply goes missing instead of wrong.

Every run has **zero false-observed key-distance claims**, and false-observed-by-rule is ≤ 0.6 % on the wall face (3.3 % for GLOMAP).

What the rows cannot show: 19–20 key distances are missing in every good mode. These are both corners, the w2 and w3 lengths, the door, and the facing and overhead stations. The sparse cloud has about 15 points on w2 (the photos show it edge-on), and the courtyard ground is only ever seen at 81–84°.

### 3.2 Scale from ARKit (50 seeds per profile, no SfM rerun; `results/facade/scores/scale_study_incremental_{name,tour}.json`)

The no-pose SfM model was similarity-fitted to the ARKit-noisy camera centres (largest tracking epoch; resets allowed). The table uses the walk-order variant ("tour", 104 m path); filename order (161 m) gives the same picture.

| Profile | ARKit odometry scale err, median / p90 | SfM + ARKit fit scale err, median / p90 | → error on a 26 ft distance, median / p90 |
|---|---|---|---|
| lidar_modern | 0.7 / 2.1 % | 1.0 / 3.9 % | 3.0 / 12.2 in |
| nonlidar_modern | 2.6 / 5.1 % | 2.4 / 5.6 % | 7.6 / 17.6 in |
| nonlidar_2018 | 6.3 / 16.7 % | 7.0 / 17.5 % | 22 / 55 in |

SfM removes ARKit's drift and jitter (camera-shape error drops to 0.3–1.7 cm RMS in every scene) but inherits the session's scale bias. So the seed-0 rows in §3.1 are one draw, with a LiDAR scale error of +0.29 %, which is better than median.

For distances of 2 m or less (opening widths, sills, gaps) the scale term stays at ≤ 2.5 in even at LiDAR p90. For corner positions and wall lengths out to 26 ft it does not. These numbers inherit the packet generator's scale model, and the non-LiDAR profile is assumed, not measured.

### 3.3 Dense (OpenMVS) through the shared scorer, facade

| Mode | KDs scored / missing | median / p90 / max (in) | Wall face ≤ 10 cm | Ground strip ≤ 10 cm | Overhead ≤ 10 cm |
|---|---|---|---|---|---|
| GT poses, dense | 21 / 29 | 1.31 / 677 / 933 | 63 % (sparse 48 %) | 15 % (sparse 0 %) | 45 % (sparse 18 %) |
| No poses, dense | 33 / 16 | 1.15 / 34 / 741 | 65 % (sparse 47 %) | 16 % (sparse 0 %) | 84 % (sparse 13 %) |

Dense MVS raises surface completeness everywhere but **degrades the extracted chain**:

- w1's lower band is patchy (28 of 63 quarter-metre bins on the wall plane are empty);
- 21 % of its wall-band points sit 0.3–1 m behind the wall (virtual depth in window glass);
- the reference extractor then splits w1 at 6–7 m and invents corners at −134° and −46°.

This comes from dense clutter meeting an extractor tuned on sparse clouds, not from a failure of the pose modes. I stopped the dense `arkit_lidar_prior` run at the wrap-up, so it has no row. Dense `arkit_lidar_fixed` crashed inside the shared scorer (`AttributeError: 'float' object has no attribute 'min'`, `best_fp = fp.min(1)`); I did not score it.

**Extra (my own, quick; not comparable across paths).** Official ETH3D multi-view evaluator on the full facade dense cloud:

| Run | F1 @ 2 / 5 / 10 cm | Accuracy @ 5 cm | Completeness @ 5 cm |
|---|---|---|---|
| GT poses | 0.40 / 0.63 / 0.74 | 0.83 | 0.51 |
| No poses (similarity-aligned) | 0.45 / 0.65 / 0.73 | 0.85 | 0.52 |
| Fixed ARKit poses, LiDAR (rigid-aligned) | 0.06 / 0.18 / 0.32 | n/a | n/a |

Laser wall surfaces: recon accuracy 0.8 in median and 4.9–6.5 in p90; 70–73 % of wall points have a recon point within 5 cm. Laser edge cohort (jambs, sills, corners): 77 % completeness at 5 cm, accuracy p90 4.5–5.8 in.

### 3.4 Pose accuracy on all three ETH3D scenes (camera centres vs GT)

| Scene | incremental (similarity RMS) | ARKit prior LiDAR / non-LiDAR (rigid RMS, scale err) | fixed ARKit LiDAR / non-LiDAR (RMS) | GLOMAP |
|---|---|---|---|---|
| facade (76) | 1.7 cm, 76/76 registered | 3.3 cm (+0.3 %) / 11.8 cm (−1.2 %) | 27 / 48 cm | **4.0 m, failed** |
| electro (45) | 0.3 cm, **41/45** registered | 6.6 cm (+2.6 %) / 5.7 cm (+2.2 %) | 22 / 34 cm | **2.5 m, failed** |
| delivery_area (44) | 0.4 cm, 44/44 registered | 2.1 cm (−0.6 %) / 7.5 cm (−2.3 %) | 17 / 29 cm | 0.4 cm, ok |

Electro and delivery_area have no shared-scorer GT scene, and their dense runs were cut at the wrap-up, so they give pose evidence only.

### 3.5 Online walkarounds (robustness, real, no GT)

| House | Registered (largest model) | Models | Dense |
|---|---|---|---|
| yt-0gotY_syWWU, 640×480 at 1.2 fps, full loop | 33 / 151 | 4 (33, 22, 21, 11) | 456 k points: one outside corner, two walls |
| yt-5-M-7f35Tyg, 360×480 at 1.5 fps, side and back wall | 74 / 150 | 4 (74, 19, 18, 15) | 839 k points |
| yt-4h8kyjnPo9s, 1280×720 at 1.7 fps, brick | 43 / 148 | several | OpenMVS crashed on this model at full and half resolution (corrupted 262 TB allocation) |

Image-only SfM does not hold a whole-house loop from low-rate video. The ARKit-prior mode is exactly what would glue these fragments, but these videos have no ARKit poses, so that is untested.

## 4. Runtime and cost per house (4 vCPU, 15 GB, no GPU)

Timings are for the facade (76 images at 2048 px); the VM was running 2–3 jobs at once, so standalone times are lower.

| Stage | Time |
|---|---|
| SIFT extraction + exhaustive matching | 110 s + 156 s (ARKit positions would allow spatial matching instead) |
| Incremental SfM / ARKit-prior SfM / fixed-pose triangulation | 181 s / 95–107 s / 11–16 s |
| OpenMVS dense (1024 px depth maps, with the geometric-consistency pass) | 22.6 min contended (about 12–15 min estimated alone) |
| Shared scorer | about 1 min sparse, about 5 min dense |

A sparse ARKit-prior result takes about 6 min of CPU per house (about $0.02 on a 4-vCPU instance at about $0.17/h). Adding dense brings it to about 20–25 min (about $0.07).

A GPU would change the dense stage most: COLMAP's CUDA patch-match (BSD) takes about 1–2 min, and GPU SIFT and matching cut the front end to under 1 min.

## 5. Failure modes (all observed)

1. **Fixed ARKit poses**: 17–48 cm camera error puts triangulated points on the wrong side of the jambs; 4.8–12 in median on facade KDs, most walls lost.
2. **ARKit scale bias** is the floor on long distances (§3.2); SfM cannot fix it.
3. **Edge-on walls and a distant ground** (w2 seen at about 78°, ground at 81–84°) have too few points for any mode; KDs go missing (honest) or the door gets misplaced by 1–4 m (in non-prior modes, graded grazing).
4. **GLOMAP (COLMAP 4.2, CPU)**: low reprojection error (0.34 px) but folded global positions on 2 of 3 scenes.
5. **Dense MVS clutter**: window glass produces virtual depth behind the wall and the lower band is patchy; the shared extractor splits walls and invents corners (§3.3).
6. **Online video**: every walkaround fragments into 4 models; one crashed OpenMVS v2.3.
7. Environment: OpenMVS v2.3 aborts under glibc fortify (`Util::formatTime` snprintf size). I patched it in `setup.sh`. COLMAP's dense stereo is CUDA-only.

## 6. Licence

- COLMAP, pycolmap and GLOMAP (merged into COLMAP 4): BSD-3, commercial use fine.
- OpenMVS: **AGPL-3.0**. Commercial use is possible, but serving it over a network triggers source disclosure. Prefer COLMAP's CUDA stereo (BSD) or a learned-depth path.
- ETH3D data: CC BY-NC-SA 4.0 (evaluation only).
- Online videos: YouTube standard licence (internal evaluation only).

## 7. What going deeper would take (recommendation for the depth phase)

Build next, in order:

1. **The server pose stage: pycolmap incremental SfM with ARKit priors.** Use positions as priors plus gravity. Treat tracking-reset epochs as separate prior groups. Use spatial matching from the ARKit positions instead of exhaustive matching. This is the only mode that met the bar, and it also removes the fragmentation seen on video. It needs real phone captures (packets with actual ARKit poses) to confirm, because all ARKit noise here is simulated.
2. **A scale reference.** Options: LiDAR depth when present, a tap-to-tap tape distance, or a known object such as the meter socket. Declare it via `scale_source` / `scale_reference_ids`. ARKit's session scale bias alone gives 3–12 in at 26 ft with LiDAR and 8–18 in without. The packet generator's scale model also needs validating against real phones.
3. **Dense only for completeness, and only after filtering.** Ground strip, overheads and far walls need dense geometry, but sparse is the more reliable measurement cloud today. Try COLMAP's CUDA patch-match on a GPU box, or A3-style learned depth fused on the SfM poses. Either way, filter points behind fitted wall planes (window glass). Separately, the reference extractor should be made robust to dense clouds; that is for the harness owner (§3.3).
4. **Capture guidance.** Edge-on walls and the distant ground stay unobserved for any backend. The capture flow must ask for head-on views of each wall and the ground strip; the harness's coverage rule already flags this correctly.

## Files

- Code (store): `internal/breadth/a1-classical/`:
  - pipeline: `setup.sh` (VM rebuild, including the OpenMVS patch), `prepare.py`, `sfm.py`, `densify.py`, `online.py`;
  - scoring: `hscore.py` (shared scorer wrapper), `score.py` (ETH3D evaluator extras), `scale_study.py`, `summarize.py`;
  - drivers: `run_scene.sh`, `watch_score.sh`, `collect.sh`.
- Small results (store): `internal/breadth/a1-classical/results/`:
  - per scene: run JSONs, timings, scale studies, extras, and light COLMAP models for incremental and prior;
  - online-house reports and plan images;
  - `summary.md` (the full shared-scorer table).
- Shared-scorer outputs: `internal/eval-harness/results/eth3d-facade/A1-colmap/`. Dense `points.ply` files over 20 MB were moved to the VM (`~/a1/work/facade/scores/`), with a pointer file left in place.
- VM only: ETH3D data, dense clouds, OpenMVS workspaces, the GT scene build (`~/a1/facade-gt`); all regenerate with `setup.sh`, `prepare.py`, `run_scene.sh` and harness `build_gt.py`.
