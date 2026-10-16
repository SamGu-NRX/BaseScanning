---
cursor:
  subagentId: "bc-0fee9895-3632-5ced-bc27-540dd99aca5d"
---

# Breadth-phase gate decision: server pipeline

2026-09-26, 21:36 UTC. Reports that had not arrived by then are marked **PENDING** and are not reflected in the recommendation. Hunter's constraints, which rank everything below:
- **Regular non-Pro iPhone, no LiDAR, is the primary target.** **When the phone has LiDAR, use it by default** (object placement, scale, coverage / occlusion, densifying) and fall back to the image paths otherwise; every step in Section 1 has both branches.
- **No tape measure or any tool.** Photos (and on-screen taps where an app is involved) are allowed; physical measuring is not.
- **No Anthropic models in the pipeline.** Reads and object confirmation use **GPT-5.5 + Grok 4.7** as the agreement pair, with cheap tiers from OpenAI or xAI only.
- **Two flows.** (1) **Guided flow:** continuous ARKit capture through the iOS team's guided app. (2) **Photo flow, the priority:** about 60 ordinary iPhone photos, no LiDAR, no tools, possibly no usable ARKit poses.

**Report status**

| Report | Status |
|---|---|
| **E2E criteria on reconstructions** (verdicts on photo-built models vs labels and laser-geometry verdicts) | **PENDING** (not arrived by 21:36 UTC); primary evidence when it lands |
| **A10 sparse-photo flow** (sweeps 10 to 60 photos) | **PENDING** (not arrived by 21:36 UTC); primary photo-flow evidence when it lands |
| B3 cheap gate (classifier + crops ahead of tiered VLMs; also measures GPT-5.5 + Grok 4.7 agreement safety) | **PENDING** (not arrived by 21:36 UTC); will update the scene-understanding recommendation, the pair's safety and the per-house VLM cost |
| A9 zero-effort scale (meter model → datasheet, fused objects, footprints, passive sensors, targeted review) | final (20:18 UTC), folded in: **the scale layer** |
| A6 iPhone quality (real iPhone-class frames, real ARKit, laser truth: LaMAR) | final (20:31 UTC), folded in |
| A7 iPhone degradation (DSLR degraded to iPhone quality) | **PENDING** (not arrived by 21:36 UTC) |
| A1–A3, A5, A8, B1, B2, C1–C3 | final, folded in |
| A4 Gaussian splatting / neural fields | never run: **untested** |

## Top open risk: the evidence is DSLR imagery, not phone imagery

Every accuracy number in Tracks A and C, and every measured term in A8, comes from **DSLR or rig imagery** (ETH3D's 24 MP DSLR photos downsampled to 2048 / 1024 px; Tanks and Temples' 1080p barn) with laser truth. Phones add noise, rolling shutter, motion blur, auto-exposure, compression, unexposed lens distortion and lower resolution, and houses add low-texture siding. ARKit poses are **simulated** (the modern non-LiDAR profile was **assumed**; A6's real no-depth ARKit sessions now roughly match it, and the 2018 profile is calibrated on real iPhone data). The only phone imagery is YouTube walkarounds (360×480 to 1920×1080, no ARKit, no truth), used for robustness. **Nothing has run on a real iPhone capture of a real house, in either flow.** Tables mark **DSLR** vs **phone** evidence.

**A6 partly answers this risk.** It re-ran A1 and A2 on real iPhone-class ARKit frames (LaMAR: 1440 × 1920 `capturedImage`, rolling shutter, real per-frame ARKit poses, laser truth) along a Zürich street of facades. Caveats: every such public dataset was captured on LiDAR-equipped Apple devices, so the non-LiDAR proxy is the 8 sessions recorded without scene depth; the scenes are street facades, not detached houses; frames are dense video-rate (~0.3 m apart), not ordinary photos. A true non-LiDAR iPhone capture with truth still does not exist, and nothing has run on a real house. ETH3D's image-only runs remain the closest structural analogue of the photo flow, and A6 shows phone imagery makes that mode markedly worse (below).

## The two flows at a glance

| | (1) Guided flow: continuous ARKit capture | (2) Photo flow: ~60 ordinary iPhone photos |
|---|---|---|
| Input | 60–150+ overlapping ARKit keyframes with pose, intrinsics, epoch, tracking; checklist stills; on-screen taps | ~60 photos with EXIF (intrinsics from focal length); gravity / location if attached; ARKit poses only if present |
| Sensor branch | LiDAR branch on Pro phones, image branch otherwise (Section 1) | image branch (LiDAR only if captured in-app on a Pro phone) |
| Poses | A1 COLMAP incremental SfM with **tight ARKit priors (σ ≈ 3 cm, no robust loss; A6)**, spatial matching; fall back to fixed ARKit triangulation if SfM splits | A1 COLMAP incremental SfM **image-only** (ARKit priors if any poses exist) |
| Dense geometry | π³ 8–16-view windows on the refined poses | same |
| Scale (no tape, A9) | meter read as an exact make and model → its datasheet cover size, measured in **two close-ups** (head-on + ~30°) taken inside the ARKit session; ARKit as the gate; doors / garage / siding / footprint only as checks | same without the ARKit gate; close-ups must register via a bridge sequence of photos walking up to the meter |
| Scale error (A9, sim., meter terms modelled) | **0.6 / 2.0 / 3.1%** (median / p90 / p95); 95% bound 1.4% median, ≤ 2% for 77% of houses | one close-up, no ARKit: 0.8 / 2.8 / 4.5–4.7% (A9); two close-ups without ARKit not reported |
| Shape accuracy, **real phone frames** (A6, ≤ 10 m from camera) | openings 0.5–2 m: **0.9–1.9 / 2.8–4.2 in** per segment (in bar, incl. no-depth sessions); 15–26 ft: 3.8 / 11.3 in with ARKit scale, **3.0 / 7.4 in with correct scale** (scorer floor 1.7 / 5.4) | image-only: openings 1.5 / 14.3 in; 15–26 ft 4.6 / 57.2 in; **folded or fragmented on 2 of 5 segments** (−46%, −19% scale) even with dense frames |
| Shape accuracy, DSLR (ETH3D) | 0.84 / 2.90 in on the meter wall (non-LiDAR sim.) | 0.60 median all KDs; 0.67 / 7.0 in on truly observed elements |
| Houses within 6 in at 15 / 26 ft | 95% / 89% | ~91% / 81% (one close-up, no ARKit), **if the model doesn't fragment** |
| Site decisions the rules engine keeps (A9 C1 sweep) | **67%** (tape would be 81%, ARKit alone 24%) | ~56% (one close-up) to 67%; lower if close-ups fail to register |
| Main failure risk | scale (A6: real ARKit session scale 1.2% median / 2.8% p90, no-depth sessions worse, +2.6% cost ~5 in at 20 ft); addressed by A9's meter scale | **image-only SfM on phone imagery**: folds / fragments (A6, 2 of 5 real segments; every YouTube walkaround); without ARKit the pieces can't be joined or share scale |
| Meter anchor | on-screen tap | tap if the app is used; otherwise detection (B1 meter instance recall 0.62 at best) + the close-up registered into the model |
| Decides well | meter and panel reads; clear-margin placement near the meter; gas / pool / facing / headroom away from thresholds | same, when photos overlap and register into one model; otherwise reads plus review |
| Confidence | **medium** for openings near the meter (real phone evidence); low-medium for 15–26 ft (needs A9 scale, untested on phones) | **low**: the only real phone evidence for image-only reconstruction is negative |

**What the guided flow buys over the photo flow:** ARKit priors that stop fragmentation (untested on phones, but it is the one mechanism that can join fragments), a trajectory to catch a bad feed-forward window, taps that anchor the meter exactly, an extra (weak) scale term, and on-device capture guidance (overlap, blur, coverage). **What it doesn't buy:** much scale accuracy (ARKit's non-LiDAR scale is 2–6% off; the meter model dominates). It does make the close-ups register more reliably (A9: keep the session running while walking up to the meter).

