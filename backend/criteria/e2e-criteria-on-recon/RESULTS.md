---
cursor:
  subagentId: "bc-361c113f-1d37-5324-a753-5d980d2e4ade"
---

# E2E: criteria on photo-built 3D models (photos → A1 model → scene → C1 / C2 verdicts)

Code, per-run results and taps: `internal/breadth/e2e-criteria-on-recon/` (`code/`, `results/summary.{md,json}` = every site × variant × engine row, `results/flips.json`, `results/scores.json`). Heavy artefacts (clouds, grids, verdicts) stay on the VM under `~/e2e/work/`.

## 1. Headline

- **Photo-built models never produced an unsafe pass or a false reject.** Across 102 photo-model verdicts on ETH3D, 87 of them scored against labels (including the World Labs worlds), there were 0 unsafe passes and 0 false rejects. The 2 walkarounds had no labels to score against. The catch: **no ETH3D site has a pass anywhere in its labels, so this isn't yet a pass-safety test.**
- **Site decisions: 100 % manual review, on the laser and on every photo model.** Photo and laser therefore agree on every site decision where a photo scene exists: facade, electro, and all four walkaround or World Labs verdict sets. Delivery never got a photo scene, because its meter jamb wasn't reconstructed.
- **What changes is decidability.** The share of candidate × criterion cells the engines decide (pass or fail rather than unsure) drops as follows (mean over the 7 engine/profile runs):

| Site | Laser | Photo, true scale | Photo, ARKit-only | Photo, meter make/model (A9) |
|---|---|---|---|---|
| Facade | 38 % | 13 % | 11 % | 12 % |
| Electro | 75 % | 25 % | 18 % | 24 % |

  - C1 PR #11 (demo rules) holds up best. On facade it decides 61 % of cells on the laser, 37 % on photos at true scale, 15 % with ARKit scale and 29 % with meter-model scale. On electro the same figures are 82 %, 56 %, 21 % and 54 %.
  - C1 rules-3d drops to 1–7 %, because its evidence rule makes almost everything on photo models unsure.

## 2. Recipe (as updated mid-run)

