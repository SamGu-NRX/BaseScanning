---
cursor:
  subagentId: "bc-aba4e2e6-a6ee-581e-8139-624901e05808"
---

# A9: metric scale with no tape and no tools (breadth result)

Builds on A8 (`A8-scale-without-lidar.md`): same ARKit draws, learned-depth scene errors, recon-fidelity and edge-precision terms, same 20 000-house Monte Carlo seed, same shared-scorer runs. Code and outputs: `internal/breadth/a9-no-tape/` (see "Files"). Labels: **(real)** measured on real images or laser, **(sim.)** simulated, **(model)** from datasheets, standards or published error figures.

## 1. Verdict

**Go, with a smaller auto-decide envelope than the tape: the primary scale source should be the meter itself, read as an exact make and model.** The app takes two meter close-ups (head-on and about 30° to the side) inside the normal ARKit walk. The server reads the model from the plate, looks up that model's cover diameter, and uses ARKit as the gate. Door, garage, brick, siding and footprint references are used **only as a consistency check, never fused as equals**.

Expected results (sim., meter terms modelled):
- Scale error 0.6 % median, 2.0 % p90, 3.1 % p95.
- 95 % of houses within 6 in at 15 ft, 89 % within 6 in at 26 ft. The tape reached 98 % / 97 %.
- The declared 95 % bound is 1.4 % median, and at most 2 % for 77 % of houses.
- Of the rules engine's site decisions, 67 % stay decidable, against 81 % with the tape and 24 % with ARKit alone. The rest go to manual review. The engine made no wrong rejects, and 2 unsafe passes in 1 230 runs, both at the tail of the scale error.

Why objects stay a check and not a fused source:
- Fused symmetrically, all the zero-effort sources look better in simulation: 1.5 % p90, 94 % within 6 in at 26 ft.
- On the one real US building with truth (the Tanks and Temples barn), the measured garage door, man door and siding each snapped to the neighbouring standard size. All three agreed on a −9.7 % scale, with a confident 1.3 % bound and 0 % coverage. They even outvoted a simulated meter anchor.

**No-go as a primary source:**
- Generic meter standard (unknown model): 4.9 % p90.
- Typed objects plus ARKit: 3.2 % p90 in simulation, and −9.7 % on the real barn.
- Footprint data: 5.5 % p90.
- Passive priors (camera height, stride, learned depth): 5.3 % p90.
- ARKit alone: 5.7 % p90 (modern profile), 16.7 % (2018 profile).

## 2. Inputs and outputs

| Input | Label | Use |
|---|---|---|
| A8's ARKit bootstrap (A1: `nonlidar_modern` assumed, `nonlidar_2018` from real ADVIO), learned-depth scene errors, recon fidelity 0.35 %, edge 0.7 px, door outline 1.35 %, siding 0.34 %, brick 0.12 % | real / sim. as in A8 | The same terms, reused through A8's `combine.py` |
| B2's 22 ANSI socket-meter photos (Wikimedia Commons) | real | New GPT-5.5 prompt asking for make, model family, cover material, socket type and base visibility. Truth comes from Commons titles or by reading the plate by eye (`results/meter_model_check.csv`) |
| Meter datasheets | model | Itron CENTRON: cover 6.29 in (poly) or 6.42 in (glass), overall 6.95 in. Aclara I-210+: 6.94 in maximum. Landis+Gyr Focus AXe gives packaging dimensions only. ANSI sealing ring: 7.065 in outside diameter (non-standard 7.3 in). Base: 6.95 in (ANSI C12.10) |
| Barn references from A8, in true units: garage width 119.6 in, man-door outline 86.8 in, siding reveal 6.72 in | real | Real-measurement test of the typed-object fusion (`barn_typed.py`) |
| C1 engine and scenes: facade M1/M2/M3, electro E1, delivery D1/D3 (laser geometry, invented meters) | real geometry, sim. perturbation | New sweep: 11 scale bounds × 5 profiles × 4 trials (true error ±bound and 2 × N(0, bound/2.5)) with 2 in (p90) shape noise, scored per check and per site |
| Footprint, pedometer, camera height and TrueDepth error figures | model (published) | See §3.3 |

Produced:
- `results/no_tape_mc.{md,json}`: 23 methods and combinations over 20 000 houses.
- `results/houses.npz`: per-house error and bound.
- `results/c1_scale_sweep.jsonl` and `results/c1_scale_summary.{md,json}`.
- `results/barn_typed.json`.
- `results/meter_model_gpt-5.5.jsonl` and `results/meter_model_check.csv`.