## 1. Recommended pipeline: one pipeline, two branches per step

Hunter's rule: **when the phone has LiDAR, use it by default; otherwise fall back to images.** The branch is chosen per capture from the packet (`session.lidarAvailable`, `sceneDepthEnabled`, depth files present) and per region: LiDAR depth only reaches ~5 m, so even on a Pro phone the image branch fills farther walls and ground. The photo flow always runs the no-LiDAR branch unless it is captured in-app on a Pro phone.

| Step | LiDAR branch (default when depth is present) | No-LiDAR branch (fallback) | Evidence: LiDAR / no LiDAR |
|---|---|---|---|
| 0. Ingest | validate schema, SHA-256, tap replay, epochs; depth + confidence files sized correctly | same; photo flow: EXIF focal → intrinsics, gravity / location if attached | capture spec §6 |
| 1. Poses | COLMAP incremental SfM with tight ARKit priors (σ ≈ 3 cm, no robust loss), spatial + sequential matching, one prior group per epoch; fixed-ARKit triangulation where SfM splits | guided: same; photo flow: image-only COLMAP (ARKit priors if any poses exist); unjoined fragments reported as unobserved | A6 (real phone, both); image-only folds on 2 of 5 real segments (A6) |
| 2. Scale | align the SfM model to LiDAR depth (robust ratio of SfM points to high-confidence LiDAR within 5 m); meter make/model scale (A9) as the cross-check | A9: meter make / model → datasheet cover size in two close-ups; ARKit as gate; objects and footprint as checks only | LiDAR: real LiDAR vs laser 1.7 cm median, 5.9–7.8 cm p90 (A6); **alignment itself not yet measured**; no LiDAR: 0.6 / 2.0 / 3.1% (A9, sim.) |
| 3. Dense geometry | fuse high-confidence LiDAR depth on the refined SfM poses within ~5 m; π³ windows beyond | π³ windows on SfM poses (dense fill for coverage, not a measurement source on phones); DA3-Large as a second opinion | sim. LiDAR fused 0.7 / 2.7 in on true poses, 1.9 / 9.8 on un-refined poses (A3); π³ oracle p90 9–15 in on phone frames (A6) |
| 4. Coverage and occlusion | §3.1 rule with LiDAR depth as the seen / hidden test (0.10 m relief per frame, 0.4 m on the fused mesh) | §3.1 rule with predicted depth (Metric3D v2 ViT-S or MoGe-2) as the test | predicted depth matched laser depth at 0.10 m relief: 0.0 ft false-observed (A3); PR #12 LiDAR test 0.1 ft |
| 5. Object detection | OWLv2 (+ optional cheap OpenAI / xAI tier) on every frame → GPT-5.5 + Grok 4.7 confirm on selected frames | same | B1 (real images) |
| 6. Object 3D placement | project boxes (or masks) onto LiDAR depth and cluster across views | guided: taps on gas meter, AC, door / window corners; otherwise **"present, location unsure"** until lifting onto SfM / π³ depth is tested | 4/6 objects, 0 false, 0.35 m with depth as oracle LiDAR; 0/6 under non-LiDAR drift (B1) |
| 7. Close-up reads | GPT-5.5 + Grok 4.7 agreement pair + zxing (meter number, class label, make / model, panel) | same | B2, A9 |
| 8. Criteria | C1 decides (contract mode, C5 margin with the branch's scale bound) ◄ C2 veto ◄ C3 downgrade-only attributes | same, with wider scale bounds and more "location unsure" objects | C1, C2, C3; A9 sweep |
| 9. Output | verdict v0 + `views_needed` | same (photo flow: views_needed only useful if more photos can be requested) | contract |

Flow-specific notes:
- **Guided flow:** taps anchor the meter exactly; ARKit priors prevent fragmentation; two meter close-ups are taken inside the running session so they register.
- **Photo flow:** keep the largest model containing the meter; try π³ across fragment gaps but never merge by guesswork; bridge photos walking up to the meter so the close-ups register; flag π³ / DA3 windows that disagree (there is no ARKit trajectory to check them).

Compute: about 15–25 min CPU per house for A1 sparse on phone-resolution frames (A6), plus π³ / depth / C2 minutes; a GPU is optional. VLM cost is in Section 3.

## 2. Photo flow: ~60 ordinary iPhone photos

### Evidence specific to the photo flow

| Evidence | Imagery | Result | What it says |
|---|---|---|---|
| A1 image-only incremental SfM, ETH3D facade / electro / delivery (76 / 45 / 44 photos) | DSLR | 76/76, 41/45, 44/44 registered; camera RMS 1.7 / 0.3 / 0.4 cm; facade KDs 0.60 / 24.9 in all (tail from the edge-on door), **0.67 / 7.0 in on truly observed elements** | ~60 ordinary photos of a building are enough for SfM when they overlap; shape is at the bar on observed walls |
| A1 GLOMAP image-only | DSLR | wrong poses on 2 of 3 scenes | use incremental, not global |
| A1 / A3 image-only SfM on phone walkarounds (~150 frames at 1.2–1.7 fps, with pans) | **phone** | 33/151, 74/150, 43/148 in the largest model; 3–6 fragments each | the photo flow's main risk; ordinary photos without overlap discipline fragment |
| Capture spec §5.1 phone walks | **phone** (not a breadth report) | 480p walk at 3 fps: 135/141; 1080p at 2.5 fps: 153/183 | overlap, not resolution, decides registration |
| PR #18 reviewer, 7 casual phone photos; Hunter's 3-view probe | **phone** | 5/7 registered, 339 points; 0 points | far below 60, but shows the floor |
| A2 π³ / DA3 on 8-view image-only groups | DSLR | π³ with correct scale 0.6 / 2.9, 0.9 / 2.1, 2.1 / 5.5 in on 1–3 m pairs; DA3 images-only one group p90 36 in | good dense layer; needs a cross-check without ARKit |
| A8 reference measurement inside image-only SfM models | DSLR | 0.1–0.4% on crisp edges; recon-vs-laser local scale −0.27% ± 0.35% | the scale machinery already runs on image-only models |
| A5 World Labs, 8 photos | DSLR + phone | meter erased, 0–2 of 8 inputs register | not a photo-flow shortcut |
| C3 VLM on photos without geometry | **phone** | unsafe placement passes | geometry is still required |
| **A6 image-only SfM on real iPhone-class frames** (87–205 dense frames per 35 m segment) | **phone** (LaMAR) | folded / fragmented on 2 of 5 segments (−46%, −19% scale; 67/99 registered on one); 15–26 ft p90 57 in; openings p90 14 in | the photo flow's biggest measured weakness, even with better overlap than ~60 photos would give |
| A6 π³ / DA3 images-only on real phone frames | **phone** | π³ oracle-scale p90 9–15 in per segment (2.9 in on ETH3D); DA3 images-only failed 1 of 10 windows (+45% scale) | feed-forward is weaker on phone frames than on DSLR |
| A10 (10 → 60 photos) | **PENDING** | | will set the photo-count curve and phone robustness |

## 3. Shared evidence by track

### Track A (non-LiDAR first)

| Path (variant) | Imagery | Criteria distances (median / p90 in) | Scale | Runtime, licence | Gate |
|---|---|---|---|---|---|
| **A1** SfM + ARKit priors, non-LiDAR (guided) | DSLR, sim. ARKit | **0.84 / 2.90** on the meter wall (30 scored, 20 missing) | own ARKit: 2.4% / 5.6% → 7.6 / 17.6 in at 26 ft | ~6 min CPU; BSD-3 | **Go** (guided backbone) |
| **A1 tight ARKit prior (σ 3 cm), real phone frames** (A6) | **phone**, real ARKit, laser | openings 1.3 / 4.6 (points); 15–26 ft 3.8 / 11.3 (ARKit scale), 3.0 / 7.4 (oracle); the ETH3D recipe (σ 0.3 m robust) bent to 76 in p90 on one segment | real ARKit 1.2% / 2.8% session scale | 15–25 min CPU / house | **Go**: recipe change adopted |
| **A1** SfM image-only (photo) | DSLR; **phone (A6)** | DSLR 0.60 / 24.9 all, 0.67 / 7.0 observed; **phone: 4.6 / 57.2 at 15–26 ft, folds on 2 of 5 segments** | none | ~7 min CPU; BSD-3 | **Conditional**: photo backbone only because nothing better exists; weak on phones |
| A1 LiDAR profile (bonus) | DSLR, sim. ARKit | 0.84 / 2.06 | 1.0% / 3.9% | same | Go |
| **A2 π³** (shape; takes the fused scale) | DSLR; **phone (A6)** | DSLR 0.9 / 2.0 observed facade KDs; **phone: 5.4 / 13.4 at 15–26 ft with ARKit fit, oracle p90 9–15 in** | none | 2–3 min CPU; BSD-2 | **Demoted**: dense fill for coverage, not a measurement source, on phones |
| A2 π³ / DA3 with non-LiDAR ARKit scale | DSLR, sim. ARKit | 3.3 / 16.1; 2.2 / 51.7 | −3.7% | – | ARKit scale alone fails |
| **A9 meter exact model → datasheet, two close-ups, ARKit gate, objects as check** | real plate reads (21/21 model families) + DSLR edge precision; meter dimensions modelled; ARKit sim. | – | **0.6 / 2.0 / 3.1%** → 89% of houses within 6 in at 26 ft; 67% of site decisions kept | < 1 s + one field in the B2 call | **Go**: the scale layer |
| A8 meter base only / typed objects fused | DSLR; sizes modelled; real barn | – | base only 0.6 / 7.6 / 9.4% (ring or cover confused for base); objects: real barn −9.7% with a confident bound | – | objects as checks only |
| A8 door, brick, siding, learned depth alone | DSLR; sizes modelled | – | p90 8.5–50%; depth −12.5% to +20.3% | – | weak terms only |
| A3 predicted-depth occlusion | DSLR, true poses | 0.0 ft false-observed at 0.10 m relief | – | BSD-2 / MIT | **Go** (occlusion) |
| A3 fused depth over ARKit poses | DSLR, sim. ARKit | 7.4 / 27.2 and 9.3 / 31.9 | – | – | **Drop** |
| A5 World Labs Marble | DSLR + 1 phone walkaround | 19.4 / 39.1 (facade, declared scale) | off −31% / +12% | $1.28 / world | **Drop** |
| A7 / A10 | **PENDING** | | | | |

### Tracks B and C (identical in both flows)

| Path | Imagery | Key numbers | Gate |
|---|---|---|---|
| **B2 GPT-5.5 + Grok 4.7** meter number | real close-ups, mixed cameras, mostly non-US | agreement on 68/73 (93% coverage), **65/68 correct (96%, Wilson low 87.8%)**; the 3 misses (m38, m61, m66) are both models picking the same wrong identifier; GPT-5.5 alone 95% top-1, Grok 90%; top-3 97–99% | **Go** with one-tap confirm; auto-fill only if B3 shows the pair clears the bar (the dropped Opus pair was 65/65) |
| B2 class label / CL320 | real, 22 US plates | 22/22; **CL320 recall unmeasured** | Go |
| B2 panel brand / amps / hazard | real | GPT-5.5 brand 16/19 (3 wrong), Grok 4.7 12/19 (0 wrong); hazard recall 5/5 both; false alarms GPT-5.5 3 + 1, **Grok 4.7 0** | Go: hazard flagged by either → review; brand only where both agree or printed |
| B2 can type / rust from close-ups; server OCR | real | 73% best 5-way; OCR top-1 59–68% | **Drop** |
| **B1 OWLv2** + **GPT-5.5 / Grok 4.7** | phone walkaround frames, Commons, DSLR | OWLv2 P/R 0.31 / 0.69; VLM 0.48 / 0.61 and 0.50 / 0.54; presence recall ≥ 0.8 all classes; electric meter instance P/R 0.71 / 0.62 (Grok) | **Go** for presence |
| B1 3D lift | DSLR, oracle depth | 4/6 clean; **0/6 under non-LiDAR drift** (photo flow has no drift, but no refined-pose lift has been tested) | unproven: "present, location unsure" |
| **C1** rules in 3D (contract mode) | laser geometry, invented meters | 0 site unsafe passes; 17/17 contract verdicts agree; informed sweep 4 unsafe in 1,104 (none on truth fails) | **Go**: decider |
| **C2** volumetric | laser; synthetic gas / AC | 0 unsafe; 8/10 vs PR #11; 94–100% per position; never run on a reconstruction | **Go**: veto |
| C3 VLM judge (Base prompts) | **real phone walkaround frames** + DSLR | placement passes where labels say review on 3–4 of 6 houses; 11 / 11 / 21 unsafe slots (Sonnet / Opus / GPT-5.5; only GPT-5.5 remains allowed, the least safe of the three) | **Drop as decider**; downgrade-only attributes |

### Per-house VLM cost (list prices, no batching; OpenAI + xAI only; B3 pending)

| Step | Guided flow | Photo flow (~60 photos) | Source |
|---|---|---|---|
| Presence screen on every frame / photo | OWLv2 on CPU, $0; optional cheap tier GPT-5.4-mini ($0.0017 / img) or Grok 4.20 NR (~$0.003–0.004) | OWLv2 $0; cheap tier on all 60 ≈ $0.10–0.25 | B1, B2 |
| Object confirmation on selected frames, GPT-5.5 + Grok 4.7 pair | 5–10 frames × ($0.054 + $0.029) ≈ **$0.42–0.83** | same with frame selection; **all 60 frames ≈ $5.0** (what a gate must avoid) | B1 per-image costs |
| Meter + panel reads, pair (1 meter + 2 panel photos; includes make / model for scale) | GPT-5.5 ≈ $0.094 + Grok 4.7 ≥ $0.023 ≈ **$0.12** (GPT-5.5 alone ≈ $0.09) | same | B2 |
| C3 downgrade-only attributes (GPT-5.5, subset of prompts, photos-first caching) | ≈ $0.2–0.6 | same | C3 ($1.68 for all 13 prompts uncached; caching 3–4× cheaper) |
| **Total VLM per house** | **≈ $0.75–1.65** | **≈ $0.85–1.8 with frame selection; ≈ $5.5–6 without** | our sum |

Notes: Grok 4.7's cost is a lower bound (xAI's reported tokens may exclude hidden reasoning) and its latency is 41–54 s median per image, so the pair runs in parallel and the house takes about a minute of VLM wall time. A tiered order (cheap tier first, Grok 4.7 next, GPT-5.5 only on positives or disagreement) would cut the confirmation line; B3 measures exactly that and the pair's agreement safety, and will replace these figures.

