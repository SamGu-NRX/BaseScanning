---
cursor:
  subagentId: "bc-523191a1-af98-5e0a-a32c-f2416a3650b1"
---

# A6: real iPhone-class imagery with real ARKit poses and laser truth (A1 and A2 re-tested)

Scope: item 1 only (real phone imagery with ground truth). The ETH3D degradation sweep and scale recovery without LiDAR belong to the two parallel agents. Code and raw results: `internal/breadth/a6-iphone-quality/`.

## 1. Verdict

**Conditional go for non-LiDAR iPhones: go on short distances, no-go on 15–26 ft distances unless a scale reference is added.** On real phone frames with the device's real ARKit poses:

- **Openings and gaps (0.5–2 m).** The best path, COLMAP with *tight* ARKit priors, is inside the 2–6 in bar: 0.9–1.9 in median and 2.8–4.2 in p90 on every segment, including the sessions recorded without LiDAR depth.
- **15–26 ft from the meter.** No path meets the bar using ARKit's own scale. The best path gets 2.9–6.9 in median and 7.4–12.4 in p90. On the no-depth February session, ARKit's scale was +2.6% and that alone costs about 5 in at 20 ft. With the scale corrected (oracle), those segments drop to 2.4–2.8 in median and 5.6–7.4 in p90, so **scale is the main error** and a scale reference is the gating item.
- **A1 recipe change.** The ETH3D-tuned recipe (priors with σ 0.3 m and robust loss) bends on real street walks (76 in p90 on one segment), and so does image-only SfM (breaks on 3 of 5 segments). **Use σ ≈ 3 cm, no robust loss** (effectively "trust ARKit locally, let bundle adjustment fix jitter").
- **A2 (π³ / DA3-Large) is behind A1 on real phone frames**, unlike on ETH3D. π³ + ARKit fit gets 5.4 in median / 13.4 in p90 on 15–26 ft (oracle scale: 3.8 / 10.6). DA3 fed ARKit poses gets 5.5 / 16.9 in. DA3 images-only failed one window outright (+45% scale).

**Caveat on "non-LiDAR".** Every public phone dataset with laser truth and real ARKit was captured on LiDAR-equipped Apple devices. LaMAR's phones are iPad Pro/iPhone-class with LiDAR hardware. The closest proxy is sessions recorded **without scene depth** (8 of 43 sessions). Their ARKit baselines are 2× worse (below), and 3 of my 5 reconstruction segments come from them. A true non-LiDAR iPhone (ADVIO, iPhone 6s) could not be scored at inch level because its ground truth is too noisy (§3.4).

## 2. Inputs (all real) and outputs

**Dataset used: LaMAR, LIN scene** (ETH Zürich / Microsoft, ECCV 2022). This is an outdoor old-town street in Zürich, lined with building facades.

| Item | What it is |
|---|---|
| Phone frames | `ARFrame.capturedImage` at 1440 × 1920 (portrait), JPEG, about 64° FOV, rolling shutter, autofocus, 10 fps. This is exactly the packet spec's keyframe format (1920 × 1440 wide camera). |
| ARKit | Real per-frame ARKit poses (`trajectories.txt`) and per-frame ARKit intrinsics (`sensors.txt`), 43 sessions of 1–10 min, June 2021 to June 2022, day and night. |
| Ground-truth poses | Laser-registered, from LaMAR's GT pipeline (COLMAP subset, 13,398 iOS frames). Pose σ from their covariances: 1.0 cm median, 2.2 cm p90. |
| Laser | NavVis M6/VLX scans. I used LaMAR's depth maps rendered from the laser mesh at the NavVis cameras, back-projected into a 2 cm voxel cloud per segment (7–11 M points), then z-buffered into each phone GT camera. |
| Check of the GT chain | Laser depth vs the iPad's own high-confidence LiDAR depth within 5 m: **1.7 cm median, 5.9–7.8 cm p90**, which is the LiDAR's own error plus pose error. The chain is good to about 2 cm. |
| Licence | CC BY-SA 4.0 (commercial use allowed, share-alike on derived data). |

**Test segments.** Five segments of about 35 m of walking each (roughly one house side plus corners), from three sessions:

| Segment | Session conditions | ARKit whole-session scale |
|---|---|---|
| `2022-06-17_s0`, `2022-06-17_s1` | June morning, depth recorded | −0.6 % |
| `2022-02-10_s0`, `2022-02-10_s1` | February afternoon, **no depth**, the worst ARKit session | +4.3 % |
| `2021-06-02_s0` | June 2021, **no depth** | −1.6 % |

