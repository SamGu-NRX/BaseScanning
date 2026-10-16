---
cursor:
  subagentId: "bc-e80101e6-ab23-59e9-98b8-16d83f085d53"
---

# B1 detection: builder handoff notes

## 1. One-paragraph summary
Path **B1** (finding criteria objects). It measured instance precision/recall (P/R) at IoU 0.5 (0.3 for downspout, fence, driveway, pool) and image-level presence P/R on 163 real images (48 house frames, 35 Commons photos, 80 PR #16 meter close-ups). Labels were made by an AI agent. It also lifted window/door boxes into 3D on the ETH3D facade against laser-scan GT. Headline: `grok-4.7` instance micro P/R 0.50 / 0.54, `gpt-5.5` 0.48 / 0.61, OWLv2 0.31 / 0.69. Presence recall is at least 0.8 per class for the big VLMs. Depth-based 3D lift found 4 of 6 GT objects with 0 false objects and 0.25 m horizontal / 0.35 m 3D median error. Verdict: **go**, with an OWLv2 proposer plus VLM confirmation, **conditional** on LiDAR-class depth and poses for 3D placement. Report: `internal/breadth/B1-detection.md`. Code: `internal/breadth/b1-detection/code/`. Results: `internal/breadth/b1-detection/results/` (`metrics.md` is the full table). Not in any repo yet.

## 2. Environment that worked
- VM: 4 vCPU, 15 GB RAM, no GPU, Ubuntu, Python 3.12.3. First run `sudo apt-get update && sudo apt-get install -y python3.12-venv p7zip-full`.
- Venv: `python3 -m venv /home/ubuntu/b1/venv && /home/ubuntu/b1/venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install transformers accelerate timm einops opencv-python-headless scipy matplotlib plyfile openai pillow numpy`.
- Versions that worked: torch 2.14.0+cpu, transformers 5.17.0, openai 3.19.2, numpy 2.5.2, Pillow 12.3.0, scipy 1.18.1.
- `export HF_HOME=/home/ubuntu/b1/hf`. The model cache was 2.6 GB (OWLv2, GDINO tiny/base, Florence-2, MM-GDINO). Weights download from HF on first use without a token. They load in about 4 s.
- Weights and licences:
  - `google/owlv2-base-patch16-ensemble`: Apache-2.0.
  - `IDEA-Research/grounding-dino-tiny`: Apache-2.0.
  - `florence-community/Florence-2-base`: MIT. Use the native transformers class, no `trust_remote_code`.
- open3d installs but won't import (needs libEGL). Use `plyfile` + numpy for scans.
- VLMs through the OpenAI SDK:
  - `gpt-5.5`: OpenAI.
  - `grok-4.7`: `base_url="https://api.x.ai/v1"`. xAI returns exact cost per call.
  - Env var names in this VM: `OpenAIChatGraphPooledProdApiKey`, `XAI_CHATGRAPH_PROD_API_KEY`. These are prod pooled keys, so keep spend small.
- Unavailable here: Anthropic (HTTP 400 "credit balance too low"), Gemini/Vertex (403, API disabled on a project we don't own), SAM 3 (`facebook/sam3` gated, needs `HF_TOKEN`).

## 3. Reproduce the headline result
Store root `S=/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`, code dir `$S/internal/breadth/b1-detection/code`, `P=/home/ubuntu/b1/venv/bin/python`.
1. Re-fetch images, about 2 min. Meters and Commons photos are VM-only. Run `$P fetch_images.py`: Commons API `prop=imageinfo&iiurlwidth=1600`, POST in batches of 40 with a User-Agent. Commons returns 429 if hammered; 4 files were lost (c034, m38, m43, m68). Runners skip missing files.
2. Detectors: `$P run_detectors.py owlv2-base --images ../eval_images_batch1.json`, then the same for `gdino-tiny`. That is about 8 min each for 171 images when running alone. Output goes to `results/dets/<model>.jsonl`. Rows: `{id, model, seconds, size, dets:[{label, score, box}]}`, box normalised xyxy in the upright image.
3. VLMs: `$P run_vlm.py gpt-5.5 --images ../eval_images_batch1.json` and `$P run_vlm.py grok-4.7 ...`, plus `--out` for other sets. Timing: gpt-5.5 about 17 s/image, grok about 51 s/image. Cost: $4.90 and $2.64 for 90 images. Output goes to `results/vlm/<model>.jsonl` with `present{}`, `ground_type`, `tokens_in/out` and `cost_usd`. Both runners resume by skipping ids already written.
4. Labels: `$P compile_labels.py`. This turns `labels/raw/*.jsonl` + `labels/candidates/*.json` into `labels/labels.jsonl`. Expect `163 rows; missing: ['commons/c034']` and instances `door 30, gas_meter 29, ac 11, downspout 11, electric_meter 8, meter_can 6, pool 4, hose_bib 3, main_panel 2`.
5. Scoring: `$P evaluate.py` (2 s) writes `results/metrics.{json,md}`. Expect micro rows `owlv2-base 0.31 / 0.69`, `gpt-5.5 0.48 / 0.61`, `grok-4.7 0.50 / 0.54`.
6. 3D ground truth:
   - `bash $S/internal/data/scripts/fetch_gt.sh eth3d facade /home/ubuntu/b1/gt` (176 MB 7z).
   - Clone house-scanning and fetch PR #13 (`git fetch origin pull/13/head:pr13`).
   - `$P -c "import eth3d; eth3d.build_gt()"` writes `results/eth3d_gt.json`.
7. 3D lift:
   - Detect on the 76 facade frames: `$P run_detectors.py gdino-tiny --images ../facade76_images.json --out ../results/facade76/gdino-tiny.jsonl` (about 5 min).
   - Lift: `$P lift3d.py --dets ../results/facade76/gdino-tiny.jsonl --thr 0.35 --noise lidar_modern,nonlidar_modern,nonlidar_2018 --seeds 20`.
   - The first run renders and caches the scan depth maps (`/home/ubuntu/b1/eth3d_facade_depths.npz`, 31 MB, about 2 min). After that a run takes seconds for GDINO.
   - Expected: `depth recovered 4 false_3d 0 centre_err_m_median 0.35 centre_err_xy_m_median 0.25` and `triangulate recovered 3 ... 0.59`.

## 4. Parameters that matter
- Detector thresholds, pre-registered: OWLv2 0.2, GDINO 0.35. Storage thresholds are low (0.08, OWLv2 0.04) so thresholds can be swept.
  - OWLv2 at 0.2 over-fires on the long tail. Best-F1 thresholds, tuned on test so optimistic, are about 0.3–0.5: gas meter F1 0.79 at 0.32, AC 0.90 at 0.39, meter can 0.91 at 0.50, hose bib 0.80 at 0.52.
  - Use **about 0.3 as a proposer** and let the VLM fix precision.
- Image size: max side 1024 for detectors and VLMs. The facade VLM run used 2048 px frames, which cost about 3× more.
- VLM request: one call per image covering all classes, `detail: high`. Boxes are `box_2d [ymin,xmin,ymax,xmax]` in 0–1000 with confidence, plus `present` (yes/no/unsure) per class and `ground_type`. The JSON is parsed from text, with no `response_format`.
  - Count every VLM box, with no confidence threshold. Their confidences are not calibrated: best-F1 cuts land at 0.6–0.96.
- Match IoU: 0.5, and 0.3 for thin or extended classes. Don't raise it for downspouts: they fail even at 0.3.
- Depth lift (`lift_depth`):
  - Depth of a box = 30th percentile of the valid depth in its central half. The median lands behind recessed glass and the window surround biases it.
  - Greedy per-class 3D clustering with radius 0.6 m and at least 2 views.
- Triangulation (`lift_triangulate`): max_gap 6 frames, epipolar gate 25 px, at least 3 votes, reprojection gate 30 px. A dedupe after DLT refinement was added.
  - Sweep: gap 6–12, epipolar gate 15–25, votes 3–5, minimum ray angle 0–6°. It never beat 3 of 6. With 5 votes it recovers 0. The ray-angle gate only removes false objects.
- Scoring: a hit is within 0.8 m of the GT centre. The ROI is the w1–w3 ground floor with centre z between -1.5 and 2.5 m, within 1.5 m of the wall lines.

## 5. Dead ends
- **Florence-2 base**: `<OPEN_VOCABULARY_DETECTION>` returns a box for every prompt, even absent ones (a pool on a meter close-up, a gas meter on the water meter). It gives no scores and takes 12 calls per image (13 s clean). Micro precision is 0.06. Don't use it.
- **Grounding DINO**:
  - The stock `post_process_grounded_object_detection` merges overlapping phrases ("electric meter" inside "electric meter socket box") and mislabels. Fixed in `run_detectors.py`: the class score is the mean sigmoid over that class's token span.
  - Even fixed, it misses equipment: gas meter recall 0, electric meter 0.12. Only doors are usable (0.62 / 0.64).
- **MM-Grounding-DINO** (`openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det`): degenerate `pred_boxes` (w ≈ 0.01, h ≈ 0.95) in transformers 5.17. The port is broken.
- **OWLv2 boxes** are in padded-square pixels. Pass `target_sizes=[(side, side)]` with side = max(w, h), then renormalise by the real w and h. Otherwise every box is skewed.
- **`gpt-5.4-mini` boxes**: instance P/R 0.13 / 0.14 even at `detail: high`, although its presence answers are good. Don't use it for localisation.
- **Depth-free triangulation of box centres**: repeated facade windows make epipolar association ambiguous, and too few frames see each window. See §4.
- **Running jobs concurrently on 4 cores**: runtimes inflate 3–10× (Florence reached 57 s/image) and timing numbers become useless. Run detectors one at a time. The `gpt-5.4-mini` drift run on the facade went past 15 min under contention and was abandoned.
- **Hand-boxing every window, fence and driveway** on general photos was intractable, so those classes are presence-only. Window instances are scored in 3D instead.

## 6. Data quirks
- `eval_images_batch1.json` has 171 rows:
  - `tt-barn` (7 frames) got in by accident. It is dropped in `compile_labels.py`: it's a barn.
  - Paths for the `meters` and `commons` sets point to `/home/ubuntu/b1/data/`. Rewrite them on a new VM.
- The gas-meter count is skewed: 17 of 29 instances are one meter bank (`commons/c078`). grok boxes the bank as one object, so its gas instance recall is 0.31 while its presence recall is 1.0.
- PR #16 meters are close-ups, mostly non-US, with image-level truth only (electric meter yes, gas meter no). They are optimistic for recall and useful as gas/panel hard negatives.
- There is no US main panel in house context on Commons. Panel n is 2, both from one install time-lapse, where the camera is fixed and frames differ only by people.
- `commons/c022` is a water meter and serves as the hard negative for gas meter.
- Label decisions and relabels are in `labels/raw/*.jsonl` notes:
  - Garage doors count as `door`.
  - Wheelie bins, porch lights, vents, radio masts and porch columns were rejected as false positives for can, AC and downspout.
  - Classes that couldn't be judged go in `ignore`.
- ETH3D facade GT:
  - COLMAP world-to-camera poses, metric, PINHOLE rescaled to the 2048 px frames.
  - PR #13 clicks are on keyframes downscaled ×0.25 from 6048 px. Multiply by 4 × 2048 / 6048.
  - Scans 1–3 need `scan_alignment.mlp`.
  - The GT centre is the middle of the clicked opening. Detector boxes include lintels and sills, so about 0.2 m of the 3D error is vertical bias.
- Only 7–8 of the 76 facade frames see each GT window, and the ground is seen at 81–84° incidence.

## 7. Integration notes for the pipeline
- Inputs:
  - Keyframes (upright RGB, EXIF applied).
  - Per-frame intrinsics and camera-to-world poses in the scene frame.
  - Per-frame metric depth: LiDAR depth or a render of the fused A3 mesh. This is required for 3D placement.
- Stage flow:
  1. OWLv2 on every keyframe at score ≥ 0.3.
  2. Select 5–10 frames: highest proposal density plus a meter close-up.
  3. `gpt-5.5` (or `grok-4.7`) per selected frame returns presence, boxes and ground type.
  4. Keep a class if the VLM says `present: yes`. For each kept class, seed from VLM boxes and support them with OWLv2 boxes of IoU ≥ 0.3 in other frames.
  5. Back-project the box-centre depth (30th percentile) and cluster in 3D per class.
- Class map to `docs/scene-and-verdict-format.md` §2.5:

  | B1 class | scene-format `class` |
  |---|---|
  | `electric_meter` | `meter` (also the `frame.anchor`) |
  | `meter_can` | `meter_can` |
  | `main_panel` | `panel` |
  | `gas_meter` | `gas_meter` |
  | `ac_condenser` | `ac_unit` |
  | `door` | `door` (split `garage_door` by aspect, or ask the VLM) |
  | `window`, `downspout`, `hose_bib`, `pool` | same names |

  - `fence` becomes a wall with `role: fence` or `class: other`.
  - `driveway` becomes a `ground.regions` entry with material `drive`.
  - Ground-type mapping: grass → lawn, concrete_or_paving → concrete/pavers, mulch_or_soil → mulch/dirt, gravel → gravel, deck → deck, other → unknown.
- Per object, emit:
  - `source {kind: detector|vlm, model, views[] {image, box_px, score}}`, keeping every supporting view.
  - `confidence`: the recognition score only.
  - `plus_minus_m`: from the geometry spread (cluster MAD plus depth and pose error). **Never from confidence.**
  - `geometry wall_rect` when snapped to a wall, otherwise `point`.
  - `evidence: observed` only with 2 or more views that have measured depth. One view, or no depth, means `inferred`.
- Degrade gracefully:
  - With no depth, or non-LiDAR poses, emit objects with `evidence: inferred` and a large `plus_minus_m` (≥ 1 m). Downstream checks go UNSURE.
  - If the VLM is unavailable, run OWLv2-only at 0.3, mark objects as `detector`, and downgrade to unsure.
  - A class that is never proposed is **unknown, not absent**, unless the relevant walls are `observed` in coverage.
- Error codes to surface: `vlm_unavailable`, `vlm_parse_error`, `no_depth_for_lift`, `pose_quality_low`, `single_view_object`, `class_absent_unobserved`.
- Cost: about $1.1 per house with gpt-5.5 at 20 frames, $0.6 with grok. OWLv2 costs about 1 min of CPU per 20 frames.

## 8. Open risks
- **Per-class weaknesses:**
  - **Downspouts** are the weakest class. Best instance P/R is 0.43 / 0.55 (grok) and OWLv2 gets 0.03 / 0.18, even at IoU 0.3. Presence precision is about 0.6. Porch columns, gas risers, cables and masts are the confusers. Cheapest fix: prompt the VLM with a wall crop plus a thin-vertical-pipe definition; test on 20 labelled wall crops.
  - **Hose bibs** (n=3), **main panels** (n=2), **pools** (n=4), **meter cans** (n=6) and **electric meters in context** (n=8): the numbers are indicative only. Collect 20 or more in-context US examples each.
  - **Doors**: presence 0.95 but instance at most 0.62 / 0.64. Loose boxes hurt `opening_clearance` extents. Snap to wall and depth edges.
- **Never tested:**
  - Classes: `disconnect`, `wall_box`, `vent`, `gas_regulator`, `gas_pipe`, `generator`, `basement_well`, `eave`, `porch_roof`, `stairs`, `hot_tub`, `gate`.
  - Instance-level window, fence and driveway on house photos.
  - Window `operable` / `ground_level` attributes.
  - Cheapest check: add those classes to the VLM prompt and label 30 house frames.
- **Models not tested**: Claude, Gemini (likely the best VLM grounding), SAM 2/3 masks (needed for mask-based depth and extents), GDINO-base, Florence-2-large.
- **3D placement needed**, measured on ETH3D:
  - Per-frame metric depth. The scan oracle gave 4/6 at 0.25 m horizontal error; without depth, 3/6 at 0.53 m.
  - LiDAR-grade poses. With `lidar_modern` drift, still 4/6 at 0.52 m. With `nonlidar_modern` or `nonlidar_2018` drift, 0/6 and 1–4 false objects.
  - Per-frame recall high enough for 2 or more supporting views. Each window was covered in only 0–4 of 7–8 frames.
  - On non-Pro iPhones (no LiDAR), expect object placement to fail the 0.8 m bar. Cheapest experiment: run `lift3d.py` using A3's monocular/MVS depth instead of scan depth, with the `nonlidar_modern` profile.
- **Walkthrough video frames** (blur, low resolution, tiny meters at house distance): the recall hit is unmeasured. Test by tiling 2048 px frames into 2×2 crops for OWLv2 on the yt houses.
- **Labels are agent-made and thresholds were tuned on the test set.** Get a human pass and use a held-out split.

## 9. What you would do differently
- Start with the VLM + OWLv2 pairing and skip Florence and MM-GDINO. Their failures showed up in the first smoke test.
- Label from pooled candidates from the start (`label_sheets.py` / `cands.py`, 2 images per sheet with a grid), with presence-only for dense classes.
- Run detectors strictly one at a time and time them clean on a separate small subset.
- Evaluate 3D lift with real reconstructed depth (A3) rather than scan depth, so the result carries over to phone captures.
- Collect US in-context panel, meter-can and downspout photos before modelling. The long tail decides the verdict.
