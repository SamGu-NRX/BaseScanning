---
cursor:
  subagentId: "bc-ff4d66a4-24c3-5a09-a83a-065b4e6b7bc5"
---

# A7: regular-iPhone degradation sweep (ETH3D facade + electro)

Real ETH3D DSLR photos and laser scans, turned into simulated regular-iPhone frames. The degradation model, and the non-LiDAR ARKit priors (A1's stored draw), are synthetic. Code, per-run JSONs and the generated tables are in `internal/breadth/a7-iphone-degradation/`. Status: interim. 10 of 17 conditions are complete for A1 and 9 for the feed-forward paths; see §8 for what is pending.

## 1. Verdict

**Conditional go: daylight and steady walking only. No-go for the SfM pose stage at dusk or with walking blur.**

- **In daylight, a regular iPhone keeps A1 (COLMAP + non-LiDAR ARKit priors) at reference quality.** The daylight combined walk (ISO 50, 1/1000 s, 30°/s hand motion, rolling shutter, HDR + sharpening, JPEG q90) still registers 73/76 (facade) and 39/45 (electro). Ground-free facade geometry stays in the bar: 1.6 / 4.3 in (median / p90), against 1.6 / 2.9 in on the DSLR reference.
- **These effects break A1 registration, and then no measurement comes out at all:**

| Effect (real per-frame values at 1920 px) | facade (76 frames) | electro (45 frames, glass facade) |
|---|---|---|
| Walking blur, 5.6 px median / 11.7 px p90 (`blur_8`) | survives (73/76) | **breaks** (32/45; prior alignment fails) |
| Walking blur, 11 px median / 22 px p90 (`blur_16`) | **breaks** (65/76, two models, meter wall missing) | **breaks** (14/45) |
| Dusk light, ~100 lux, ISO 400 at 1/60 s | survives (73/76), but geometry p90 degrades to 15.9 in | **breaks** (33/45; geometry p90 108 in) |
| Dusk light, ~25 lux, ISO 1600 | survives (73/76) | **breaks** (32/45) |
| Late dusk, ~6 lux, ISO 6400-equivalent | **breaks** (meter wall not reconstructed) | **breaks** (23/45) |
| Dusk walk: ISO 1600, 1/60 s, 30°/s (8.8 px blur), rolling shutter, HDR | **breaks** (66/76) | **breaks** (15/45) |
| iPhone field of view + resolution alone | survives (73/76) | survives (39/45; reference 41/45) |
| HDR tone mapping + sharpening | survives | survives (40/45) |
| Daylight combined walk | survives | survives |

- **The feed-forward depth models (π³, DA3-Large) barely degrade.** On the extraction-free surface metric (recon vs laser after ICP), electro stays at 1.0–1.7 in median surface error with 77–95 % completeness under every condition, including ISO 6400, 11 px blur and the dusk walk. Facade stays at 2.3–4.4 in with 32–83 % completeness, versus 2.3–3.1 in on the reference. The fragile part is the SfM **pose** stage, which uses SIFT features, not the depth models. The feed-forward rows use exact poses or exact scale, so they say nothing about ARKit's own tracking under blur and low light (not simulated; §5).
- **Against the 2–6 in bar, two effects dominate, independent of image quality:**
  1. The iPhone's narrower vertical field of view (53° vs the DSLR's 62°) thins out the wall base. The reference extractor then fits the "ground" to 0.6 m-tall street furniture, so every height KD is off by 20–36 in, with confident ±0.08 m claims. This happens in all 6 facade A1 iPhone runs that still produce a scene (datum off by −20 to −36 in).
  2. Non-LiDAR ARKit scale (A1/A2: −2 % ± 2 %) is still unsolved.

  Capture rules that prevent the breaks are in §6.

## 2. Inputs and what was produced

