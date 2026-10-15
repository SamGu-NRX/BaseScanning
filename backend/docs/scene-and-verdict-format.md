# Scene v0 and verdict v0: the contract between reconstruction, criteria and scoring

Status: v0, doc revision 0.1 (2026-09-26, 22:5x UTC; change log in §8). Owner: eval-harness worker. Machine-readable truth is the JSON Schemas; this doc explains them and adds the rules a schema cannot express. Where this revision adds an optional field or enum value, the schema copies (`internal/eval-harness/schemas/`, `server/contracts/schemas/`) follow by the WS-00 spec PR and are named in §8.

| File | Format id | Schema |
|---|---|---|
| Scene (every Track A/B path writes one, every Track C path reads it) | `base-scene/v0` | `internal/eval-harness/schemas/scene.v0.schema.json` |
| Verdict (every Track C path writes one per scene and rules profile) | `base-verdict/v0` | `internal/eval-harness/schemas/verdict.v0.schema.json` |
| Labels (hand-worked truth for one site) | `base-labels/v0` | `internal/eval-harness/schemas/labels.v0.schema.json` |

Validate before handing anything on: `python -m evalharness.cli validate <file.json>` (schema plus the cross-reference checks in §2.10). Unknown fields are rejected everywhere; put extras in `provenance.notes` or `producer.params`.

Related: criteria and thresholds `docs/criteria-spec.md`; capture packet `docs/capture-packet-spec.md`; harness code, commands and results `internal/eval-harness/README.md`. Reuse notes: `docs/reuse-from-prs.md` (when it lands). Derived from PR #11 (`scene.schema.json`, `result.schema.json`, `rules.yaml`), PR #4 (scoring semantics), PR #13 (facade scene, margin check) and PR #12 (visibility oracle).

## 1. Frame: `meter_anchored/v0`

