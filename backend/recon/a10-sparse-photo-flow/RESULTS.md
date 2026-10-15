---
cursor:
  subagentId: "bc-d9e604e6-c7a1-5638-a8c5-15c866d74790"
---

# A10: the photo flow (40 / 60 / 70 regular-iPhone photos), COLMAP vs π³ / DA3

Code, selections, per-run rows and generated tables: `internal/breadth/a10-sparse-photo/` (`results/summary.md` is the full table; this report quotes it). Real images and laser everywhere except where marked **(sim.)**. Photo counts follow Hunter's final instruction: N = 40, 60, 70 (5–20 dropped).

## 1. Verdict

**Conditional go: COLMAP incremental SfM (BSD) is the pose and measurement engine for the photo flow; π³ / DA3 are no-go as pose engines and only useful as depth on COLMAP's poses.**

- **Accuracy:** on DSLR-quality photos, COLMAP from images alone meets the 2–6 in bar at every count tested: facade 0.3–0.6 in median, 2.5–4.5 in p90 at true scale (40 / 60 / 70 photos, guided or careless). Electro gets 2.3–2.5 / 3.9–5.1 in.
- **Registration:** COLMAP registered 85–100 % of photos in one model on every ETH3D set. Cameras land within 0.3–1.7 cm (electro up to 7 cm on a careless set).
- **Photo count:** 40 is enough, and 60 / 70 add nothing on ETH3D: key-distance counts, completeness and C1 decidability are identical. Coverage is set by where photos are taken, not how many.
- **Limits:**
  1. **Scale.** A no-tool scale reference is needed (A9 meter make/model: 0.6 % median / 2.0 % p90, i.e. +1.9 / +6.2 in at 26 ft). ARKit's own scale (+2.5 % calibrated to A6's real LaMAR no-depth sessions) costs 1.8 / 7.3 in on facade.
  2. **Coverage.** The sparse cloud covers 59 % of the meter wall and about 1 % of the ground strip, so C1 decides only 15–23 % of criteria (laser GT ceiling 62 %).
  3. **Photo quality.** iPhone-quality frames (A7 `combo_day`, iPhone FOV at 1920 × 1440) are about 5–8× worse: facade 3.1–3.8 in median, 14–26 in p90.
  4. **Spacing.** Photos spread along a whole house break SfM (below).
- **No-go:** π³ alone (0.1–0.4 m camera error, stacked phantom walls: 26 in median at 40 photos, 3.4 / 18 in at 70) and DA3 alone (9–19 of 40 poses right). VGGT and MASt3R were not run (non-commercial licences, CPU memory at 40–70 views).

**Best combination found:** COLMAP poses plus learned depth (`cda3`, `cpi3`). This roughly doubles scored key distances (25 → 35–40) and completes the meter wall (up to 99 %) at 1.0–2.5 in median on DSLR photos, but its p90 (7–21 in) is outside the bar. Use it for coverage evidence, and measure on the sparse model.

## 2. Inputs and outputs

| Input | Real / sim. | Use |
|---|---|---|
| ETH3D facade (76 DSLR), electro (45), delivery_area (44), laser GT scenes rebuilt with the harness (`build_gt.py`, `build_gt_c2.py`) | real | accuracy. On electro/delivery, "N60 / N70" is impossible, so their largest set is all images (`all45`, `all44`) |
| Same photos at iPhone quality: A7 `degrade.py combo_day` (iPhone 15 main-camera FOV crop, 1920 × 1440, ISO 50, 1/1000 s, 30°/s hand motion, rolling shutter, HDR + sharpen, JPEG q90) | sim. degradation of real photos | quality effect (pessimistic: real photos are 4032 px) |
| ARKit priors for ETH3D: true centres × 1.026 + 2 cm jitter, σ 3 cm, no robust loss (A6 recipe, scale from A6's real no-depth LaMAR sessions). Old recipe kept as `prior_old` (σ 0.3 m robust, A1's simulated draw) | **sim.**, calibrated to real | COLMAP with priors |
| LaMAR LIN: 3 of A6's segments (06-17 s0 with depth; 02-10 s0 / s1 without depth = non-LiDAR proxy), real 1440 × 1920 ARFrames, **real ARKit poses**, laser. A6's GT chain rebuilt on this VM (13 398 GT iOS frames, same as A6) | real | real-phone test of 40 / 60 / 70 frames |
| Walkaround houses yt-KSXy, yt-LCPB, yt-Qm4b (video frames, no GT, no ARKit) | real | registration / robustness only |

