---
cursor:
  subagentId: "bc-c58db527-2f93-56d2-af32-ef71a2ea3ed0"
---

# C1 rules engine in 3D: builder handoff notes

`S` = `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`, `C` = `S/internal/breadth/c1-rules-3d`, `H` = `S/internal/eval-harness`. All work ran on a 4 vCPU / 15 GB / no-GPU Ubuntu 24.04 cloud VM.

## 1. One-paragraph summary
Path C1: the production criteria decider (Track C gate, `S/internal/gate/track-c.md`). It evaluates Base placement rules on a multi-wall 3D scene. Inputs: a wall graph with inside/outside corners, candidates on every wall at a 2 in grid, Dijkstra cable routes that wrap corners, clearances measured in plan or 3D across corners, and headroom, facing, footprint and ground slope/step checks against scanned geometry. It outputs `base-verdict/v0`. Headline on ETH3D laser ground truth, with invented meters:
- hand-worked decisions: facade 15/15, electro 10/10 blind; delivery 6/15 blind, with all 9 misses traced to the hand-work;
- 55/55 contract verdicts validate; site `placement` 17/17 agrees with `S/internal/criteria/labels-v0`, with 0 site unsafe passes.

Verdict: **go as decider; conditional on geometry error p90 ≤ ~6 in and scale ≤ ±1 %**. Report: `S/internal/breadth/c1-rules-3d.md` (the older `C1-rules-3d.md` is the same content). Code: `C/*.py`, `C/profiles.yaml`. Not in the `house-scanning-server` repo yet. Do not confuse it with harness `run-c1` (`H/evalharness/adapters/hs11.py`), which runs PR #11's single-chain solver, not this engine.

## 2. Environment that worked
- Python 3.12 (3.11+ fine). `uv venv ~/c1venv --python 3.12 && VIRTUAL_ENV=~/c1venv uv pip install -r C/requirements.txt`. Requirements: numpy, scipy, shapely (2.x API: `get_coordinates`, `make_valid`, `set_precision`), pyyaml, pillow, matplotlib, jsonschema, opencv-python-headless (only for the harness validator). Add `pydantic` to run `H`'s CLI. Everything is permissive (BSD/MIT/Apache).
- The engine and verdicts need only the saved scenes in `C/outputs/scenes/` (about 0.5 MB each).
- Scene building additionally needs:
  - ETH3D `<scene>_dslr_scan_eval.7z` and `<scene>_dslr_undistorted.7z` from `https://www.eth3d.net/data/` (facade, electro, delivery_area; about 2.8 GB total, about 3 min). `apt install p7zip-full` first, because 7z is not preinstalled.
  - PR #13's builder: `git clone https://github.com/SamGu-NRX/house-scanning.git`, then `git worktree add ../verif origin/t3/verification`.
- The PR #13 builder's `load_scan_crop` hard-codes facade's 3 scans. For electro (4 scans) and delivery (2) use `scantools.load_scan` (already switched in `build_scene3d.py`).
- ETH3D is CC BY-NC-SA, research only: no scene JSON or derived asset goes in a repo.
- The `/home/ubuntu` paths are hard-coded: `scantools.py` `ETH3D`, `build_scene3d.py` `PR13`, `elevations.py`, `emit_all.sh` `PY=`, and `score_labels.py` `LABELS`. Make them env vars or CLI args when porting.

## 3. Reproduce the headline result
```bash
cd C
python run.py outputs/scenes/facade-M1.json public A B C strictest   # ~3 s/profile; public reject, C pass w1 31.00-33.58 route 8.517
python run_all.py outputs/scenes/*.json --contract                   # -> outputs/results/*__contract.json
bash emit_all.sh                                                     # 70 verdicts -> outputs/verdicts/ (~95 s, 11 in parallel)
cd $H && for f in $C/outputs/verdicts/*.json; do python -m evalharness.cli validate $f; done
                                                                     # 55 contract verdicts ok; 5 of 15 *-c1rule FAIL (expected)
cd $C && python score_labels.py $(ls outputs/verdicts/*.json | grep -v c1rule)
                                                                     # "20 verdicts with labels; 17 scored; agree 17/17; site unsafe passes 0"
python sweep.py --trials 4 --workers 4 && python analyze_sweep.py    # 2268 runs, ~12 min at ~3.3 runs/s -> outputs/sweep/summary.md
python from_base_scene.py $H/results/eth3d-facade/gt-laser/scene.json outputs/scenes/harness-gt-laser.json
```
Scene rebuild: `python build_scene3d.py annotations/facade.json [--cameras all|pr13] [--coverage-rule c1|contract] [--suffix=-x]`, 13–60 s per site. Use `--suffix=-x`: argparse rejects `--suffix -x` because the value starts with `-`.