**Input frames.** Each segment uses the packet rule (a keyframe every 0.5 m or 15° of ARKit motion) plus all GT-posed frames. That gives 87–205 frames per segment, about 0.3 m apart. All frames are at full 1440 × 1920 for A1 and 378 × 504 for A2.

**Methods:**

- **A1** (pycolmap 4.2, A1's code path): SIFT 8k, spatial + sequential matching, device ARKit intrinsics fixed. Modes:
  - `prior`: A1 recipe, σ 0.3 m, robust loss;
  - `prior_s03`: σ 3 cm, no robust loss;
  - `prior_s10`: σ 10 cm (first segment only);
  - `fixed`: triangulate with ARKit poses;
  - `noprior`: image-only SfM, then a similarity fit to the ARKit cameras;
  - `gt`: triangulate with laser-registered poses, the scorer floor.
- **A2** (A2's `Pi3` and `DA3` adapters, unchanged, at 378 × 504): 16-view windows, 2 per segment (10 windows).
  - π³: images only, scaled by a similarity fit of its cameras to the real ARKit cameras.
  - DA3-Large `KT`: fed the ARKit poses and intrinsics.
  - DA3-Large `img`: images only, plus the same similarity fit.

**Scorer.** The shared `score-cloud` needs an ETH3D GT scene (wall chain, openings), which doesn't exist for LaMAR, so I used an alignment-free distance scorer:

- Every reconstructed point keeps its GT-view pixel. Its laser twin is that pixel lifted with the GT pose and the laser depth.
- The error of a distance is |P_i − P_j| − |Q_i − Q_j|, binned by true length: 0.5–2 m (openings), and 4.57–7.92 m (15–26 ft).
- Points within 10 m of the camera only.
- **Patch pairs** (headline) average points in 25 cm laser patches first, which mimics KDs fitted to surfaces. Raw point pairs are also in the tables.
- Gross local outliers (people, cars, vegetation, sky against the laser) are dropped by a similarity fit per 4 m cell, so large-scale bending still counts as error.
- "ARKit scale" means the metric the phone gives. "Oracle" rescales by the similarity fit of the cameras to GT (the shape-only upper bound).

## 3. Metrics against ground truth

### 3.1 Real ARKit alone: 15–26 ft baselines between camera positions (all 43 LIN sessions, no reconstruction)

Pairs of frames whose true positions are 15–26 ft apart and at most 60 s apart. The error is ARKit distance minus laser-registered distance.

| Sessions | Pairs | \|err\| median / p90 | ≤ 4 in / ≤ 6 in |
|---|---|---|---|
| All 43 | 61,020 | **3.6 / 12.6 in** | 54 / 70 % |
| 35 with scene depth recorded | 53,631 | 3.3 / 11.0 in | 58 / 74 % |
| **8 without depth** (non-LiDAR proxy) | 7,389 | **7.4 / 18.9 in** | 28 / 41 % |
| All, frames ≤ 15 s apart | 53,014 | 3.5 / 12.2 in | 55 / 71 % |

- **Whole-session similarity scale error:** 1.2 % median and 2.8 % p90 in absolute value, with a range of −2.9 to +4.3 %.
- **30 m sub-window scale error:** 0.5–3.6 % per session.

This is the first measured (not simulated) ARKit number in the project. It sits between the packet generator's `lidar_modern` profile (0.7 / 2.1 % median / p90) and its `nonlidar_modern` profile (2.6 / 5.1 %). The no-depth sessions match `nonlidar_modern`. `results/arkit_scale_lamar_lin.json`, `arkit_scale_lamar_split.json`.

### 3.2 A1 and A2 on real phone frames: pooled over 5 segments (≤ 10 m from the camera)

| Path | 15–26 ft patches, ARKit scale: median / p90 (worst segment p90) | ≤ 4 / ≤ 6 in | 15–26 ft, oracle scale | Openings 0.5–2 m, points: median / p90 |
|---|---|---|---|---|
| GT-pose triangulation (scorer floor) | 1.7 / 5.4 (6.4) | 82 / 92 % | same | 1.1 / 4.3 |
| **A1 tight prior σ 3 cm** | **3.8 / 11.3 (12.4)** | 43 / 62 % | 3.0 / 7.4 | **1.3 / 4.6** |
| A1 fixed ARKit poses | 5.0 / 12.3 (18.5) | 44 / 59 % | 3.7 / 11.2 | 2.2 / 7.3 |
| A1 recipe (σ 0.3 m robust) | 4.1 / 13.1 (**76.4**) | 46 / 65 % | 4.3 / 14.2 | 1.3 / 5.3 |
| A1 image-only + ARKit fit | 4.6 / 57.2 (134.6) | 30 / 40 % | 6.3 / 62.3 | 1.5 / 14.3 |
| A2 π³ + ARKit fit | 5.4 / 13.4 (21.2) | 38 / 52 % | 3.8 / 10.6 | 1.8 / 5.3 |
| A2 DA3-L fed ARKit poses | 5.5 / 16.9 (24.7) | 35 / 49 % | 4.8 / 13.6 | 2.3 / 6.4 |
| A2 DA3-L images + ARKit fit | 7.7 / 25.1 (136.8) | 29 / 40 % | 7.9 / 20.5 | 2.7 / 8.1 |

Cells are medians over segments (A1) or windows (A2).

- **Within 6 m of the camera:** A1 tight prior 3.4 / 10.1 in (oracle 3.1 / 6.4); π³ 4.3 / 11.5 in; floor 1.4 / 4.2 in.
- **The scorer floor matters.** Even with laser-registered poses, sparse-point distances have a 4.2–6.4 in p90 at 15–26 ft. The p90 bar can't be certified below about 5 in with this scorer. Read each path's p90 as "floor + path error".

Full tables: `a6-iphone-quality/results/rescored_table.md` (every mode × range × scale variant) and `per_segment_table.md`.

### 3.3 Per segment (15–26 ft patches, ≤ 10 m; ARKit scale → oracle scale; long-pair scale error)

| Segment | A1 tight prior | A1 fixed ARKit | π³ + ARKit | DA3 fed ARKit | Floor |
|---|---|---|---|---|---|
| 06-17 s0 (depth) | 3.2 / 7.4 → 3.0 / 6.6 (−1.2 %) | 3.4 / 9.9 | 3.8 / 10.5 | 7.3 / 20.3 | 1.4 / 4.6 |
| 06-17 s1 (depth) | 3.8 / 12.3 → 3.6 / 13.3 (+0.7 %) | 3.4 / 10.4 | 8.3 / 17.3 | 4.5 / 18.4 | 1.7 / 5.9 |
| 02-10 s0 (no depth) | 6.3 / 11.2 → **2.8 / 7.4** (+2.6 %) | 5.6 / 12.3 | 5.8 / 16.3 | 6.5 / 15.0 | 1.6 / 5.3 |
| 02-10 s1 (no depth) | 6.9 / 11.3 → **2.4 / 5.6** (+2.7 %) | 8.1 / 18.5 | 7.7 / 15.3 | 11.2 / 21.4 | 1.9 / 5.4 |
| 2021-06-02 s0 (no depth) | 2.9 / 12.4 → 3.7 / 12.6 (−0.4 %) | 5.0 / 14.5 | 4.8 / 10.8 | 3.5 / 10.4 | 1.7 / 6.4 |

Openings (0.5–2 m patches), A1 tight prior: 0.9 / 2.8, 1.0 / 4.1, 1.6 / 3.9, 1.9 / 4.2 and 1.0 / 3.9 in, all inside the bar.

The recon's camera scale against GT (6–20 cm camera RMS; 67 of 99 frames registered on 02-10 s0, all frames elsewhere) tracks ARKit's local scale: −0.4, −0.4, +1.6, +2.3 and −1.2 %. The February segments show the scale failure directly: shape is good (oracle 2.4–2.8 in median), the phone's scale is not.

### 3.4 Real non-LiDAR ARKit (ADVIO, iPhone 6s, 2018): inconclusive

ADVIO (Aalto, CC BY-NC 4.0) has real ARKit poses from a non-LiDAR iPhone, indoor and outdoor, but its "ground truth" is inertial navigation plus manual fix points.

- **Raw numbers.** On 15–26 ft baselines ARKit vs GT is 26 in median indoors and 58 in outdoors, and one outdoor sequence has a broken ARKit track.
- **Control shows the GT is the noise.** ARKit and the rig's independent Tango VIO agree with each other (8–15 in median) better than either agrees with the GT (13–33 in).
- **What it does say.** This data cannot score inches. It does say that 2018-era non-LiDAR phone VIO disagrees with another VIO by 8–15 in median at house scale, which points the same way as §3.1's no-depth sessions. The iPhone video is 1280 × 720, so I did not reconstruct from it. `results/arkit_scale_advio.json`.

## 4. Runtime and cost (4 vCPU CPU VM, contended; about 6–7 GB RAM free)

| Stage | Time per 35 m segment (87–205 frames at 1440 × 1920) |
|---|---|
| SIFT extraction / spatial + sequential matching | 34–152 s / 46–264 s |
| A1 tight-prior mapping / fixed triangulation | 24–217 s / 1–14 s |
| π³ / DA3-L, per 16-view window at 378 × 504 | 60 s / 44 s (bf16 CPU) |

- A house of about 4 sides is roughly 4× a segment: about 15–25 min of CPU for A1 sparse (about $0.05–0.07 at $0.17/h).
- Scoring is extra (laser z-buffering, 2–3 min per segment), and a GPU is not needed for A1 sparse.

## 5. Failure modes (all observed on real data)

1. **ARKit scale on no-depth sessions:** +1.6 to +2.7 % on the February segments, which is 4–7 in at 20 ft. No SfM mode removes it, because SfM inherits the ARKit scale. This is the gating item.
2. **Soft priors bend.** With σ 0.3 m robust (the ETH3D recipe) or σ 0.1 m, SfM on forward street walks drifts away from ARKit (1.5 m / 0.8 m camera RMS on `06-17 s0`, p90 76–100 in). ETH3D's simulated drift was larger than real ARKit's local error, so the recipe was tuned the wrong way.
3. **Image-only SfM fragments or folds** on 2 of 5 segments (−46 % and −19 % scale, 67 of 99 frames registered). The February segment `s0` also split into 2 models in the prior modes (67 of 99 registered); fixed ARKit triangulation still covered all 99.
4. **A2 shape on real phone frames** is worse than on ETH3D DSLR: π³'s oracle-scale p90 is 9–15 in per segment here, versus 2.9 in on the ETH3D facade. Its per-window similarity scale to ARKit swings from −2.7 to +3.9 %, because 16 cameras over about 14 m is a short baseline for the fit.
5. **DA3 images-only** failed on 1 of 10 windows (+45 % scale). Fed ARKit poses, DA3 is stable but blurs long distances (p90 15–21 in).
6. **Low light / low texture** (February 14:47) gives 9–13 k sparse points per segment, versus 36–46 k on June segments.
7. **Scorer limits:** the harness's KD scorer is unavailable (no GT scene for LaMAR), and the sparse-point floor sits at about 5 in p90.

## 6. Licences

- LaMAR: CC BY-SA 4.0 (commercial evaluation fine; derived data share-alike).
- ADVIO: CC BY-NC 4.0 (evaluation only).
- COLMAP/pycolmap: BSD; π³: BSD-2; DA3-Large 1.1: Apache-2.0.
- Checked and not used:
  - ScanNet++ (iPhone 13 Pro + Faro laser; indoor; application-gated, non-commercial terms);
  - ARKitScenes (iPad Pro + Faro; indoor; Apple research licence);
  - MARViN (iPhone 14 Pro Max ARKit, outdoor buildings, but COLMAP pseudo-GT, no laser);
  - ETH3D low-res rig (grayscale global-shutter rig, not a phone).

## 7. What going deeper would take

1. **Server recipe change now:** ARKit position priors at σ ≈ 3 cm, no robust loss (plus a fallback to fixed ARKit triangulation when SfM splits). Spatial matching from ARKit positions works and is 2–3× cheaper than exhaustive.
2. **Scale reference (other agent's scope).** It turns the no-depth segments from 6–7 in median into 2.4–2.8 in median. LaMAR's frames can test it: the street has doors, and a tap-tape proxy is any laser-measured length in view.
3. **A true non-LiDAR ARKit capture with laser truth doesn't exist publicly.** The cheapest real test is one of our own captures: a non-Pro iPhone running the iOS team's app at an ETH3D-like site, or any wall we laser/tape measure.
4. **A KD-level scorer for LaMAR:** fit wall planes and openings on the laser in 2–3 facade spans, so results are comparable with the harness and free of the 5 in point floor.
5. **Longer A2 windows** (32–64 views, GPU), plus a global fusion scaled over the whole segment's ARKit track instead of 16 cameras, before judging A2 finally on phone frames.

## Files

- **Code** (store, `internal/breadth/a6-iphone-quality/`):
  - data and GT: `setup.sh`, `lamar_io.py`, `laser.py`, `segments.py`, `build_laser.py`;
  - ARKit baselines: `arkit_scale.py`, `arkit_scale_split.py`, `arkit_scale_advio.py`;
  - reconstruction: `a1_lamar.py`, `a2_lamar.py`, `run_a1_all.sh`, `run_a2_all.sh`;
  - scoring: `pairscore.py`, `rescore.py`, `per_segment.py`.
- **Results** (store, `results/`): the ARKit scale JSONs, `laser_check.json`, `segments.json`, `a1/*.json`, `a2/*.json`, `rescored.json`, `rescored_table.md`, `per_segment_table.md`.
- **VM only** (`~/lamar`, `~/a6`, `~/advio`), all regenerable: LaMAR frames, GT model, laser clouds, per-point arrays.