**Photo selection** (`select.py`), from GT geometry (frustum + incidence):
- `guided` = the shot list: the meter close-up, then greedy picks maximising two-view coverage of the meter wall, adjacent walls, the ground strip to 14 ft and the overhead band.
- `careless` = the N photos nearest one random standing spot that sees the meter.

At N ≥ 40, every selection already reaches each ETH3D scene's maximum frustum coverage. Guided saturates at 3 (facade), 7 (delivery) and 26 (electro) photos; careless at 4–38. The remaining gaps (facade ground 8 %, overhead 0 %) come from where ETH3D's cameras stood, not from the count.

**Methods:**
- `colmap`: SIFT 8192, exhaustive matching per scene, known K fixed, `image_names` per set; scored at true scale (similarity to GT cameras).
- `prior`: the same with ARKit priors; scored rigidly, so its scale error stays in.
- `pi3`, `da3`: all N views in one pass, true scale by Sim(3) to GT centres.
- `cda3`: DA3-Large fed COLMAP poses.
- `cpi3`: π³ depth rescaled per view to COLMAP points and placed at COLMAP poses.
- `pi3c`: π³ with a per-view consistency pass.
- LaMAR only: `fixed`, A6's fallback that triangulates with real ARKit poses.

Every ETH3D row runs `score-cloud`, then `run-c1` at ground limits 80° and 75°.

## 3. Metrics against ground truth

### 3.1 ETH3D, DSLR-quality photos (true scale unless the method carries its own)

KD = harness key distances. "Obs" = KDs on truly observed elements (contract §3.1). Completeness = laser samples with a recon point within 10 cm after ICP. False-observed claims were 0 in every row.