- **Real:** ETH3D `facade` (76 images) and `electro` (45 images), undistorted full-resolution DSLR (6200×4132, fx ≈ 3410), and the laser GT scenes, rebuilt with the harness (`build_gt.py`, `build_gt_c2.py`).
- **Synthetic iPhone model** (`degrade.py`). The target is an iPhone 15 main wide camera producing ARKit `capturedImage` frames at 1920×1440, JPEG q0.9 (capture-packet spec).
  - `base`: centred crop to the 26 mm-equivalent field of view (f = 1442 px, 67°×53° vs the DSLR's 84°×62°), area-resampled, a lens/demosaic MTF (σ 0.6 px), JPEG q90. Intrinsics are exact.
  - Every other condition adds stages in physical order:
    1. motion blur and rolling shutter (16 ms readout), in linear light, from one hand-motion draw per frame shared across conditions (lognormal magnitude, σ 0.5; pitch at 0.5× yaw);
    2. Poisson–Gaussian sensor noise, with a 10,000 e⁻ full well at ISO 50 per 12 MP-equivalent pixel, 4.41 pixels binned per output pixel, 2.5 e⁻ read noise, and colour-correction noise amplification;
    3. ISP non-local-means denoise scaled to the noise;
    4. Smart-HDR-like local tone mapping (wide-Gaussian base, so halos) and unsharp masking;
    5. JPEG or HEIC encoding.

  All sensor numbers are assumptions, not phone measurements. Light-to-exposure mapping at f/1.6 with ARKit's 1/60 s cap: ISO 50 ≈ 10,000 lux at 1/1000 s; ISO 400 ≈ 100 lux; ISO 1600 ≈ 25 lux; ISO 6400 ≈ 6 lux. Blur at 1/60 s is about 0.42 px per °/s of rotation. The real median / p90 per condition: `blur_8` 5.6 / 11.7 px, `blur_16` 11.1 / 22.2 px, `blur_32` 22.3 / 44.4 px, dusk walk 8.8 / 17.5 px, rolling-shutter skew at 30°/s 8.4 / 16.8 px. Visual check: `a7-iphone-degradation/results/conditions_contact.jpg` (1:1 crops of every condition).
- **Paths rerun on every condition:**
  - **A1:** A1's exact recipe (SIFT, exhaustive matching, fixed K, incremental SfM with position priors σ 0.3 m) using A1's stored `nonlidar_modern` seed-0 prior positions, so every condition sees the same simulated ARKit trajectory. Metric, rigid alignment.
  - **π³:** 16 views per scene (facade: A2's meter-wall set; electro: the 16 views nearest the meter and walls), aspect-matched network input (518×392). Two variants: (a) π³'s own poses, scaled by a Sim(3) to the exact camera centres (A2's "π³ + exact scale"); (b) π³ depth placed at the exact poses with one global scale ("π³ fused on a known trajectory", A2's recommended use).
  - **DA3-Large:** same views, fed exact poses plus the frame's intrinsics (504×378).
- **Scoring:** the shared scorer (`score-cloud`, rotation-aware alignment for π³/DA3), plus an extraction-free surface metric (`surface.py`). `dslr_ref` (A1/A2's original full-FOV input) was rerun with the same code as the reference.

## 3. Metrics against the laser

How to read the tables:
- KDs are compared on a **fixed set**: the KDs each path's `dslr_ref` run scored on truly observed elements. Errors are |error| median / p90 / max in inches.
- **Ground-free geometry** covers edge positions, widths, gaps and opening heights (top minus bottom). **Ground datum** is the median error of the ground-referenced KDs (meter height, sills, heads).
- **Bar** follows A1's 2–6 in rule on the raw fixed set: `in` = all within 6 in with p90 ≤ 4 in; `edge` = p90 ≤ 6 in; `out` = worse; `broken` = registration below 90 % of the reference, meter wall not reconstructed, or fewer than half the KDs kept.
- **Surface** is recon-to-laser distance after ICP (median / p90 in) and laser completeness within 5 cm.

Full tables, including π³'s own-pose variant: `a7-iphone-degradation/results/summary.md`.

### 3.1 A1, COLMAP + non-LiDAR ARKit priors (the leading pose path)

**facade** (fixed set: 21 KDs)

| Condition | Registered | Keypoints (median) | Verified pairs | Centre RMS | Scale err | Raw fixed set | Ground-free | Ground datum | Bar |
|---|---|---|---|---|---|---|---|---|---|
| dslr_ref | 76/76 | 11398 | 2042 | 14.3 cm | −1.42 % | 3.2 / 5.5 / 5.9 | 1.6 / 2.9 / 4.0 | −4.3 | edge |
| base | 73/76 | 13024 | 1852 | 14.3 cm | −1.45 % | 4.0 / 24.9 / 25.8 | 1.4 / 3.1 / 4.7 | −24.6 | out (datum) |
| noise_iso400 | 73/76 | 11808 | 1817 | 13.3 cm | −1.34 % | 3.6 / 37.7 / 93.7 | 1.5 / 15.9 / 59.3 | −35.7 | out |
| noise_iso1600 | 73/76 | 10963 | 1725 | 6.2 cm | −0.61 % | 16 of 21 kept: 0.5 / 19.9 / 20.5 | 0.3 / 0.9 / 1.2 | −19.3 | out (datum) |
| noise_iso6400 | 72/76 | 9982 | 1588 | 10.4 cm | −1.02 % | — | — | — | **broken** (no meter wall) |
| blur_8 | 73/76 | 11774 | 1524 | 14.4 cm | −1.45 % | 4.1 / 26.3 / 27.5 | 1.4 / 3.2 / 4.9 | −25.8 | out (datum) |
| blur_16 | 65/76 | 9700 | 1165 | 15.4 cm | −1.69 % | — | — | — | **broken** |
| hdr_sharp | 73/76 | 12588 | 1812 | 17.4 cm | −1.77 % | 3.6 / 37.7 / 81.4 | 1.9 / 9.1 / 46.9 | −20.2 | out |
| combo_day | 73/76 | 13540 | 1810 | 15.5 cm | −1.25 % | 16 of 21 kept: 6.4 / 20.2 / 21.4 | 1.6 / 4.3 / 8.4 | −19.7 | out (datum) |
| combo_dusk | 66/76 | 9288 | 1220 | 13.6 cm | −1.20 % | — | — | — | **broken** |

**electro** (fixed set: 21 KDs)

| Condition | Registered | Keypoints (median) | Verified pairs | Centre RMS | Scale err | Raw fixed set | Ground-free | Ground datum | Bar |
|---|---|---|---|---|---|---|---|---|---|
| dslr_ref | 41/45 | 6865 | 346 | 5.7 cm | +2.20 % | 2.6 / 5.1 / 7.0 | 3.3 / 5.2 / 7.0 | +2.4 | edge |
| base | 39/45 | 8022 | 272 | 7.0 cm | +2.69 % | 2.4 / 6.7 / 7.8 | 2.9 / 6.7 / 7.8 | +1.2 | out (marginal) |
| noise_iso400 | 33/45 | 4538 | 256 | 7.2 cm | +2.73 % | 3.4 / 29.0 / 182.7 | 3.6 / 108 / 183 | +0.9 | **broken** |
| noise_iso1600 | 32/45 | 4569 | 249 | alignment failed | — | — | — | — | **broken** |
| noise_iso6400 | 23/45 | 3427 | 204 | 56 cm | +5.9 % | — | — | — | **broken** |
| blur_8 | 32/45 | 3439 | 202 | alignment failed | — | — | — | — | **broken** |
| blur_16 | 14/45 | 1752 | 131 | 17.8 cm | +6.7 % | — | — | — | **broken** |
| hdr_sharp | 40/45 | 13808 | 272 | 6.0 cm | +2.30 % | 2.0 / 5.6 / 7.9 | 3.2 / 5.9 / 7.9 | +2.0 | edge |
| combo_day | 39/45 | 10121 | 264 | 8.9 cm | +2.64 % | 3.4 / 6.3 / 10.4 | 3.6 / 6.7 / 10.4 | +2.2 | out (marginal) |
| combo_dusk | 15/45 | 3697 | 152 | 20.3 cm | +7.1 % | — | — | — | **broken** |

What the A1 rows show:
- **The break mechanism is feature starvation.** Keypoints drop from about 8,000 to 1,750–4,500 on electro, and verified pairs drop sharply. Electro's glassy facade has little texture to begin with, so it breaks first.
- **Facade is more tolerant but falls over at the same qualitative points:** 11 px blur, ISO 6400, and the dusk walk.
- **The facade "out" verdicts on surviving runs are the ground-datum flip (§5.1), not image quality.** Their ground-free geometry stays at 1.4–1.9 in median.

### 3.2 Feed-forward paths (depth quality; poses exact or exact-scaled)

Surface metric, median / p90 in (completeness at 5 cm). Shared-scorer KD verdicts for the same runs are in `results/summary.md`.

| Condition | π³ at exact poses, facade | π³ at exact poses, electro | π³ own poses, facade (pose RMS) | DA3 fed poses, facade | DA3 fed poses, electro |
|---|---|---|---|---|---|
| dslr_ref | 2.7 / 7.9 (68 %) | 2.5 / 6.8 (47 %) | 2.3 / 7.8 (29 cm) | 3.1 / 9.6 (50 %) | 0.9 / 4.3 (94 %) |
| base | 3.6 / 8.9 (59 %) | 1.2 / 4.4 (86 %) | 2.9 / 8.4 (51 cm) | 3.6 / 9.7 (45 %) | 1.1 / 4.4 (89 %) |
| noise_iso400 | 3.5 / 9.0 (58 %) | 1.3 / 4.1 (93 %) | 3.1 / 8.4 (52 cm) | 3.2 / 8.1 (51 %) | 1.1 / 4.5 (87 %) |
| noise_iso1600 | 3.2 / 8.6 (54 %) | 1.2 / 3.7 (91 %) | 2.8 / 8.0 (60 cm) | 4.0 / 8.6 (38 %) | 1.1 / 4.4 (85 %) |
| noise_iso6400 | 3.4 / 9.4 (59 %) | 1.3 / 4.8 (85 %) | 3.0 / 9.5 (54 cm) | 2.9 / 8.1 (52 %) | 1.2 / 4.5 (81 %) |
| blur_8 | 3.7 / 9.2 (52 %) | 1.3 / 5.2 (85 %) | 3.1 / 8.3 (46 cm) | 4.4 / 9.2 (32 %) | 1.0 / 4.8 (91 %) |
| blur_16 | 3.9 / 9.5 (50 %) | 1.5 / 4.8 (84 %) | 3.6 / 8.6 (47 cm) | 2.8 / 9.7 (55 %) | 1.0 / 4.2 (89 %) |
| hdr_sharp | 3.2 / 9.2 (63 %) | pending | 2.8 / 8.5 (62 cm) | pending | pending |
| combo_day | 3.2 / 9.5 (65 %) | 1.2 / 4.0 (92 %) | 3.6 / 9.7 (65 cm) | 4.1 / 8.8 (37 %) | 1.7 / 5.3 (84 %) |
| combo_dusk | 2.7 / 7.4 (71 %) | 1.3 / 3.7 (91 %) | 2.3 / 7.2 (70 cm) | 3.3 / 9.8 (45 %) | 1.7 / 4.8 (77 %) |

- **Shared-scorer bar, π³ at exact poses on electro:** `in` for base (1.6 / 2.9 / 4.0 in) and for the daylight walk (1.9 / 3.7 / 4.5 in); `edge` for ISO 1600, blur_8 and HDR; `out` for the dusk walk (2.9 / 5.9 / 6.5 in, 21 of 26 KDs kept).
- **Facade π³ and DA3 KD verdicts swing between 6 and 26 kept KDs on near-identical clouds.** This is the extractor instability on dense clouds that A2 already flagged: the extractor keeps or drops whole groups of windows. It also produces isolated blow-ups (π³-at-exact-poses ISO 1600 p90 1016 in; π³ own-pose blur_16 p90 1402 in) from phantom walls. So the surface metric is the reliable signal for these paths.
- **π³'s own poses get 2× worse at the iPhone field of view** on this two-cluster, wide-baseline view set (camera RMS 29 → 51 cm, up to 70 cm at dusk). With the narrower field of view, π³ links the near (about 12 m) and far (about 43 m) station clusters less reliably. Use a known trajectory, not π³ poses, on sparse wide-baseline sets.

## 4. Runtime and cost (4 vCPU, no GPU; about 8 GB of the 16 GB was usable)

- **A1 per facade condition:** extraction 40–165 s, exhaustive matching 116–237 s, mapper 21–154 s, scorer about 1 min.
- **Degraded frames inflate matching time 4–5×** (blur_16: 1,145 s; ISO 6400: 907 s). With low inlier ratios, COLMAP's geometric-verification RANSAC runs to its trial cap. That is a real server-cost risk, and another reason to gate frames at capture.
- **π³ / DA3 inference:** 33–112 s per 16 views on CPU. The shared scorer on dense clouds takes 1–28 min (the dominant cost).
- **Sweep cost so far:** about 4.5 h of VM time. An oversubscription lesson: COLMAP plus PyTorch spinning thread pools on 4 cores slowed π³ about 30×. Cap threads per job.

## 5. Failure modes (all observed)

1. **Ground-datum flip (facade, all 6 surviving iPhone-FOV A1 runs; datum −20 to −36 in vs −4 in on the DSLR reference).**
   - The ETH3D photographer pitched the DSLR up at a tall facade. At the iPhone's 53° vertical field of view the wall base is thin, so the reference extractor's RANSAC ground snaps to the tops of street furniture 0.45–0.75 m tall, 1–4 m in front of the wall (checked in the laser).
   - Every height KD is then off by 20–36 in with a ±0.08 m claim. That is a confident wrong answer, not an "unsure".
   - Production needs both a capture rule (§6) and a ground check (lowest well-supported plane, with an "unsure" flag when support is thin).
2. **SIFT feature starvation → registration loss → prior-alignment collapse.** At 32 of 45 registered frames, electro's prior-aligned model lands with a 70× (ISO 1600) or 6× (blur_8) scale error. A1's pipeline has no guard against this; it needs a sanity check of the model's cameras against the ARKit trajectory.
3. **Meter wall not reconstructed** ("meter clicks hit no wall"): facade ISO 6400 and blur_16. No measurement is possible.
4. **Scorer pitfall, fixed in my harness copy:**
   - Problem: `taps.py` projects the perfect GT clicks through the DSLR camera and only rescales by the width ratio. Any input with a different field of view, crop or principal point (every phone frame) gets clicks on the wrong pixels, which gave about 20 in errors on every path before the fix.
   - Patch (`a7-iphone-degradation/harness-patch.diff`): project through the GT pose with the recon image's own intrinsics and size. It is a no-op for full-FOV inputs.
   - **Harness owner: please upstream it.**
5. **Harness drift since A1/A2 scored:** A1's stored non-LiDAR model now scores 1.73 / 4.11 / 4.76 in (was 0.84 / 2.90 / 5.20), and the door is now graded "observed". All A7 comparisons are internal: same code, same harness, `dslr_ref` rerun.
6. **Not simulated:**
   - ARKit tracking under the same blur and low light (the priors stay clean). In reality VIO also degrades, so these results are optimistic for A1 and for any ARKit-scaled path.
   - Lens distortion and chromatic aberration (the ISP corrects them).
   - Video codecs, and OIS (which would cut hand-shake blur, but not deliberate panning).

## 6. Capture rules that would prevent the observed breaks

Thresholds come from the first level at which a break was observed. Electro, the low-texture glass facade, sets the limits; facade tolerates about 2× more.

1. **Motion: keep the exposure-times-rotation blur under about 3–4 px.**
   - Electro breaks at 5.6 px median blur (blur_8); facade at 11 px.
   - At ARKit's 1/60 s cap this means turning at 7–10°/s or less while capturing: a slow, steady walk, no panning while moving.
   - App enforcement: drop frames whose `exposure.duration × |gyro rate| × f` exceeds about 4 px. The packet already carries exposure and IMU; the spec's current "drop above 1/60 s while moving" is too loose. Prompt "slow down" when the drop rate rises.
   - `blur_4` is pending to confirm the exact electro threshold.
2. **Light: daylight only, at 1,000 lux or more on the wall (ISO ≤ about 100 at 1/60 s).**
   - At about 100 lux (ISO 400) electro already breaks; at about 6 lux both scenes break. Dusk capture is no-go for A1.
   - App enforcement: block capture when EXIF `ISOSpeedRatings` is above 200 or `BrightnessValue` is below about 3.
3. **Framing: keep the wall base and at least 1 m of ground in frame in several views**, and hold the phone level rather than pitched up. The phone's 53° vertical field of view loses the ground that a DSLR keeps. This prevents the ground-datum flip (§5.1). Add a live "show the ground" prompt.
4. **No sustained fast pans between wall segments.** The 90°/s rolling-shutter run is pending; the 30°/s run is pending on the A1 lane. The blur rule already covers pans.
5. **HDR and sharpening need no rule.** `hdr_sharp` stays within reference registration on both scenes. JPEG q25 / HEIC q30 results are pending. Keep the spec's JPEG q0.9.
6. **Server-side guards:**
   - reject or flag a reconstruction if its registered fraction falls below about 90 % of the frames;
   - reject it if the SfM-to-ARKit similarity scale deviates more than 5 % from 1;
   - flag it if the ground plane has thin support.

## 7. Licence

- ETH3D: CC BY-NC-SA, evaluation only; derived frames stay on the VM.
- COLMAP / pycolmap: BSD-3. π³: BSD-2. DA3-Large 1.1: Apache-2.0.
- The degradation code is ours.

## 8. Unfinished, and what going deeper would take

- **Pending (both lanes still running on the VM, in tmux sessions `a7-lane-a1` / `a7-lane-ff`):**
  - A1 on both scenes, and π³ / DA3: `rs_30dps`, `jpeg_q25`, `heic_q30`, `combo_dusk_slow` (ISO 1600 at 10°/s: is a slow walk enough at dusk?), `blur_32`, `rs_90dps`, `blur_4`.
  - DA3 on both scenes and π³ electro for `hdr_sharp`.
  - Dropped from the grid as redundant: `noise_iso50` (inside `combo_day`), `jpeg_q50`, `combo_night`.
  - Rerun `python summarize.py > results/summary.md` to refresh the tables when the lanes finish.
- **Deeper work:**
  1. Real iPhone captures of one ETH3D-like wall at noon and at dusk, to validate the noise and blur model (every sensor number here is assumed).
  2. Simulate ARKit tracking degradation together with the images.
  3. Try blur- and noise-robust learned features (SuperPoint/ALIKED + LightGlue) in A1. The breaks are feature starvation, so this is the most likely fix.
  4. Harden the harness extractor against dense clouds and the ground snap.
  5. Rerun with multiple hand-motion and ARKit seeds; each cell here is a single draw.

## Files (all under `internal/breadth/a7-iphone-degradation/`)

- **Code:**
  - `setup.sh` (VM rebuild);
  - `degrade.py` (iPhone model and conditions);
  - `a1run.py` (A1 path + scorer);
  - `ffrun.py` (π³ / DA3 + scorer);
  - `surface.py` (surface metric);
  - `summarize.py` (tables);
  - `rescore.py`;
  - `master2.sh` (two-lane driver; `master.sh` / `queue.sh` are the earlier serial drivers);
  - `harness-patch.diff` (the perfect-click intrinsics fix).
- **Results:**
  - `results/<scene>/<A1|pi3|da3>/<condition>.json` (per run: registration, timings, scores, surface);
  - `results/summary.md` and `results/summary.json` (the generated tables);
  - `results/conditions_contact.jpg` (visual check of every condition).
- **VM only:** degraded frames (`~/a7/work`), scorer outputs (`~/a7/scores`), GT builds (`~/a7/gt`), logs (`~/a7/logs`), and the patched harness copy (`~/a7/harness`).