Fusion method: each source is a likelihood over the log-scale on a 0.05 % grid. Size-based references are a mixture over their possible true sizes (standard sizes) with prior weights. The weights can be updated per house by a scale-free cue: garage and brick aspect ratio, or siding material. There is a 3 % uniform outlier term. ARKit, footprint and the priors use a Student-t (ν = 4). The estimate is the posterior median; the bound is the half-width of the 95 % interval.

## 3. Metrics against ground truth

### 3.1 Meter make and model (B2 photos, real) → datasheet dimensions (model)

- **Model read:** GPT-5.5 names the model family correctly on **21/21 checkable plates** (Wilson 95 % lower bound 85 %; m37 not checked). All 22 had the type printed. B2 already found the manufacturer on 22/22 with three models. The model call can be added to B2's meter-number prompt at no extra cost.
- **What limits precision is not the reading.** The contours differ by model:
  - cover: 6.29–6.42 in on CENTRON, others unknown;
  - sealing ring: 7.065 / 7.3 in;
  - base: 6.95 in, often hidden by the ring or the ringless socket cover.
  - The cover rim sits 2.7–3.0 in in front of the socket face (CENTRON), and further on an oblique view (m34).
  - The real photos show rings on 13 of 22. Measuring the ring as if it were the "6.95 in base", as A8 assumed, costs +1.7 to +5 %.

| Meter route (+ARKit modern unless noted), sim. with real edge precision | Abs scale error median / p90 / p95 | Within 6 in at 15 ft / 26 ft | 95 % bound median |
|---|---|---|---|
| Generic cover prior (6.29–6.60 in, model unknown), 10 % contour confusion | 1.7 / 4.9 / 6.9 % | 78 / 55 % | 4.3 % |
| A8's base-only model (reference) | 0.6 / 7.6 / 9.4 % | – / 88 % | – |
| Exact model, public datasheets only (35 % of meters covered) | 1.2 / 3.7 / 4.8 % | 86 / 68 % | 4.2 % |
| **Exact model, Base-built dimension table (90 %)**, one close-up, 85 % register in the SfM | 0.8 / 2.6 / 4.0 % | 92 / 82 % | 1.9 % |
| Same, every close-up registers | 0.7 / 2.2 / 3.3 % | 94 / 86 % | 1.9 % |
| Same, ARKit 2018 profile / no ARKit | 0.8 / 2.8 / 4.5–4.7 % | 91 / 81 % | 2.0 % |
| **Two close-ups** (rim triangulated: depth offset ±0.15 in, 95 % register) | 0.6 / 2.0 / 3.1 % | 95 / 89 % | 1.4 % |

Where the error budget sits for a known model (1σ):
- rim-depth offset 0.3 in at 39 in (0.8 %);
- recon fidelity 0.5 %;
- moulding tolerance 0.3 %;
- edge pixels 0.25 %;
- cover-material mix-up on dual-cover models: 25 % of meters × 10 % × 2 %.

The tail comes from close-ups that don't register (these fall back to the meter seen in the walk frames: 1450 px focal at 4.5 m, SD 3 %), unknown models, and 3 % contour confusion. **Exact versus generic: p90 2.6 % against 4.9 %, and 82 % against 55 % within 6 in at 26 ft.**

### 3.2 Several known objects with the ambiguity resolved (sim. priors vs real barn)

Modelled objects:
- Entry-door leaf (80 / 78 / 84 / 96 in; 10 % still take the frame).
- Garage door: 10 residential sizes, picked from the aspect ratio.
- Brick: modular, standard, king, queen or engineer, typed from the face aspect.
- Siding: exposure by material, material right 85 %.
- Windows are left out: their sizes run in 2 in steps from 24 to 48 in, so they give no usable prior.
- Outlet and hose-bib plates are left out: exterior outlets mostly have in-use covers of no standard size (NEC 406.9(B)), and the plates are about 40 px across in walk frames.

| Combination | Median / p90 / p95 | Within 6 in at 15 / 26 ft | Coverage of the 95 % bound |
|---|---|---|---|
| Typed objects + ARKit modern (sim.) | 0.8 / 3.2 / 5.6 % | 90 / 79 % | 93 % |
| Typed objects + ARKit 2018 (sim.) | 1.0 / 10.8 / 15.6 % | 78 / 68 % | 92 % |
| **Barn, real measurements, garage + door + siding + ARKit modern** | **9.6 / 9.7 %** (bias −9.7 %) | – | **0 %** (bound 1.3 %) |
| Barn, same + simulated exact-model meter | 9.1 / 9.6 % | – | 71 % |
| Barn, same with a 20 % off-catalogue size term | 1.4 / 9.4 % | – | 82 % |
| Barn, ARKit + simulated meter only | 0.8 / 2.7 % | – | 93 % |