Expected output: `facade-M1 public` gives reject with 416 candidates, 0 pass, 0 unsure. The PR #13-content check (`facade-pr13cov-M1 public`) gives manual_review, best w2 at u 2.67, route 13.07 ft (PR #11 gave 12.97).

## 4. Parameters that matter
**Rule sets** (`C/profiles.yaml`; each is `base` merged with an override via `engine3d._merge`; strictest is emitted as `S` in verdicts):

| Key | public (PR #11) | A | B | C | S (per-key strictest) |
|---|---|---|---|---|---|
| battery w × d × h (ft) | 2.58 × 1.83 × 3.29 | 2 × 2 × 3 | 2 × 2 × 3 | 2.58 × 1.83 × 3 | 2.58 × 2 × 3.29 |
| gas / drive / pool (ft) | 3 / 5 / 10 | 5 / 5 / 5 | 5 / 5 / 5 | 3 / 3 / 10 | 5 / 5 / 10 |
| openings | 3 ft, all windows | 3 ft + no window above at any height | 3 ft, sill ≤ 6 ft only | 0 ft, sill ≤ 6 ft only | 3 ft + any height |
| route max / confident | 20 / 15 | 15 / 15, 0 corners | 25 / 25, 1 corner | 26 / 16 | 15 / 15, 0 corners |
| crossings | door fail, window detour | window/door fail | door fail, window detour | door over-review, window detour | window/door fail |
| facing | 3 ft from front | inherit | inherit | 5 ft from wall | 5 ft from wall |
| headroom | 6.5 | inherit | inherit | fail < 6, pass > 7 | 7 |
| other | – | no reject | no reject | 3 ft backing, dirt allowed | 3 ft backing, no reject |

Common to all profiles: meter working space 2.5 × 3 ft, ground allowed concrete/gravel/lawn/mulch, slope ≤ 10 %, step ≤ 0.25 ft, corner allowance 0.5 ft, route height 1 ft. Judgement calls, which Base must confirm:
- "ground-level window" = sill ≤ 6 ft;
- A = no window above the battery at any height;
- C's headroom band 6–7 ft;
- C's 3 ft backing;
- the slope and step limits (C2 uses 5°; unify them).

**Margin rule** (`at_least`, `at_most`, `banded` in `engine3d.py`; EPS 1e-9):
- PASS if value − err − thr > 0; FAIL if thr − (value + err) > 0; otherwise UNSURE with cause `margin`.
- For reach: FAIL if value − err > max; PASS if value + err < confident; otherwise UNSURE.
- Distance errors add linearly: object ± plus wall ± (plus meter ± for reach).

**Decision rule:**
- any PASS candidate gives `pass`, or `manual_review` when `auto_approve` is off;
- otherwise any UNSURE gives manual_review;
- `reject` requires `allow_reject` and no unexplored wall end within reach (route distance − meter ± ≤ max).

**Coverage evidence rule as implemented:**
- Engine side (`contract: {fail_needs_observed: true}`, the default in `to_verdict.py`; `--c1-rule` turns it off): every PASS or FAIL whose region is not ≥ 90 % observed becomes UNSURE `unobserved`. Regions per check:
  - own wall stretch plus the subject object's span for backing, opening, under-window and wall-equipment;
  - all route stretches for route checks;
  - the meter wall ±1.25 ft for working space;
  - a ground strip 0 to max(depth, thr + 2 ft) for gas, AC, drive, pool, footprint and ground;
  - an overhead strip 0–3 ft for headroom; ground 0–5 ft for facing.
- Verdict side: the coverage entries use the same regions, graded observed (≥ 0.9) / partial (≥ 0.5) / unobserved. The rule is tagged `min_views` 1 unless the scene was built with `--coverage-rule contract` (then 2 views at ≤ 75°). Evidence does not call `query_region` yet.

**Scene coverage defaults:**
- walls: 1 view within 75°;
- ground: pixel footprint along the ray ≤ 3 in, up to 88° (the harness `footprint3in`);
- unseen shadows narrower than the object that could hide in them are ignored (`HIDE_FT`: gas 1, AC 2, pool 6, drive 8 ft);
- the 0.25 ft wall-foot strip counts as seen where the wall's low band is.

The facade verdict flips on the ground rule: all review under angle75 or angle80, C passes under footprint3in. Don't change a ground rule silently.

**Grid step:** 1/6 ft (2 in) for verdicts; 1/3 ft is OK for sweeps (about 2× faster; decisions identical on the ground-truth scenes).

## 5. Dead ends
- An 82° ground incidence cutoff: the DSLR stood about 10 m back, so it zeroed coverage along the whole wall base. Replaced by the pixel-footprint rule.
- Laser points on wall faces fell into the ground height grid's 10th percentile and faked a 0.35 ft step at every wall foot. Exclude points within 0.12 ft of a face.
- `_covered(fp, unseen, radius=0)` used a distance test, so it was always true. Radius 0 must be an area test (< 0.02 ft²).
- Plan distance can't go negative, so a spot directly under a window was UNSURE, not FAIL, under C's 0 ft rule. Same-wall openings now use a signed along-wall gap.
- The unexplored-end disc boundary was treated as an "unseen wall" and poisoned spots 20 ft away. Use the wall's continuation line past the end instead.
- Sweep hangs and crashes:
  - A meter perturbed past its wall end gives a negative Dijkstra edge and an infinite loop with unbounded heap growth, which OOM-kills the pool (fixed: clamp `meter_u` to [0, L]).
  - Self-intersecting carried polygons raise GEOS `TopologyException` (fixed: `make_valid` in `_poly`, snap coverage to 0.01 ft).
  - `signal.alarm` plus `RLIMIT_AS` did not save the pool from the MemoryError. Fix causes; don't rely on limits.
- Don't run the verdict emitters (11 processes) while the 4-worker sweep runs: the VM OOM-killed the sweep.

## 6. Data quirks
- Frame: `c1-scene3d/0` is PR #13's scene frame in feet (y = fitted ground normal). Reuse `meta.frame_A` / `meta.frame_origin_scan_m`, because the origin depends on the RANSAC inlier set. `base-scene/v0` is meter-anchored and in metres; `from_base_scene.py` converts it (outward = baseline turned 90° clockwise, `[dx, dz]` → `[-dz, dx]`).
- Facade:
  - w2 has a ground-floor arched window (u 1–6.5 ft, sill 3.5 ft) visible only in the laser; PR #13 missed it (the harness GT has since added it);
  - w2 has no camera coverage within 75°;
  - w3 ends at 12.7 ft (PR #13 had 16.2, which runs past the pilaster);
  - w1.a is the scan edge, not a wall.
- Electro: modelled as one wall; the glass facade has no laser returns and the jog is scaffold clutter. Doors at u 3.3–7.0 / 8.5–15.7 / 17.4–24.5 / 26.5–33.2 ft; the pilasters between them are 1.3–2 ft.
- delivery_area is indoors (logic test). C1's D1–D3 meters are not the harness or label meter, so don't score them against `eth3d-delivery.*` labels.
- Invented: every meter, `gas_w2`, and all ground materials.
- The label for facade C placement is `unknown` (review is favoured because the gravel's drivability is undeterminable); C1 passes it only because gravel is labelled allowed.

## 7. Integration notes for the pipeline
- Input: `base-scene/v0` (`docs/scene-and-verdict-format.md` §2): walls, chain corners and ends (`limit` / `unexplored`), objects (`wall_rect` for openings), ground regions with material, coverage bands (wall / ground / overhead), overheads, facing, and a meter with `plus_minus_m`.
- Every `plus_minus_m` must be an honest p90 bound including scale × distance. The sweep's naive mode, where error was not reported, produced 80 unsafe passes.
- Dropped on input: `generated` objects (treated as unknown). Material `unknown` means no patch, so ground is UNSURE, never allowed.
- Output: one `base-verdict/v0` per scene × profile (`public`, `A`, `B`, `C`, `S`):
  - a site `placement` criterion (pass → pass, manual_review → unsure, reject → fail);
  - the 13 candidate checks for the best spot, each with `measured` or `fact` plus `coverage[]`;
  - candidate runs in chain `s_m`;
  - `views_needed` (a band per unobserved check; `past_end` per unexplored end in reach). Empty on pass or reject.
- Validate every verdict (`python -m evalharness.cli validate`). Score with `score-verdicts`, which scores only against labels of the verdict's own `rules_profile`; `--any-profile` overrides.
- Error codes to surface: no meter object; meter wall missing; a free end left unclassified; a scene without coverage (must give all-unsure, never pass).
- Degrade gracefully when the ground grid is missing: flat plane plus UNSURE ground_surface.

**Adapter fixes made during scoring** (harness `hs11` adapter and scorer; mirror them in the port):
- Grazing-only fails: a fail whose evidence is `grazing` or `unobserved` becomes unsure with `unsure_cause` `grazing` or `unobserved`. Sweep runs are re-checked: a fail run stays fail only if at least one failing check rests on all-observed regions. The site decision is then recomputed: reject becomes manual_review with `coverage_downgrade` and a head-on `views_needed`. On facade GT, 6 of 27 fail runs rested only on grazing w2, so PR #11's reject became manual_review.
- Wrong-label scoring: delivery demo-rule verdicts had been scored against the A/B/C/S labels (a profile mix-up). `score-verdicts` now matches `evaluator.rules_profile` to the labels' `rules_profile`.
- C1's own verdicts carry `rules_profile` S for strictest, and site ids map to `eth3d-facade` / `eth3d-electro`. The labels-v0 file names use `S`.
- Validator: `views_min` is now defaulted in `sceneio` (it used to be a KeyError). C1 still emits it.

**Geometry accuracy the engine needs** (sweep: 4 scenes × public/C/S × 4 seeds, engine told the p90; GT-pass runs = 12):
- 0 in: 12/12 passes kept; 2 in: 11/12; 4 in: 8/12; 6 in: 6/12; 8 in: 2/12; ≥ 10.8 in or any ARKit drift profile: 0/12, all manual_review.
- Scale ±1 %: 12/12 kept; +2 %: 4/12; −2 %: 0/12; ±5 %: 0/12.
- Unsafe passes when informed: 4 / 1,104, all on truth-unsure spots. When naive: 80, including 12/48 at scale +5 %.
- The binding distances are the clear wall between openings (a battery plus 2 × 3 ft = 8.58 ft; facade C passes on a 5.13 ft gap with 0 ft windows), opening edges, and ground within 10 ft (pool). Target: opening-edge and wall-end p90 ≤ 4–6 in, scale ≤ ±1 %, and ground seen to 12 ft out.

## 8. Open risks
- No real house or real meter has been run. Cheapest test: feed `H/results/eth3d-facade/A1-colmap/{gt_dense,incremental_dense}/scene.json` through `from_base_scene.py`, then run `score-verdicts`.
- Wall and chain extraction on real iPhone captures (bump-outs, decks, fences) is untested; C1 assumes straight walls joined at corners.
- Non-Pro iPhones: ARKit drift of 11–27 in at 10–30 ft means every site goes to review. Only a scale reference plus SfM (A1 conditional go) keeps passes.
- The strictest profile (0 corners, 15 ft same wall) forbids every corner route; Base must confirm.
- The pool/drive 10 ft radius forces review behind any occluder.
- Ground material comes from hand labels; it needs Track B/C3 attributes as downgrade-only inputs.
- The sweep drift model is half shared, half independent, with 4 seeds; it is not ARKit-correlated.
- C2 veto integration (conservative AND per position) is not built.

## 9. What you would do differently
- Build the `base-scene/v0` reader and contract-mode evidence first: the evidence rule changed more verdicts than any geometry fix.
- Call `query_region` for evidence instead of C1's own coverage, and record view counts and angles.
- Put all thresholds in one cited rules file shared with C2 and `S/internal/criteria/criteria.yaml`.
- Fuzz the engine with degenerate geometry (meter past a wall end, invalid polygons, zero-length walls) before any sweep.
- Get candidate-scope start-range labels for A/B/C/S with a fixed meter, so unsafe passes are scored per position, not per site.