| Scene / set | Method | Reg. | KDs | All med / p90 (in) | Obs med / p90 | Meter wall / ground / overhead | C1 decidable (laser GT 62 % facade, 92 % electro) |
|---|---|---|---|---|---|---|---|
| facade N40 guided | COLMAP images only | 38/40 | 25 | **0.4 / 2.5** | 0.5 / 2.2 | 59 / 1 / 23 % | 23 % |
| facade N40 guided | COLMAP + ARKit prior 3 cm (+2.6 % scale) | 38/40 | 25 | 1.9 / 7.4 | 2.1 / 6.4 | 56 / 1 / 35 % | 15 % |
| facade N40 guided | COLMAP + prior, old recipe (A1 draw −1.2 %) | 38/40 | 25 | 1.4 / 3.5 | 1.5 / 3.3 | 59 / 1 / 16 % | 15 % |
| facade N40 guided | COLMAP poses + π³ depth | 38/40 | 39 | 2.5 / 7.0 | 2.5 / 4.6 | **99** / 21 / 73 % | 8 % |
| facade N40 guided | COLMAP poses + DA3 depth | 38/40 | 40 | 1.7 / 18.1 | 1.6 / 6.9 | 65 / 27 / 86 % | 0 % |
| facade N40 guided | π³ alone | 36/40 | 11 | 25.8 / 38.9 | – | 3 / 8 / 2 % | 0 % |
| facade N40 guided | π³ + consistency pass | 39/40 | 6 | 15.0 / 28.7 | – | 7 / 6 / 6 % | – |
| facade N40 guided | DA3 alone | 9/40 | 38 | 1.8 / 6.2 | 1.9 / 4.7 | 71 / 27 / 42 % | 8 % |
| facade N40 careless | COLMAP images only | 40/40 | 25 | 0.6 / 2.6 | 0.7 / 2.5 | 59 / 1 / 24 % | 23 % |
| facade N40 careless | COLMAP poses + DA3 depth | 40/40 | 30 | **0.5 / 1.8** | 0.5 / 1.9 | 74 / 38 / 22 % | 0 % |
| facade N40 careless | COLMAP poses + π³ depth | 40/40 | 38 | 1.2 / 5.1 | 1.5 / 5.5 | 99 / 19 / 23 % | 8 % |
| facade N40 careless | π³ alone / DA3 alone | 33 / 19 of 40 | 11 / 39 | 26.1 / 43.2 · 5.2 / 22.8 | – | – | 15 / 8 % |
| facade N60 guided | COLMAP images only | 59/60 | 25 | 0.4 / 3.3 | 0.6 / 3.1 | 59 / 1 / 23 % | 23 % |
| facade N60 guided | COLMAP + ARKit prior 3 cm | 59/60 | 25 | 1.8 / 7.3 | 1.9 / 6.3 | 56 / 1 / 35 % | 15 % |
| facade N60 guided | COLMAP poses + π³ / DA3 depth | 59/60 | 40 / 12 | 2.7 / 16.7 · 3.2 / 7.1 | 2.8 / 5.5 · 3.3 / 7.4 | 97 / 88 % meter wall | 8 / 0 % |
| facade N60 guided | π³ alone | 55/60 | 20 | 2.2 / 6.0 | 1.6 / 4.0 | 81 / 27 / 45 % | 0 % |
| facade N60 careless | COLMAP images only / + prior | 59/60 | 24 / 25 | 0.3 / 2.9 · 1.8 / 7.2 | – | 59 / 1 / 22 % | 23 / 15 % |
| facade N70 guided | COLMAP images only | 70/70 | 25 | 0.4 / 4.5 | 0.6 / 4.4 | 60 / 1 / 24 % | 23 % |
| facade N70 guided | COLMAP + ARKit prior 3 cm | 70/70 | 25 | 1.8 / 7.3 | 1.8 / 6.3 | 56 / 1 / 36 % | 15 % |
| facade N70 guided | COLMAP poses + π³ depth | 70/70 | 38 | 1.3 / 8.1 | 1.2 / 3.4 | 93 / 16 / 50 % | 8 % |
| facade N70 guided | COLMAP poses + DA3 depth | 70/70 | 35 | 1.0 / 7.1 | 1.3 / 9.6 | 60 / 26 / 32 % | 0 % |
| facade N70 guided | π³ alone | 63/70 | 18 | 3.4 / 17.9 | 3.4 / 17.9 | 85 / 30 / 36 % | 0 % |
| facade N70 careless | COLMAP images only / + prior / + DA3 depth | 70/70 | 25 / 25 / 38 | 0.6 / 3.1 · 1.8 / 7.4 · 1.4 / 20.7 | 0.6 / 2.9 · 1.8 / 6.4 · 1.5 / 4.5 | 59 / 1 / 23 % (COLMAP) | 23 / 15 / – % |
| electro N40 guided | COLMAP images only | 38/40 | 29 | 2.5 / 4.7 | 2.8 / 3.6 | 39 / 7 / 42 % | 31 % (see 3.4) |
| electro N40 guided | COLMAP + ARKit prior 3 cm | 38/40 | 27 | 2.8 / 6.0 | 2.9 / 5.3 | 21 / 7 / 38 % | 31–38 % |
| electro N40 guided | π³ alone | 39/40 | **63** | 1.7 / 4.7 | 1.9 / 5.3 | 98 / 100 / 100 % | 23 % |
| electro N40 guided | COLMAP poses + DA3 / π³ depth | 38/40 | 16 / 18 | 3.3 / 19.9 · 4.5 / 16.4 | 1.4 / 3.0 · 4.7 / 10.3 | 88 / 100 / 98 % | 31 % |
| electro N40 careless | COLMAP images only / + prior | 34/40 | 30 / 27 | 2.3 / 3.9 · 2.8 / 5.9 | 2.5 / 3.5 · 3.1 / 5.4 | 39 / 7 / 42 % | 31 / 38 % |
| electro all 45 | COLMAP images only / + prior / + DA3 depth | 41/45 | 30 / 27 / 16 | 2.3 / 5.1 · 2.7 / 5.9 · 2.5 / 19.4 | 3.1 / 4.1 · 3.0 / 5.6 · 1.5 / 2.9 | – | 31–38 % |
| delivery (all sets) | every method | 40/40, 44/44 (π³ 36/40) | – | not scorable (harness extractor error, §5) | – | – | – |