- **Poses and geometry.** A1 COLMAP incremental with simulated non-LiDAR ARKit position priors, **σ 3 cm and no robust loss** (A6's real-iPhone recipe). This replaces σ 0.3 m with robust loss. I re-ran it on all three sites: 76/76, 41/45 and 44/44 images registered, 0.31–0.34 px reprojection error. Patched A1 code: `code/a1_sfm_tight_patch.py`, driven by environment variables.
- **A1 leads (A6).** The measurement cloud is A1's sparse points (`a1t`).
  - A second variant, `a1t_pi3`, adds π³ dense depth run on the refined A1 poses. It uses 16-view windows at 504×336, one depth scale per view from SfM depth, and keeps a point only when two views agree on it within 1 %.
  - The first π³ fusion (one scale per window, one agreeing view within 2 %) left the wall 13 cm thick. With it the extractor found 6–12 key distances at a 9.7 in median, so I dropped it.
- **Scale modes** (applied about the meter, so the error grows with distance from it):
  - `true`: error 0, declared bound 0.
  - `arkit`: the model's own ARKit scale, bound 6 %. With tight priors that error is **−2.95 % on facade and +2.48 % on electro**, versus −1.17 % and +2.2 % under the old recipe. Tight priors lock the SfM onto ETH3D's simulated drift, which A6 says is larger than real ARKit.
  - `meterModel`: A9's method (meter make and model read from close-ups). Median error +0.6 %, applied on the adverse side, with A9's median declared bound of 1.4 %.
  - `meterP90`: the same method at its p90, +2.0 % with a 2.0 % bound.
- **Scenes.** Built with the reference extractor, using the harness's perfect meter taps and opening clicks, and ground gravity fixed to within 3° (ARKit gravity).
  - Perfect semantics copied from GT: ground-region materials, and the synthetic electro gas meter and delivery AC.
  - C2 carves its grid from the model's own rays (z-buffered recon cloud, 75° cutoff). It indexes spots on the GT chain rescaled by the scale error, and takes coverage evidence from the photo model.
- **Engines.**
  - C1 PR #11 (`run-c1`, demo rules).
  - C1 rules-3d, profiles A/B/C/S (`from_base_scene` + `to_verdict`).
  - C2, demo and S.
  - All verdicts were scored with `score-verdicts` against `labels-v0` A/B/C/S and the facade demo start-range labels.

Geometry of the scenes (key distances vs laser, median / p90, from `results/eth3d/*e2e_info.json`):

| Site | a1t true | a1t arkit | a1t meterModel | a1t_pi3 true |
|---|---|---|---|---|
| Facade | 35 KDs, 0.59 / 6.6 in | 35, 3.0 / 12.9 in | 31, 0.74 / 3.2 in | 30, 1.2 / 4.2 in |
| Electro | 38, 2.5 / 5.0 in | 35, 2.5 / 5.0 in | 29, 1.9 / 3.8 in | 66, 1.3 / 4.3 in |
| Delivery | no scene | no scene | no scene | no scene |

For delivery, the meter clicks hit no wall. The 1.7 m jamb between the sliding door and the corner has almost no points, in both sparse and fused clouds, because every camera sits on one side of it.

## 3. Per-site table

"Laser" = the same engines on the harness GT scene. "Site" = site decision. "Fail pos" = share of battery positions that fail (the rest are unsure; none pass anywhere). "Flip cells" = share of position × criterion cells whose outcome differs from the laser verdict.

Engine columns are C1 PR #11 / C2 S. C1 rules-3d is in `summary.md`: always manual review, with 0–15 % fail positions.

| Site | Label (A/B/C/S) | Laser | Photo true | Photo ARKit | Photo meterModel | Photo meterP90 | Flip cells (true → ARKit → meterModel) |
|---|---|---|---|---|---|---|---|
| Facade | review ×4 (C: pass also acceptable, unscored) | review; fail pos 78 % / 80 % | review; 73 % / 82 % | review; 10 % / 80 % | review; 95 % / 79 % | review; 90 % / 78 % | C1: 35 → 55 → 53 %; C2: 39 → 42 → 40 % |
| Electro | review ×4 | review; 100 % / 100 % | review; 100 % / 95 % | review; 90 % / 98 % | review; 100 % / 96 % | review; 100 % / 94 % | C1: 52 → 71 → 67 %; C2: 34 → 35 → 37 % |
| Delivery | A/B review; C/S reject | review (C1-3d, C2 S; over-caution vs C/S) | no scene → review | no scene | no scene | no scene | – |

Scores against the labels, over every photo verdict:
- 0 unsafe passes, 0 false rejects, 0 missed reviews.
- Site agreement: 100 % on facade and electro.
- Facade demo start-range agreement: 1.0 for C1 PR #11 and C2 at every scale mode (434 positions), the same as the laser.
- Delivery with no scene counts as manual review. That agrees with A/B and is over-caution against C/S (so is the laser verdict).

## 4. Why criteria flip (laser vs photo, per position)

Causes are attributed per flipped cell:
- **missing:** the photo model has no candidate there, because that wall wasn't reconstructed.
- **scale:** the true-scale photo model agrees with the laser there, so the scale mode caused the flip.
- **shape:** the true-scale photo model already disagrees (geometry or coverage).

- **Missing coverage is the largest cause on facade.** Sparse A1 recovers w1 well, but w2 and w3 are seen edge-on: 546–2 790 missing cells, depending on the chain the extractor built. The fused π³ variant finds a phantom wall left of w1 and still misses w2/w3.
- **Scale drives the ARKit-mode losses.** Under the ARKit bound (6 % × distance), decisive fails turn into unsure:
  - C1 PR #11 facade fail positions go 73 % → 10 %;
  - scale is the cause of 1 081 of the 3 551 flipped cells on facade, and 693 of the 2 381 on electro.
  - With meter-model scale (1.4 % bound) the fails come back: 95 % on facade, 100 % on electro.
- **Shape/coverage causes most of the rest.** The photos' own coverage grades regions grazing or unobserved that the laser GT grades observed, so decisive → unsure dominates, with 700–2 400 cells per run. π³ doesn't fix this, and on facade and electro it adds walls and headroom noise.
- **Some flips are real disagreements**, where a decisive laser outcome becomes the opposite decisive outcome:
  - **pass → fail** (conservative): C2 `wall_backing`, `headroom`, `facing_gap` and `ground_surface`, 100–250 cells per run. The photo cloud is noisier, so C2 sees clutter.
  - **fail → pass** at criterion level (unsafe in principle): facade C1 `opening_clearance` 10 positions at true scale; electro C1 `opening_clearance` 19 (π³, meterModel); C1-3d S `headroom` 3–7; C2 `wall_backing`/`headroom` 1–7.
  - None of these reached a candidate or site pass, because another criterion still failed or was unsure at every one of those positions. They are the cases to watch once a site with passing spots is labelled.

## 5. Walkaround houses (image-only; the YouTube houses have no ARKit)

Of the houses the dataset marks as registering well, these three have a visible meter. None has labels in `docs/house-labels.md`; the labelled houses register poorly. Here, pycolmap 4.2 ran with the dataset's relaxed settings. Scale is a passive 1.5 m camera-height prior (7 % bound). No meter plate is readable in these videos, so A9's method wasn't possible. The meter and AC were tapped by hand; KSXy also has one window clicked by hand. Only C1 engines were run (C2 needs its labelled-scene format).

| House | Registered | Scene | Site (all 5 engine/profiles) | Positions fail / unsure | Cells decided | Dataset note |
|---|---|---|---|---|---|---|
| `yt-Qm4b1cw8pV4` | 112/141 | w1 6.3 m; meter 1.63 m up | review ×5 | 3–8 % / 92–97 % | 37–43 % | "likely PASS pending side-yard width" |
| `yt-KSXy6LFwQU4` | 176/176 | w1 + w2, corner inferred; AC, window | review ×5 | 0–47 % / 53–100 % | 25–29 % | "manual_review / probable FAIL" |
| `yt-LCPBf86oloE` | 44/183 (6 fragments) | none | – | – | – | the meter-wall frames t0178–t0184 (blank stucco close-ups) don't register without ARKit |

- The pipeline decides about 25–43 % of criterion cells and sends 100 % of site decisions to review.
- The dominant unsure causes are unobserved ground strips (every ground-dependent check: drive, pool, gas, AC, ground surface), unexplored wall ends, and headroom.
- Nothing contradicts the dataset notes, but none of these houses can be scored.

## 6. World Labs (A5, `metric-flagged` scenes, generated regions honoured)

- Facade and electro both come out as manual review under every engine and profile, with 0 unsafe passes and 0 false rejects.
- Decidability is the lowest of any variant: 6 % (facade) and 12 % (electro) of cells, with 91–93 % of positions unsure. Generated and unobserved regions are correctly treated as unknown.
- C1 PR #11 on electro fails 37 % of positions, from walls placed with a +12 % scale error.
- C2 wasn't run on these worlds.

## 7. Limits and bugs found

- **No pass test.** No ETH3D site has a labelled pass, and position-level labels exist only for facade (demo rules) and delivery. So "0 unsafe" is necessary but not sufficient. The next test needs a site with passing spots, such as facade under C with a pixel-footprint ground rule.
- **Simulated ARKit.** ARKit error is simulated, and the tight priors inherit its large simulated drift. Real-phone scale error should be smaller (A6).
- **Harness bugs found:**
  - The C2 verdicts on the fused facade models fail `validate`, because `worst_best_incidence_deg` reaches 90.2°, above the schema's maximum of 90 (coverage rounding). Those 6 verdicts don't exist.
  - The reference extractor emitted a reversed opening span (facade door on w3). The PR #11 adapter then crashed. I sort spans in `e2e_scene.py`.
- **Perfect inputs.** Meter taps, opening clicks, materials and fixtures are perfect (GT or hand) everywhere. The numbers measure geometry, coverage and scale, not detection.
