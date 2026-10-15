---
cursor:
  subagentId: "bc-40f7af01-969a-5377-9507-a40fb8d9ab1d"
---

# C2 volumetric battery-fit check: builder handoff

## 1. One-paragraph summary
Path `C2-volumetric`. It sweeps a battery box and its clearance envelopes over every wall-adjacent free column of a 5 cm occupancy grid (states unknown / free / occupied, carved from real sensor rays). Corners, recesses, bins and ledges are handled by the checks themselves; there is no wall extraction. On ETH3D laser scenes it matches PR #11's site decision on 8 of 10 scene × rule-set runs; the 2 misses are delivery, explained below. Along the walls the two agree on 94–100% of positions. Scored delivery verdicts: 0 unsafe passes, 0 false rejections, position agreement 0.52 (S) / 0.53 (demo). Verdict: **conditional go, as a geometry veto on top of C1**. Every number so far comes from the ETH3D laser (or laser geometry seen from the DSLR poses); it is not yet proven on phone reconstructions. Report: `internal/breadth/C2-volumetric.md`. Code: `internal/breadth/c2-volumetric/` (store only; nothing in any repo).

## 2. Environment that worked
- VM: 4 vCPU, 15 GB RAM, CPU only, Ubuntu, Python 3.12.3. No GPU needed anywhere.
- System packages: `sudo apt-get install -y p7zip-full python3.12-venv`.
- Python packages: `python3 -m venv ~/c2/venv && . ~/c2/venv/bin/activate && pip install numpy scipy shapely scikit-image numba plyfile pyyaml jsonschema pillow matplotlib fastapi python-multipart uvicorn`, plus `pip install -r internal/eval-harness/requirements.txt`.
- Versions that worked: numpy 2.5.3, scipy 1.18.1, numba 0.67.0, shapely 2.1.2, scikit-image 0.26.0, plyfile 1.1.5, PyYAML 6.0.3, jsonschema 4.26.0, matplotlib 3.11.2. Licences BSD / PSF-style.
- Data: ETH3D `https://www.eth3d.net/data/<scene>_dslr_scan_eval.7z`, `_dslr_undistorted.7z` and `_dslr_occlusion.7z` for facade, electro and delivery_area. That is about 2.9 GB of archives, extracted into `~/c2/data/<scene>/`. Licence CC BY-NC-SA 4.0 (non-commercial).
  - The occlusion archive extracts into `<scene>/<scene>/`; move its contents up one level.
  - `curl -O` failed silently once; use `curl -sfL` in a retry loop.
- PR #11 solver (comparison only): `git clone https://github.com/SamGu-NRX/house-scanning.git hs && git -C hs fetch origin pull/11/head:pr11 pull/13/head:pr13 && git -C hs worktree add ../pr11 pr11`. Heads used: pr11 cd0da8b, pr13 52aa87f.
- The first numba run compiles for about 10 s; later runs load from `cache=True`.