**40 / 60 / 70 comparison (facade, DSLR, guided):**
- COLMAP images only: 0.4 / 2.5, 0.4 / 3.3 and 0.4 / 4.5 in; registered 38/40, 59/60, 70/70.
- π³ alone: 25.8 / 38.9, 2.2 / 6.0 and 3.4 / 17.9 in.
- DA3 alone: registers only 9 of 40 poses correctly, so it wasn't rerun at 60 / 70 beyond the hybrids.
- More photos never improved COLMAP. The p90 grows slightly, because the additional photos are farther or more oblique ones that the extractor also uses.

Delivery pose accuracy (cameras vs laser-registered GT): COLMAP 0.4 cm RMS (40/40 and 44/44); π³ alone 23 cm, 36/40.

### 3.2 ETH3D at iPhone quality (A7 `combo_day`), facade guided

| Set | Method | Reg. | KDs | All med / p90 (in) | Obs med / p90 | Meter wall ≤ 10 cm |
|---|---|---|---|---|---|---|
| N40 | COLMAP images only | 38/40 | 19 | 3.1 / 14.1 | 3.8 / 13.5 | 44 % |
| N40 | COLMAP + ARKit prior 3 cm | 38/40 | 18 | 3.0 / 25.4 | 3.7 / 25.4 | 44 % |
| N60 | COLMAP + ARKit prior 3 cm | 58/60 | 18 | 3.5 / 24.8 | 4.8 / 24.9 | 43 % |
| N60 | COLMAP poses + π³ depth | 58/60 | 14 | 4.2 / 10.1 | 3.4 / 6.9 | 88 % |
| N60 | π³ alone | 48/60 | 12 | 3.9 / 17.6 | 3.2 / 18.0 | 77 % |
| N70 | COLMAP images only | 67/70 | 19 | 3.8 / 25.6 | 5.5 / 25.4 | 43 % |
| N70 | COLMAP + ARKit prior 3 cm | 68/70 | 18 | 3.3 / 25.7 | 4.6 / 25.9 | 43 % |

Camera centres at iPhone quality: COLMAP 16 cm median error, with the prior 6 cm (DSLR: 1.5 cm). The narrower iPhone FOV at 15 m standoff plus ISP effects is the cause, consistent with A7.

Some iPhone-quality rows (facade N60 images-only, N40 / N60 DA3 hybrids) were scored before the tap fix in §5 and are invalid (they show 11–20 in medians). A rescore is queued (`rescore.py`) and still pending.

### 3.3 LaMAR LIN: real phone frames, real ARKit (A6 pair scorer, 25 cm patches, ≤ 10 m)

15–26 ft pairs, median / p90 in; "own scale" is ARKit (prior, fixed) or an ARKit Sim(3) fit (images only).

