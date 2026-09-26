---
cursor:
  subagentId: "bc-40f7af01-969a-5377-9507-a40fb8d9ab1d"
---

# C2: volumetric placement check (Track C, phase 1 breadth)

## 1. Verdict

**Go, as a CPU-only geometry backend and cross-check for C1.** C2 matches PR #11's site decision on 8 of 10 real-scan scene × rule-set runs; the other 2 differences are explainable, not bugs. It also finds real-geometry failures PR #11 cannot see (ground slope, missing backing, protruding sills, corner recesses). Its verdicts validate against `base-verdict/v0`, with 0 false coverage claims in the audit. Scored against the delivery position labels, it has **0 unsafe passes** and 52% position agreement; the rest is over-caution (§3.4). There are two caveats. First, it needs a dense metric grid carved from real observation rays; every C2 number here comes from the ETH3D laser, or from laser geometry seen through the DSLR poses. Second, the facade and electro labels are site-level only.

*Update 17:35 UTC.* Two fixes (§3.4). (1) Every check now applies the C5 margin rule: pass needs margin > uncertainty, fail needs margin < −uncertainty, anything in between is unsure. `verdict.py` also enforces this as a safety net; it previously let headroom pass on a 0.06 ft margin against ±0.16 ft. (2) Delivery was re-swept with its ground taken from the rebuilt GT scene, where the floor is `drive`. All sweeps and verdicts were re-emitted; no facade or electro decision changed.

## 2. Inputs (real vs synthetic) and what was produced

| Scene | Geometry | Labels | Invented / synthetic |
|---|---|---|---|
| ETH3D facade (courtyard wing, w1–w3) | real laser (3 scans), 5 cm grid | PR #13's clicked windows and door, identical to PR #11's input | meter invented (PR #13) |
| ETH3D electro (utility-door wall and glass facade) | real laser (4 scans) | 4 doors placed from the laser recess profile (0.16 m recess, 3.04 m high), glass facade from photos | meter invented; **gas meter synthetic** (voxels inserted) |
| ETH3D delivery_area (loading garage) | real laser (2 scans) | sliding garage door and mesh cage from the laser profile and photos | meter invented; **AC unit synthetic** (voxels inserted) |

Observation sources for the facade grid: the laser (best case), laser geometry z-buffered into PR #13's 7 keyframes, and the same for all 76 DSLR photos. Both photo modes have perfect geometry, so observation is their only variable.

