---
cursor:
  subagentId: "bc-3d8b8a80-8af0-5c5e-85bb-bebf6e1707f2"
---

# A8: metric scale without LiDAR (breadth result)

Code, annotations and raw results: `internal/breadth/a8-scale/` (see "Files"). Real images and laser everywhere except where marked **(sim.)** or **(model)**.

## 1. Verdict

**Go: one homeowner tape distance of at least 10 ft along the meter wall is the primary scale source.** Cross-check it against a head-on meter close-up and ARKit's own scale, and prompt a re-measure when two precise sources disagree. Expected scale error: **0.4 % median, 0.9 % p90, 1.1 % p95**. That is 1.5 in median and 3.6 in p90 on a 26 ft distance (1.1 / 2.7 in at 15 ft), shape error included, with 99 % of houses within 6 in at 26 ft. About 14 % of captures get a re-measure prompt.

**No-go as the primary source:**
- ARKit alone. Non-LiDAR p90 is 5.7 % (modern, sim.) to 17.8 % (2018, sim. from real ADVIO), which gives 18–56 in at 26 ft.
- Learned metric depth. Scene-level error measured here ranges from −12.5 % to +20.3 %.
- Any single known-size object. The measuring is precise (0.1–1.4 %), but which feature was measured and what size the object really is are not, so the p90 is 6–15 %.
- Several weak references combined with no homeowner action: 1.7 % median, 4.7 % p90, 5.4 / 14.9 in at 26 ft, only 54 % within 6 in.

## 2. Inputs and outputs