| Segment | Set | COLMAP images only: reg., own scale → true scale | COLMAP + ARKit prior 3 cm: reg., own → true | Fixed ARKit triangulation: reg., own | Openings 0.5–2 m (prior) |
|---|---|---|---|---|---|
| 06-17 s0 (depth) | N40 guided | 34/40, 3.0 / 7.2 → 3.1 / 7.3 | 34/40, 3.2 / 7.3 → 3.1 / 7.3 | 40/40, 3.8 / 9.7 | 0.9 / 2.8 |
| 06-17 s0 | N60 guided | 60/60, 3.2 / 6.7 → 3.0 / 6.5 | 60/60, 3.1 / 6.6 → 2.9 / 6.4 | 60/60, 3.5 / 10.9 | 0.8 / 2.8 |
| 06-17 s0 | N70 guided | 70/70, 3.8 / 7.5 → 3.4 / 6.9 | 70/70, 3.8 / 7.5 → 3.3 / 6.7 | 70/70, 2.9 / 9.1 | 0.9 / 2.8 |
| 06-17 s0 | N40 careless | 40/40, 2.2 / 5.9 | 40/40, 2.1 / 5.7 → 3.5 / 7.3 | 40/40, 4.2 / 12.7 | 1.2 / 3.4 |
| 02-10 s0 (no depth) | N40 guided | **7/40** | **7/40** | 40/40, 3.4 / 10.0 | 0.9 / 1.8 (7 frames) |
| 02-10 s0 | N60 guided | 37/60 (2 models), 4.8 / 14.0 → 2.9 / 10.3 | 26/60 (3 models), 6.2 / 10.1 → 1.4 / 6.1 | 60/60, 2.9 / 13.3 | 1.4 / 3.6 |
| 02-10 s0 | N70 guided | 41/70, 7.2 / 12.1 → 3.3 / 7.8 | 41/70, 6.7 / 11.7 → 3.1 / 7.6 | 70/70, 4.8 / 12.9 | 1.5 / 4.3 |
| 02-10 s0 | N40 careless | 23/40, 8.9 / 13.7 → 3.0 / 7.3 | 23/40, **79.8 / 100.4** (model bent, −34 % scale) | 40/40, 8.0 / 13.4 | 1.4 / 4.1 |
| 02-10 s1 (no depth) | N40 guided | **2/40** | **2/40** | 40/40, 7.6 / 20.9 | – |
| 02-10 s1 | N60 guided | 30/60, 11.3 / 15.8 → 1.9 / 5.6 | 30/60, 11.2 / 15.4 → 1.7 / 5.0 | 60/60, 9.0 / 19.5 | 2.8 / 5.3 |
| 02-10 s1 | N70 guided | 70/70, 6.2 / 15.8 → 2.8 / 9.8 | 70/70, 8.0 / 13.6 → 3.0 / 7.3 | 70/70, 8.2 / 20.0 | 2.3 / 4.8 |
| 02-10 s1 | N40 careless | 40/40, 12.2 / 16.6 → 2.0 / 5.4 | 40/40, 13.4 / 17.9 → 1.9 / 5.2 | 40/40, 10.2 / 19.5 | 3.3 / 5.9 |

DA3 fed the prior poses (`cda3`) is worse than sparse everywhere on LaMAR (4.6–12.4 / 13–23 in on 15–26 ft pairs), as A6 found.

What LaMAR shows:
1. **Openings are inside the bar on every registered model** (0.8–3.3 in median, 1.8–5.9 in p90).
2. **15–26 ft distances meet the bar only at true scale.** ARKit's scale on no-depth sessions (+2.3 to +5.5 % on long pairs) costs 6–13 in median.
3. **Spreading 40 frames evenly along a dim ~35 m walk (one every 0.9 m) breaks SfM:** 2–7 of 40 registered. It takes 60–70 frames per ~35 m side on the worst session. Consecutive frames (careless) register better but cover half the wall.
4. **The 3 cm prior doesn't rescue a fragmented set,** and bent one set badly.
5. **Fixed ARKit triangulation always registers everything** but is 3–10 in median.

### 3.4 Criteria (C1 via `run-c1`, 13 checks for the best candidate; `score-verdicts` not scored, see §7)

