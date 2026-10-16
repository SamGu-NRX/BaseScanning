---
cursor:
  subagentId: "bc-aba4e2e6-a6ee-581e-8139-624901e05808"
---

# A9 handoff: no-tape metric scale from meter make/model

## 1. One-paragraph summary
Path `A9`. It asks how well metric scale can be set on a non-LiDAR iPhone with no tape or other tool. Headline (Monte Carlo, 20 000 houses): two meter close-ups, an exact model read from the plate, and a cover-diameter lookup, with ARKit as the gate, give **0.6 % median / 2.0 % p90 / 3.1 % p95** scale error. That puts 95 % of houses within 6 in at 15 ft and 89 % at 26 ft. The declared 95 % bound is 1.4 % median, and in C1's rules engine 67 % of decidable site decisions stay decided. Verdict: **conditional go**. The meter term is simulated (see §8), and doors, garages, brick, siding and footprints may only check the meter, never be fused with it. Report: `internal/breadth/A9-no-tape-scale.md`. Code: `internal/breadth/a9-no-tape/`. Nothing is in `house-scanning-server` yet.

What is measured and what is simulated:
- **Measured (real):**
  - GPT-5.5 names the meter model family right on 21/21 checkable plates (B2's Commons photos; `results/meter_model_check.csv`).
  - 13/22 photos show a sealing ring.
  - The barn's garage width (119.6 in), man-door outline (86.8 in) and siding reveal (6.72 in) in true units (A8).
  - Recon fidelity 0.35 % SD and edge snapping 0.7 px (A8, ETH3D laser).
  - C1 engine geometry from the ETH3D laser.
- **Simulated or modelled:**
  - **The whole meter scale term**: registration rate 85 % (one close-up) or 95 % (two), rim-depth SD 0.3 in (one view) or 0.15 in (two views), contour confusion 3 % (8 % in the stress case), cover moulding 0.3 %, dimension-table coverage 90 %.
  - The ARKit `nonlidar_modern` profile (assumed; `nonlidar_2018` is calibrated on ADVIO).
  - Every object's size priors, footprint errors (taken from published figures), TrueDepth (from published figures), and the C1 perturbation.
  - No scene with truth contains a meter.

## 2. Environment that worked
- 4 vCPU, 15 GB RAM, no GPU. Python 3.12.3.
- `pip install numpy scipy shapely pyyaml pillow openai`. Versions that ran: numpy 2.4.4, scipy 1.18.1, shapely 2.1.2, PyYAML 6.0.1, pillow 12.3.0, openai 3.19.2.
- No model weights. The VLM is the OpenAI API (`gpt-5.5`), keyed by env var `OpenAIChatGraphPooledProdApiKey`; the value is not stored anywhere.
- A8's `common.py` imports `pycolmap`, which isn't needed here. `no_tape_mc.py` stubs it: `sys.modules.setdefault("pycolmap", types.ModuleType("pycolmap"))`.
- **Store file system is slow**: importing from `/cursor/stores/...` took about 28 s and served stale file versions right after edits. Copy code to local disk before running.
- Wikimedia returned HTTP 429 on bulk downloads. `meter_model.py` uses `Special:Redirect/file/<name>&width=2048`, a 3 s sleep between files and backoff.

## 3. Reproduce the headline result
```
S=/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth
mkdir -p ~/a9/a9/results && cp -r $S/a8-scale ~/a9/a8 && ln -sfn ~/a9/a8 ~/a9/a8-scale
cp -r $S/c1-rules-3d ~/a9/c1 && cp $S/a9-no-tape/*.py ~/a9/a9/
cd ~/a9/a9
python3 no_tape_mc.py --a8 ~/a9/a8 --out results          # ~3.5 min; writes no_tape_mc.{md,json}, houses.npz
C1_DIR=~/a9/c1 python3 c1_scale_sweep.py --workers 4 --out results/c1_scale_sweep.jsonl   # 1230 runs, ~9 min
python3 analyze_c1.py results                              # <1 s; c1_scale_summary.md
python3 barn_typed.py ~/a9/a8                              # ~2 min; barn_typed.json
mkdir -p ~/a9/b2/inputs && cp $S/b2/vlm.py ~/a9/b2/ && cp $S/b2/inputs/meter_manifest.csv ~/a9/b2/inputs/
sed -i 's#HERE.parent / "b2"#Path.home() / "a9/b2"#' meter_model.py && python3 meter_model.py --model gpt-5.5   # ~50 s, 22 calls
```
- `no_tape_mc.py` needs to reach A1's `.../a1-classical/results/facade/scores/scale_study_incremental_tour.json`, the store path hard-coded in A8's `combine.py`.
- `no_tape_mc.md`: the row starting `Recommended: meter anchor (two close-ups)` should read `0.6 / 2.0 / 3.1 % | … | 95 % | 89 % | 64 % | 1.4 / 4.6 % | 94 %`.
- Also in `no_tape_mc.md`: `ARKit modern only` reads `2.4 / 5.7 / 6.9 %`, and the tape reference reads `0.4 / 1.0 / 1.2 %`.
- `c1_scale_summary.md`, first table:

  | Bound | Decided sites kept | Truth passes kept | Truth rejects kept |
  |---|---|---|---|
  | 1.0 % | 31/36 | 23/28 | 8/8 |
  | 2.0 % | 22/36 | 16/28 | 6/8 |
  | 3.0 % | 16/36 | 16/28 | 0/8 |
- `barn_typed.json`, key `offcat 0.0 | nonlidar_modern | ARKit + garage + door + siding`: `bias_pct` about −9.65, `coverage95` 0.0.
- Everything is deterministic: RNG seed 7, inherited from A8's `combine.RNG`, and the import order matters. Adding a source earlier in `main()` shifts all later draws, which moves the numbers by about 0.1 point.

## 4. Parameters that matter
| Parameter (`no_tape_mc.py`) | Value | Tried | Sensitivity |
|---|---|---|---|
| `meter(p_table)`: model covered by the dimension table | 0.90 | 0.35 (public datasheets only) | p90 goes from 2.6 to 3.7 %; the biggest lever |
| `meter(p_register)`: close-up registers in the SfM | 0.85 (one view), 0.95 (two) | 0.70–1.0 | 1.0 gives p90 2.2 %; 0.70 is the stress case |
| `meter(depth_known)`: rim offset SD in front of the socket face, in inches | 0.3 (one view), 0.15 (two) | 0.6 | At 39 in: 0.8 % vs 1.5 % 1σ |
| `meter(p_label)`: cover / ring / base mix-up | 0.03 | 0.08 | Tail only |
| Fallback for an unregistered close-up | `C8.meter(4.5, 1450.0)`, SD 3 % | – | ARKit frames are 1920 px, about 1450 px focal |
| `EPS_OUT` uniform outlier weight | 0.03 | – | Keep above 0 or tails get overconfident |
| `--offcat` off-catalogue size weight | 0 | 0.2 | Did not fix the barn failure |
| Conflict gate | objects' bound < 4 % and disagreement > 3 joint SDs | – | Fires on 2 % of houses, which have an 11 % p90 |
| Bound inflation to 95 % coverage | 1.02–1.14 per method; use 1.1 | – | Without it, coverage is 92–94 % |
| Grid | −0.35 to +0.35 in log-scale, step 0.0005 | – | Must contain ARKit 2018's −18 % tail |
| C1 sweep: shape noise 2 in p90, error ±b and 2 × N(0, b/2.5) | – | – | Informed plus-minus = max(gt_pm, shape_p90 + b × distance) |

## 5. Dead ends
- **Fusing size-based objects as equals** (door leaf, garage size from aspect, brick from face aspect, siding by material). It looks best in simulation (1.5 % p90, 94 % within 6 in at 26 ft). On the real barn, all three objects snapped one standard size off and agreed at −9.7 % with a 1.3 % bound, and they outvoted a simulated meter (bias −9.1 %). Standard sizes are about 10 % apart, so a 10 % scale error is self-consistent. The off-catalogue term (0.2) didn't help.
- **Windows** (24–48 in, 2 in steps) and **outlet or hose-bib plates** give no usable prior. Most exterior outlets have in-use covers (NEC 406.9(B)), and the plates are about 40 px across in walk frames.
- **Rear dual-camera depth (AVDepthData)** is reported with *relative* accuracy (optical-centre error), so it is not a scale source.
- **Focal-length metadata, gravity and ARKit planes** carry no independent scale on non-LiDAR phones.
- **Footprints** as a scale source: 5.5 % p90. County outlines follow the roof and include overhangs; Microsoft footprints have IoU 0.85–0.86. Google Open Buildings has no US coverage.
- **Camera height, stride and learned depth** combined with ARKit: 5.3 % p90.
- **C1's original `sweep.py` scale levels** tell the engine the drawn error as the bound (`|s|`). The builder needs the bound decoupled from the error; `BoundPerturber` in `c1_scale_sweep.py` does this.

## 6. Data quirks
- **Meter dimension entries on hand** (inches; source; status):

  | Model | Contour | Value | Source |
  |---|---|---|---|
  | Itron CENTRON | cover, polycarbonate | 6.29 | Itron CENTRON product sheet (na.itron.com attachments 3803848 / 3803954), dim A |
  | Itron CENTRON | cover, glass | 6.42 | Same sheet |
  | Itron CENTRON | overall / base (dims B, G) | 6.95 | Same sheet |
  | Itron CENTRON | depth C | 2.70 poly / 3.03 glass | Same sheet |
  | Itron CENTRON | profile dims D, E | 3.16 / 4.53 poly; 3.55 / 4.90 glass | Same sheet; reference points unclear |
  | Aclara I-210+ | overall diameter | 6.94 max | Aclara I-210+ spec (smartgridawareness.org copy; PSC FL 06853-2015 filing) |
  | Aclara I-210+ | overall depth | 5.25 max | Same spec |
  | Aclara I-210 CL320 RD | overall | 6.95 diameter × 6.75 H | Hubbell ACL-ACLM-SF-EN-00710 |
  | Landis+Gyr Focus AX / AXe | – | no body dimensions; packaging 8.75 × 8.75 × 9.25 only | NY DPS filing; L+G product sheets |
  | ANSI S-base (any model) | base | 6.95 | ANSI C12.10 via the Itron sheet |
  | Sealing ring | outer diameter | 7.065 standard; 7.3 non-standard | Anitya JY-01 listing; Square D ARP00026 |

- In the Monte Carlo, the cover values 6.35, 6.50 and 6.60 (the generic prior for other solid-state and older glass meters) are **assumed**, not sourced.
- In the 22 photos, the base flange is usually hidden: by the sealing ring (13/22) or by the ringless socket cover.
- Cover rims sit 2.7–5 in in front of the socket face; m34 is strongly oblique.
- GPT-5.5's `cover_material` call is unverified; the CENTRON glass-versus-poly call is the open one.
- Model truth came from Commons titles or reading the plate by eye; m37 was not checked. B2's `meter_number` fields hold real numbers: don't copy them anywhere.
- The C1 sweep's `ground_surface` "wrong" flips (about 1 %) are an artefact: the ground height grid isn't rescaled with the scene.
- The only stable site passes out to a 6–8 % bound are delivery_area D1 and D3 under profiles A and B, which are indoor (a logic test). The outdoor facade passes are lost at a 1.5–2 % bound.

## 7. Integration notes for the pipeline
- **Inputs:**
  - the two meter close-ups, registered in the SfM, each with pose, intrinsics and w/h, `pose_source: sfm`;
  - B2's VLM answer extended with `manufacturer`, `model_family`, `model_evidence`, `cover_material`, `socket_type`;
  - ARKit poses (for the scale gate);
  - optional check references: doors and garage doors on the meter side, the footprint from the service address.
- **Two-view rim measurement** (modelled only; not implemented):
  1. The app takes close-up A head-on at about 3 ft and close-up B about 30° to the side at the same distance. Both are taken in the running ARKit session while walking up from the wall view, so they register in the house SfM.
  2. In each view, fit the cover's outer ellipse (sub-pixel edge snapping as in A8's `refine.py`). Also fit the next concentric contour (ring or socket rim) where visible.
  3. Match rim points between A and B along epipolar lines, or intersect the two back-projected ellipse cones. Triangulate the rim, then fit a 3D circle (centre, normal, radius) in recon units. This removes the "rim lies on the socket plane" assumption; the rim-depth SD was modelled as 0.15 in.
  4. Scale = table cover diameter / (2 × fitted radius). Check the second contour's ratio against the table (ring 7.065 / cover); if it differs by more than 2 %, the contours are mislabelled.
  5. Reject the pair if the circle's out-of-plane residual exceeds 1 mm, if single-view scales disagree by more than 1 %, or if either view fails to register. On a reject, use the one-view model (SD 3 % fallback) and request a retake.