Produced (all under `internal/breadth/c2-volumetric/`):
- The engine (`c2vol/`): `grid.py` does ray carving. `build.py` builds laser or photo grids, with labelled glass filled as surfaces and synthetic solids inserted. `sweep.py` sweeps the battery and its envelopes over every free column next to a wall or next to unobserved space; there is no wall extraction, and corners fall out of the checks. `verdict.py` writes `base-verdict/v0` with contract §3.1 evidence.
- Tools (`tools/`): `to_pr11.py` converts a C2 scene plus grid into a PR #11 `scene.json`, with walls, spans, overheads, facing and coverage bands taken from the same grid. The others are `make_scenes.py`, `run_pr11.py`, `plot_sweep.py` and `elevation.py`. `run_all.sh` reruns every comparison.
- Rules: `rules_strictest.yaml` (the strictest of profiles A/B/C, sources cited) and `rules_pr11demo.yaml` (mirrors PR #11's demo numbers).
- Results (`results/`): the sweeps (`sweep/*.json.gz`), verdicts (`verdicts/*.json.gz`), PR #11 inputs and outputs (`pr11in/`, `pr11/`), grids, `agreement.txt`, and plots such as `viz/c2_facade_laser_demo.png`, `c2_facade_photos_kf_demo.png`, `c2_electro_demo.png` and `c2_delivery_demo.png`.

## 3. Metrics

### 3.1 Site decision: C2 vs PR #11 on the same scene, labels and grid-derived coverage

| Scene / observation | Rules | C2 decision (best spot, route) | PR #11 decision (best spot, route) |
|---|---|---|---|
| facade, laser | demo | manual_review (w2 start 14.3 ft, 13.8 ft) | manual_review (w2 start 12.4 ft, 12.9 ft) |
| facade, laser | strict | reject (every spot fails) | reject |
| facade, 7 keyframes | demo | manual_review (w2 start 12.6 ft, 13.2 ft) | manual_review (w2 start 12.4 ft); on PR #13's own scene: 12.5 ft, 13.0 ft |
| facade, 7 keyframes | strict | reject | reject |
| facade, 76 photos | demo / strict | manual_review / reject | manual_review / reject |
| electro | demo / strict | manual_review / manual_review | manual_review / manual_review (unexplored end) |
| delivery | demo / strict | **manual_review** | **reject** |
| delivery, floor = `drive` (GT scene ground) | demo / strict | manual_review (every spot fails; unobserved wall-side space within reach) | not rerun (the PR #11 scene has no drive region) |

- **Delivery difference:** C2 proposes "frontier" spots where observed free space meets unobserved space that could hide a wall. Here that is a recess just south of the corner, 4.7 ft from the meter, which the hand-drawn wall chain simplified away. C2 also refuses to reject while unobserved wall-side cells lie within cable reach (2,283–2,695 cells). This is by design and conservative. PR #11 only knows the chain it is given.
- **Electro:** C2 and PR #11 agree, and their reasons match. C2's unsure spots sit around the door wall's east corner, the space PR #11 flags as `unexplored_end`.

### 3.2 Per-position agreement along the chain

Measured on C2 spots within 0.35 m of the chain, compared with PR #11's sweep runs (`results/agreement.txt`):
- Outcome agreement: facade demo 94–96%, facade strict 100%, electro 100%, delivery 100% (every position fails in both).
- The facade demo disagreements (15–21 positions) are PR #11 unsure where C2 fails. They sit on w2 at 11.7–13.4 ft, where C2 measures a **5.1–5.8° gravel slope** against the 5° placeholder; PR #11 has no slope input.
- The window-clearance check fails in both engines at the same 220–235 facade positions. C2 fails another 105–180 that PR #11 leaves unsure within its label ± band. The route fails in both at 193–349 positions. The synthetic gas meter (electro) and synthetic AC (delivery) fail identically in both engines (51/71 and 74 positions).
- Checks only C2 can fail: backing or body collision (97–189 positions: corners, recesses, bins, a cage mesh that is not a wall), ground slope/step (39–219), and headroom from protruding sills and pipes (8–77).
- Checks only PR #11 fails: facing near w1's inside corner (9–33 positions, from the converter's straight-out facing probe). Headroom showed 73 PR #11-only fails on electro; that was a converter bug reading pillars as 4 ft overheads, which is now fixed.

### 3.3 Contract verdicts (`base-verdict/v0`, facade GT scene `eth3d-facade/gt-laser`)

- Six verdicts (laser, 7 keyframes and 76 photos, each under `demo` and `S`). Each passes `python -m evalharness.cli validate`. `audit-verdict` on the laser/demo verdict: 3,172 coverage claims, **0 false observed claims**, 0 contract errors.
- Every site decision is **manual_review**, which matches `labels-v0/eth3d-facade.*`: the site decision is manual_review and `placement` under S is `borderline`. There are 0 passes anywhere, so 0 unsafe passes, trivially. `score-verdicts` does not exist in the CLI yet, and the labels hold no candidate-scope truth, so no per-position score was possible.
- **§3.1 effect.** Each verdict downgrades 579–731 C2 pass/fail criteria to unsure: 389–508 `grazing` and 155–223 `unobserved`. The largest groups are `ground_surface` (162–214), the route checks, and drive/pool clearance.
  - The courtyard ground is graded `grazing` everywhere (82.9° against the 80° ground limit), and w2 is `grazing` (78.4° against 75°).
  - The strict verdict turns C2's raw reject into manual_review for two reasons: both chain ends are `unexplored` (criteria spec §4.1), and route fails on w2 rest on grazing evidence.
  - Views needed: head-on photos of the ground strips of w1–w3 and of the w2/w3 faces.
- **Does §3.1 explain the unsure spots?** Only partly. C2's raw unsure spots (2–14 per scene, all on w2 or frontier spots) come from its own volumetric rule: unknown voxels inside an envelope, or unobserved backing. The §3.1 grading adds a different kind of unsure: seen, but only at a steep angle. Every spot on the grazing w2 and every ground-dependent check becomes unsure. Earlier the unsure counts ran to about 1,700 envelope checks; those were artefacts (5 cm sampling holes, the ground layer, BFS leaks). They were fixed in the engine, not explained by the contract (section 5).

### 3.4 Margin fix and delivery re-score (`internal/eval-harness/results/eth3d-delivery/verdictscores/gt.C2.*`)

**Margin rule (C5), all checks.** `c5(margin, err)` in `c2vol/sweep.py` now decides:
- facing, headroom, the labelled clearances (gas, AC, openings, fence, drive, pool) and route length;
- ground slope (±1°) and ground step (±half a voxel), both placeholders;
- meter working space and wall equipment, graded by keep-out gap or penetration depth against the combined label and voxel error.

A decisive outcome with unknown voxels in its envelope is still unsure. `verdict.py` independently demotes any pass or fail whose `margin_ft / plus_minus_ft` breaks C5. It also moves the same-wall corner failure from `route_length` to `route_path`, so no route fail contradicts its own margin. Result: 0 C5 violations in every re-emitted verdict. On delivery under S, 4 headroom checks that used to pass are now unsure (margin).

**Delivery re-sweep.** The GT scene was rebuilt locally with `build_gt_c2.py --ground-material g1=drive`; it is identical to the published `gt-laser/scene.json`. `sweep.py --ground-from` reads a scene v0's `ground.regions`, maps them back to the scan frame through `frame.from_source`, and applies them in two ways: the drive surface fails `ground_surface`, and every drivable region feeds `drive_clearance`. Raw C2 now fails all 978 spots under both rule sets (`drive_clearance` 978, `ground_surface` 968). The site decision stays manual_review, because 2,283–2,695 unobserved wall-side cells lie within cable reach and both chain ends are unexplored.

**Two verdict emitter fixes found during scoring:**
- The scorer counts positions outside every candidate as unsure. Adjacent candidate runs now meet at the midpoint of gaps up to 0.3 m.
- A drive/pool clearance whose measured gap is below the clearance is now decided on the ground up to that gap. Before, it demanded the whole clearance strip be observed.

`score-verdicts` results. Both verdicts validate, and every row has **0 unsafe passes** and 0 false rejections.

| Verdict | Position agreement (before → now) | Site vs A/B label (manual_review) | Site vs C/S label (reject) |
|---|---|---|---|
| demo | 0.36 → 0.53 | agree | over-caution |
| S | 0.24 → 0.52 | agree (before: missed review) | over-caution |

- **What remains is over-caution.** 181 of the 363 w2 positions have no C2 candidate within 0.35 m of the wall line: pallets, the cage and clutter stand up to about 1 m in front of it. Mapping spots up to 1.2 m out onto the wall's s (`--max-dist 1.2`, file `verdicts/delivery.S.d12.json.gz`) lifts S agreement to 0.58.
- The rest is `drive_clearance` on floor that §3.1 grades grazing or unobserved (views needed: ground strips of w1 and w2).
- C2 cannot reject here while unobserved space lies within cable reach, even though the labels say C and S reject. That is the policy choice discussed in §5.4.

## 4. Runtime and cost per house (4 vCPU, 15 GB RAM, CPU only, $0)

| Step | Time |
|---|---|
| Laser grid, 5 cm (facade 640×580×106) | 40–70 s (scan loading dominates; carving 1–17 s) |
| Photo grid (7 or 76 cameras, stride 4) | 32–58 s |
| Sweep, both rule sets (360–3,400 candidates) | 12–105 s each |
| PR #11 via converter | 20 s convert, 0.2–0.3 s solve |
| Verdict with §3.1 evidence | about 35 s (16 s building the coverage query) |

That comes to about 2–4 minutes per house. A GPU would not change anything; everything is numba or numpy.

## 5. Failure modes (found and handled, or open)

1. **Glass gives no laser returns**, which leaves ray shadows in front of windows. This is handled by filling labelled openings as surfaces (occlusion-stopped extra rays). Unlabelled windows on other buildings still leave shadows.
2. **Terrain on ledges and sills.** The lowest-hit-with-free-above rule picked a 1 m ledge on the tower wall (w2). It is fixed with a 20th-percentile gate over 0.5 m blocks and a rule that a hit must have no solid 10 cm below it.
3. **Envelope flood leaks.** A 26-connected flood slips diagonally through rasterised slanted walls, and flooding through unknown space enters building interiors through unobserved wall patches. The fix is a 6-connected flood through observed free space that counts only the unknown frontier. Unknown pockets thinner than 10 cm count as sampling holes; this is a documented placeholder.
4. **Frontier spots make C2 conservative.** It returns manual_review whenever unobserved wall-side space lies within reach (delivery). That is safe, but it will raise the manual-review rate on real phone captures.
5. **Placeholders decide outcomes:** slope 5°, backing 85% with a 0.5 ft gap, 7.5 ft minimum wall height, the 0.1 m wall-relief skip for headroom, and unknown tolerance 0. The w2 slope result depends on the 5° value.
6. **Semantics still come from labels.** C2 measures geometry; doors, windows, gas and AC must be labelled. Synthetic solids cast no ray shadow.
7. **The PR #11 comparison is sensitive to the hand-drawn chain** (the delivery corner recess) and to converter heuristics (facing, overheads).
8. **Two notions of "unseen".** C2's voxel unknown (rays) and §3.1's view count plus incidence angle differ. Verdicts apply both, but the sweep itself does not yet know incidence angles.
9. **Candidate runs are fragmented** (165–216 per facade verdict) because per-criterion outcomes flicker at 10 cm spacing near thresholds.

## 6. Licence

- ETH3D: CC BY-NC-SA 4.0, research and non-commercial only. Derived grids and plots stay in the private store.
- numba, numpy, scipy, scikit-image, shapely and matplotlib: BSD / PSF-style, commercial use allowed.
- PR #11 and PR #13 code comes from the house-scanning repository and was run unmodified.

## 7. Depth-phase recommendation

**Keep both engines. Give each the job it is good at, and join them at the scene v0 / verdict v0 contract.**

| | C1 (PR #11 rules engine via the `hs11` adapter) | C2 (volumetric sweep) |
|---|---|---|
| Input | the scene's abstractions: wall chain, spans, `overheads[]`, `facing[]`, coverage bands | a dense occupancy grid with real observation rays (the scene's `coverage_grid`, or one built from a Track A cloud and its cameras) plus the scene's labelled objects and ground regions |
| Owns | the rule catalogue and policy: profiles A/B/C/S, route legality and length along the chain, opening and equipment spans, chain ends, site composition, the user-facing verdict | geometry the chain cannot express: body collision, real backing (flush wall vs fence, bin, ledge or glass), ground slope and step, true 3D headroom and facing, corners and recesses, and unobserved space inside each envelope |
| Speed | about 0.3 s per scene | about 30–100 s per scene on 4 vCPU |

**How to combine them.** C1 stays the decision engine and the one verdict users see. C2 runs as a second opinion on the same scene v0, with two jobs:
1. **Veto.** For each C1 candidate span, look up C2's outcome at the same s. A C2 fail or unsure (backing, collision, slope, headroom or unobserved envelope) turns a C1 pass into unsure, with C2's reason and `views_needed`. C2 never turns a C1 fail into a pass.
2. **Extra evidence and checks.** C2 fills in overheads and facing that the extractor could not derive cleanly. It also flags frontier spots, i.e. possible walls in unobserved space, as `past_end` / `region` views needed.

Combine at the verdict level: one candidate list keyed by s, taking the worse outcome per criterion. On every scene so far the two agree on 94–100% of positions, so the veto will mostly add C2-only failures (§3.2), not flip decisions.

**Depth-phase work for C2, in order:**
1. **Consume Track A scene v0 end to end.** Build the grid from the scene's own rays (`evalharness.coverage.build_grid`) instead of the laser. Rerun on the COLMAP and depth-fusion facade scenes to measure how reconstruction holes and false free space move C2's outcomes. This is the real go/no-go for phones.
2. **Put the §3.1 rule inside the sweep** (per-voxel view count and best incidence), so C2's unknown matches the contract and the verdict-time downgrade becomes a no-op.
3. **Candidate mapping.** Project spots that stand on clutter in front of the wall onto the wall's s (§3.4: agreement 0.52 → 0.58 at 1.2 m). Merge runs that flicker near thresholds.
4. **Policy decisions for Base:** the ground incidence limit (the facade ground sits at 82–84°), and whether unobserved space within cable reach should block a reject (C2 does; the delivery C/S labels say reject). Also replace the placeholders (slope 5°, backing share, 7.5 ft minimum wall height, 10 cm hole size).
5. **Electro verdicts.** `build_gt_c2.py` now builds its scene v0, so the electro verdicts are one run away; they were not emitted here (no new experiments).

**Reproduce.** `bash internal/breadth/c2-volumetric/run_all.sh [WORK_DIR]` rebuilds the grids and GT scenes, then runs the sweeps, PR #11, verdicts, validation and scoring. VM setup: `internal/handoffs/C2-volumetric.md`. Essential code lives in `c2vol/` (`eth3d`, `grid`, `build`, `sweep`, `verdict`) and `tools/` (`to_pr11`, `make_scenes`, `run_pr11` plus its strict override, and the plot/elevation helpers). Scenes are in `scenes/`; rules in `rules_strictest.yaml` and `rules_pr11demo.yaml`.

## 8. Summary

1. C2 is a CPU-only volumetric placement check (about 2–4 minutes per house, $0). It matches PR #11's site decision on 8 of 10 ETH3D runs; the 2 differences on delivery come from unobserved space C2 sees and a wall chain cannot express.
2. It adds real-geometry checks PR #11 cannot make (slope, backing, collision, 3D headroom). Along the walls the two agree on 94–100% of positions.
3. Verdicts follow the contract: C5 margins on every check, §3.1 coverage evidence, 0 false observed claims, 0 unsafe passes.
4. Delivery with the floor as `drive`: raw C2 fails every spot, and the site decision agrees with the A/B labels. Position agreement is 0.52 (0.58 with clutter mapping); the rest is over-caution from grazing floor and clutter in front of the walls.
5. Depth phase: C1 decides and C2 vetoes. Next, prove C2 on Track A reconstructions rather than the laser, which is the real go/no-go.