- **Units:** meters in the scene (`_m` suffix). Feet only in verdicts and labels (`_ft`), because every threshold is written in feet.
- **Origin:** on the ground directly below the electric meter (the object named by `frame.anchor.element_id`, class `meter`).
- **+y:** up, against gravity. **+z:** horizontal, straight out of the meter's wall. **+x = y × z:** along the meter wall, to the right as seen by someone outside facing it.
- **Plan points** are `[x, z]`. **s** is distance along the wall chain from the meter, positive to the right seen from outside; on the meter wall s = x (same sign as PR #11's s).
- **Poses and pixels** follow the capture packet: camera-to-world `mat4`, 16 numbers column-major; ARKit camera axes (+x right, +y up, looks along −z); intrinsics `[fx, fy, cx, cy]` in continuous pixels with (0, 0) the top-left corner of the top-left pixel.
- `frame.from_source` is the similarity (scale included) from the producer's own world (`source_frame`: `arkit_world`, `colmap_model`, …) into this frame. Assets with `in_scene_frame: false` are transformed by it on load.
- `frame.anchor.plus_minus_m` bounds the origin; `heading_plus_minus_deg` bounds the meter wall's direction (it sets x and z). Both are part of every element's error (§3).
- "Gravity" is whatever the producer used (`provenance.gravity_source`). For the ETH3D facade the ground truth uses the fitted ground normal, 1.3° off the scanner vertical (inherited from PR #13); the simulated ARKit there uses the same up.

Why meter-anchored: every criterion is a distance from the meter, along its wall, or out from a wall. Anchoring there makes a scene comparable across paths without registration, and makes anchor error (where the path put the meter) a scored quantity instead of a nuisance.

## 2. Scene fields

Required top level: `format`, `scene_id`, `provenance`, `frame`, `walls`, `chain`, `ground`, `objects`, `coverage`. Optional: `assets`, `overheads`, `facing`, `generated_regions`, `keyframes`.

### 2.1 `provenance`
- `data`: `real` (real images + real metadata) / `hybrid` (real images, simulated metadata, e.g. simulated ARKit) / `synthetic`.
- `site`: shared by every scene of the same house; scorers pair GT and recon by it.
- `producer.path`: track path id (`A1-colmap`, `A2-mapanything`, `A5-worldlabs`, `gt-laser`, …), plus `version`, `code_ref`, `params` (free object; record seeds and noise draws here).
- `inputs`: `packet_id`, `packet_sha256`, `images_given`, `images_used`, `sensors_used[]`.
- `scale_source`: `ground_truth` / `arkit_poses` / `lidar` / `tap_distance` / `scale_reference` / `native_metric` / `none`, plus `meter_model` (added in doc revision 0.1 for A9's meter make/model scale; until the schema copies carry it, producers write `scale_reference` with `scale_reference_ids: []`). If scale came from a known distance, list the key-distance ids in `scale_reference_ids`; the scorer skips them (they match by construction, PR #4). The full scale block (`{source, model, scale_factor, origin_m, rel_bound_95, status, estimates[], conflict, …}`, A9 §9 and WS-00 `ScaleBlock`) travels verbatim at `producer.params.scale` and as `scale.json` next to `scene.json`; there is no top-level `scale` key.
- `gravity_source`: `ground_truth` / `arkit` / `imu` / `ground_plane_fit` / `none`.
- `runtime_s`, `hardware`, `cost_usd`, `license` (commercial use of the model matters), `notes[]`.

### 2.2 `assets[]`
`{id, kind, path, format, sha256, in_scene_frame, count, fields, generated_field, note}`. `kind`: `point_cloud`, `mesh`, `gaussians`, `depth_maps`, `coverage_grid`, `images`, `other`. `path` is relative to the scene JSON. PLY must be binary little-endian. The geometry scorer reads the first `point_cloud` (or samples the first `mesh` when there is no point cloud). `generated_field` names a per-point 0/1 field marking generated geometry; absent means the producer asserts none is generated.

### 2.3 `walls[]` and `chain`
- Wall: `id`, `role` (`house`, `garage`, `shed` can back a battery; `fence`, `hedge` are facing obstacles; `retaining`, `other`), `baseline_m` (two plan points, **left end first seen from outside**; outward normal = baseline direction turned 90° clockwise seen from above), `base_y_m` (default 0), `height_m`, optional `polygon_m` (3D outline for gables/steps), `s_range_m` (chain walls), `plus_minus_m`, `evidence`, `observed_fraction` (share of the face 0–2.13 m high that coverage marks observed), `support {points, views, fit_rms_m}`.
- Multiple walls are the norm: the meter wall, adjacent walls round corners, sheds, fences.
- `chain`: the walls a battery could back onto, left to right seen from outside, including the meter's wall. `wall_ids[]`, `corners[] {id, left_wall, right_wall, point_m, s_m, turn_deg, plus_minus_m, evidence}` (turn positive = outside corner), `ends {left, right}` each `{kind: limit | unexplored, point_m, s_m, note}`. `limit` = the wall really ends for battery purposes (property line, fence, obstruction), `unexplored` = it continues but was not captured. A reject needs both ends `limit` and observed (criteria spec §4.1).
- A corner closed by intersecting two fitted lines when the corner itself was not seen is `inferred`.

### 2.4 `ground`
`surface` is a `plane` (`normal`, `offset_m`: normal·p = offset) or a `heightfield` asset; `plus_minus_m`; `regions[] {id, material, polygon_m, slope_deg, confidence, plus_minus_m, evidence, source}`. Materials: `concrete, gravel, lawn, dirt, mulch, pavers, deck, drive, walkway, pool, water, unknown`. Ground under a footprint with no region is unknown material, never assumed allowed.

### 2.5 `objects[]`
`{id, class, label, geometry, wall_id, confidence, plus_minus_m, source, attrs, evidence}`.
- `class`: `meter, meter_can, disconnect, panel, wall_box, vent, gas_meter, gas_regulator, gas_pipe, ac_unit, generator, battery, window, door, garage_door, basement_well, downspout, hose_bib, eave, porch_roof, stairs, deck, overhang, pool, hot_tub, gate, bin, plant, shrub, tree, other`. Open-vocabulary text goes in `label`.
- `geometry`: `box {center_m, size_m [w, h, d], yaw_deg}`, `point {pos_m}`, `wall_rect {wall_id, s_m [s0, s1], bottom_m, top_m, standoff_m}` (openings and wall-mounted equipment; heights above ground), or `footprint {polygon_m, bottom_m, top_m}`.
- `confidence` is recognition confidence only. `plus_minus_m` is a position bound and is **never derived from confidence**.
- `source {kind, model, views[] {image, box_px [u0,v0,u1,v1], points_px, score}}`; `kind`: `detector, vlm, tap, annotation, tape, laser_scan, lidar, reconstruction, generated`. Source views are what lets a reviewer (and the scorer) trace every object back to pixels.
- `attrs`: `operable`, `ground_level`, `well`, `movable`, `text` (OCR). **A missing attribute is unknown, never false**: an unknown-operability window still counts as an opening.

### 2.6 `coverage` (fog of war)
What the capture actually saw. `method` (free text), optional `grid`, optional `bands`.
- `grid {asset, voxel_m, origin_m, shape, min_views}`: npz with uint8 `state[nx,ny,nz]` (0 unknown, 1 observed free, 2 observed occupied) and optional `views` (distinct cameras, capped at 255). Index = floor((p − origin)/voxel). Outside the grid is unknown. A voxel counts as observed when state > 0 and views ≥ `min_views` (default 1).
- Build it from rays of real observations only (camera centre → triangulated point, depth pixel, LiDAR return). Space a model filled in does not count. `evalharness.coverage.build_grid` does this for any ray set; `bands_from_grid` derives the bands.
- `rule {min_views, max_incidence_deg {wall, ground}, observed_share}`: the observation rule of §3.1 that the bands and `walls[].observed_fraction` were graded by.
- `sensitivity[] {surface, max_incidence_deg, bands[]}`: the ground bands again under each alternative ground limit (75° and 80°), so criteria paths can run ground-dependent checks under both (`run-c1 --ground-limit 75`).
- `bands[] {wall_id, band, s_m, out_m, views, best_incidence_deg}` in meters, PR #11 `coverage.observed` compatible, per 0.25 m station along each chain wall:
  - `wall`: grid observed 0.1 m out at 0.15, 0.5 and 1.0 m high **and** the face itself well observed there (§3.1). A face seen only edge-on is not a wall band. `views` = fewest well-angled views in the run; `best_incidence_deg` = its worst best viewing angle.
  - `ground`: strip observed out to `out_m` from the wall (grid probe 5 cm above ground **and** the ground surface well observed), with `views` / `best_incidence_deg` as for walls.
  - `overhead`: column 0.2–0.91 m out observed up to `out_m` high (absent `out_m` = observed to ≥ 3 m). Emitted only when ≥ 2.13 m.
  - `facing`: straight out at 0.91 m high observed to `out_m` (absent = ≥ 18.3 m).
- **Rule for readers:** a check whose deciding space is unknown is UNSURE with `unsure_cause: unobserved`, never PASS.

### 2.7 `overheads[]` and `facing[]`
- `overheads[] {wall_id, s_m, clearance_m, object_id, plus_minus_m, evidence}`: clear height under anything overhead within **0.91 m (3 ft)** of the wall (eaves, porch roofs, stairs, decks), counted from 1.22 m up.
- `facing[] {wall_id, s_m, depth_m, lower_bound, object_id, plus_minus_m, evidence}`: gap from the wall straight out to the nearest fixed obstacle 0.3–1.83 m high. `lower_bound: true` = nothing found out to `depth_m` and the view reached that far.

### 2.8 `generated_regions[]`
Space where geometry was generated or completed instead of observed (world models, inpainting, hole filling, symmetry completion): `{id, geometry {kind: box | footprint | asset_mask, …}, method, note}`. `asset_mask` points at an asset whose `generated_field` marks the elements. A criteria path treats everything inside as unknown.

### 2.9 `keyframes[]`
Cameras in the scene frame as the producer estimated them: `{id, image, pose, intrinsics, w, h, pose_source}` (`ground_truth, arkit, sfm, model, sfm_scaled_by_arkit`). Needed for coverage audits and for `views_needed`.

### 2.10 Cross-reference checks (enforced by `validate`)
Anchor names an object of class `meter`; chain walls exist; `wall_rect` walls exist; object ids unique; the coverage grid asset exists in `assets`.

## 3. Evidence and uncertainty rules

**Evidence** (`walls`, `corners`, `ground.regions`, `objects`, `overheads`, `facing`):

| Value | Meaning | What a criteria path may do with it |
|---|---|---|
| `observed` | Seen in ≥ 1 real input image and supported by measured geometry | pass, fail or unsure |
| `inferred` | Extrapolated from observations: plane extended across an occluder, corner closed by intersecting lines, wall run past its last seen point | fail or unsure, **never pass** |
| `generated` | Produced by a generative model or hole filling | nothing: treat as unknown (`unsure_cause: generated_only`) |

**Uncertainty:** every `plus_minus_m` / `plus_minus_ft` is a **bound**: the producer claims the true value lies within it (not a σ). Bounds combine linearly (PR #11/#13): a distance between two elements carries the sum of their bounds plus the frame anchor bound where the anchor matters. Producers should include scale error times distance from the meter (e.g. 4 % × s for non-LiDAR ARKit, 2σ of the drift profile) and drift growth per meter walked. The geometry scorer reports calibration: the share of key distances whose |error| ≤ the producer's own bound (§6.1). A well-calibrated path sits near or above 95 %.

**Margin rule (C5):** with measured value m ± u and threshold T, margin = signed distance to T, positive on the passing side. PASS needs margin > u (`margin_over_uncertainty` > 1), FAIL needs margin < −u, anything between is UNSURE with `unsure_cause: margin`.

### 3.1 Coverage evidence on every measurement (required)

Why: in the C3 photo baseline (`internal/breadth/C3-vlm-baseline.md` §5.5) a model with 3D tools passed a clearance rule by putting the battery on the facade's w2. The photos show that wall only edge-on, but the laser backend answered `measure` / `facing_clearance` there without complaint. Geometry existing is not the same as geometry having been seen.

**Rule.** Every measurement or query answer carries `coverage_evidence` for each region it depends on: both ends of a gap and the wall between them, the footprint's wall patch and ground, the facing volume. A surface probe is **well observed** when at least `min_views` = 2 input views see it (inside the image, in front of the camera, not occluded by the scene's own geometry) at a viewing angle within its surface's limit from the surface normal (0° = head-on, 90° = edge-on). The limit is configurable per surface type (`evalharness.coverage.INCIDENCE_LIMITS_DEG`, recorded in every scene's `coverage.rule` and every evidence object's `rule`):

| Surface | Default limit | Why |
|---|---|---|
| `wall` (a wall-face patch) | 75° | PR #13's grazing limit; C3's edge-on w2 failure sits at 78° |
| `ground` (the strip in front of a wall) | 80° | ground is always seen obliquely from standing height |
| `free` (facing / overhead volumes) | none | free space needs 2 views at any angle |

**Ground rule variants (open decision).** Ground is graded under three variants, all reported side by side:

| `rule_id` | A ground view counts when | Source |
|---|---|---|
| `angle75` | incidence ≤ 75° | same limit as walls |
| `angle80` | incidence ≤ 80° | **current default** |
| `footprint3in` | one full-resolution pixel covers ≤ 3 in (7.6 cm) of ground along the viewing ray, i.e. distance / (fx · cos incidence) ≤ 0.0762 m, and incidence ≤ 88° | C1 rules-3d worker's recommendation (`internal/breadth/C1-rules-3d.md`): resolution, not angle, decides whether a surface type and a 3 in step can be read |

Every ground `coverage_evidence` carries `sensitivity[] {rule_id, max_incidence_deg, max_footprint_m, status, observed_share, views_median}`. The scene's `coverage.sensitivity[]` holds the ground bands under each variant, and `run-c1 --ground-rule angle75|angle80|footprint3in` runs C1 on them. The geometry scorer reports ground-strip coverage under each.

This is Base's call. The angle rules never count the ETH3D courtyard ground as observed, because it is seen from 13–41 m at 81–84°. The footprint rule counts it, because a 6048 px DSLR resolves about 1 in per pixel there. The same is true of a 4032 px phone at 30 ft, so the footprint rule trusts oblique views of high-resolution images. The angle rules instead demand a downward shot.

The region's `status`:

| status | Meaning | What a criteria path may do |
|---|---|---|
| `observed` | ≥ 90 % of probes well observed | pass, fail or unsure |
| `grazing` | seen, but only at angles past the surface's limit | **unsure**, `unsure_cause: grazing`; views_needed: a head-on photo of that wall, or a downward photo of that ground strip |
| `partial` | some probes well observed, others not seen | **unsure**, `unobserved` |
| `unobserved` | < 50 % of probes seen by any view | **unsure**, `unobserved` |
| `generated` | mostly generated geometry | **unsure**, `generated_only` |

`coverage_evidence` fields: `region` (scene coordinates: `{wall_id, s_m, height_m}` = wall-face patch; `{wall_id, s_m, out_m}` = ground strip in front; `{wall_id, s_m, out_m, height_m}` = free-space volume; or `{element_ids, images}` for close-up reads), `status`, `observed_share`, `seen_share`, `views_min`, `views_median`, `best_incidence_deg` (median over probes of the most head-on view; null for volumes), `worst_best_incidence_deg` (clamped to the schema maximum of 90: coverage rounding produced 90.2° on the π³-fused facade models and dropped six C2 verdicts, E2E §7), `rule {min_views, surface, max_incidence_deg, observed_share}` (the limit applied), `sensitivity[]` (ground only: `{max_incidence_deg, status, observed_share, views_median}` at 75° and 80°), `probes`, `method`.

**Where it lives:** `criteria[].measured.coverage[]` and `criteria[].fact.coverage[]` are required arrays (min 1). A pass or fail whose evidence is not all `observed`, or whose `views_median` is below the evidence's own `rule.min_views`, or whose median best angle is past its surface's limit, breaks the contract: `validate` (`sceneio.verdict_contract_errors`) rejects the verdict, and the verdict scorer counts such a pass as unsafe. `rule.min_views` is 2 for surface probes (walls, ground, free volumes) and 1 for close-up reads (`region {element_ids, images}` on a meter or panel photo, where one legible image is the unit of evidence and `views_median` counts legible images). A pass with no measurement or fact at all is also rejected.

**How to produce it:** any measurement backend (C1's solver, C3's tools, a VLM tool server) calls `evalharness.coverage.query_region(scene, scene_dir, region)`, or `python -m evalharness.cli query-coverage --scene S --region '{...}'`, with the scene's own keyframes and geometry. It returns exactly this object. A backend that cannot produce it must answer the measurement as `unsure` / `not_evaluated`, never as a bare number.

**Honest limits.** Occlusion is tested against the scene's own points (z-buffer at 1/8 resolution, 5 × 5 min filter). A sparse cloud has holes, so it can over-count views; the geometry scorer measures that as `false_observed_by_rule` against the laser truth. On the ETH3D facade the DSLR cameras stand 13–41 m from the meter wall at about 1.5 m height, so the courtyard ground is only ever seen at 81–84°. It is `grazing` under both 75° and 80°, so every ground-dependent check there is unsure under either limit.

**Phone captures differ.** A phone held at chest height (about 1.3 m) and pointed down at the ground 1–2 m in front of the wall sees it at roughly 40–60°, well inside both limits. ETH3D's distant DSLR is a worst case, not the expected capture. The needed-views output therefore asks for exactly that shot: every ground `views_needed` entry (from a grazing or unobserved ground region, or a PR #11 ground band request) carries `target: "downward photo of the ground in front of this wall, from chest height"` with the wall, the s range and how far out (`out_m`) it must reach. The packet spec should add a matching capture prompt.

## 4. Verdict fields

Required: `format`, `verdict_id`, `scene {scene_id, site, sha256, producer_path, data}`, `evaluator {path, version, rules_profile, rules_sha256, auto_approve, runtime_s, cost_usd}`, `site {decision, reasons, chosen_candidate}`, `criteria[]`, `views_needed[]`. Optional `candidates[]`.

- One verdict = one scene × one rules profile (`rules_profile`: `A`, `B`, `C`, `S` strict composite, or `public`/`demo` for PR #11's placeholder rules). Report several profiles as several files. The strict composite is always spelled `S`: the C1 rules-3d engine's internal name `strictest` is emitted as `S`, labels use `S`, and the schema description that still says "strictest" is corrected by the spec PR.
- `site.decision`: `pass` / `manual_review` / `reject`. "Needs more photos" is `manual_review` plus a non-empty `views_needed`.
- `criteria[]` item: `id` (§5; the site-scope meter-number criterion is `meter_number`, never `meter_identity`, which was a stale example in the schema description and is corrected by the spec PR), `scope` (`site` or `candidate` + `candidate_id`), `outcome` (`pass` / `fail` / `unsure`), `unsure_cause` (`margin`, `unobserved`, `grazing`, `unknown_attribute`, `rule_requires_review`, `generated_only`, `not_evaluated`), `reason`, `measured {value_ft, plus_minus_ft, coverage[], subject, element_ids}` or null, `fact {value, clean, coverage[], raw}` for non-metric findings (`coverage[]` required in both, §3.1), `threshold {value_ft, comparison at_least|at_most, review_value_ft, rule_key, source, placeholder}` or null, `margin_ft`, `margin_over_uncertainty`, `evidence {element_ids, views, coverage}`, `views_needed[]` (ids into the top level).
- `candidates[] {id, wall_id, start_s_m [a, b], outcome, failing[], unsure[], footprint_m}`: battery left-edge positions in scene s (meters), so they index straight back into the scene. A run of equal outcome may be one candidate with a range.
- `views_needed[] {id, kind: band | past_end | close_up | confirm_attribute | region, band, wall_id, s_m, out_m, side, target, criteria[], why, view_angle_deg?}`: the fog-of-war contract back to iOS. Empty for pass and reject. Ground requests always ask for a downward chest-height shot (§3.1); grazing wall requests ask for a head-on shot from 2–4 m. `view_angle_deg: [min, max]` (optional, added in doc revision 0.1, degrees from head-on) is the viewing-angle band the new photo must satisfy for the region to grade `observed`: `[0, 30]` for `band: wall` and `past_end`, `[20, 60]` for `band: ground`, omitted for `facing`, `overhead`, `region`, `close_up`, `confirm_attribute`. Every `band`, `past_end` and wall `region` entry carries `wall_id` and `s_m`, and `why` names the cause (`unobserved` or `grazing`), because on photo-built models coverage grading, not geometry error, is the main reason outcomes stay undecided (E2E §4). No customer-facing `prompt` lives here; the result layer adds it.
- `evaluator.ground_incidence_deg`: set on sensitivity runs to the ground limit of the bands used.
- Mapping the criteria spec's vocabularies onto `outcome`: decision prompts `pass`→pass, `manual_review`→unsure (`rule_requires_review` or the real cause), `reject`→fail. P12 flags (inverted): `no`→pass, `yes`→fail, `manual_review`→unsure. Feature extractors: clean fact → pass (value in `fact`), unclean → unsure `unknown_attribute`. `needs_more_photos` → unsure `unobserved` + `views_needed`.

## 5. Criterion catalogue

Candidate-scope placement checks (PR #11 solver names; C1 emits all 13 through the adapter). Profile values from `docs/criteria-spec.md` §4.2; PR #11 demo value in brackets.

| id | Question | Scene inputs | Threshold (A / B / C) [demo] |
|---|---|---|---|
| `wall_backing` | Footprint backs onto a real building wall, not past a corner or a gap | walls, chain, corners | same wall / same+adjacent / building or shed wall |
| `ground_surface` | Allowed ground under the footprint, mild slope | ground.regions | – / listed materials / same + planter |
| `meter_working_space` | Footprint outside the meter's working envelope | meter | 2.5 ft wide × 3 ft deep (NEC 110.26) |
| `gas_clearance` | Distance to gas meter, regulator, pipes | objects gas_* | 5 / 5 / 3 ft [3] |
| `ac_clearance` | Distance to AC / HVAC | objects ac_unit | Base: fixed footprint only [3 ft] |
| `drive_clearance` | Distance to drivable surface | ground.regions drive | 5 / 5 / 3 ft beyond footprint [5] |
| `pool_clearance` | Distance to permanent pool | objects pool, regions pool | 5 / 5 / 10 ft [10] |
| `opening_clearance` | Horizontal gap to doors, wells, operable ground-level windows | objects window/door/garage_door/basement_well + attrs | clear / 3 ft each side (two openings ≥ 8.58 ft apart with a 31 in battery) / a few inches [3] |
| `wall_equipment_above` | Not under a wall box or vent | objects wall_box, vent, panel | C: prohibited [0 ft overlap] |
| `facing_gap` | Wall → fence/hedge/wall gap | facing[] | – / – / ≥ 5 ft incl. depth [3 ft from front] |
| `headroom` | Clear height above the footprint | overheads[] | – / 6–7 / 6–7 ft (fail < 6, pass ≥ 7) [6.5] |
| `route_path` | Cable route legal (door, garage, drive crossings) | objects on the wall, chain | profile table rows "on route" |
| `route_length` | Routed length meter → battery | chain, meter, objects | 15 ft same wall / 25 ft wall run / pass ≤ 16, review ≤ 26 [20, review > 15] |

Also candidate scope, not yet in C1: `disconnect_space` (A/B: ~2–2.5 ft open wall beside the meter, 6 in bend room), `walkway` (C), `street_facing` (C), `requires_member_action` (movable blockers).

Site-scope criteria (ids = criteria spec "Our id"): `placement` (overall placement decision for the profile; `fact.value` carries the profile), `meter_number`, `cl320`, `oversized_can`, `meter_type`, `meter_mount`, `multiple_mains`, `main_panel_type`, `meter_panel_relationship`, `meter_not_accessible`, `panel_not_accessible`, `panel_in_house`, `not_same_wall`, `panel_recall`. Site outcome composition: criteria spec §1 proposal (reject if any decision prompt rejects; else manual_review on any review / unclean / missing view; else pass).

## 6. Scorers

Both write JSON (machine) and Markdown (people) under the output directory given on the command line.

### 6.1 Geometry vs ground truth: `python -m evalharness.cli score-geometry --gt GT/scene.json --recon R/scene.json --out DIR`

A path with only a point cloud or mesh (any frame) plus its cameras uses `score-cloud --gt GT/scene.json --cloud X.ply --cameras MODEL_OR_JSON [--metric] --out DIR` instead. The harness aligns it to the GT cameras (rigidly when `--metric`, so scale error is kept), builds the scene with the reference extractor using perfect GT taps and clicks, and scores it the same way. Usage: `internal/eval-harness/README.md`.

Recon elements are matched to GT in the shared meter frame first (meter by anchor; chain walls by direction ≤ 20° + s-range overlap + offset ≤ 0.5 m; corners through their walls; objects by class group with Hungarian assignment on centre distance, gate 1.5 m + 5 % of distance from the meter). Recon ids are renamed to GT ids.

**Key distances** (id → criterion fed). Error = recon − GT, reported in inches.

| id | What | Feeds |
|---|---|---|
| `meter.height` | Meter height above ground | meter_working_space, route |
| `corner.s[c]` | s of corner c | wall_backing, route_length |
| `corner.turn[c]` | Turn angle (degrees, separate stats) | wall_backing |
| `wall.length[w]` | Chain wall length, only between two corners (free ends are extent-limited) | wall_backing |
| `opening.edge_s[o:left\|right]` | s of each opening edge | opening_clearance, route_path |
| `opening.bottom[o]`, `opening.top[o]` | Sill / head height | opening exemptions, route detours |
| `opening.width[o]` | Opening width | opening_clearance |
| `opening.gap[a~b]` | Clear wall between consecutive openings (decides "battery + 2 × 3 ft = 8.58 ft") | opening_clearance |
| `equipment.meter_dist[o]`, `equipment.s[o]` | Other objects: 3D distance to the meter, s along the chain | gas/ac/pool clearance, wall_equipment_above |
| `facing.depth[w@s]` | Facing depth at GT stations with depth < 4 m | facing_gap |
| `overhead.clearance[w@s]` | Overhead clearance where GT clearance < 3 m | headroom |
| `ground.region_dist[m]` | Nearest region edge of material m to the meter wall | drive/pool clearance, ground_surface |

Status per key distance (PR #4): `scored`, `scale_reference` (skipped), `missing` (GT has it, recon does not: counts against completeness, not error), `extent_limited` (a free end), `phantom` (recon element with no GT match). Stats: median, p90, max |error|; share ≤ 2 / 4 / 6 in; **calibration** = share with |error| ≤ the recon's own bound.

**Scale error:** median recon/GT ratio over key distances > 3 m, plus Umeyama scale over matched corners and object centres. **Anchor error:** rigid ICP of recon points onto the laser (translation, yaw) = how far the path's meter frame is off.

**Criteria regions** (reach 7.92 m = 26 ft along the chain from the meter both ways, clipped to the chain; the strictest profile's needs):

| Region | Extent |
|---|---|
| `meter_wall` | meter wall face, 0–2.13 m high |
| `wall_face` | all chain walls in reach, 0–2.13 m (7 ft) high |
| `ground_strip` | ground 0–4.27 m (14 ft) out from the chain walls |
| `overhead` | volume 0–0.91 m out × 0–2.13 m high |

Per region: **surface completeness** = share of GT surface samples (laser points in the region) with a recon point within 5 / 10 / 20 cm, in the declared frame and after ICP; **coverage honesty** = share of region samples the recon's grid claims observed vs share truly seen (visibility oracle: GT keyframes, laser z-buffer + occluders, 75° incidence gate), and **false-observed** = claimed seen but truly unseen (this is what would let a path pass on space nobody saw). The same is reported **by the §3.1 rule**: `truth_by_rule` (status), `truly_observed_by_rule`, `truly_grazing_only` (seen, but only edge-on), `claimed_by_rule` (the recon's own query: its cameras and its geometry) and `false_observed_by_rule`.

**Coverage per key distance:** every opening and meter key distance carries `coverage_claimed` (what the recon's own query would attach) and `coverage_truth` (the GT query). Error stats are also reported restricted to truly observed elements (`on_truly_observed_elements`), and `false_observed_claims` lists elements the recon would call observed but the truth grades grazing / partial / unobserved.

**Unobserved geometry:** share of recon points in space no input camera saw (oracle, no incidence gate); share inside the recon's own unknown voxels; share flagged generated.

### 6.2 Verdicts vs hand labels: `python -m evalharness.cli score-verdicts --labels L.json --verdict V.json --out DIR`

Labels (`base-labels/v0`) give truth per criterion over `start_s_m` ranges or per site: `pass`, `fail`, `borderline` (correct answer is unsure), `not_pass` (fail or unsure both fine; PR #13's `outcome_not`), `unknown` (not scored). Candidate outcomes are sampled every 2 in (0.0508 m) of battery start position; a position no candidate covers counts as unsure. Site-scope labels are matched to the verdict's site-scope criteria by id, except `placement`, which is read from `site.decision` (pass → pass, manual_review → unsure, reject → fail). A labelled criterion the verdict does not report is `not_evaluated` (counted, not scored). `labels_site.site_decision` is compared with `site.decision`.

Label sources: site-scope labels per profile A/B/C/S in `internal/criteria/labels-v0/<site>.<profile>.json` (`docs/house-labels.md`); physical start-range labels for PR #11's demo rules in `internal/eval-harness/labels/<site>.demo.labels.json`. Score a verdict against several files at once (`--labels a.json b.json …`).

| Output | Definition |
|---|---|
| **unsafe pass** (headline) | verdict pass where truth is fail, borderline or not_pass; count of labelled positions and wall length (m, ft), plus site-level unsafe pass (site pass when the label is manual_review or reject) |
| missed review | verdict fail where truth is borderline |
| over-caution | verdict unsure where truth is pass or fail |
| false rejection | verdict fail where truth is pass |
| agreement | share of scored positions where the verdict is acceptable for the truth |
| manual-review rate | share of positions unsure, and the site decision |
| pass on unknown | verdict pass where truth is unknown (informational) |
| C5 consistency | criteria whose outcome contradicts their own margin rule (PR #13 `resultcheck` style) |
| coverage contract | pass / fail not backed by `observed` evidence (§3.1); **every such pass counts as unsafe**, whatever the label |
| false coverage claims | `python -m evalharness.cli audit-verdict --verdict V --gt GT --recon R`: claims of `observed` that the truth (GT laser + the scene's cameras) grades grazing / partial / unobserved, and how many decisive outcomes rest on them |

## 7. How a path plugs in

1. **Reconstruction (Track A) / understanding (Track B):** write `scene.json` + assets in one folder; put the meter at the origin (if you only have your own world, fill `frame.from_source`); mark evidence honestly; build coverage from your real observation rays (`evalharness.coverage.build_grid`, then `bands_from_grid`); flag generated geometry. The reference extractor `evalharness/extract.py` turns any metric point cloud + cameras + meter taps into a scene (walls, chain, openings from clicks, overheads, facing, coverage) and is a fine starting point. `python -m evalharness.cli validate scene.json`.
2. **Criteria (Track C):** read a scene, write one verdict per profile. Attach `query_region` evidence to every measurement and fact (§3.1); anything not `observed` is unsure. Measurement tools given to a model must return that evidence with each number and refuse to report grazing or unobserved regions as measured. C1 = PR #11's solver through `evalharness/adapters/hs11.py`: `python -m evalharness.cli run-c1 --scene S --out V.json [--ground-limit 75]`. It converts scene v0 to a PR #11 scene in feet, dropping generated elements and giving walls that are not `observed` no wall band. It then runs the vendored solver (demo rules unless `--rules`) and maps the result to verdict v0: checks at the chosen spot, the sweep as candidates, and missing evidence as `views_needed`. Coverage evidence is attached to every measured check, and passes or fails resting on anything but `observed` are downgraded to unsure. Sweep runs are re-checked the same way: a pass run with non-observed evidence becomes unsure, and a fail run stays a fail only if at least one failing check rests on fully observed regions. A reject with any downgraded run becomes manual_review.
3. **Score:** `score-geometry` against the site's GT scene, `score-verdicts` against its labels. Put results under `internal/eval-harness/results/<site>/<path>/`.

Current sites with ground truth (all laser scans with invented meters): `eth3d-facade` (PR #13), `eth3d-electro` and `eth3d-delivery` (seeded from the C2 worker's labelled scenes; their gas meter and AC are synthetic fixtures labelled `SYNTHETIC`). Results and commands: `internal/eval-harness/README.md`. The reference extractor sorts every opening span (`s0 < s1`) before handing a scene to the PR #11 adapter: a reversed door span on facade w3 crashed the adapter in the E2E run (E2E §7). No ETH3D site carries a labelled `pass`, so 0 unsafe passes on these sites is necessary, not sufficient; WS-13's real house R1 supplies the first `start_range` `pass` labels.

## 8. Change log

- Doc revision 0.1 (2026-09-26, 22:5x UTC, coordinator; per `internal/build/reconciliation-queue.md` and `internal/build/seam-decisions.md` §9 item 2). Text-only fixes: `rule.min_views` wording in §3.1 (2 for surface probes, 1 for close-up reads; the code already applied the evidence's own `rule.min_views`); the criterion id `meter_number` (the schema description example `meter_identity` is stale); the strict composite is `S` (`strictest` is C1 rules-3d's internal name); `worst_best_incidence_deg` clamped to 90; sorted opening spans in the extractor; the scale block's home (`producer.params.scale` + `scale.json`, no top-level key). Additive fields that need the schema copies updated by the WS-00 spec PR (`internal/eval-harness/schemas/verdict.v0.schema.json`, `scene.v0.schema.json`, `server/contracts/schemas/`): optional `views_needed[].view_angle_deg: [min, max]`, and `meter_model` in `provenance.scale_source` (producers write `scale_reference` with empty `scale_reference_ids` until then). Also in that PR: `rules_profile` description "public, A, B, C or strictest" → "public, demo, A, B, C or S", and the `id` description example `meter_identity` → `meter_number`. Nothing else changed; every existing valid document stays valid.
