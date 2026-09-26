---
cursor:
  subagentId: "bc-e80101e6-ab23-59e9-98b8-16d83f085d53"
---

# B1: finding the criteria objects (detection)

All results are on **real** images. Labels were made by an AI agent, not a human (same caveat as PR #16). Code, labels and raw outputs: `internal/breadth/b1-detection/` (see the last section).

## 1. Verdict

| Approach | Verdict |
|---|---|
| **VLM via API: `grok-4.7` / `gpt-5.5`** | **Go** as the primary finder for criteria objects (presence plus rough boxes). Best instance micro P/R ≈ 0.5 / 0.55–0.6; presence recall ≥ 0.8 on every class. |
| **OWLv2 base (CPU)** | **Go** as a cheap local proposer: best open-vocabulary detector (micro P/R 0.31 / 0.69; gas meter 0.72 / 0.79; AC 0.55 / 1.00), 2–3 s/image on CPU. Needs a verifier for precision. |
| Grounding DINO tiny (CPU) | Go only for doors/windows (door P/R 0.62 / 0.64); **no-go** for utility equipment (gas meter recall 0, electric meter 0.12). |
| `gpt-5.4-mini` | Go for image-level presence only (cheap: $0.002/image, presence as good as big VLMs on common classes); **no-go** for boxes (instance micro P/R 0.13 / 0.14). |
| Florence-2 base | **No-go**: never abstains (returns a box for every prompt: pool on meter close-ups, gas on the water meter), micro precision 0.06. |
| Claude (`claude-sonnet-5`), Gemini, SAM 3, MM-GDINO, GDINO-base, SAM 2 | **Not tested**: Anthropic key has no credit (HTTP 400, rechecked 17:40 UTC), Vertex Gemini 403 (API disabled, not ours to enable), SAM 3 gated (no HF token), MM-GDINO transformers port broken, GDINO-base and SAM 2 not run in time. |
| 3D lift, depth-based (box → depth map → 3D cluster) | **Go**, conditional on per-frame depth (LiDAR/dense recon) and good poses: 4/6 GT objects, 0 false, 25 cm horizontal / 35 cm 3D median error with clean poses. |
| 3D lift, depth-free triangulation of box centres | **No-go** at current detector recall: 3/6, 59 cm error clean; ~0–1/6 under any ARKit drift. |

## 2. Inputs (all real) and outputs

- **Evaluation set** (`eval_images_batch1.json`, frozen): 163 labelled images.
  - 48 house frames from `internal/data/houses/` (3 YouTube walkarounds, Base-install time-lapse, ETH3D facade + electro). `tt-barn` (7 frames) **dropped**: it is a barn.
  - 35 Wikimedia Commons photos picked for rare classes (gas meters, AC, taps, pools, downspouts, fences, driveways, meters, a water meter hard negative). 1 (`c034`) lost to Commons rate limit.
  - 80 PR #16 meter close-ups (image-level truth only: electric meter present, gas meter absent). Optimistic, mostly non-US.
- **Labels** (`labels/labels.jsonl`): boxes for electric meter, meter can, panel, gas meter, AC, door, downspout, hose bib, pool (104 instances total: door 30, gas 29 of which 17 are one meter bank, AC 11, downspout 11, electric meter 8, can 6, pool 4, hose bib 3, panel 2). Window, fence, driveway are **presence-only** on general photos (too many/extended to box by eye); window instances are scored in 3D on ETH3D instead. Plus ground type per image.
- **3D GT**: ETH3D facade laser scan (3 scans, aligned) + PR #13's hand-clicked window/door extremes, back-projected via z-buffered scan depth → 3D centres of 5 ground-floor windows (w1) and the entrance door (w3) (`results/eth3d_gt.json`).
- **Produced**: detector/VLM outputs on the eval set (`results/dets/`, `results/vlm/`), window/door detections on all 76 facade frames (`results/facade76/`), metrics (`results/metrics.{json,md}`), 3D lift results (`results/lift3d_*.json`, `results/sweep_triangulate_gdino-tiny.json`).

## 3. Metrics against ground truth

Instance matching at pre-registered IoU 0.5 (0.3 for downspout/fence/driveway/pool), default thresholds pre-registered (GDINO 0.35, OWLv2 0.2, VLM: every box they return). Cells are **precision / recall**. Florence-2 covers 72 of the images (run cut at wrap-up); `gpt-5.5` and `grok-4.7` were run on house + Commons images only (83) to cap spend on prod keys.

**Instance P/R, houses + Commons + meters (meters contribute no instances)**

| class | n | GDINO-tiny | OWLv2 | Florence-2 | gpt-5.4-mini | gpt-5.5 | grok-4.7 |
|---|---|---|---|---|---|---|---|
| electric_meter | 8 | 0.08 / 0.12 | 0.12 / 0.25 | 0.02 / 0.25 | 0.00 / 0.00 | 0.33 / 0.38 | **0.71 / 0.62** |
| meter_can | 6 | 0.04 / 0.17 | 0.21 / 0.83 | 0.04 / 0.67 | 0.00 / 0.00 | 0.71 / 0.83 | **1.00 / 1.00** |
| main_panel | 2 | 0.08 / 1.00 | 0.00 / 0.00 | 0.02 / 0.50 | 0.00 / 0.00 | 0.40 / 1.00 | 0.50 / 1.00 |
| gas_meter | 29 | – / 0.00 | **0.72 / 0.79** | 0.12 / 0.33 | 0.20 / 0.03 | 0.62 / 0.62 | 0.29 / 0.31 |
| ac_condenser | 11 | 0.25 / 0.64 | 0.55 / 1.00 | 0.10 / 0.45 | 0.40 / 0.36 | 0.50 / 0.64 | **0.83 / 0.91** |
| door | 28 | **0.62 / 0.64** | 0.45 / 0.79 | 0.27 / 0.89 | 0.09 / 0.11 | 0.41 / 0.50 | 0.45 / 0.46 |
| downspout | 11 | 0.22 / 0.18 | 0.03 / 0.18 | 0.00 / 0.00 | 0.40 / 0.18 | 0.32 / 0.64 | **0.43 / 0.55** |
| hose_bib | 3 | 0.08 / 0.33 | 0.33 / 0.67 | 0.00 / – | 0.25 / 0.33 | 0.33 / 0.67 | 0.50 / 0.67 |
| pool | 4 | 0.19 / 0.75 | 1.00 / 0.75 | 0.00 / – | 1.00 / 0.75 | **1.00 / 1.00** | 0.67 / 0.50 |
| **micro** | 102 | 0.22 / 0.34 | 0.31 / 0.69 | 0.06 / 0.47 | 0.13 / 0.14 | 0.48 / 0.61 | 0.50 / 0.54 |

**Image-level presence P/R (houses + Commons; n = positive images)**

| class | n | GDINO-tiny | OWLv2 | gpt-5.4-mini | gpt-5.5 | grok-4.7 |
|---|---|---|---|---|---|---|
| electric_meter | 6 | 0.81 / 0.46* | 0.94 / 0.95* | 0.93 / 0.98* | 0.83 / 0.83 | 1.00 / 0.83 |
| meter_can | 4 | 0.04 / 0.25 | 0.36 / 1.00 | 0.33 / 1.00 | 0.80 / 1.00 | 1.00 / 1.00 |
| main_panel | 2 | 0.10 / 1.00 | 0.00 / 0.00 | 1.00 / 1.00 | 0.50 / 1.00 | 0.67 / 1.00 |
| gas_meter | 13 | – / 0.00 | 0.82 / 0.69 | 1.00 / 0.38 | 1.00 / 1.00 | 1.00 / 1.00 |
| ac_condenser | 9 | 0.26 / 0.78 | 0.64 / 1.00 | 1.00 / 1.00 | 0.75 / 1.00 | 0.90 / 1.00 |
| window | 45 | 0.78 / 0.87 | 0.91 / 0.93 | 0.87 / 1.00 | 0.88 / 1.00 | 0.96 / 0.98 |
| door | 19 | 0.69 / 0.95 | 0.78 / 0.95 | 0.72 / 0.95 | 0.83 / 1.00 | 0.95 / 0.95 |
| downspout | 11 | 0.33 / 0.27 | 0.23 / 0.64 | 0.57 / 0.36 | 0.61 / 1.00 | 0.62 / 0.73 |
| hose_bib | 3 | 0.08 / 0.33 | 0.33 / 0.67 | 0.33 / 0.67 | 0.60 / 1.00 | 0.75 / 1.00 |
| fence | 22 | 0.59 / 0.86 | 0.74 / 0.91 | 0.76 / 1.00 | 0.76 / 1.00 | 0.88 / 1.00 |
| pool | 4 | 0.19 / 0.75 | 1.00 / 0.75 | 1.00 / 0.75 | 1.00 / 1.00 | 1.00 / 0.75 |
| driveway | 10 | 0.22 / 0.70 | 0.32 / 0.90 | 0.56 / 0.90 | 0.53 / 0.90 | 0.64 / 0.70 |

\* includes the 77 scored PR #16 meter close-ups for these three models: electric meter presence recall on meters alone is GDINO 0.47, OWLv2 0.96, mini 1.00.

- **Ground type** (VLMs only, 83 images): `gpt-5.5` 0.88, `grok-4.7` 0.82, `gpt-5.4-mini` 0.77.
- **Cross-class confusions**: gas meter said on electric-meter images: OWLv2 2/82, all others 0 except Florence 5/5. Water meter (c022) called gas meter: only Florence.
- Best-F1 (thresholds tuned on test, optimistic) in `results/metrics.md`: OWLv2 reaches gas F1 0.79, AC 0.90, can 0.91 at higher thresholds (~0.3–0.5), i.e. OWLv2's default 0.2 is too low.
- Small n: panel (2), hose bib (3), pool (4), can (6), electric meter in context (8). Treat those rows as indicative only. No main panel in house context exists on Commons.

### 3D lift (ETH3D facade, real laser-scan GT; 76 frames)

ROI: ground floor of walls w1–w3, where PR #13's labels are complete (6 GT objects). A hit is a same-class 3D object within 0.8 m of the GT centre. Detector: GDINO-tiny at 0.35 (window/door) unless noted.

| lift | poses | recovered / 6 | false 3D | median 3D err | median horizontal err |
|---|---|---|---|---|---|
| depth (scan depth as oracle LiDAR) | clean | **4** | **0** | 0.35 m | **0.25 m** |
| triangulation (no depth) | clean | 3 | 0 | 0.59 m | 0.53 m |
| depth, gpt-5.4-mini boxes | clean | 1 | 4 | 0.68 m | 0.65 m |
| triangulation, gpt-5.4-mini boxes | clean | 0 | 2 | – | – |
| depth | ARKit `lidar_modern` drift (20 seeds) | 4 (median) | 1.5 | 0.52 m | – |
| triangulation | `lidar_modern` | 1 | 2 | 0.57 m | – |
| depth | `nonlidar_modern` | 0 | 4 | (0.66 m when hit) | – |
| triangulation | `nonlidar_modern` | 0 | 2.5 | – | – |
| depth / triangulation | `nonlidar_2018` | 0 / 0 | 1 / 1 | – | – |

- The bottleneck is **per-frame detector recall**, not geometry: each GT window is in frame in only 7–8 of 76 frames and GDINO covers it in 0–4 of those (29% of view-hits). Triangulation needs ≥3 agreeing views; depth lifting needs 2. A triangulation parameter sweep (`sweep_triangulate_gdino-tiny.json`, tuned on this scene) never beats 3/6.
- Vertical error dominates the 35 cm: detector window boxes include lintels/sills while PR #13 clicks the opening.
- Under non-LiDAR ARKit drift both lifts collapse at the 0.8 m bar; with `lidar_modern` drift the depth lift keeps 4/6 at ~0.5 m.

## 4. Runtime and cost (4-core CPU VM, no GPU)

| model | s/image (CPU, max side 1024) | $ / image | $ per house (≈20 frames) |
|---|---|---|---|
| OWLv2 base | 2.0 (clean smoke), 2.6 in batch | 0 | 0 (~1 min CPU) |
| GDINO tiny | 3.5 | 0 | 0 (~1 min CPU) |
| Florence-2 base | 13 clean (12 calls/image); 57 under contention | 0 | 0 (~4 min CPU) |
| gpt-5.4-mini | 1.0 latency | 0.0017 (0.0047 on the 2048 px facade) | ~$0.04–0.10 |
| gpt-5.5 | 17 latency (reasoning, ~1.6k output tokens) | 0.054 | ~$1.1 |
| grok-4.7 | 51 latency | 0.029 | ~$0.6 |

Batch timings for detectors were measured with other jobs running, so treat clean smoke numbers as the reference. Total VLM spend this path: **$8.18** (mini $0.64, gpt-5.5 $4.90, grok $2.64). 3D lift itself is seconds once depth maps exist (rendering 76 scan depth maps: ~2 min).

## 5. Failure modes

- **Florence-2 never abstains**: a box for every prompt ("swimming pool" = whole meter photo). Unusable without a verifier.
- **GDINO-tiny misses equipment**: gas meter recall 0, electric meter 0.12. The stock HF post-processing also mislabels overlapping phrases ("electric meter" inside "electric meter socket box"); fixed in `run_detectors.py` with per-class token-span scoring.
- **OWLv2 over-fires at 0.2** on the long tail (can, downspout, driveway presence precision 0.2–0.4); precision rises sharply at 0.3–0.5.
- **VLM boxes are loose**: presence is strong, but boxes miss IoU 0.5 often (door instance ~0.45 / 0.5 even though door presence is 0.95). `gpt-5.4-mini` boxes are almost never localised correctly.
- **Gas-meter banks and close-ups**: grok boxes a whole 17-meter bank as one object (gas instance recall 0.31); gpt-5.5 and OWLv2 box individual meters.
- **Thin/tiny objects**: downspouts (IoU 0.3 even) and hose bibs are the weakest everywhere; wall-mounted electric meters at house scale are tiny.
- **Confusables that VLMs mostly got right**: wheelie bins, porch lights, vents, radio masts, porch columns (all showed up as detector false positives for can/AC/downspout).
- **3D**: repeated facade windows make epipolar association ambiguous; short coverage (7–8 frames per window) plus 29% per-view recall starves multi-view methods; non-LiDAR drift of ~1 m destroys object-level 3D placement.

## 6. Licence

| component | licence | commercial |
|---|---|---|
| OWLv2 (`google/owlv2-base-patch16-ensemble`) | Apache-2.0 | yes |
| Grounding DINO tiny (`IDEA-Research/grounding-dino-tiny`) | Apache-2.0 | yes |
| Florence-2 (`florence-community/Florence-2-base`) | MIT | yes |
| SAM 2.1 (untested) / SAM 3 (gated, untested) | Apache-2.0 / SAM licence | yes / check terms |
| OpenAI `gpt-5.5`, `gpt-5.4-mini`; xAI `grok-4.7` | API ToS (paid) | yes, per ToS |
| Eval data: Commons photos (CC BY / BY-SA, per `commons_houses.csv`), PR #16 meters (CC per manifest), YouTube frames (uploader's standard licence: internal eval only), ETH3D (CC BY-NC-SA: non-commercial, eval only) | – | eval only |

## 7. Recommendation for the depth phase / going deeper

1. **Pipeline**: OWLv2 (threshold ~0.3) as a free local proposer on every frame + a VLM (`gpt-5.5` or `grok-4.7`) on 5–10 selected frames per house to confirm presence, class and ground type; `gpt-5.4-mini` as a cheap presence screen. Estimated ≤ $1 per house.
2. **Lift with depth, not triangulation**: back-project boxes (or SAM 2 masks) into the LiDAR depth / fused reconstruction from A3 and cluster across views. Gate on LiDAR-class poses; for non-LiDAR captures object placement is not trustworthy at < 0.8 m, so report those objects as "present, location unsure".
3. **Raise per-frame recall before anything else**: run detectors at a lower threshold and let multi-view voting + VLM verification restore precision; tile high-res frames for tiny meters/taps.
4. **Unblock the untested**: Anthropic credit (Claude), a Gemini API key (native box output, likely the best VLM grounding), HF token for SAM 3; a GPU makes Florence-2-large, GDINO-base and SAM masks on every frame cheap.
5. **Better eval**: human-checked labels, window/fence/driveway instances on more houses, US main panels in context (none on Commons; PR #17 found only 7), a second laser-scanned scene with more windows per frame, and a held-out threshold split (best-F1 numbers here are tuned on test).

## Files

Folder `internal/breadth/b1-detection/`:
- `code/`: `common.py` (classes, prompts, IoU), `run_detectors.py`, `run_vlm.py`, `fetch_images.py`, `commons_search.py`, `build_eval_set.py`, `label_sheets.py`, `cands.py`, `render.py`, `compile_labels.py`, `evaluate.py`, `eth3d.py` (GT from scan + PR #13), `lift3d.py`, `sweep_triangulate.py`.
- `labels/`: `raw/*.jsonl` (per-sheet decisions), `labels.jsonl`, `candidates/`, `sheets/`, `sheet_index.json`.
- `results/`: `metrics.md`, `metrics.json`, `dets/`, `vlm/`, `facade76/` (OWLv2 facade run partial, 40/76, not used), `eth3d_gt.json`, `lift3d_gdino-tiny.json` (incl. 20-seed drift runs), `lift3d_gpt-5.4-mini.json` (clean only; drift run exceeded time), `sweep_triangulate_gdino-tiny.json`.
- `eval_images_batch1.json`, `facade76_images.json`, `commons_houses.csv`, `vm-copies/` (meter manifest, Commons candidates). Images on the VM (`/home/ubuntu/b1/data`) re-fetch from Commons per the handoff.