Real finding: standard sizes are spaced about 10 % apart (8 → 9 → 10 ft doors, 78 / 80 / 84 in, 6 / 7 in reveals). A scale error of about −10 % therefore maps every object onto its neighbouring size at once. Here: 119.6 in read as 108 in, 86.8 as 78.4, 6.72 as 6.07. The fused result looks confident and is wrong. The barn is a non-residential building with off-catalogue sizes, which is exactly the case priors can't exclude. Objects are therefore used only to check the meter: flag a conflict when the objects' own estimate has a bound under 4 % and differs from the meter by more than 3 joint SDs. That fires on 2 % of simulated houses, whose meter p90 error is 11 %. Those houses get a meter re-shoot. The rest (clean houses) have a p90 of 1.8 %.

### 3.3 Public footprints (model, published error figures)

- **County GIS:** stereo-compiled planimetrics at ASPRS 1"=100' class 1, i.e. about 1 ft RMSE per coordinate. L.A. County's outlines include permanent overhangs, so they are **roof outlines, not walls**.
- **Microsoft US footprints:** IoU 0.85–0.86, 1.6–2.5° rotation error, simplified shapes; a Polish study found a 4 m shift. The shift doesn't matter for lengths; a 0.5 m (1.6 ft) boundary error is assumed.
- **Google Open Buildings** has no US coverage.
- **Model assumptions:** a 25–60 ft wall with both ends reconstructed (60 % of houses). Roof edge matched to roof edge (fascia ends) in 70 %, otherwise walls plus a 0–2 ft overhang prior. 8–15 % gross errors (wrong edge, additions).
- **Result:** p90 5.5 %, 44 % within 6 in at 26 ft. It adds almost nothing on top of the meter. **Check only.**

### 3.4 Passive phone data

| Source | Evidence | Declared SD | Verdict |
|---|---|---|---|
| ARKit plane detection and gravity | Planes on non-LiDAR phones use VIO scale; gravity gives direction only | – | No independent scale; ARKit's own scale is already the gate |
| Camera height over the reconstructed ground | US adult stature SD about 9.5 cm (both sexes), holding-height style about 8 cm on about 1.40 m (model) | 9 % | Weak prior |
| Step count × stride prior | CMPedometer steps within 2–7 %, but distance off by 10–40 % (up to +43 ± 42 %); the capture walk is slow and irregular | 16 % | Weak prior |
| Focal length and intrinsics metadata | Needed by the SfM; carries no scale | – | Not a source |
| Rear dual-camera depth (AVDepthData) | Apple reports it as *relative* accuracy (optical-centre error) | – | Not a source |
| **TrueDepth (front) scan of the meter at 30–45 cm** (near-zero effort: flip the phone for 2 s) | Lab: plane distances within 0.05–0.36 mm at 175–450 mm; up to 5 % in daylight at up to 1 m. Assumed 30 % failure in sun | 1.5 % | Best add-on, **needs a device test outdoors** |

- Priors plus ARKit modern: p90 5.3 %.
- Adding TrueDepth to two close-ups: 60 % of houses have a bound ≤ 1.5 %, 71 % of site decisions stay decidable, against 67 % without it.

### 3.5 Accept the uncertainty: what the rules engine can still decide (C1 engine on laser geometry, sim. perturbation)

1 230 runs. Every run carries 2 in (p90) of shape noise. The 0 % row is shape noise only.

| 95 % scale bound | Decided sites kept | Truth passes kept | Truth rejects kept | Unsafe passes | Wrong rejects |
|---|---|---|---|---|---|
| 0 % | 89 % | 6/7 | 2/2 | 0 | 0 |
| 0.5–1 % | 86–89 % | 23–24/28 | 8/8 | 0 | 0 |
| 1.5 % | 75 % | 19/28 | 8/8 | 2 (D3 A/B, true error +1.5 %) | 0 |
| 2 % | 61 % | 16/28 | 6/8 | 0 | 0 |
| 3–6 % | 44 % | 16/28 | 0/8 | 0 | 0 |
| 8 % | 28 % | 10/28 | 0/8 | 0 | 0 |
| ≥ 12 % | 0 % | 0/28 | 0/8 | 0 | 0 |

Which sites survive:
- The real outdoor passes (facade under profile C: a spot between windows about 30 ft along the wall) survive to a 1–1.5 % bound.
- The facade rejects survive to 2 %.
- Only the near-meter delivery passes (profiles A and B; indoor, so a logic test only) survive to 6–8 %.