| Scene | Laser GT scene (all DSLR cameras), ground 80° / 75° | COLMAP images only (40 / 60 / 70) | COLMAP + prior | COLMAP + DA3 / π³ depth |
|---|---|---|---|---|
| facade | 62 % / 62 % decidable, manual_review | 23 % at every N (pass 2, fail 1, unsure 10), manual_review | 15 % | 0–8 % |
| electro | 92 % / 77 %, manual_review | 31 %, manual_review | 31–38 % | 31 % |
| delivery | 92 % / 62 %, manual_review | not scorable | – | – |

Every recon verdict is manual_review, and no reconstruction produced a pass the laser scene does not support. The main "unsure" cause is `unobserved`: 9 of 10 unsure checks on facade COLMAP. Those checks need the ground under and around the footprint (gas, AC, drive, pool, ground surface), the wall above, and facing/headroom.

The dense hybrids score worst on decidability for a harness reason, not a geometry one. The reference extractor splits the meter wall on dense clouds, and the recon coverage grid then attaches no view evidence (`views_min` 0).

**Which extra photo helps most** (C1 `views_needed` on the laser GT and on recon scenes):
1. A head-on photo of each adjacent wall section from 2–4 m: the most frequent request on facade and delivery, where ETH3D's cameras see those walls edge-on.
2. A downward photo of the ground in front of the meter wall from chest height, out to about 13 ft (recon scenes, all facade rows).
3. "Keep walking past the right end" (electro, and the facade recon scenes).
4. An overhead band photo (prior and hybrid rows).

Ground coverage is the binding constraint. It stays at ≤ 1 % within 10 cm on facade for every sparse method, and at 18–40 % claimed only under the footprint-3in rule.

### 3.5 Walkaround houses (no GT; COLMAP images only, relaxed mapper)

| House | 40 guided (whole walk) | 60 guided | 40 careless (by the meter) | 60 careless | Tagged frames registered (60 guided) | π³ vs COLMAP |
|---|---|---|---|---|---|---|
| yt-KSXy (1080p) | 22/40 | 34/60 (2 models) | 40/40 | 60/60 | meter 3/3, corners 0/2, ground 0/1, overhangs 0/2 | 1–4.5 % of extent, 6–8° |
| yt-LCPB (1080p) | 3/40 | 22/60 | pending | 53/60 | meter 0/1, corners 1/3, ground 2/2 | 1.5 % / 0.8° (22 frames) |
| yt-Qm4b (480p) | 27/40 (2 models) | 52/60 | pending | pending | meter 2/2, corners 2/2, ground 1/1, overhangs 1/1 | 0.7–2.1 %, 1–10° |

Frames spread around a whole house (about 1 m or more and 6° or more apart) fragment. The same count concentrated on one or two sides registers fully. The capture must be dense per side, not thin around the house.

### 3.6 Scale

- **True scale:** as in the tables.
- **No-tool scale:** the rigid rescoring at A9's meter-make/model scale (0.6 % median, 2.0 % p90) and A8's weak-reference p90 (4.7 %) is queued (`rescale.py`) and **pending**. The analytic effect on the long distances is |error| × D: at 26 ft it adds 1.9 / 6.2 in (A9 median / p90) or 14.7 in (weak references, p90).
- **ARKit's own scale:** measured in the prior rows. With a +2.5–2.8 % scorer scale error it gives 1.8 / 7.3 in on facade.

## 4. Runtime and cost (4 vCPU, CPU only, often two queues contending)

- **COLMAP:** SIFT + exhaustive matching for 76 photos about 4 min, done once per scene. Mapping 20–140 s per set. A 70-photo house is about 5–7 min of CPU, about $0.02.
- **Feed-forward, one pass:** π³ 130 s (40 views, 7.5 GB RSS) to 470 s (70 views). DA3-Large 70–280 s. On a GPU, all under 30 s.
- **Scoring:** `score-cloud` 15–60 s per run; C1 about 10 s.

## 5. Failure modes (observed)