| Input | Real / sim. | Use |
|---|---|---|
| ETH3D `facade` (76 DSLR frames, laser), `electro` (45, laser) | real | Accuracy: reference lengths in A1's scale-free incremental COLMAP models vs the laser, same pixels |
| Tanks and Temples `barn` (137 frames at 1080p, laser-registered T&T poses) | real | A US building (80 in class man door, lap siding, roll-up door). Our own COLMAP SfM, with metric scale from a Sim(3) onto the T&T metric camera centres (1.4 cm median residual over a 221 m loop). `Barn.ply` could not be downloaded (Google Drive quota), so barn truth is the laser-registered cameras, not the laser points |
| Reference objects | real, hand-annotated | 41 view measurements of 15 objects: doors (height, width), garage door, windows, brick courses, siding boards, and "tape" segments of 2.6–5 m between crisp edges. Rough edges clicked on one frame, projected into other frames through the SfM, then every frame snapped independently (sub-pixel strongest-gradient line fit) |
| ARKit non-LiDAR scale | **sim.** | A1's 50-seed study: `nonlidar_modern` is an assumed profile; `nonlidar_2018` is calibrated on real ADVIO iPhone measurements. SfM+ARKit fit scale, bootstrapped |
| Learned metric depth | real | MoGe-2 ViT-S and Metric3D v2 ViT-S, 16 frames per scene at 1024 px, true focal length given. Scale = median of predicted depth / SfM depth at visible SfM points |
| US object sizes | **(model)** from standards and datasheets | Meter base (ANSI C12.10; Itron CENTRON datasheet), doors (6'8" slab; IRC R311.2 minimum 78 in clear), brick (modular 3 courses = 8 in; standard, queen, king, engineer), siding exposures |
| Homeowner tape reading | **(model)** | N(0, 1/4 in), plus 5 % gross errors of ±6–12 in |

Produced: per-view segment measurements (`results/<scene>/segments.json`), a summary (`results/segments_summary.md`), learned-depth scale per image (`results/<scene>/mdepth_*.json`), a 20 000-house Monte Carlo per method and combination (`results/combine.{md,json}`), and shared-scorer runs on the facade with the cloud scaled by each method's error (`results/facade/scorer/`).

## 3. Metrics against ground truth

### 3.1 Measuring a reference in a scale-free reconstruction is precise (real)

All numbers are view-to-view, i.e. the same physical object measured from different photos with independent edge snapping.

| Reference (scene) | Views | Unit size measured | View-to-view SD | Recon vs laser, same pixels |
|---|---|---|---|---|
| Tape-like segment, 4.96 m between window jambs (facade, 13.6 m standoff) | 4 | 195.3 in | **0.16 %** (about 6 mm per endpoint) | −0.43 to −0.58 % |
| Tape-like segment, 2.65 m between door edges (electro) | 2 | 104.3 in | 0.26 % | −0.14, −0.18 % |
| Lap siding, 12 boards (barn) | 6 | 6.72 in per board (6.70–6.76) | **0.34 %**; auto-count right 6 of 6 | camera truth |
| Brick courses, 27 courses (facade) | 3 | 2.98 in per course (7.57 cm, European) | **0.12 %** when counted right; the automatic counter was right in 1 of 4 views (it locked onto 6-course colour bands or a harmonic) | −0.57 to −0.66 % |
| US man door, outline (barn) | 5 (+1 gross) | **86.8 in** (85.6–88.2), frame head to threshold | 1.35 % | camera truth |
| Double steel doors, width (electro) | 2 + 2 | 79.8 / 83.4 in | 2.0 / 4.3 % (leaf edge vs the jamb seen obliquely) | −0.17 to +0.51 % |
| Roll-up garage door (barn) | 2 | views disagree by more than 25 % | – (degenerate edge snap) | – |

Across all 17 ETH3D measurements, the recon (× true scale) vs the laser for the same pixels is **−0.27 % mean, SD 0.35 %** (range −0.66 to +0.51 %). That is the local-scale fidelity of the SfM model at 5–15 m standoff. 7 of 41 view measurements were dropped (edge not found, or plane fit failed on textureless door leaves before track-based points were used).

**Takeaway.** Once the right feature is identified and the model is scale-free, measuring it costs only 0.1–0.4 % (crisp edges) or about 1.4 % (door outlines). Precision is not the problem; the object's true size and the feature's identity are (§3.2).

### 3.2 Real-world variation of known-size objects (model, from standards and datasheets)

| Reference | Nominal | Variation that goes 1:1 into scale | Verdict |
|---|---|---|---|
| Meter base flange | 6.95 in (Itron CENTRON: 17.7 cm base; ANSI C12.10 interchangeability) | Base: about ±0.5 %. The cover/dome is 6.29 in (poly) or 6.42 in (glass), so outlining the cover instead of the base is −8 to −10 %. Sockets and cans vary from 8 × 11 to 13 × 19 in (unusable) | Good **check**, not primary: 44 px across from a house photo (2.2 % per edge-pixel pair) vs about 500 px in a 1 m close-up |
| Door leaf height | 80 in (6'8"); older 78 in, newer 84 / 96 in | Leaf 80 in on about 80 % of doors (assumed share). The visible outline is not the leaf: the barn outline read 86.8 in (**+8.5 %**) | Weak prior only |
| Door width | 36 / 34 / 32 / 30 in | ±6–10 % plus leaf/jamb ambiguity (2–4 % measured) | No |
| Brick course | Modular 2.667 in (3 = 8 in); standard 2.625, queen/king 2.75–3.0, engineer 3.2 | −2 to +20 % by brick type; the facade's European course (2.98 in) read as modular would be −10.5 % | No, unless the type is known |
| Siding reveal | Vinyl 4 / 4.5 / 5 / 6 / 7 in; fibre cement and hardboard 4–8 in (barn: 6.72 in) | ±20–35 % | No |

### 3.3 Learned metric depth as a scale prior (real)

Scene-level scale error (median of 16 per-image scales), with the per-image p10 to p90 in brackets:

| Scene | MoGe-2 ViT-S | Metric3D v2 ViT-S |
|---|---|---|
| electro (laser) | +16.8 % (+0.5 to +33.1) | +6.8 % (−4.9 to +17.6) |
| facade (laser, 13–15 m standoff) | −12.5 % (−18.8 to −3.2) | **+20.3 %** (+11.8 to +32.1) |
| barn (laser-registered cameras) | +3.5 % (−3.7 to +16.3) | −9.1 % (−17.4 to +2.0); A3 found −10.1 % |

The error is scene-dependent, as large as ±20 %, and the two models don't even agree in sign (facade). This matches A2/A3 (MapAnything −16 to −36 %, raw MoGe-2 +9 %). Use it only as a sanity prior with about a 12 % 1σ bound, e.g. to reject a tape that was entered in the wrong unit.

### 3.4 ARKit's own scale (sim., from A1)

SfM with an ARKit Sim(3) fit removes drift but keeps the session scale error:

| Profile | Median | p90 |
|---|---|---|
| `nonlidar_modern` (assumed) | 2.4 % | 5.6 % |
| `nonlidar_2018` (real ADVIO iPhone split) | 7.0 % | 17.5 % |

The user's quoted range of −1.2 % to −17.9 % sits inside these draws.

### 3.5 Error on long distances by method (Monte Carlo, 20 000 houses)

Measured terms feed the model: recon fidelity, endpoint and edge precision, door-outline spread, the learned-depth scene errors, and the ARKit draws. Size variation and homeowner tape errors are modelled (§2). The long-distance error is |scale error| × D plus shape error (1σ 1.25 in, from A1's observed facade key distances).

| Method | Abs scale error, median / p90 / p95 | 15 ft error, median / p90 (in) | 26 ft error, median / p90 (in) | 26 ft within 6 in | 15 ft within 2 in |
|---|---|---|---|---|---|
| ARKit non-LiDAR, modern (sim.) | 2.4 / 5.7 / 6.9 % | 4.5 / 10.8 | 7.6 / 18.2 | 40 % | 23 % |
| ARKit non-LiDAR, 2018 (sim., real ADVIO) | 7.1 / 17.8 / 19.4 % | 12.9 / 32.5 | 22.1 / 55.8 | 5 % | 3 % |
| Learned metric depth | 11.0 / 20.1 / 21.4 % | 19.7 / 36.2 | 34.2 / 62.6 | 4 % | 2 % |
| Meter base, from a house photo at 4.5 m | 1.8 / 5.9 / 9.3 % | 3.4 / 10.9 | 5.7 / 18.7 | 52 % | 31 % |
| Meter base, close-up at 1 m | 0.6 / 7.6 / 9.4 % | 1.4 / 12.4 | 2.0 / 22.8 | 88 % | 66 % |
| Door leaf 80 in | 2.1 / 8.5 / 13.0 % | 4.1 / 15.5 | 6.6 / 26.6 | 47 % | 30 % |
| Brick, modular assumed, counted | 1.0 / 15.4 / 16.7 % | 2.4 / 26.5 | 3.6 / 47.2 | 61 % | 45 % |
| Siding, 6 in assumed | 19.6 / 49.7 / 50.3 % | 34.9 / 89.3 | 60.9 / 154.9 | 14 % | 11 % |
| Tape 6 ft | 0.5 / 1.5 / 7.1 % | 1.3 / 3.6 | 1.9 / 5.2 | 93 % | 69 % |
| **Tape 10 ft** | 0.4 / 1.2 / 2.1 % | 1.2 / 3.2 | 1.6 / 4.4 | 94 % | 74 % |
| Tape 10 ft, no gross errors | 0.4 / 1.0 / 1.2 % | 1.1 / 2.7 | 1.5 / 3.7 | 99 % | 77 % |
| ARKit (modern) + depth | 2.2 / 5.6 / 6.9 % | 4.1 / 10.3 | 6.9 / 17.6 | 45 % | 27 % |
| Weak refs, no homeowner action: ARKit (2018) + meter from house photo + door + depth | 1.7 / 4.7 / 6.0 % | 3.2 / 8.7 | 5.4 / 14.9 | 54 % | 33 % |
| Weak refs + meter close-up | 0.6 / 6.8 / 8.7 % | 1.4 / 11.2 | 2.1 / 20.6 | 87 % | 65 % |
| **Recommended: tape 10 ft + meter close-up + ARKit, re-measure on conflict** | **0.4 / 0.9 / 1.1 %** | **1.1 / 2.7** | **1.5 / 3.6** | **99 %** | 78 % |

Combination rule: an inverse-variance mean in log space with 3σ gating. If two precise sources (declared SD ≤ 1.5 %) disagree by more than 3 joint SDs, the homeowner re-measures (14 % of houses); if they still disagree, the confirmed tape wins. Declared SDs: tape 0.6 %, meter close-up 1 %, meter from a house photo 3 %, door 3.5 %, ARKit 3 % (modern) / 10 % (2018), depth 12 %. Coverage of 2 × declared SD is 89–93 %, so the server's 95 % bound should be about **2.5 × SD** (§8).

Against the criteria:
- 6 in on a 26 ft route needs ≤ 1.9 % scale error with no shape error, and only the tape paths meet it at p90.
- 2 in at 15 ft needs ≤ 1.1 %, and the recommended path meets it for 78 % of houses; the rest is shape error.
- The 6 in B-profile conduit rule remains out of reach, as C1 already said.

### 3.6 Shared scorer, ETH3D facade (real geometry; A1 incremental sparse cloud scaled by each method's error; `score-cloud --metric`)

| Run | applied scale err | KDs scored | all KDs median / p90 (in) | KDs < 1 m median | KDs 1–3 m median / max | KDs > 3 m: n, median / max (in) | scorer scale err |
|---|---|---|---|---|---|---|---|
| oracle | +0.0 % | 31 | 0.43 / 7.5 | 0.5 | 0.1 / 30.6 | 5, 5.8 / 10.1 | +0.17 % |
| tape | +0.6 % | 31 | 1.18 / 11.7 | 2.4 | 0.5 / 25.2 | 5, 7.8 / 15.8 | +0.75 % |
| plus1p5 | +1.5 % | 31 | 1.12 / 5.9 | 2.2 | 1.1 / 33.9 | 5, 1.1 / 2.1 | +1.66 % |
| minus1p5 | -1.5 % | 31 | 1.85 / 10.9 | 4.0 | 0.9 / 14.4 | 5, 6.3 / 15.5 | -1.25 % |
| arkit_modern_med | -2.4 % | 35 | 2.32 / 10.4 | 3.6 | 1.6 / 170.1 | 8, 6.1 / 118.8 | -2.24 % |
| arkit_modern_p90 | -5.6 % | 35 | 4.72 / 24.7 | 5.2 | 3.5 / 167.4 | 8, 9.2 / 107.5 | -5.40 % |
| arkit_2018_med | -7.0 % | 35 | 6.67 / 31.0 | 6.6 | 4.6 / 165.9 | 8, 13.9 / 101.5 | -6.78 % |
| arkit_2018_p90 | -17.5 % | 35 | 13.57 / 67.7 | 16.8 | 11.1 / 158.1 | 8, 27.8 / 105.8 | -17.25 % |
| brick_modular | -10.0 % | 35 | 9.53 / 45.0 | 9.6 | 6.6 / 163.4 | 8, 18.3 / 88.3 | -9.86 % |
| moge2 | -12.5 % | 35 | 9.32 / 56.5 | 12.0 | 7.9 / 161.2 | 8, 19.6 / 84.4 | -12.37 % |
| metric3d | +20.3 % | 23 | 20.11 / 48.6 | 20.1 | 14.0 / 29.7 | 4, 38.1 / 49.3 | +4.28 % |

"KDs > 3 m" are the long key distances; the 88–170 in maxima in the runs at −2.4 % and beyond are the extractor splitting the rescaled chain differently (phantom corner), not scale. The Metric3D run aligned with only 23 key distances scored and a scorer-estimated scale of +4.3 % (alignment degraded at +20 %), so read its row as a lower bound.

The scorer carries the scale through: its estimate matches the applied error to within 0.25 points in every run but Metric3D's. The long-distance error then grows with it: the median over key distances > 3 m is 5.8 in (oracle), 6.1 (ARKit modern median), 9.2 (modern p90), 13.9 (2018 median), 18.3 (brick as modular), 19.6 (MoGe-2), 27.8 (2018 p90) and 38.1 in (Metric3D).

On this sparse cloud the oracle's long key distances already carry about 6 in of extractor offset. The tape (+0.6 %) and ±1.5 % runs therefore sit inside the extractor's run-to-run noise (long median 1.1–7.8 in), and the harness cannot separate a 1 % scale error from extraction noise here. The error-versus-distance model in §3.5, built on measured components, is the cleaner measure below 2 %. The scorer confirms it above 2 %.

## 4. Runtime and cost per house (4 vCPU, no GPU)

- Reference measurement: under 1 s per object-view once the SfM exists (a plane fit on the image's own SfM tracks plus edge snapping). The combination is negligible.
- Learned depth, if used as a prior: MoGe-2 ViT-S 3.4–3.7 s per image and Metric3D ViT-S 2.4–3 s per image at 1024 px, about 1 min for 16 frames. A GPU would not change accuracy.
- The SfM itself (A1's stage) is shared with the rest of the pipeline: barn, 137 frames, exhaustive matching took 15.5 min (matching 765 s); spatial matching from ARKit poses would cut that.
- Cost: under $0.01 per house on top of the SfM.

## 5. Failure modes (observed)

1. **Feature identity, not precision.** The door outline read 86.8 in against an 80 in leaf (+8.5 %). Door widths flip between the leaf and the jamb (2–4 %). A meter detector that outlines the cover instead of the base flange is −8 to −10 % (model). Every single-object reference has a heavy tail, so it must be checked by a second, independent source.
2. **Counting repeats.** Brick courses at about 7 px per course: the automatic count was right in 1 of 4 views. Siding boards at 32–48 px: 6 of 6. Counting needs 20 px or more per unit, i.e. a close-up, and brick or siding sizes are ambiguous anyway (§3.2).
3. **Textureless faces.** Door leaves and roll-up doors have few SfM points. Plane fits failed or picked the far side of the building until the fit used only points tracked in that image (barn, 360° loop). The roll-up door's two views disagreed by more than 25 % (degenerate snap). The server must use track-visible points and reject references whose views disagree.
4. **Learned depth bias is scene-level** (±20 %), and the two models disagree in sign on the facade.
5. **Gross tape errors** (misreads, the wrong feature) survive a 10 % ARKit gate. Only a second precise reference (the meter close-up) catches them, which is why the re-measure prompt exists.
6. Environment: `Barn.ply` download blocked by the Google Drive quota. Metric3D needed `mmcv-lite` and a CPU patch (`device="cuda"` hard-coded in its depth-bin code).

## 6. Licence

- Code here: ours. COLMAP / pycolmap: BSD-3.
- MoGe-2: code MIT, DINOv2 parts Apache-2.0 (commercial OK). Metric3D v2: code BSD-2; the weights licence still needs verifying (as A3 noted).
- ETH3D: CC BY-NC-SA 4.0 (evaluation only). Tanks and Temples: CC BY 4.0 (verify before external use).
- ANSI C12.10 figures were read from public excerpts and a manufacturer datasheet; no standard text is redistributed.

## 7. What going deeper would take

1. **Real phone captures with a real tape**: the PR #7 tape protocol on a current non-LiDAR iPhone. This replaces the assumed `nonlidar_modern` profile, the homeowner-error model and the 6 mm endpoint term (DSLR at 2048 px here; iPhone 1× frames at 4032 px should be better).
2. **A real meter in laser truth.** None of the three scenes has one, so the meter-base term is modelled from edge precision. One laser or LiDAR-scanned house with a meter, or a tape-measured meter wall, would validate the ellipse fit and the cover-vs-base confusion rate.
3. **Automate the reference finder**: a VLM or OWLv2 proposal, then the same edge snapping and track-based plane, with the views-disagree rejection. B1's loose VLM boxes (IoU about 0.5) are not precise enough on their own.
4. **Put the scale block into the contract** (§8) and have C1 consume `plus_minus` including the scale term.
5. Rerun the shared scorer on a dense or fused cloud once the extractor is stable (the sparse facade cloud's extractor noise masks scale errors under 1.5 %).

## 8. What the iOS app should ask the homeowner

1. **Tape one straight distance on the meter wall, at least 10 ft (ideally 10–16 ft).** It should run between two crisp vertical edges: the house corner, a door or window trim edge, or the side of the meter socket box. Measure level at chest height and enter feet and inches. Then tap both ends on a photo that shows the whole run; the server snaps to the edges.
   - Under 6 ft the endpoint error (about 6 mm each) and a misread both weigh more: tape 6 ft has a 7.1 % p95, against 2.1 % for 10 ft.
   - The app shows ARKit's own length for the run. If the two differ by more than 10 %, it asks "Please check the measurement" before submitting.
2. **One head-on close-up of the meter** from about 3 ft, with the whole glass and ring and a strip of wall visible. This is the independent check (base flange 6.95 in) and also serves the meter criteria.
3. **If the server flags a conflict** (about 14 % of captures), ask once: "Please re-measure the same distance."
4. Keep the normal ARKit walk (slow, overlapping, no tracking loss). ARKit scale is only the gate and the fallback.

## 9. How the server should represent scale uncertainty in the verdict

- **Scene level.** Add a `scale` block alongside the existing `scale_source` / `scale_reference_ids`:

  ```json
  "scale": {"source": "tap_distance", "rel_bound_95": 0.012, "status": "verified",
            "estimates": [{"kind": "tap_distance", "value_m_per_unit": 2.803, "rel_sd": 0.006, "used": true, "ref_ids": ["kd.tape1"]},
                          {"kind": "meter_base", "rel_sd": 0.01, "residual_pct": 0.3, "used": true},
                          {"kind": "arkit_poses", "rel_sd": 0.10, "residual_pct": -4.1, "used": true},
                          {"kind": "learned_depth", "rel_sd": 0.12, "residual_pct": 9.0, "used": false}],
            "conflict": false}
  ```

  Status values:
  - `verified`: a tape plus an agreeing second precise source;
  - `single_reference`: a tape only, or the meter only;
  - `unverified`: ARKit or weak references only, or an unresolved conflict.

  `rel_bound_95` is about 2.5 × the posterior SD, because 2 SD covered only 89–93 % in the Monte Carlo. Recommended values: 1.2 % for `verified`, 2.5 % for tape only, 6 % for weak references without a tape, 18 % for ARKit only.
- **Per measurement.** The existing linear bound rule stays: `plus_minus_ft = shape_bound + rel_bound_95 × distance_ft`, where distance is the distance from the scale reference to the measured element (e.g. 26 ft × 1.2 % = 3.7 in). The C5 margin rule then does the rest: pass needs margin > bound, fail needs margin < −bound, and anything between is unsure.
- **Explain the cause.** When the scale term is the larger part of the bound and flips a pass or fail to unsure, set `unsure_cause: margin` with an added `bound_breakdown {shape_in, scale_in}`. With `scale.status != verified`, the verdict adds `member_action: "measure_one_distance"`, so the app asks for the tape instead of sending the house to manual review.
- **Never** let learned depth or an unconfirmed single object reference produce `verified`, and never let a scale bound over 6 % support a reject on the 16–26 ft reach rules (C: reject needs near-certainty).

## Files

All under `internal/breadth/a8-scale/`:

- Code:
  - `common.py`: scene loading, Sim(3) truth scale, occlusion-aware plane measurement;
  - `refine.py`: edge snapping;
  - `measure.py`: reference measurement, annotation transfer, repeat counting;
  - `stats.py`, `mdepth.py`, `combine.py`, `score_scaled.py`, `scorer_summary.py`, `sfm_barn.py`, `view.py`.
- `annotations/{facade,electro,barn}.json`: the rough hand annotations.
- `results/`:
  - `segments_summary.{md,json}`, `<scene>/segments.json`;
  - `<scene>/mdepth_{moge2-vits,metric3d-vits}.json`;
  - `combine.{md,json}`;
  - `facade/scorer/<run>/geomscore.{json,md}` and `facade/scorer/summary.md`.
- VM only (regenerable): ETH3D data and the facade GT build (`~/a8/data`), the barn SfM (`~/a8/work/barn`, via `sfm_barn.py`).
