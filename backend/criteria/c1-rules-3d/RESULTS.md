---
cursor:
  subagentId: "bc-c58db527-2f93-56d2-af32-ef71a2ea3ed0"
---

# C1: structured placement rules in 3D

## How to run

From `internal/breadth/c1-rules-3d/` with Python 3.11+ and `requirements.txt` installed (e.g. `uv run --no-project --with-requirements requirements.txt python ...`). The engine and verdict steps run on the saved scenes in `outputs/scenes/`; only scene building needs the ETH3D data.

- Evaluate one scene under profiles (default all five): `python run.py outputs/scenes/facade-M1.json public A B C strictest`
- All scenes × profiles to `outputs/results/`: `python run_all.py outputs/scenes/*.json [--contract]`
- Contract verdicts (`base-verdict/v0`) to `outputs/verdicts/`: `python to_verdict.py outputs/scenes/facade-M1.json public A B C strictest [--c1-rule]`; all of them: `bash emit_all.sh` (edit its `PY=` line first)
- Score verdicts against the site labels: `python score_labels.py outputs/verdicts/*.json` (reads `internal/criteria/labels-v0` via the `/cursor/stores/...` path in `LABELS`)
- Error sweep and summary: `python sweep.py --trials 4 --workers 4 [--resume]`, then `python analyze_sweep.py` (writes `outputs/sweep/summary.md`)
- Convert a shared-contract scene: `python from_base_scene.py BASE_SCENE.json OUT_scene3d.json`
- Rebuild a scene from the laser (needs ETH3D at `/home/ubuntu/eth3d` and PR #13's builder at `/home/ubuntu/work/verif/verification/scenes/eth3d-facade`; edit `ETH3D` / `PR13` in `build_scene3d.py`, `scantools.py`, `elevations.py`): `python build_scene3d.py annotations/facade.json [--cameras all] [--coverage-rule contract]`
- Wall elevations and plan plot: `python elevations.py annotations/facade.json outputs/scenes/facade-M1.json`; `python plotscene.py outputs/scenes/facade-M1.json out.png`

Smoke check on Hunter's Mac (2026-09-26): `run.py outputs/scenes/electro-E1.json public C` gives manual_review for both, 212 candidates, 0.3 s each.

Code and outputs: `internal/breadth/c1-rules-3d/` (engine `engine3d.py`, profiles `profiles.yaml`, scene builder `build_scene3d.py`, contract I/O `from_base_scene.py` / `to_verdict.py`, sweep `sweep.py` + `analyze_sweep.py`, hand-worked answers `expected/`). Previous agent's handoff: `internal/handoffs/C1-rules-3d.md`.

## 1. Verdict

**Go** for C1 as the rules backend: on laser ground truth it matches hand-worked answers on facade (15/15) and electro (10/10, blind), and on delivery_area every one of the 9 disagreements with my blind hand-work traced to my omission, not the engine; it emits contract verdicts that validate (55/55) and agree with every scored placement label (17/17, 0 unsafe site passes), and runs in 1–6 s per scene × profile on CPU. Its limit is upstream: with honest error bounds it only passes a site when reconstruction error is ≤ ~6 in; at ARKit-drift levels every pass becomes manual review.

## 2. Inputs and what was produced

**Real (ETH3D laser + DSLR photos, research-only):** facade (courtyard wing: w1 52.0 ft, w2 11.6 ft, w3 12.7 ft; inside corner w1/w2, outside corner w2/w3), electro (one 37.6 ft recessed-door service wall facing an asphalt yard), delivery_area (indoor hall: B 51.2 / R 31.3 / T 32.0 ft, inside corner B/R, outside corner R/T; **indoors, logic test only**). Walls, joints, ground height grid, overheads, standing obstacles, per-wall/ground/overhead camera coverage (z-buffer against the scan, all cameras) all come from the scan. Openings: PR #13's photo clicks on facade plus laser-read openings from wall elevations (`outputs/views/*_elev_*.png`).

**Invented (label every dependent result):** all electric meters (facade M1 = PR #13's, M2 next to the outside corner, M3 next to the inside corner; electro E1; delivery D1–D3 next to corners), the facade gas meter `gas_w2` on the perpendicular wall, and ground material labels (facade gravel, electro asphalt = drive or untyped, delivery concrete).

**Synthetic:** the error sweep (perturbed copies of the real scenes).

**Produced:** 3D engine (wall graph with inside/outside joints, candidates on every wall, Dijkstra cable routes wrapping corners with height-aware crossings, plan/prism clearances across corners, headroom/facing/footprint against scanned geometry, ground slope/step, three-valued margin rule, PR #11's decision rule); 5 profiles (public = PR #11, A, B, C, strictest = S); 11 scene files; 70 verdicts in `base-verdict/v0` (`outputs/verdicts/`); a `base-scene/v0` reader (runs the harness's `gt-laser` facade scene directly); 2 268-run error sweep.

Scene-build fixes made on the way (each changed verdicts): ground seen by camera judged by pixel footprint along the ray (≤ 3 in, ≤ 88°) instead of an 82° cutoff that discarded the whole wall base; wall-face points kept out of the ground grid (they had faked a 0.35 ft step at every wall foot); per-foot plinth/pilaster offset so footprints start at the proud face; sub-foot laser holes in flat ground filled only from ≥ 8 agreeing neighbours; unseen shadows ignored only when narrower than the object that could hide in them (gas 1 ft, AC 2, pool 6, drive 8); same-wall opening distance signed (a spot under a window is negative, not 0).

## 3. Metrics against ground truth

**Consistency with PR #11** (public, PR #13 content and 7 keyframes): same decision (manual_review), same wall (w2), route 13.07 ft vs PR #11's 12.97. The spot starts ~1 ft further along w2 because the true plan distance to w1's last window across the inside corner is shorter than PR #11's unrolled chain distance.

**Facade, laser truth, 3 meters × 5 profiles (15 cases, `expected/facade.json`; arithmetic written after the first smoke run, so a check, not a blind test):** 15/15 decisions, best walls and routes agree, including C's 11 passing grid spots between windows 3 and 4 (u0 29.28–31.04) and C on M2 routing round both corners (22.5 ft → review). Public gives **reject** on M1/M3: PR #13's "w2 could hold a spot" does not survive the laser, which shows an arched ground-floor window on w2 (u 1–6.5 ft, sill 3.5 ft) that PR #13 and the harness `gt-laser` scene both miss. PR #13's w3 (16.2 ft) runs past the tower's far pilaster; the laser ends it at 12.7 ft.

**Electro and delivery_area (25 cases, `expected/electro-delivery.json`, written blind):** electro 10/10. Delivery 6/15 of my hand answers held; all 9 misses were my oversights that the engine applied correctly: public/C/S pool rules need 10 ft of observed floor, and a crate's shadow leaves ≥ 6 ft of unseen floor 8.9 ft in front of R; on D2 the invented meter sits on T's unobserved stretch, so every route starts on unseen wall and nothing can pass.

**3D effects measured, not projected:** gas meter on the perpendicular wall seen across the inside corner (plan distance 51 − u1 vs 52 − u1 + 1 unrolled, ~2 ft shorter); upper-floor windows only matter under A's "no window above at any height"; routes wrap inside and outside corners with a 0.5 ft allowance; strictest (0 corners, 15 ft) makes every off-meter-wall spot unreachable, so no corner-wrapping route is ever legal under S.

**Contract verdicts vs labels (`internal/criteria/labels-v0`, site-scope `placement`):** 20 verdicts have labels (facade M1 = the labelled meter, harness gt-laser, facade under the contract coverage rule, electro typed/untyped × A/B/C/S): 17 scored, **17/17 agree**, **0 site unsafe passes**; facade C is `unknown` in the labels (pass acceptable) and not scored. All 55 contract verdicts pass `evalharness.cli validate`. The 15 comparison verdicts under C1's native evidence rule: 5 break §3.1 (route length or under-window passes on partial/unobserved walls), and facade C passes, which would count as a site unsafe pass against the label's manual_review. The harness has no `score-verdicts` yet, so `score_labels.py` implements §6.2 for the site-scope labels.

**Coverage rule decides facade.** Under the contract's §3.1 rule (2 views ≤ 75° for ground too) no courtyard ground is ever observed (0 ft²), so all 15 facade cases are manual_review. Under C1's pixel-footprint rule the ground is seen at ~1 in per pixel and C passes (M1, M3). This is the contract's open decision; my recommendation is a resolution-based ground rule, because a phone at 4032 px and 30 ft resolves ~1 in at 83°.

**Error sweep (synthetic perturbation of real scenes; 4 scenes × public/C/S × 24 levels × 4 seeds × 2 modes; `outputs/sweep/summary.md`).** GT passes exist only under C (facade M1, M3, delivery D3).

48 runs per row and mode; "GT passes kept" counts the 12 runs whose ground truth is pass.

| Level | Informed: flips / unsafe / GT passes kept | Naive: flips / unsafe |
|---|---|---|
| 0 in | 0% / 0 / 12 of 12 | – |
| 2 in | 2% / 1 / 11 of 12 | 2% / 2 |
| 4 in; multi-view depth good (2.8 in walls, 8 in edges) | 8% / 0–1 / 8 of 12 | 2–4% / 3–4 |
| 6 in; mvd mid (5 / 8 in) | 12% / 1 / 6 of 12 | 2–4% / 3–4 |
| 8 in | 21% / 0 / 2 of 12 | 4% / 4 |
| mvd poor (10.8 in) | 29% / 0 / 0 of 12 | 6% / 4 |
| ≥ 12 in, photo + tape, ARKit drift (LiDAR, non-LiDAR, 2018) | 40–42% / 0 / **0 of 12** | 8–19% / 0–5 |
| scale ±1% | 0% / 0 / 12 of 12 | 0% / 0–4 |
| scale +2% / −2% | 17% / 25%, 0 unsafe, 4 / 0 of 12 | 0% / 4 / 0 |
| scale ±5–10% | 42% / 0 / 0 of 12 | 0% / 0 (−) to **12 (+)** |

Informed (engine told the p90): 4 unsafe passes in 1 104 runs, all on spots that are only UNSURE in truth (within the window-5 / pool margin), none on a truth FAIL. Naive (engine keeps the laser ±): 80 unsafe passes, 15 of them on truth FAILs (openings, meter working space); positive scale error is the worst case because it widens the gap between windows. Rejects never appear where truth is not reject.

## 4. Runtime and cost per house

4-core CPU VM, 15 GB, no GPU. Engine: 1.4 s (1 wall) to 6 s (3 walls, 650 candidates) per scene × profile at a 2 in grid; ~0.3–0.6 s at 4 in. Five profiles ≈ 7–30 s per house. Scene build from the laser + all cameras: 13–60 s. Contract verdict emission including coverage evidence: ~5–10 s per verdict. $0.

## 5. Failure modes

- **Evidence rule matters more than geometry:** C1's native "unseen is unsure" let route-length and under-window outcomes pass on partially seen walls; fixed by the contract mode (`contract: fail_needs_observed`), which is now the default for emitted verdicts.
- **Engine bug found by the sweep:** a meter measured past its wall's end gave the route graph a negative edge and Dijkstra looped forever (fixed: clamp). Perturbed polygons needed `make_valid` / 0.01 ft snapping.
- **Rule interpretations are placeholders** (`profiles.yaml` header): ground-level window ≤ 6 ft sill, A = no window above at any height, C headroom band 6–7 ft, C 3 ft backing, 10% slope / 0.25 ft step. Base must confirm.
- **Ground material is a hand label.** The facade C label is review because gravel drivability is undeterminable; C1 passes it only because gravel was labelled allowed. Material must come from Track B with evidence.
- **Pool / drive radii (10 ft) demand large observed ground;** any occluder shadow within 10 ft forces review, even indoors where a pool is absurd.
- **Invented meters dominate verdicts** (D2 meter on an unseen wall; facade reject hinges on the meter's position).
- Site-scope criteria (meter number, CL320, panel) are out of C1's scope; C1 emits only `placement` plus the 13 candidate checks.
- Coverage evidence in C1 verdicts comes from C1's own coverage (view counts not recorded, rule tagged `min_views` 1 unless built with `--coverage-rule contract`); it does not yet call `evalharness.coverage.query_region`.

## 6. Licence

ETH3D data CC BY-NC-SA, research only: nothing derived goes into the house-scanning repo. Code uses numpy, scipy, shapely, pillow, pyyaml, matplotlib, jsonschema, opencv-python-headless (BSD / MIT / Apache): commercial use fine.

## 7. What going deeper would take

1. Call `query_region` for every criterion (needs the scene's keyframes and PLY; the `base-scene/v0` reader already exists) and run `audit-verdict` against `gt-laser`.
2. Base decisions: ground coverage rule (angle vs pixel footprint), the five interpretation calls, and whether S really forbids all corner routes.
3. Real houses with real meters (labels only exist for invented or absent meters so far); feed Track A scenes through `from_base_scene.py` and sweep with their measured error instead of the table.
4. Candidate-level labels (`start_s_m` ranges) so agreement is scored per position, not per site.
5. Missing checks: disconnect space (A/B), walkway and street-facing (C), movable blockers.
6. Correlated drift model in the sweep (currently half shared, half independent per point) and ≥ 10 seeds per level.

Harness notes for the eval-harness owner: `validate` crashes with `KeyError: 'views_min'` when a coverage entry omits that optional field (`sceneio.py` line 68 evaluates the default eagerly); the `gt-laser` facade scene lacks the w2 arched window and the w1 upper windows.