1. **π³ / DA3 as pose engines at 40–70 widely spread views.** π³ poses are 9 cm median but 0.3–1.6 m and 2–6° on the near-meter close-ups. At a 15 m standoff that stacks about 8 phantom copies of the meter wall. π³ depth is fine (34 of 40 views within 5 % of the laser scale) apart from a few views at 0.5–1.2×. DA3 alone gets 9–19 of 40 poses. A per-view consistency pass (`pi3c`) did not fix π³ because the poses themselves are wrong.
2. **iPhone-quality frames:** COLMAP camera error rises about 10×, and p90 rises from 2.5–4.5 to 14–26 in.
3. **Sparse spacing along a walk** (LaMAR no-depth, houses): 2–7 of 40 registered. Priors don't help; the prior model even bent (80 in).
4. **Scale:** ARKit no-depth scale is +2.3 to +5.5 % on real LaMAR long pairs, which costs 6–13 in at 15–26 ft.
5. **Harness issues found:**
   - (a) `taps.py` rescales GT pixels by width, which is wrong for cropped or different-FOV frames. It invalidates harness scores on A7's iPhone-FOV conditions (A7's own `combo_day` / `base` harness rows are affected too). I patched my local copy to project through the recon intrinsics, marked in `geomscore.py`. The DSLR rows are unchanged (2.51 → 2.64 in p90).
   - (b) On delivery_area the extractor raises `meter clicks hit no wall` for every reconstruction.
   - (c) On dense clouds the extractor splits the meter wall and coverage evidence is lost, so C1 decidability collapses.

## 6. Licence

- COLMAP / pycolmap BSD-3; π³ BSD-2; DA3-Large 1.1 Apache-2.0 (all commercial OK).
- VGGT-1B CC BY-NC (weights downloaded, not run); MASt3R CC BY-NC-SA (not run).
- ETH3D CC BY-NC-SA and YouTube frames: evaluation only. LaMAR CC BY-SA 4.0.

## 7. Unfinished / pending, and what going deeper would take

**Pending at wrap-up:**
- the rescore of the pre-fix iPhone-quality rows;
- the electro / delivery iPhone-quality sets;
- facade N70 DA3 alone;
- the no-tool scale rescoring (A9 median / p90, A8 weak p90);
- the LCPB / Qm4b careless house sets.

`score-verdicts` was not run: only facade has demo-profile labels, and C1 returned manual_review everywhere, agreeing with the laser verdicts. Queues: `~/a10/queue2b.sh`, `queue3b.sh`, `queue_houses2.sh`. Refresh the tables with `python summarize.py`.

**Next:**
1. Server recipe: COLMAP with the A6 prior plus fixed-ARKit fallback, a per-side density rule (at least 60–70 frames per 35 m side, or overlapping photos about 0.5 m apart), and scale from A9's meter read.
2. Fix the harness taps for non-rescaled cameras upstream, and the dense-cloud extractor, then re-measure C1 decidability on hybrid clouds.
3. A real non-Pro iPhone capture at a laser or tape site (no public dataset has one).
4. A GPU run of π³ / VGGT-Commercial with a windowed, COLMAP-anchored fusion.

**Shot list this supports** (about 40 photos, weighted to the meter side):
- a meter close-up, head-on;
- a slow walk along the meter wall at 2–4 m, one photo per step (about 0.5–1 m), to both natural ends and past them;
- each adjacent wall head-on from 2–4 m, plus the corners at 45°;
- 2–3 downward ground photos from chest height, out to about 13 ft;
- one looking up at the eave or overhang above the meter.

## Files

All under `internal/breadth/a10-sparse-photo/`:
- **Code:** `select.py`, `run.py`, `hybrid.py`, `rescale.py`, `rescore.py`, `lamar_a10.py`, `house.py`, `diag_depth.py`, `summarize.py`.
- **Selections and results:**
  - `selections/eth3d-*.json`;
  - `results/<scene>/<quality>/<set>/<method>.json`;
  - `results/lamar/`, `results/houses/`, `results/gt_c1.json`;
  - `results/summary.md` / `summary.json`.
- **VM only:** ETH3D and LaMAR data, clouds, COLMAP models, and scorer outputs under `~/a10/runs`.