- **Outputs** (`docs/scene-and-verdict-format.md`):
  - Add `scale_source: scale_reference`, with the meter's id in `scale_reference_ids` so the scorer skips it.
  - Add a `scale` block: `{source: "meter_model", model, contour, views, rel_bound_95, status, estimates[], conflict}`, with status `verified` / `single_reference` / `unverified`.
  - Every `plus_minus_*` gets `+ rel_bound_95 × distance from the meter`; that sum is a bound, not a σ.
- **Error codes to surface:**
  - `meter_model_unread`
  - `meter_model_not_in_table`
  - `meter_closeup_unregistered`
  - `meter_contour_ambiguous`
  - `scale_conflict_objects`
  - `arkit_gate_fail` (meter and ARKit differ by more than 3 × 10 %)
- **Degrade gracefully:**
  - unknown model: use the generic cover prior, SD 2.2 %, status `unverified`;
  - no close-up registered: use the walk-frame meter, SD 3 %;
  - neither: ARKit only (8.5 % bound), which sends everything far from the meter to review.
- **`member_action`:**
  - `retake_meter_closeups` or `truedepth_meter_scan`; never a tape.
  - Never let size-based objects alone produce `verified`.
  - No reject on the under-window and opening-clearance checks when the bound is over 2 %.

## 8. Open risks
- **The meter term is entirely simulated.** Cheapest test: one real house with truth (a LiDAR iPhone scan or a surveyed wall) captured on a non-LiDAR iPhone. Take 2 close-ups × 3 standoffs and compare against a caliper-measured cover.
- **Registration of a 1 m close-up into the house SfM** (a 5–10× scale jump). Test with the same capture: register it or report the failure rate.
- **Dimension-table coverage.** Measure about 15 common models once with calipers (cover diameter, rim offset from the base, ring). Until then only CENTRON has cover dimensions.
- **Contour confusion rate.** Run the two-contour ratio check on B2's 22 photos by hand clicks.
- **The ARKit `nonlidar_modern` profile is assumed.** Run offline visual-inertial bundle adjustment on ADVIO's iPhone IMU.
- **TrueDepth outdoors** (published: up to 5 % error in daylight) is untested. One device test in sun and in shade.
- **The C1 sweep has only 9 truth-decided site cases, 4 of them indoor**; the site percentages carry wide error bars.

## 9. What you would do differently
- Test the typed-object fusion on the real barn *before* building the Monte Carlo around it. The priors match the simulator by construction.
- Put the second meter close-up and the two-view circle fit in scope from the start. It is the only change that tightened the tail without adding user effort.
- Ask Base for a meter model census (which models are installed in its territory) instead of assuming 90 % table coverage.
- Decouple bound from error in C1's sweep upstream, rather than subclassing its perturber.
- Keep everything on local disk; the store is for final artefacts only.