Per check, the share of truth-decided instances that stay decided and right:

| Check | 1 % | 2 % | 3 % | 6 % | 12 % |
|---|---|---|---|---|---|
| meter working space, route length, drive / AC clearance | 98–99 | 96–98 | 95–97 | 90–93 | 70–85 |
| headroom, facing gap, wall backing | 96–98 | 94–98 | 92–93 | 85–88 | 73–83 |
| pool clearance, gas clearance | 95–97 | 91–94 | 85–92 | 72–83 | 43–69 |
| opening clearance | 97 | 92 | 86 | 62 | 28 |
| under window | 89 | 80 | 73 | 58 | 46 |
| footprint clear (shape-limited: 89 % at 0 %) | 63 | 48 | 43 | 35 | 23 |

Wrong pass↔fail flips stay at about 1 % or less up to a 6 % bound: facing gap 0.7–0.8 %, headroom 0.4–0.5 %, and ground surface 0.9–1.1 %, which is a sweep artefact because the height grid isn't rescaled.

Mapping each simulated house's bound onto the site curve (bound inflated by the factor that gives 95 % coverage, 1.02–1.14 here):

| Method | Site decisions kept | Houses with bound ≤ 2 % |
|---|---|---|
| ARKit modern only | 24 % | 0 % |
| Meter exact, one close-up, objects as check | 56 % | 57 % |
| **Meter exact, two close-ups, objects as check** | **67 %** | **77 %** |
| + TrueDepth | 71 % | 79 % |
| All zero-effort, symmetric fusion (sim. only, not trusted after the barn) | 68 % | 76 % |
| Tape 10 ft + meter + ARKit (A8) | 81 % | 94 % |

The shared scorer on the real facade geometry (A8 §3.6) confirms the error model. At ±1.5 % applied scale error, the long key distances read 1.1–6.3 in (median), inside extractor noise. At −2.4 % they read 6.1 in, and at −5.6 % 9.2 in.

## 4. Runtime and cost per house (4 vCPU, no GPU)

- Meter model read: one extra field in B2's GPT-5.5 call (about 7 s, $0.036 per photo, marginal cost about $0). Ellipse and contour fitting: under 1 s.
- Fusion: under 10 ms per house (a 1 401-point grid × up to 11 sources). Footprint lookup: one GIS or parquet query.
- The C1 sweep (1 230 runs) took about 9 min; the Monte Carlo (23 combinations × 20 000 houses) took about 3 min.
- GPU: not needed.

## 5. Failure modes

1. **Coherent wrong modes in size-based references** (real, barn). Several objects agree on a scale about one standard-size step (≈10 %) off, with a tight bound. This is the reason objects are a check only.
2. **Contour identity on the meter** (real photos): cover, sealing ring (13/22 have one), base and ringless socket cover sit 3–15 % apart. The fitter must label the contour, and check it with the known ratio between two contours.
3. **Rim depth.** The cover rim sits 2.7–5 in in front of the socket face. One close-up needs the datasheet offset; two views triangulate the rim.
4. **Close-up registration.** A 1 m close-up may not register in the house SfM (a scale jump of about 5–10×). Unregistered close-ups fall back to a 3 % term. Keep the ARKit session running while walking up to the meter.
5. **Unknown or undocumented models.** Public datasheets give a cover diameter only for Itron CENTRON; without a Base-built table, p90 goes from 2.6 to 3.7 %.
6. **Assumptions to validate first:** registration rate, rim-depth SD, contour-confusion rate, the TrueDepth failure rate in sun, and the ARKit "modern" profile. The stress case (70 % registration, ±0.6 in rim depth, 8 % confusion, 20 % door errors) gives p90 2.0 % and 88 % within 6 in at 26 ft for the symmetric fusion.

## 6. Licence

- Code: ours.
- Commons meter photos: per B2's manifest (CC BY / BY-SA / PD), internal evaluation only.
- Microsoft footprints: ODbL. County GIS: per county (often public domain, some licensed). Google Open Buildings: CC BY 4.0 / ODbL, no US coverage.
- Datasheet figures are cited as facts.
- ETH3D (CC BY-NC-SA) and Tanks and Temples (CC BY) as in A8.
- The VLM API is used under OpenAI's terms.

## 7. What going deeper would take