## 4. What accuracy is achievable without a tape

**Answer (A9, simulated with modelled meter terms): about 0.6% median / 2.0% p90 scale error, 89% of houses within 6 in at 26 ft, and two thirds of site decisions still decidable.** The rest go to targeted manual review. This comes from reading the meter's exact make and model (GPT-5.5 named the model family on 21/21 checkable real plates) and using that model's datasheet cover diameter, measured in two close-ups, with ARKit as a gate.

| Zero-effort scale route | Scale error, median / p90 / p95 | Within 6 in at 15 / 26 ft | Site decisions kept (C1 sweep) | Evidence |
|---|---|---|---|---|
| ARKit only (modern, assumed) | 2.4 / 5.7 / 6.9% | – / 40% | 24% | A8 / A9, sim. |
| Generic meter size (model unknown) | 1.7 / 4.9 / 6.9% | 78 / 55% | – | A9, sim. + real edge precision |
| Exact model, public datasheets only (35% of meters) | 1.2 / 3.7 / 4.8% | 86 / 68% | – | A9 |
| Exact model, Base-built dimension table (90%), one close-up | 0.8 / 2.6 / 4.0% | 92 / 82% | 56% | A9 |
| **Exact model, two close-ups, objects as check (recommended)** | **0.6 / 2.0 / 3.1%** | **95 / 89%** | **67%** | A9 |
| + TrueDepth front-camera scan of the meter (near-zero effort; untested outdoors) | – | – | 71% | A9, modelled |
| Typed objects (doors, garage, brick, siding) + ARKit | sim. 0.8 / 3.2 / 5.6%; **real barn: −9.7% with a confident 1.3% bound** | – | – | A9 (real barn) |
| Footprint data (county GIS, Microsoft) | p90 5.5% | – / 44% | – | A9, modelled |
| Passive priors (camera height, stride, learned depth) | p90 5.3% | – | – | A9, modelled |
| Excluded for comparison: tape 10 ft + meter + ARKit | 0.4 / 0.9 / 1.1% | 98 / 97% | 81% | A8 |

