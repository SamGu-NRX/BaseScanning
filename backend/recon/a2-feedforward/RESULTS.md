---
cursor:
  subagentId: "bc-901df09a-a67a-5f2b-b517-cda7466f7eab"
---

# A2: feed-forward multi-view models (MapAnything, π³, Depth Anything 3, VGGT)

Real images, real laser truth; ARKit-like poses are synthetic (packet generator drift profiles on ETH3D's true cameras). Code, scores and run records: `internal/breadth/a2-feedforward-code/` (see its "Files" section below). Handoff from the previous agent: `internal/handoffs/A2-feedforward.md`.

## 1. Verdict

| Model | Licence | Verdict | One line |
| --- | --- | --- | --- |
| **π³ (Pi3)** | BSD-2 | **Go (geometry backbone; needs external scale)** | Best shape: facade meter wall, 0.8 / 2.9 in (median / p90) on truly observed key distances when scaled from exact poses; on ARKit-like scale it misses the 2–6 in bar. |
| **DA3-Large 1.1** | Apache-2.0 | **Go (pose-conditioned alternative)** | The only commercial model that takes poses and returns depth in their scale: 0.7 / 2.7 in with exact poses. With non-LiDAR poses the scale follows the phone (−3.6%) and p90 blows out. |
| MapAnything (apache ckpt) | Apache-2.0 | **No-go** | Metric scale is off by −16 to −30% images-only. Intrinsics don't help; poses fix it only partly (−13% to +0.4%). Key distances 16–24 in; 4× slower. |
| VGGT-1B | CC BY-NC (commercial ckpt gated) | Not scored | Runs on CPU (12 s per 8 views), but the grid was cut at the wrap-up. NC licence; the commercial checkpoint needs manual approval. |
| MASt3R, DA3-Nested-Giant | CC BY-NC(-SA) | Not run | NC licences, lowest priority. |

**Against the 2–6 in bar (C1 error sweep: ≤4 in keeps 8 of 12 true passes, and scale must be within ±1%):** π³ and DA3-Large shapes clear the geometry part (p90 2–3 in on observed facade elements, 2–6 in on ETH3D pair distances). **No model plus phone metadata clears it on scale.** The ARKit-like session scale draws alone are −1.8% to +4.4% for LiDAR (4 of 9 within ±1%) and −0.4% to −17.9% for non-LiDAR. A feed-forward model scaled by phone poses inherits that error 1:1, and none of the models recovers true metric scale by itself.

**Closest combination:**
1. **π³ with one known reference distance** (a taped length or known object): 0.6 / 2.0 in (delivery_area), 0.8 / 2.9 in (electro), 1.6 / 6.7 in (facade, all points), for 1–3 m pairs.
2. **DA3-Large fed LiDAR-grade poses whose session scale lands within 1%** (the facade LiDAR draws are +0.3%): shared scorer 2.0 / 7.3 in on observed key distances.
3. **π³ plus LiDAR-profile poses**: in-bar only on the draws with scale within 1% (e.g. electro: median 1.2–1.8 in, p90 2.9–6.9 in).

Non-LiDAR poses never reach the bar.

## 2. Inputs and outputs

- **Real:** ETH3D DSLR images (facade, electro, delivery_area; undistorted, 1024 px eval width) and laser scans. The 8 seed groups of 8 views per scene are PR #12's groups, unchanged.
- **Synthetic:** ARKit-like metadata from `internal/packet-generator` (`align()` + `apply_drift()` over the whole capture in shot order, resets off, factory-intrinsics error). Profiles `lidar_modern`, `nonlidar_modern`, `nonlidar_2018` × 3 draws, plus `exact` (ETH3D truth) as the ceiling. Walk lengths: 38 m (delivery_area), 50 m (electro), 161 m (facade).
- **Metadata modes:** `images` (none), `K` (intrinsics fed), `KT` (intrinsics + poses fed: MapAnything, DA3), and `posthoc` (images-only run, scaled by a Sim(3) fit of its cameras to the ARKit-like cameras; this is the only metadata route for π³ and VGGT).
- **Produced:** 231 group runs (π³ 23, DA3-L 69, MapAnything 48, plus smoke runs), npz depth + intrinsics + poses at 1024 px, unmasked. Shared-scorer runs: 16 facade frames that face the meter wall (DSC_0357–0366, 0394, 0395, 0419–0422), 7 model × mode runs, 44 scored variants.
- **Scorers:**
  - PR #12's pair-length scorer (vendored unchanged) with additions: post-hoc ARKit scale, completeness, pass rates, masked variant, and contract §3.1 coverage (a pair counts as well observed when both endpoints are seen by ≥ 2 group views at ≤ 75° from the laser normal).
  - The shared `evalharness score-cloud` on the facade GT scene (built on this VM with `eth3d/build_gt.py`).
- **Online houses:** only a 6-frame π³ smoke run on `yt-5-M-7f35Tyg` (see §5). The 16-frame house runs were queued behind the metadata grid and cut at the wrap-up.

## 3. Metrics against ground truth

### 3a. Shared scorer (facade meter wall, 16 views, `score-cloud`)

Alignment: GT cameras via camera rotations (Procrustes) plus centres, never the laser (see §5 for why the harness's centre-only default was replaced). Rigid when the cloud claims metric scale, similarity for shape-only. Key distances (KDs) are in inches. "Observed" means KDs on elements the visibility oracle grades truly observed (≥ 2 views, ≤ 75°), per contract §3.1. **False-observed claims were 0 in every row.**

| Model + metadata | KDs scored / missing / phantom | Observed KDs median / p90 (n) | All KDs ≤ 4 in | Scale error | Wall face ≤ 10 cm | vs 2–6 in bar |
| --- | --- | --- | --- | --- | --- | --- |
| π³ + exact scale (oracle) | 47 / 2 / 3 | **0.8 / 2.9 (31)** | 82% | 0.0% | 94% | **meets** |
| π³ shape only (similarity) | 36 / 13 / 0 | 0.9 / 2.0 (21) | 82% | n/a | 95% | shape meets |
| π³ + LiDAR ARKit (draw 0, +0.5%) | 42 / 8 / 15 | 1.9 / 21.1 (31) | 66% | +0.5% | 94% | median yes, p90 no |
| π³ + non-LiDAR modern (draw 0) | 42 / 8 / 6 | 3.3 / 16.1 (31) | 61% | −3.7% | 88% | no |
| π³ + non-LiDAR 2018 (draw 0) | 45 / 4 / 6 | 3.8 / 13.3 (31) | 44% | −3.9% | 88% | no |
| DA3-L fed exact poses | 26 / 24 / 27 | **0.7 / 2.7 (21)** | 96% | 0.0% | 70% | **meets** (fewer KDs) |
| DA3-L fed non-LiDAR poses (draw 0) | 32 / 18 / 9 | 2.2 / 51.7 (26) | 53% | −3.6% | 77% | no |
| DA3-L + LiDAR ARKit post-hoc (draw 0) | 35 / 15 / 12 | 2.0 / 7.3 (26) | 65% | +0.6% | 77% | near |
| MapAnything, own metric scale | 14 / 36 / 18 | 23.1 / 47.8 (11) | 7% | −29.1% | 74% | no |
| MapAnything fed exact poses | 20 / 30 / 63 | 20.9 / 41.8 (16) | 20% | −3.0% | 81% | no |

The full table, including the harness's centre-only default rows, is in `a2-feedforward-code/outputs/shared-scorer/summary.md`; per-variant `geomscore.json/md` are in `geomscores.tgz`.

### 3b. PR #12 pair scorer (8-view groups, 8 seeds; MapAnything 4 seeds)

Within 6 m of the seed camera, surface interior, 1–3 m pairs. Cells are median / p90 inches (scale error). Posthoc and draw ranges are min–max over 3 draws. Facade is shown on "all points" (see §5, crop artifact).

| Model + metadata | delivery_area | electro | facade (all points) |
| --- | --- | --- | --- |
| π³ + exact scale | **0.6 / 2.9 (+0.8%)** | **0.9 / 2.1 (+0.9%)** | 2.1 / 5.5 (−2.2%) |
| π³ + one taped distance | **0.6 / 2.0** | **0.8 / 2.9** | 1.6 / 6.7 |
| π³ + LiDAR ARKit, 3 draws | 1.3–5.1 / 3.1–9.7 | 1.2–1.8 / 2.9–6.9 | 1.9–3.2 / 5.2–12.5 |
| π³ + non-LiDAR modern, 3 draws | 1.4–6.2 / 5.6–12.9 | 1.3–4.5 / 4.9–11.1 | 4.9–6.2 / 13.5–13.8 |
| π³ + non-LiDAR 2018, 3 draws | 2.3–12.5 / 9.1–30.7 | 6.0–17.2 / 11.5–31.2 | 13.6–16.5 / 39–189 |
| DA3-L fed exact poses | 0.9 / 6.8 (−0.6%) | **0.8 / 2.4 (+0.4%)** | 2.3 / 7.0 (−1.9%) |
| DA3-L fed non-LiDAR poses (draw 0) | 2.2 / 9.1 (−2.0%) | 3.9 / 11.3 (−2.8%) | 4.6 / 16.3 (−3.4%) |
| DA3-L images + exact post-hoc | 1.4 / 36.2 (−1.3%) | 1.8 / 4.6 (+2.0%) | 2.6 / 8.0 (−2.4%) |
| MapAnything images (own scale) | 23.7 / 43.0 (−29.9%) | 15.3 / 32.0 (−17.7%) | 29.1 / 41.3 (−35.5%) |
| MapAnything + exact K | 22.8 / 43.0 (−28.8%) | 13.8 / 31.8 (−16.0%) | 27.3 / 38.5 (−32.6%) |
| MapAnything fed exact K + poses | 5.2 / 23.1 (−7.7%) | 1.8 / 5.6 (+0.4%) | 18.8 / 27.6 (−22.8%) |
| MapAnything fed non-LiDAR poses | 7.0 / 21.3 (−9.8%) | 5.1 / 20.0 (−0.9%) | 19.6 / 32.2 (−25.0%) |

- **Vertical interior (walls), within 6 m:** π³ + exact scale gives 0.7 / 5.6, 0.7 / 1.6 and 1.6 / 3.2 in; DA3-L fed exact poses gives 0.8 / 6.8, 0.8 / 3.1 and 1.2 / 3.4 in.
- **Edges, 1–3 m:** π³ + exact scale gives 2.0 / 18.0 (delivery_area), 1.1 / 5.0 (electro) and 1.2 / 4.8 (facade); DA3-L fed exact poses gives 3.3 / 15.6, 2.1 / 9.7 and 1.3 / 51.8. Edges miss the bar except on facade/electro with π³.
- **Raw (unscaled) outputs:** π³ and DA3-L are not metric (−82 to −94%), as expected. MapAnything's "metric" output is −16% to −36%.
- **Completeness:** DA3 1.000. π³ 0.998, and edges 0.985. MapAnything 1.000, and edges 0.972. MapAnything's non-ambiguous mask removed nothing here, so the masked and unmasked scores are identical (PR #12's edge "fails" came from `mask_edges`).
- **Coverage evidence (§3.1):** 89% (delivery_area) and 92% (facade, all points) of pairs have both endpoints well observed. Errors on that subset are within 0.1 in of the full set, e.g. π³ + exact on delivery_area: 0.6 / 2.9 in, 90% within 3 in.
- **Compared with PR #12's best fused recipe** (MoGe-2 + triangulation, exact poses, 8 views): 2.8 in p90 on walls. π³ + exact scale matches or beats it (1.6–5.6 in p90 on walls) without any triangulation.

## 4. Runtime and cost per house

CPU: 4 cores, AMX-BF16, ~7 GB usable, bf16 autocast trunk. Medians per group:

| Model | 8 views | 16 views | Peak RSS (incl. mmapped weights) |
| --- | --- | --- | --- |
| DA3-Large | 8.7 s | 21 s | ~3–5 GB |
| π³ | 9–13 s | ~20–25 s idle (73 s measured under contention) | ~5 GB |
| MapAnything | 34 s | 81 s | ~8 GB |
| VGGT | 12 s | — | ~8.5 GB |

- **Per house** (~60 useful frames in ~6 overlapping 16-view windows): π³ or DA3 takes ~2–3 min on CPU, MapAnything ~8 min. Cost is $0 on the existing VM (≈ $0.01–0.02 at 4-vCPU on-demand rates).
- **GPU estimate:** ~1–2 s per 16-view window on an L4/A10G for these ~0.4–1.2 B-parameter models, so under 30 s and under $0.01 per house.
- A GPU does not change accuracy. It would allow one-pass 32–64-view windows or higher resolution; whether that helps is untested.

## 5. Failure modes

1. **Scale is the blocker.** π³, DA3-L and VGGT are scale-free; only MapAnything claims metric scale, and it is off by 16–36%. Post-hoc or pose-fed scale inherits the ARKit session error 1:1, which exceeds the ±1% bar in most draws.
2. **MapAnything barely uses its metadata.** Intrinsics change nothing, and exact poses still leave −7.7% (delivery_area) and −22.8% (facade). The error is scene-dependent, so it can't be calibrated away. This confirms PR #12's finding at native 518 px and 8 views.
3. **Per-group long tail.** Single groups can go bad: DA3-L images-only on delivery_area has p90 36 in (feeding poses fixes it: 6.8 in), and MapAnything on delivery_area has p90 23 in. Any pipeline needs a per-window consistency check against the ARKit trajectory.
4. **Edges and near ground** stay at 5–50 in p90 on 1–3 m edge pairs, outside the bar, and phantom geometry appears at depth discontinuities in unmasked clouds (27–63 phantom KDs for MapAnything and DA3 in the shared scorer).
5. **Crop artifact (my setup).** A 518×336 input cover-crops ~9 rows off 3:2 images, which is exactly where the nearest ground is. Facade "within 6 m" completeness drops to 0.50 (π³) and 0.27 (MapAnything), versus 1.00 for DA3 at 504×336. Use an aspect-matched size (504×336 or 518×350); a 518×350 π³ rerun was queued but cut.
6. **Shared-scorer alignment degeneracy.** `score-cloud` aligns by camera centres only. The 16 meter-wall cameras form two clusters ~30 m apart, so roll about that line is free: π³ + LiDAR scored 6 of 50 KDs with centre-only alignment and 42 of 50 with rotation-aware alignment. Recommend the harness add camera rotations to its alignment. Its extractor is also sensitive: KD counts swing 7–47 between near-identical clouds.
7. **Untested:** global consistency over a whole house loop (only local 8/16-view windows were tested) and walkaround video robustness. The only house evidence is a 6-frame π³ smoke run on `yt-5-M-7f35Tyg`: 31 s, but fragmented with 6 frames spread over 2 min of video (ground-plane tilt 21°). No robustness claim.

## 6. Licences

- **Commercially usable:** π³ (BSD-2; not Pi3X, which is NC), DA3-Large 1.1 and DA3-Metric-Large (Apache-2.0), MapAnything `map-anything-apache` (Apache-2.0).
- **Non-commercial:** VGGT-1B (CC BY-NC 4.0; `VGGT-1B-Commercial` is gated behind manual approval, same architecture), DA3-Giant / Nested (CC BY-NC 4.0), MASt3R (CC BY-NC-SA 4.0).
- ETH3D is CC BY-NC-SA: evaluation only, not redistributed.

## 7. Recommendation for the depth phase

- **Take π³ (primary) and DA3-Large (pose-conditioned alternative) forward as the dense-geometry backbone** in overlapping 8–16-view windows, fused through the ARKit trajectory.
- Drop MapAnything. Revisit VGGT only if the commercial checkpoint is approved.
- **Do not take scale from the ARKit trajectory.** The ±1% bar is tighter than ARKit's session scale. Candidates, in order:
  1. LiDAR depth maps on LiDAR phones: per-pixel metric depth, not drift-accumulated trajectory scale;
  2. a known-size reference in frame (meter can, standard door, tape), where one taped distance already gives 2.0–2.9 in p90;
  3. multi-window scale voting against the trajectory as a fallback, with honest ±.
- **Next experiments:**
  1. whole-house windowed fusion on ETH3D with global alignment, scored end to end through the shared scorer;
  2. scale from simulated LiDAR depth (packet generator) instead of trajectory;
  3. an aspect-matched input size plus confidence-filtered export to cut edge phantoms;
  4. the queued walkaround-house runs (π³ / DA3 16 frames on 3 houses);
  5. a per-window consistency check (model cameras vs ARKit cameras) to catch the long-tail groups.

## Files (all under `internal/breadth/a2-feedforward-code/`)

- `a2ff/`: `run.py` (adapters: MapAnything 518/392, VGGT, π³ incl. 518×350, DA3-Large/Nested), `prepare.py`, `score.py` (PR #12 scorer + post-hoc scale, pass rates, completeness, §3.1 coverage), `harness_run.py` (shared-scorer export + rotation-aware alignment), `harness_summary.py`, `report_tables.py`, `house.py` (walkaround runner: registration, camera-height scale, ground reach, renders), `common.py`, `peak.py`.
- `evals/`, `models/`: PR #12 code vendored unchanged. `scripts/`: VM setup, prep and run queues.
- `outputs/scores/`: `phase1.json/.md` (all PR #12-scorer results), `tables_phase1.md` (compact per-scene tables, every range and cohort).
- `outputs/shared-scorer/`: `summary.md`, `geomscores.tgz` (geomscore.json/md, alignment.json, cameras.json per variant).
- `outputs/runs/`: every run.json (checkpoint, mode, members, seconds). `outputs/<scene>/arkit_poses/`: pose settings. `outputs/houses-smoke/`: the one house smoke run.
- VM-only (regenerable with the handoff's commands): depth npz, point clouds, the ETH3D data, the facade GT build.