1. **One real house with truth** (laser, LiDAR iPhone or surveyor) captured on a non-LiDAR iPhone, including two meter close-ups and a TrueDepth scan. This replaces the modelled meter terms in §5.6, and is the single highest-value test.
2. **A meter dimension table**: calipers on about 15 common models (cover diameter, rim offset, ring, base), a one-time job. This raises coverage from 35 % to 90 %.
3. **A contour-labelling fitter**: cover and ring ellipse pair with a known-ratio check, a two-view rim triangulation, and a test of the confusion rate on B2's 22 photos.
4. **Offline visual-inertial bundle adjustment** on raw 100 Hz IMU (ADVIO has iPhone IMU with truth). This could shrink ARKit's gate term and replace the assumed modern profile.
5. **Residential size study** (real US doors and garages) to see whether a meter-gated fusion of objects can recover the simulated 94 % within 6 in at 26 ft without the barn's failure.

## 8. What the iOS app must capture (no tools)

1. **Two meter close-ups:**
   - one head-on from about 3 ft, with the whole cover, ring and socket face in frame, filling about half of it;
   - one from about 30° to the side at the same distance.
   - Take both inside the running ARKit session, walking up from the wall view. The first one already serves meter-number reading (B2).
2. **Passive logging:** ARKit poses, per-frame intrinsics, gravity, detected planes and raw CoreMotion IMU (100 Hz, for offline visual-inertial bundle adjustment). No extra user effort.
3. **Walk coverage:** include any entry or garage door on the meter side head-on at least once. These are the check references.
4. **Optional near-zero effort:** a TrueDepth scan ("turn the phone around and hold it about a foot from the meter for 2 seconds"), prompted only in shade or when the scale status comes back `unverified`.
5. The service address is already known: the server does the footprint lookup, with no user action.

## 9. How the verdict should carry scale uncertainty

Extend A8's `scale` block:

```json
"scale": {"source": "meter_model", "model": "Itron CENTRON C1SD (poly)", "rel_bound_95": 0.016, "status": "verified",
          "estimates": [{"kind": "meter_model", "contour": "cover", "rel_sd": 0.008, "used": true, "views": 2},
                        {"kind": "arkit_poses", "rel_sd": 0.03, "residual_pct": -1.9, "used": true},
                        {"kind": "garage_door_sku", "sku": "16x7", "residual_pct": 0.4, "used": false, "role": "check"},
                        {"kind": "footprint", "source": "county", "residual_pct": 2.1, "used": false, "role": "check"}],
          "conflict": false}
```

- `rel_bound_95` is the posterior 95 % half-width × 1.1, which reaches 95 % coverage in the simulation.
- Status values:
  - `verified`: an exact-model meter from a registered close-up, with ARKit and every confident check agreeing;
  - `single_reference`: the meter only, with no confident check;
  - `unverified`: a generic meter, objects only, or an unresolved conflict.
- Size-based references never produce `verified` on their own.
- Per measurement, keep A8's linear rule: `plus_minus = shape_bound + rel_bound_95 × distance from the meter`. The C5 margin rule then does the rest.
- **Routing:**
  - A bound ≤ 1.5 % keeps tight far sites decidable.
  - At 2 %, rejects and most near-meter decisions stay decidable.
  - At 3–6 %, only near-meter passes stay decidable (44 %).
  - Above 12 %, everything goes to review.
- When scale flips a decision to unsure (`unsure_cause: margin` with `bound_breakdown {shape_in, scale_in}` where the scale term dominates), set `member_action`:
  - `retake_meter_closeups` when the meter was unregistered, unknown or in conflict;
  - otherwise `truedepth_meter_scan`;
  - never a tape.
- Keep A8's rule: no reject on the 16–26 ft reach rules with a bound over 6 %. Tighten it to 2 % for the under-window and opening-clearance checks, the most scale-sensitive ones.

## Files

All under `internal/breadth/a9-no-tape/`:

- Code:
  - `no_tape_mc.py`: sources, mixture fusion, combinations, recommended policy;
  - `barn_typed.py`: real barn test;
  - `meter_model.py`: VLM make/model read;
  - `c1_scale_sweep.py`, `analyze_c1.py`: rules-engine sweep.
  - `no_tape_mc.py` imports A8's `combine.py` via `--a8`, and needs a local `a8-scale` path because the store's slow file system makes imports take about 30 s. The sweep needs C1's code via `C1_DIR`.
- `results/`:
  - `no_tape_mc.{md,json}`, `houses.npz`;
  - `barn_typed.json`;
  - `meter_model_gpt-5.5.jsonl`, `meter_model_check.csv`;
  - `c1_scale_sweep.jsonl`, `c1_scale_summary.{md,json}`.
- VM only: the downloaded Commons photos (`~/a9/meter_images`).