## 3. Reproduce the headline result
- One command runs everything: `bash internal/breadth/c2-volumetric/run_all.sh ~/c2/work`. It builds grids and GT scenes, then runs sweeps, PR #11, verdicts, `validate` and `score-verdicts`. Total about 25 min.
- Per step (from the handoff's "Re-create the VM state"):
  - `python -m c2vol.build scenes/<scene>.json ~/c2/data/<ds> OUT.npz --mode laser|photos [--cams keyframes|all]`: 40–70 s. Scan loading dominates; carving takes 1–17 s.
  - `cd internal/eval-harness && python eth3d/build_gt_c2.py --seed ../breadth/c2-volumetric/scenes/eth3d-delivery-garage.json --data ~/c2/data/delivery_area --site eth3d-delivery --out ~/c2/work/delivery-gt --ground-material g1=drive`: 40 s. The output must be identical to `results/eth3d-delivery/gt-laser/scene.json`.
  - `python -m c2vol.sweep SCENE.json GRID.npz rules_strictest.yaml OUT_DIR [--ground-from GT/scene.json]`: 12–105 s.
  - `python -m c2vol.verdict SWEEP.json GT/scene.json rules_strictest.yaml S OUT.json [--max-dist 0.35]`: about 35 s (16 s building the coverage query).
  - `python -m evalharness.cli validate OUT.json` should print `ok`.
  - `python -m evalharness.cli score-verdicts --verdict OUT.json --labels ../criteria/labels-v0/eth3d-delivery.{A,B,C,S}.json --out DIR`.
- Expected delivery output:
  - Sweep: `"counts": {"pass": 0, "unsure": 0, "fail": 978}`, decision `manual_review`, `unknown_route_cells_in_reach` 2283 (S) / 2695 (demo).
  - Score: `"unsafe_passes": 0`, `start_agreement` 0.5239 (S) / 0.5257 (demo); site agrees with A/B (manual_review) and is over-cautious against C/S (reject).
- Expected facade output (laser, demo rules): decision `manual_review`, best spot on w2 at start about 14.3 ft (PR #11: 12.4 ft), route 13.8 ft. Under strict rules both engines reject.
- **How 0 unsafe passes was measured.** `evalharness.verdictscore` samples the battery start every 2 in along each labelled `start_range` (w1: 201 positions, w2: 363 on delivery) and looks up the verdict candidate covering each position; an uncovered position counts as unsure. It counts an unsafe pass when the verdict passes but the truth is fail, borderline or not_pass. The same count also includes site-level passes and passes not backed by `observed` coverage.
  - C2 produced 0 passes on every scene. The 0 is real, but it comes from conservatism rather than a hard test of pass accuracy.
  - The facade labels are site-only (`placement` borderline under S); C2's site decision matches them.

## 4. Parameters that matter
- **Battery box** (Base Core, the same as PR #11): 31 in wide (30.68 in rounded up) × 22 in deep × 39.5 in tall, i.e. 2.583 × 1.833 × 3.292 ft.
- **Strict clearances** (`rules_strictest.yaml`; strictest of profiles A/B/C, each with its source):

  | Rule | Value | Source |
  |---|---|---|
  | gas | 5 ft | A/B |
  | drive | 5 ft | A/B |
  | pool | 10 ft | C |
  | AC | 3 ft | public |
  | fence | 3 ft | public |
  | openings (door, garage_door, window, window_well) | 3 ft; every window counts | B |
  | wall equipment | overlap only | |
  | facing | 5 ft from the wall, battery depth included; width ≥ max(battery, 2.5 ft); band 0.33–6.5 ft high | C |
  | headroom | 7 ft | C |
  | meter working space | 2.5 ft wide × 3 ft deep | NEC 110.26 |
  | route | ≤ 15 ft, same wall; doors and windows block it | A |
  | ground | allowed: concrete, gravel, lawn, mulch, dirt; `drive` fails and feeds the drive clearance | |

- **Demo clearances** (`rules_pr11demo.yaml`; the same numbers as PR #11's demo rules): gas 3 ft, facing 3 ft from the battery front, headroom 6.5 ft, route 20 ft (unsure past 15 ft), +0.5 ft per corner, windows passable.
- **Placeholders I chose** (no profile states them): slope ≤ 5° ±1°; step ≤ 0.25 ft ±half a voxel; backing ≥ 85% of the rear face within 0.5 ft; backing wall ≥ 7.5 ft tall; ≥ 80% of the footprint ground observed; wall-relief skip 0.1 m for headroom; unknown tolerance 0 voxels; sampling-hole size 10 cm; corner = wall normal turning > 30° within ±0.75 m.
- **Sensitivity:** facade w2 fails the slope check at 5.1–5.8°, so the 5° placeholder decides it.
- **Margin rule (C5, every check):** `c5(margin, err)` = pass if margin > err, fail if margin < −err, otherwise unsure. `err` is the label ± plus the 5 cm voxel, or the voxel alone. A pass that also has unknown voxels in its envelope becomes unsure. Do not relax this: before the fix, headroom passed on a 0.06 ft margin against ±0.16 ft.
- **Candidate generation** (`c2vol/sweep.py`):
  - Terrain: per column, the lowest occupied voxel with free space above and nothing solid 10 cm below. Hits more than 0.3 m from a 20th-percentile surface (0.5 m blocks) are rejected; the result is nearest-filled and median-filtered over 0.5 m.
  - Wall-like column: ≥ 50% occupied in the band terrain + 0.3–1.0 m. Skin: a free column next to a wall-like column. Frontier: a free column next to ≥ 0.5 m² of unknown columns.
  - Candidates: skin and frontier columns thinned to one per 10 cm. Yaw comes from PCA of the wall and unknown cells within 0.5 m; the normal points toward the free side.
  - Push back: the rear face is set to the most outward occupied sample behind the candidate.
  - Checks, in local u/v/w samples at 2.5 cm: body collision, backing plus wall height, ground (observed share, slope, step, material), facing, headroom, labelled distances, meter working space, unobserved envelopes, route.
- **Unobserved envelopes:** a 6-connected BFS on a 10 cm coarse grid through observed free space only, band 0.15 ft to 8 ft above the terrain. It counts unknown frontier voxels within plan distance R (path ≤ 2R + 0.5 m). Drive and pool also count unseen ground patches ≥ 0.5 m wide.
- **Route:** skimage `MCP_Geometric` over free columns within 0.25 m of a wall, plus unknown columns within 1 m (flagged). Labelled blockers are buffered 0.25 m and made impassable.
- **Ranking and site decision:**
  - Spot outcome = the worst of its checks. The best spot is the minimum of (outcome rank, sum of check ranks, route length).
  - Site: pass if any spot passes; manual_review if any spot is unsure or unobserved route cells lie within reach; otherwise reject.
  - In verdicts (`c2vol/verdict.py`): only spots within 0.35 m of the chain count; runs with equal per-criterion outcomes are merged, and adjacent runs meet at the gap midpoint (gaps ≤ 0.3 m). A reject also needs both chain ends to be `limit` (criteria spec §4.1).

## 5. Dead ends
- Unknown-means-unsure on the raw grid made 1,700+ of 1,961 facade spots unsure. The causes, in order of impact:
  - 5 cm sampling holes, fixed by a 2×2×2 morphological opening;
  - the unknown ground layer counting as unobserved, fixed by counting only from 0.15 ft above the terrain and treating ground as seen if a ray passed within 10 cm above it;
  - a 26-connected BFS leaking diagonally through rasterised slanted walls, fixed by 6-connectivity;
  - a BFS through unknown space entering building interiors, fixed by flooding free space only and counting the unknown frontier.
- Terrain as simply the lowest hit with free space above picked window sills and a 1 m ledge (facade w2). Use the percentile gate.
- Laser glass gives no returns, which leaves radial unknown streaks. Fill labelled openings with occlusion-stopped extra rays (`build.py`, on by default). Unlabelled glass keeps its streaks.
- The route over the 1-cell skin zig-zagged into window recesses (+20% length). Route over a 0.25 m band instead, with normals from PCA over a 1.0 m radius.
- The `to_pr11.py` overhead probe read protruding pillars as 4 ft overheads (73 spurious headroom fails on electro). An overhead now needs clear space at 1–4 ft beneath it.
- In bash, `(cd X && python $PWD/...)` expands `$PWD` after the `cd`. Capture paths in a variable first.
- Store writes can lag a few seconds, so a file just written may read back as "not found". Retry.

## 6. Data quirks
- The ETH3D scan frame is metres with z up. Each scanner's origin is the translation column of `scan_alignment.mlp` (the PLY camera is at 0,0,0). Laser points reach 25 m past the ROI and must be kept for ray casting.
- `images.txt` in the COLMAP calibration: every other line holds the 2D points. One facade camera sees nothing in the ROI; `depth_rays` must tolerate that.
- Facade: w1–w3 and the invented meter come from PR #13 (`s` in ft, 0 at the meter). The GT scene's `s_range_m` equals PR #13's s × 0.3048. The courtyard ground is only seen at 82–84° (grazing under §3.1); w2 at 78°.
- Electro and delivery labels are mine: laser recess profiles (`tools/elevation.py`) plus photo types. Their meters are invented; the electro gas meter and delivery AC are **synthetic** voxel boxes that cast no ray shadow.
- The delivery GT seed ground type was concrete; the GT scene overrides it to `drive` (vans park there). The C2 sweep must read ground from the GT scene (`--ground-from`), because the seed scene still says concrete.
- The delivery wall chain is simplified: it misses the recess south of the corner at (6.0, −6.0), where C2 finds frontier spots. Clutter stands up to about 1 m in front of w2, so 181 of its 363 labelled positions have no candidate within 0.35 m (with `--max-dist 1.2` agreement is 0.58).
- The eval-harness schema changed during the run (`max_incidence_deg` became `{wall, ground}`), and `build_gt.py` for the facade failed validation for a while. I used the published `results/eth3d-facade/gt-laser/scene.json` with PLYs I rebuilt myself.

## 7. Integration notes for the pipeline
- **Inputs:**
  - scene v0 (`docs/scene-and-verdict-format.md`): `walls`, `chain` (wall_ids, ends), `objects` (openings as `wall_rect`, equipment), `ground.regions`, `frame.from_source`, `keyframes`;
  - an occupancy grid carved from real rays, i.e. the scene's `coverage_grid` asset or one built with `evalharness.coverage.build_grid` from the cloud and cameras. Generated regions must be unknown.
- **Outputs:** `base-verdict/v0`, one per rules profile.
  - Candidates are keyed by chain `wall_id` and `start_s_m`, in metres.
  - Criteria use the §5 ids (wall_backing, ground_surface, facing_gap, headroom, opening/gas/ac/drive/pool/fence_clearance, wall_equipment_above, meter_working_space, route_length, route_path).
  - Each criterion carries `measured.coverage[]` from `query_region`; `views_needed` lists bands.
- **As a veto:** for each C1 candidate span, take C2's worst outcome at the same s. A C2 fail or unsure turns a C1 pass into unsure, with C2's reason. C2 never upgrades a C1 fail.
- **Error codes to surface:** `no_observed_ground`, `no_wall_candidates`, `grid_frame_mismatch` (use `frame.from_source` to map scene ↔ grid), `coverage_query_failed` (then every criterion becomes unsure / not_evaluated).
- **Degrade gracefully:** without a grid, C2 must emit every criterion as unsure / not_evaluated, never pass. Missing materials make `ground_surface` unsure.
- **Cost:** about 2–4 min per house on 4 vCPU. The Python per-candidate loop dominates; numba-ise `evaluate` for production.

## 8. Open risks
- **Untested on phone reconstructions** (COLMAP, depth fusion, ARKit mesh): holes and false free space will move outcomes. Cheapest test: build a grid from `results/eth3d-facade/A1-colmap/*` or a depth-fusion cloud with its cameras (`build_grid`), rerun the sweep, then score.
- **Phone captures are sparser than the laser.** More frontier and unknown means a high manual_review rate. Measure it on the synthetic packets with drift profiles.
- **The placeholders decide cases** (slope, backing, wall height). Get Base's numbers.
- **The frontier policy** (never reject while unknown space lies within reach) disagrees with the delivery C/S labels. Base needs to decide.
- **Wall-yaw PCA at outside corners is noisy.** Corners are mostly rejected by the backing check, but not proven.
- **Scoring coverage is thin:** only delivery has position labels, and no scene yet produced a C2 pass, so pass accuracy is untested.

## 9. What you would do differently
- Start from scene v0 and `build_grid` rays, not the laser, so the phone-capture go/no-go comes first.
- Carry per-voxel view counts and incidence in the grid, so C2's unknown equals the §3.1 rule without a downgrade pass.
- Project spots in front of clutter onto the chain from the start (the candidate coverage gap cost 0.06 agreement).
- Write the C1-veto join first; running C2 as a standalone engine duplicated policy work.
- Numba-ise per-candidate checks early; the Python loop is 90% of runtime.