What the numbers mean for decisions (A9's C1 sweep on laser geometry with 2 in shape noise, 1,230 runs):
- **Per check, share of decided cases kept at a 2% bound:** meter working space, route length, drive and AC clearance 96–98%; headroom, facing, backing 94–98%; pool and gas clearance 91–94%; opening clearance 92%; under-window 80%; footprint clear 48% (shape-limited).
- **Which sites survive:** the outdoor facade pass (a spot between windows ~30 ft along the wall, profile C) needs a ≤ 1–1.5% bound; facade rejects survive to 2%; near-meter passes survive to 6–8%.
- **Safety:** 0 wrong rejects; 2 unsafe passes in 1,230 runs, both at a 1.5% bound with a true error of +1.5% (the tail).
- **Why objects are only a check:** standard sizes sit ~10% apart, so a ~10% scale error snaps every object to its neighbouring size at once. On the real barn, garage, door and siding agreed on −9.7% and even outvoted a simulated meter. Use objects to flag a conflict (fires on ~2% of houses, which get a meter re-shoot), never to set the scale.
- **Contract:** A9's `scale` block (`source: meter_model`, `model`, `rel_bound_95`, `status`, per-source estimates with check roles); `plus_minus = shape_bound + rel_bound_95 × distance from the meter`. Routing: ≤ 1.5% keeps tight far sites; 2% keeps rejects and most near-meter decisions; 3–6% keeps only near-meter passes; > 12% sends everything to review. Member actions: `retake_meter_closeups` or `truedepth_meter_scan`, never a tape. No reject on 16–26 ft reach rules above a 6% bound; 2% for the under-window and opening checks.

**Caveats (all from A9):** the meter terms are modelled (no scene has a real meter with truth); the 90% table coverage assumes Base calipers ~15 common models (public datasheets cover only Itron CENTRON, 35%); registration of the close-ups, rim depth and contour confusion (13 of 22 real meters have a sealing ring that sits 1.7–5% off the base size) are assumptions to validate; the photo flow's no-ARKit numbers are for one close-up only.

## 5. Criterion-by-criterion matrix: how each check is decided

Covers every criterion (P1–P13 and the placement sub-checks of profiles A / B / C) and every object class Base's SSR prompts mention (from `internal/criteria/SSR_Prompts.md` and `docs/criteria-spec.md`; classes named, prompt text not copied). **Draft from component evidence; E2E and A10 will replace the reliability columns with measured rates.**

**Deciding component**
- **3D** = distance measured in the reconstruction (C1 decides, C2 vetoes); needs coverage (≥ 2 views) and the C5 margin with the scale bound (A9: 1.4% median, ≤ 2% for 77% of houses in the guided flow; wider when close-ups don't register; Section 4).
- **Det+3D** = object detection (B1: OWLv2 proposer + GPT-5.5 / Grok 4.7 confirmation) plus placing the object in 3D, then a 3D distance.
- **Read** = text reading of a close-up (B2: GPT-5.5 + Grok 4.7 agreement, zxing).
- **Photo** = a photo-only judgement by a VLM (B1 presence, or a C3-style attribute), allowed only to move an outcome **toward review**, never to pass.

**Reliability** per branch: **High** = measured on real images, decides most cases. **Medium** = evidence exists but small or indirect; clear cases decided, the rest review. **Low** = weak evidence; expect review. **Untested** = no evidence; review until tested. "same" = LiDAR doesn't change this check.
- **LiDAR (Pro phone, in-app capture):** detections projected onto LiDAR depth, scale aligned to LiDAR depth, LiDAR as the seen / hidden test, LiDAR densifying geometry within its ~5 m range. Evidence: real iPad LiDAR vs laser 1.7 cm median, 5.9–7.8 cm p90 within 5 m (A6); depth lift 4/6 objects, 0 false, 0.35 m 3D error with scan depth as oracle LiDAR (B1); simulated LiDAR fused on true poses 0.7 / 2.7 in, but 1.9 / 9.8 in on un-refined poses (A3), so fuse only on refined SfM poses. Limits: 256 × 192 depth, ~5 m range, edges not sharpened, glass unreliable.
- **No LiDAR, guided:** COLMAP with tight ARKit priors, predicted depth for occlusion, meter-model scale, taps anchor tapped objects, other objects "location unsure" until lift on refined poses is proven.
- **No LiDAR, photo flow:** image-only COLMAP (weak on phones, A6), same scale and object rules, no taps unless captured in-app.

**B1 numbers** are instance precision / recall of the best model at IoU 0.5 (0.3 for thin / extended classes), plus image-level presence recall, on 163 real images with **agent-made labels** (circular: pooled from the models' own boxes, which inflates recall). "n" is the number of labelled instances.

### 5.1 Close-up reads (meter and panel)

| Criterion | Deciding component | Evidence | LiDAR (Pro phone, in-app) | No LiDAR, guided | No LiDAR, photo flow | Gaps |
|---|---|---|---|---|---|---|
| **P4 meter number** (+ make / model for scale) | Read | B2: GPT-5.5 95% / Grok 4.7 90% top-1 (73 real plates); pair agrees on 93%, 96% correct there (shared identifier errors); top-3 97–99%; A9: model family 21/21 (GPT-5.5) | same | **High** with one-tap confirm; auto-fill pending B3 | same | mostly non-US plates; prompt for "list every identifier" untested |
| **P3 CL320** | Read | B2: 22/22 US CL labels, 0 false CL320 claims | same | **Untested for positives** | same | **0 CL320 plates in any dataset: recall unmeasured** |
| P2 oversized can | Photo (surroundings photo; can vs head) | B2 can type from close-ups: best 73% 5-way (Opus, now excluded; GPT-5.5 46%, Grok 4.7 43%); models disagree wildly | same | Low | Low | never tested on surroundings photos; US-only labels needed; 3D can height could help (unbuilt) |
| P11 meter type (can / head ratio) | Photo (single-view ratio per the prompt) | none scored | same | Untested | Untested | ratio definition conflict (#29) |
| P10 meter mount (wall / pole / bank) | Photo | B1: meter bank boxed as one object by Grok; B2 bank class 5 photos | same | Low | Low | pole mounts never tested |
| **P13 recalled brand** (FPE, Challenger, Zinsco / Sylvania / Magnetrip) | Read | B2: hazard recall **5/5** for every model on 127 real panels; brand right 16/19 (GPT-5.5) and 12/19 with 0 wrong (Grok 4.7); false alarms 0 (Grok) to 3 + 1 (GPT-5.5), GPT-5.5's sometimes with invented "printed" text | same | **Medium-high** (flag → review) | same | only 5 real hazard panels; Challenger never seen |
| P13 corrosion / rust | Photo | B2: every model calls rust on 29–87% of photos with none visible | same | **Low** (over-calls) | Low | no reliable signal; route to review only when severe and confirmed |
| P13 breaker orientation (up = OFF) | Photo / Read | none | same | Untested | Untested | never tested |
| P13 Westinghouse, push-button breakers, missing dead-front, loose wiring | Photo / Read | none beyond brand reads | same | Untested | Untested | never tested |
| Main breaker / disconnect amperage | Read | B2: mains 7/9 (GPT-5.5), 6/9 (Grok 4.7); panel ratings 5/5 both; Grok 4.20 invents "200" | same | Medium-high | same | 9 legible examples |
| **P1 multiple mains / P9 main panel type** | Photo + Read | criteria spec only; no scored run | same | Untested | Untested | panel in context n = 2 (B1); closed enclosures conflict (#30) |

### 5.2 Meter / panel relationship and access (P5, P12)

| Criterion | Deciding component | Evidence | LiDAR (Pro phone, in-app) | No LiDAR, guided | No LiDAR, photo flow | Gaps |
|---|---|---|---|---|---|---|
| P12 meter working envelope (~3 ft out × 2.5 ft wide) | 3D + Det | C1 / C2 clearance checks on laser; C3 unreliable | Medium-high (depth measures the envelope and occluders directly) | Medium (envelope is large vs error) | Medium if the meter area registers | objects in the envelope (AC, water heater, bush) rely on detection |
| P12 panel working envelope | 3D + Det (exterior); Photo (interior) | same | Medium-high exterior / Low interior | Medium exterior / Low interior | same | interior panels are outside the exterior reconstruction |
| P12 panel in living space vs garage | Photo (cues: studs, garage door, concrete floor, water heater, car) | none scored | same | Untested | Untested | never tested |
| P12 not same wall | 3D (one session meter → panel) or Photo anchors | none | same | Low (possible if the AR session spans both) | Low | never tested |
| P5 meter–panel relationship (back-to-back, garage return wall) | 3D (single session) + Photo | none | same | Low | Low | exterior-only models can't see inside faces; never tested |
| P12 overhead obstruction < 6 ft at the meter | 3D | C2 headroom (laser) | Medium-high | Medium | Medium | not tested on reconstructions |

### 5.3 Placement geometry (profiles A / B / C)

| Check | Deciding component | Evidence | LiDAR (Pro phone, in-app) | No LiDAR, guided | No LiDAR, photo flow | Gaps |
|---|---|---|---|---|---|---|
| Wall run / usable width near the meter | 3D | **real phone (A6)**: openings 0.9–1.9 / 2.8–4.2 in (guided, tight priors); image-only 1.5 / 14.3; DSLR: A1 0.84 / 2.90; C1 15/15, 10/10 on laser | Medium-high (edges still come from images) | **Medium-high** | Low-medium (image-only unreliable on phones) | no house-scale phone test |
| Wall backing (flush exterior wall, not fence / island / glass) | 3D (C2 backing) | C2 found 97–189 backing / collision fails PR #11 can't see (laser) | Medium-high | Medium | Medium | C2 never run on a reconstruction; placeholders (85%, 0.5 ft) |
| Corners and adjacent walls | 3D | sparse SfM misses edge-on walls (w2, 15 points); corner KDs missing in every good run | Medium (LiDAR fills edge-on walls within ~5 m) | Low-medium (needs head-on views) | Low-medium | whole-house geometry untested |
| Route length (≤ 16 ft pass, 16–26 review, > 26 infeasible) | 3D | C1 Dijkstra routes round corners (laser); A9 sweep: route length kept 96–98% at a 2% bound | Medium-high | Medium-high (clear cases) | Medium if walls are in one model; fragments → review | multi-wall recon untested |
| Wall ends seen (needed for a C reject) | 3D + coverage | C1 / C2 chain-end logic; A1 shows ends go missing, not wrong | Medium | Medium | Low-medium | – |
| Headroom / overhead ≥ 7 ft (eaves, soffits, porch roofs, stairs, landings) | 3D (C2) | C2 headroom fails from sills and pipes (8–77 positions, laser) | Medium-high | Medium | Medium | overhead completeness 14% sparse (A1); not tested on recon |
| Facing clearance ≥ 5 ft (wall → fence / hedge / wall) | 3D (C1 / C2) | C1 / C2 on laser | Medium-high | Medium | Medium | fences as objects untested in 3D |
| Ground slope and step (mild vs extreme; decks fail) | 3D (C2) | C2 measured 5.1–5.8° gravel slope (laser) | Medium (ground strip within ~5 m of the phone) | Low-medium | Low-medium | **ground out to 12–14 ft untested on any reconstruction**; slope threshold is a placeholder |
| Ground type (dirt / lawn / gravel / mulch / concrete / planter bed) | Photo (downgrade only) | B1 ground type: GPT-5.5 0.88, Grok 0.82 (83 images) | same | Medium | Medium | per-class breakdown not reported |
| Openings beside the spot: doors | Det+3D | B1 door instance 0.62 / 0.64 (GDINO, n = 28), presence recall 0.95–1.00; depth lift 4/6 windows / door clean, **0/6 under non-LiDAR drift** | Medium-high (depth lift 4/6, 0 false; 0.52 m under LiDAR-class drift) | Medium (tap door corners) | Low-medium | lift on refined poses untested |
| Openings: windows (presence) | Det | B1 presence recall 0.98 (Grok), precision 0.96 (n = 45 images) | same | High presence | High presence | **no window instance boxes scored** (presence only) |
| Openings: window edges for gaps (B: 3 ft each side, 8 ft between) | 3D with human-quality edges | C1 used PR #13 clicked edges; detector boxes include sills and lintels (vertical error dominates B1's 35 cm); A9 sweep at a 2% bound keeps opening clearance 92%, under-window 80% (most scale-sensitive checks) | Low-medium (256 × 192 depth doesn't sharpen edges: sim. LiDAR edges p90 8 in) | Low-medium (B tight gaps → review) | Low-medium | automatic edge extraction on reconstructions untested |
| Ground-level vs upper-floor window | Photo + 3D sill height | C1 rule "sill ≤ 6 ft" is a placeholder | Low-medium (sill height from depth) | Low | Low | definition unresolved with Base (#32); never tested |
| Operable vs fixed window | Photo | none | same | **Untested** | **Untested** | never tested; exempts fixed windows |
| Window wells | Det / Photo | none | same | **Untested** | **Untested** | never tested |
| Crawl-space openings | Det / Photo | none | same | Untested | Untested | never tested |
| Door / gate swing arc toward the spot | Photo + 3D | none | same | Untested | Untested | never tested |
| Garage door on the route (prohibited) | Det+3D | C2 delivery labelled a garage door from the laser; no detector class | Low | Low | Low | **garage door not a B1 class** |
| Driveway crossing / drivable surface near the spot | Det (presence) + 3D (ground region) + Photo (car in frame proves drivable) | B1 driveway presence P / R 0.64 / 0.70 (Grok), 0.53 / 0.90 (GPT-5.5), n = 10 images | Medium (ground region from depth) | Low-medium | Low-medium | no drivable-region segmentation; car tell untested; C1 facade C verdict hinged on a hand label (gravel) |
| Main entrance / secondary walkway, street-facing front | Photo (+ GPS / map) | none | same | Untested | Untested | never tested |
| Disconnect space (A / B, ~2 ft beside meter) | 3D | C1 / C2 widths | Medium-high | Medium | Medium | – |
| Conduit bend room 6 in (B) | 3D at ≤ 2 in | below achievable accuracy | same | **Cannot decide** | **Cannot decide** | – |
| Meter access at route start (boxed in, under porch / deck / stairs) | 3D + Photo | C2 unobserved-envelope logic | Medium | Low-medium | Low-medium | never tested on houses |

### 5.4 Object classes named in the prompts

| Object class | Where it matters | Deciding component | Evidence (B1 unless noted) | LiDAR (Pro phone, in-app) | No LiDAR, guided | No LiDAR, photo flow | Gaps |
|---|---|---|---|---|---|---|---|
| **Electric meter** | anchor of every distance; P2–P4, P10–P12 | guided: tap; photo: Det + close-up registration | instance 0.71 / 0.62 (Grok), 0.33 / 0.38 (GPT-5.5), n = 8 in context; presence recall 0.83–0.98 | **High** (tap, or detection lifted onto depth) | **High** (tap) | Medium-low | only 8 in-context meters; small at house scale; auto-location never tested end to end |
| Meter can / enclosure / cover | P2, P11, **the scale anchor** (cover contour, A9) | Det + Photo + contour fit | instance 1.00 / 1.00 (Grok), n = 6; A9: 13 of 22 real meters have a sealing ring that sits 1.7–5% off the base | same | Medium | Medium | n = 6; contour labelling (cover vs ring vs base) is the scale tail |
| Meter bank | P10, P4 unclean | Photo | Grok boxes a 17-meter bank as one object | same | Low | Low | – |
| **Gas meter** | gas clearance (3 / 5 ft), P12 | Det+3D | instance 0.72 / 0.79 (OWLv2), 0.62 / 0.62 (GPT-5.5), 0.29 / 0.31 (Grok); n = 29 but **17 are one meter bank**; no confusion with electric meters except Florence | Medium-high (tap or depth lift) | Medium (tap it) | Low-medium (lift unproven) | effective n ≈ 13 images; C3 misjudged a 1.5 ft gap as 5+ ft |
| Gas regulator, shutoff valve, gas pipe run (C: ~6 ft of wall) | gas clearance | Det+3D | none | same | **Untested** | **Untested** | never a class; pipe run extent never measured |
| **AC condenser / HVAC** | fixed footprint; P12 obstruction; harness may pass behind | Det+3D | instance 0.83 / 0.91 (Grok), 0.55 / 1.00 (OWLv2), n = 11 | Medium-high | Medium | Medium-low | n = 11; synthetic AC in C2 only |
| **Water heater** (exterior) | P12 obstruction; garage cue | Det / Photo | none | same | **Untested** | **Untested** | never a class |
| **Main panel** (exterior, in context) | P1, P9, P12, P13 | Det + Read | instance 0.50 / 1.00 (Grok), n = **2** | same | Low (in context) | Low | only 2 in-context panels; no US main panel in context on Commons |
| Main disconnect | P1 / P9, P13 | Read | amps via B2 only | same | Medium | Medium | not a detection class |
| Wall-mounted electrical / comms / cable box | never place directly under (C); movable if loose | Det+3D | none | same | **Untested** | **Untested** | never a class |
| **Vents** (dryer / exhaust) | don't place directly under (C) | Det+3D | none (B1 notes vents as false-positive confusers only) | same | **Untested** | **Untested** | never a class |
| **Downspouts / water spouts** | non-blocking (B / C); harness may pass | Det | instance 0.43 / 0.55 (Grok), 0.32 / 0.64 (GPT-5.5), n = 11; OWLv2 0.03 / 0.18 | same | Low | Low | **weakest detected class**; thin objects miss IoU even at 0.3 |
| **Hose bibs** | non-blocking | Det | instance 0.50 / 0.67 (Grok), n = **3** | same | Low | Low | n = 3 |
| PVC pipes, conduit, small pipes | non-blocking; harness may pass over | Det / 3D | none (C2 headroom caught pipes as protrusions) | same | Untested | Untested | never a class |
| **Windows** (all; ground-level / upper; operable / fixed) | openings rule, route | Det (presence) + 3D edges | presence recall 0.98, precision 0.96 (Grok, n = 45); 3D lift 4/6 clean, 0/6 non-LiDAR drift | Medium-high (presence + depth lift) | Medium | Low-medium | see 5.3: level and operability untested |
| Window wells | openings rule | Det / Photo | none | same | **Untested** | **Untested** | never a class |
| **Doors** (man doors, shed doors) | openings, route (over-door review under C; prohibited under B) | Det+3D | instance 0.62 / 0.64 (GDINO), presence 0.95 (Grok, n = 28–30) | Medium-high | Medium | Low-medium | swing direction untested |
| **Garage / garage door** | route prohibited; P5 garage wall; P12 garage cues | Det+3D + Photo | none as a class | same | **Untested** | **Untested** | never a class |
| **Fences** (whole parallel run fixed; short section / gate movable under C) | facing clearance; backing (fence-only fails); route post rules | Det (presence) + 3D | presence P / R 0.88 / 1.00 (Grok, n = 22 images) | Medium (geometry within range) | Medium presence / Low geometry | same | no instance boxes; fence vs wall backing only in C2 on laser (mesh cage) |
| Gates | movable (C); swing arc | Photo | none | same | Untested | Untested | never a class |
| **Bushes / overgrown vegetation / trees** | movable for placement (B / C) but a **fixed P12 obstruction** (conflict #12) | Det / Photo | none | same | **Untested** | **Untested** | never a class; policy conflict unresolved |
| Planter beds, mulch | allowed ground (C planter bed) | Photo (ground type) | ground type 0.82–0.88 overall | same | Medium | Medium | per-class not reported |
| **Pool** (permanent) | 5 ft (A / B) / 10 ft (C) clearance | Det+3D | instance 1.00 / 1.00 (GPT-5.5), n = **4** | Medium (detection n = 4 is the limit) | Low-medium | Low-medium | n = 4; ground out to ~12 ft untested |
| Pool tells (pump, filter, heater, PVC cluster, floaties, noodles, towels) | C: tells → review | Photo | none | same | **Untested** | **Untested** | never tested |
| Kiddie pools | exempt (B / C) | Photo | none | same | Untested | Untested | – |
| **Driveway / drivable surface** (concrete, gravel, pavers, garage apron; car proves drivable) | drive clearance; no driveway crossing | Det (presence) + 3D ground region + Photo | presence 0.64 / 0.70 (Grok), 0.53 / 0.90 (GPT-5.5), n = 10 images | Medium | Low-medium | Low-medium | no segmentation; car tell untested |
| Walkways, entry steps | not automatically drivable (B); main entrance clear (C) | Photo + 3D | none | same | Untested | Untested | never tested |
| Porch, deck, stairs, landing, crawl space | headroom fail; meter access | 3D (C2) + Photo | C2 geometry on laser only | Medium | Low-medium | Low-medium | never tested on houses |
| Eaves, soffits, low overhangs | headroom | 3D (C2) | C2 on laser | Medium-high | Medium | Medium | overhead completeness low in sparse recon |
| Columns | break the wall (A) | 3D | C2 collision (laser) | Medium-high | Medium | Medium | – |
| Sheds | shed wall allowed as backing (C) | Det / Photo | none | same | Untested | Untested | never a class |
| Bins, boxes, furniture, grills, play equipment, hoses | movable (`requires_member_action`) | Det / Photo | VLMs rejected wheelie bins as confusers | same | Low | Low | movable-vs-fixed never scored |
| Solar / transfer equipment | not the meter can (P2) | Photo | none | same | Untested | Untested | – |
| Utility pole (pole-mounted meter) | P10; B allows if conduit reaches a wall | Photo | none | same | Untested | Untested | – |
| Neighbouring structures, street | street-facing review (C) | Photo + GPS / map | none | same | Untested | Untested | – |

### 5.5 Weakest classes and criteria (flagged)

1. **Never tested at all:** CL320 positives; water heater; vents; wall-mounted electrical / comms / cable boxes; gas regulator, shutoff and pipe run; window wells; operable vs fixed and ground-level vs upper-floor windows; garage doors; bushes / vegetation (also a policy conflict); pool tells and kiddie pools; walkways and street-facing; door / gate swing; breaker orientation; panel in living space vs garage (P12); P5 relationship; P1 / P9 mains; P11 ratio; pole mounts; sheds; movable-vs-fixed classification.
2. **Too few examples to trust:** main panel in context (n = 2), hose bibs (3), pools (4), meter cans (6), electric meters in context (8), AC (11), downspouts (11), gas meters (29 instances but 17 in one bank), driveway (10 images, presence only), real hazard panels (5).
3. **Tested and weak:** downspouts (best 0.43 / 0.55); rust / corrosion (over-called by every model); meter can type from close-ups (no-go); driveway presence precision (0.53–0.64); meter bank (boxed as one); **any object's 3D placement without LiDAR (0/6 under non-LiDAR drift; refined-pose lift untested)**.
4. **Geometry weak spots:** ground out to 12–14 ft (pool, drive), corners and adjacent walls, overheads, and tight B-profile opening gaps; the 6 in conduit rule is undecidable.

**What decides well today:** meter number, class label, recalled-brand hazard flags, printed amperage (reads); window, door, fence, AC and gas-meter presence (detection); wall width, backing, headroom and facing away from their thresholds near the meter (3D, on laser, pending E2E on reconstructions).

## 6. Paths to drop, and why

| Dropped | Why |
|---|---|
| Homeowner tape (A8's primary) | no-tool constraint (it was the only tested route to ±1% at p90) |
| Fixed ARKit poses | 12.1 / 28.1 in on non-LiDAR; always re-bundle-adjust |
| ARKit, one known object, or learned depth as the only scale | ARKit 2.4–7.1% median; object tails p90 7.6–50%; learned depth ±20% |
| GLOMAP; OpenMVS dense; MapAnything | folded poses; breaks extraction and AGPL; metric scale off 16–36% |
| VGGT, MASt3R, DA3-Giant, UniDepth, Pi3X | non-commercial |
| Depth Pro, MoGe-3 | need a GPU (park) |
| TSDF over un-refined ARKit poses; triangulated detections | 7–9 / 27–32 in; 0/6 under drift |
| World Labs Marble as geometry (A5) | scale off −31% / +12%; deletes the meter; invents openings in observed views; at most a labelled preview |
| Runway GWM-1 | no 3D output |
| Florence-2, GDINO for equipment, mini boxes, server OCR as reader, can / rust from close-ups | measured failures |
| C3 as placement decider (either flow) | unsafe passes on real phone frames; unstable |

## 7. Cross-cutting findings

### Scale without LiDAR or a tape

- No zero-effort source meets ±1% at p90; A9's meter-model route gets 0.6% median / 2.0% p90. Scale error grows with distance from the reference, and placement distances are measured from the meter, so the meter is the right anchor.
- Carry the uncertainty: A9's `scale` block (Section 4); `plus_minus = shape_bound + rel_bound_95 × distance from the meter`. `verified` needs an exact-model meter from a registered close-up with ARKit and every confident check agreeing; size-based references never produce `verified`.
- Member actions need no tool: `retake_meter_closeups` when the meter was unregistered, unknown or in conflict, otherwise `truedepth_meter_scan` (flip the phone for 2 s; untested outdoors); otherwise targeted review of only the checks the scale bound flipped (`unsure_cause: margin`, `bound_breakdown {shape_in, scale_in}`).
- The close-up must register into the reconstruction (share features with a wider photo), or its scale can't transfer: include a mid-distance meter photo that bridges the close-up and the wall views.

### Coverage and viewing angle

- Every backend loses edge-on walls (78°) and distant ground (81–84°); good A1 / A2 runs go missing rather than wrong (0 false-observed key distances).
- The ground rule (angle vs pixel footprint) alone flips the facade profile-C verdict (Base's call).
- §3.1 needs each region in ≥ 2 photos at a usable angle. With ~60 photos that is achievable if the photos are taken as an overlapping walk rather than scattered shots.
- Every 3D measurement carries coverage and view angle (C3's laser tool passed on an unseen wall). Predicted-depth occlusion works without LiDAR.

### Capture requirements for the iOS team

**Both flows (A9):** **two meter close-ups** from ~3 ft, one head-on and one ~30° to the side, whole cover, ring and socket face in frame filling about half of it, plate legible (reads + scale); taken while walking up from the wall view so they register into the model (guided: inside the running ARKit session; photo flow: a few bridge photos walking in); any entry or garage door on the meter side head-on at least once (conflict check); panel closed and open; main disconnect; passive logging of raw 100 Hz CoreMotion, gravity and planes where an app is involved; optional TrueDepth meter scan when prompted.

**Guided flow:** every ARKit keyframe with its own pose, intrinsics, epoch and tracking state (including `limited` and pre-reset); ≥ 2 keyframes/s, ~60% overlap, 2–4 m from and parallel to the wall, no pans; head-on views of every wall within ~26 ft and to each wall's natural end; downward ground shots out to ~12 ft and the ~7 ft overhead; on-screen taps on the meter, gas meter and AC; blur rejection; on Pro phones, raw LiDAR depth + confidence.

**Photo flow (~60 photos):** shoot as a walk, not a collection: one photo every step or two (~0.5–1 m) along the meter wall and round the nearest corners, each overlapping the previous by about half, phone upright, wall filling most of the frame, 2–4 m back, no sky or lawn-only shots; continue to each wall's end or ~26 ft from the meter; a few downward ground photos in front of the wall; the close-ups at the end. Attach EXIF, gravity and location; attach ARKit poses if the app has any. If the app guides the shots, an on-screen tap on the meter in one photo removes the detection step.

## 8. Open risks and biggest unknowns

1. **(Top) Mostly DSLR evidence; no real non-LiDAR iPhone capture of a real house in either flow.** A6 added the first real iPhone-class frames with real ARKit and laser truth (street facades, LiDAR-class devices, no-depth sessions as the non-LiDAR proxy): the guided recipe holds on openings, and scale is the main long-range error. Tracks B / C, A8 / A9 and all criteria verdicts are still DSLR / laser. A7 pending. **Next deliverable that matters most: one real house captured both ways on a non-Pro iPhone, with team-measured check distances.**
2. **Photo flow on phone imagery.** Image-only SfM folded or fragmented on 2 of 5 real phone segments (A6) and on every YouTube walkaround, even though those had denser frames than ~60 photos; π³ / DA3 are weaker on phone frames than on DSLR. Without ARKit, pieces can't be joined. **Mitigation to decide now:** run the photo flow inside the app so every photo carries its ARKit pose (A6: fixed ARKit triangulation still covered all frames where SfM split). A10 pending.
3a. **The LiDAR branch is mostly simulated.** Real iPad LiDAR matched laser to 1.7 cm median (A6), but LiDAR-to-SfM scale alignment, LiDAR fusion on real refined poses, and object lifting onto real LiDAR depth are untested (B1 used laser depth as an oracle; A3 rendered LiDAR from the laser). Outdoor range and sunlight effects are unmeasured.
3. **No end-to-end success rate yet** (E2E pending).
4. **Zero-effort scale rests on modelled meter terms.** A9's 2.0% p90 assumes a Base-built meter dimension table (public datasheets cover 35%), a close-up registration rate, rim-depth and contour-confusion rates, and the assumed modern ARKit profile; no scene contains a real meter with truth. Typed objects failed on the one real US building (barn, −9.7% with a confident bound).
5. **Review rate is simulated only:** A9's C1 sweep keeps 67% of site decisions at the recommended bound (on laser geometry with invented meters); no reconstruction-based rate yet (E2E pending).
6. **Whole-house geometry, the A1 + π³ combination, and ground out to 12–14 ft are untested.**
7. **Object placement off LiDAR is unproven** (0/6 under drift; no refined-pose lift tested).
8. **Thin, partly circular labels**; rare classes n ≤ 8; invented meters; **CL320 unmeasured**; criteria labels are drafts.
9. **C2 never run on a reconstruction.**
10. **Providers:** with Anthropic excluded, the read pair is GPT-5.5 + Grok 4.7, whose errors are correlated on "which identifier" (3 shared misses in 73) and whose auto-fill safety is unproven until B3; Grok 4.7 is slow (41 s median, 78 s p90) and its cost is a lower bound (hidden reasoning tokens). Gemini and SAM 3 untested.
11. **Harness issues:** extractor breaks on dense clouds and masks < 1.5% scale error on sparse ones; centre-only alignment degeneracy; visibility crash on A3 meshes; `score-cloud` crash on A1 dense fixed; `validate` `KeyError: 'views_min'`; `gt-laser` facade misses windows.
12. **Traceability:** C1's `label-scores.json` holds only the 12 native-rule rows (1 unsafe pass under the native rule, as reported); the 17/17 contract tally isn't persisted; A8's scorer table is a placeholder; some B2 tables are VM-only.
13. **Licences / privacy:** ETH3D and the extra panel set are non-commercial; YouTube frames internal-only; A5's worlds (one frame shows a street number) sit on World Labs under free-account terms; customer photos would go to OpenAI and xAI.
14. **Compute:** no breadth path had a GPU.

## 9. Context dumps to write

The drafts in `internal/context-dumps/` predate the final reports; the full dumps come from the breadth agents, who hold the VM state. Suggested location: `internal/context-dumps/full/<path>.md` (the drafts belong to other agents); each reads its draft first and supersedes it.

**Every dump covers:** fresh-VM rebuild (commands, versions, patches, env var and key names without values); entry points with working examples; VM-only artefacts and how to regenerate them; hardcoded paths; results tagged DSLR / phone and real / simulated-ARKit / modelled / synthetic with source files; known bugs; open questions; ordered next steps; scene v0 / verdict v0 integration; **and what changes in each flow (guided, ~60 photos) on a non-LiDAR phone with no tape**.

| Path | Agent | Role | Must also cover |
|---|---|---|---|
| A1 classical | bc-daa6b400-6fd7-514e-837d-95d1771fa424 | pose backbone in **both** flows | ARKit-prior settings; spatial vs exhaustive matching; image-only mode and fragment handling; multi-seed non-LiDAR runs; swapping in real ARKit noise; dense-extractor diagnosis |
| A2 feed-forward | bc-901df09a-a67a-5f2b-b517-cda7466f7eab | dense layer (both flows) | adapters and checkpoints; aspect-matched input; windows on SfM poses; applying external scale; rotation-aware alignment patch; π³ vs DA3 disagreement check; bridging fragments |
| A3 depth fusion | bc-4da717fb-7eba-55dd-869e-6335aa978667 | occlusion layer (no-LiDAR branch) and **LiDAR fusion + LiDAR occlusion (LiDAR branch)** | coverage-rule port; relief results; facade `tri` failure; Metric3D weights licence |
| A8 scale | bc-3d8b8a80-8af0-5c5e-85bb-bebf6e1707f2 | reference measurement primitives | edge snapping; track-based plane fit; combiner; the modelled terms A9 reuses; scorer table |
| A9 no-tape scale | bc-aba4e2e6-a6ee-581e-8139-624901e05808 | **the scale layer** | meter make/model prompt; contour labelling and two-view rim triangulation; the mixture fusion and check-only policy; the barn failure; C1 scale sweep and routing thresholds; the `scale` block; the meter dimension table plan; TrueDepth device test |
| A6 iPhone quality | bc-523191a1-af98-5e0a-a32c-f2416a3650b1 | real-phone test bed (LaMAR) | LaMAR setup and laser z-buffering; the alignment-free pair scorer and its ~5 in floor; the σ 3 cm recipe and fallback; real ARKit scale measurements; adding a KD-level scorer; reusing LaMAR for the scale and photo-flow tests |
| B1 detection | bc-e80101e6-ab23-59e9-98b8-16d83f085d53 | presence, meter finding without a tap, 3D lift | OWLv2 threshold; VLM prompts; depth lift; meter localisation for the photo flow; proposing scale references; label provenance |
| B2 equipment reading | bc-edf41cf7-deb1-547c-8cf8-b1de02deffa1 | reads (+ make/model for scale) | prompts; agreement rule; identifier failure; make/model extraction; missing tables; CL320 leads |
| C1 rules 3D | bc-c58db527-2f93-56d2-af32-ef71a2ea3ed0 | decider | contract vs native mode; `profiles.yaml` calls; persisted 17/17 scoring; `query_region`; Track A input; sweeping a 2.5 / 6 / 9% proportional bound |
| C2 volumetric | bc-40f7af01-969a-5377-9507-a40fb8d9ab1d | veto | grid from a reconstruction's rays; placeholders; the C1 join; frontier policy |
| C3 VLM baseline (short) | bc-45fa4069-e277-509c-9604-862dba534a93 | downgrade-only attributes; photo baseline to beat | attribute prompts to keep; low-res phone failures; never copying Base's prompt text |
| A5 world models | bc-a281bf9c-5fb4-5e98-81c6-eacff867457a | dropped | no full dump; report plus world ids suffice |
| E2E, A10, A7, B3 | **PENDING** | decided when the reports land | |

Also recommended: the **eval-harness owner** documents the harness fixes in Section 8 item 11.

## 10. Decisions only Hunter or Base can make

**Base**
1. Profile per utility (B = ComEd; C elsewhere? A retired?), one threshold set, reach unit.
2. **Acceptable manual-review rate per flow**: A9 simulates ~33% of site decisions going to review without a tape (19% with one).
3. Ground coverage rule: angle vs pixel footprint.
4. Headroom and slope numbers; "ground-level window" meaning.
5. Whether the 16 ft route review trigger relaxes for measured capture.
6. Site combination: P12 `yes` vs passing placement; role of CL320 / oversized can / pole mount; whether unobserved space within reach blocks a reject; bushes.
7. Model-read policy: auto-fill bar, hazard = review only, missing brand = review.
8. Whether customer photos may go to OpenAI and xAI.
9. Data only Base has: real SSR packets (photo-flow data), few-shot reference images, CL320 plates, install outcomes, installer-measured distances.
10. **Build the meter dimension table:** calipers on ~15 common meter models (cover diameter, rim offset, ring, base), raising datasheet coverage from 35% to ~90%; Base's field teams are best placed to do it.

**Hunter**
1. Which flow is the app's default, and whether the photo flow can run inside the app (so it can attach ARKit poses, gravity and a meter tap even when the user just takes photos).
2. Commit the iOS team to the capture requirements per flow (Section 7), especially the two meter close-ups taken while walking up to the meter, the walk-style photo sequence, and passive IMU logging; and approve a TrueDepth outdoor device test.
3. Schedule the first real house captured both ways on a non-Pro iPhone, with team-measured check distances.
4. Whether a retake request is acceptable as the member action, or all flipped cases go to review.
5. Spend and access: Vertex for Gemini (optional third opinion), HF token for SAM 3, Modal / GPU budget. No World Labs purchase needed; decide whether to delete the four private worlds.
6. Licence stance: is commercial-safe required now?
7. Approve or correct the draft criteria labels (C3 flagged two likely errors on `yt-12Ks1idWj40`).
8. Where the server code lives; depth-phase budget.

## Where this corrects the earlier drafts

- Track A draft kept OpenMVS and had no A2 / A3 numbers: OpenMVS dropped; π³ is the dense layer; A3 narrowed to occlusion.
- Track B draft: triangulation "1/6 at 0.61 m", no depth lift. Final B1: triangulation 3/6 at 0.59 m; depth lift 4/6 clean, **0/6 under non-LiDAR drift**.
- Track C draft: C2 delivery agreement 0.24; final 0.52. The 5° vs 10% slope placeholder mismatch between engines still stands.
- Earlier drafts ranked on LiDAR numbers, assumed a scale reference and a single capture flow; this version ranks on non-LiDAR, no tape, two flows (guided ARKit and ~60 photos).
