---
cursor:
  subagentId: "bc-75c21987-111a-57ac-9f5d-8bf4548db8e4"
---

# B3: cheap gate before the vision-language models

All numbers are on **real images**: B2's 83 meter and 128 panel photos, B1's Commons photos and labels, and all 1,321 frames of the 11 walkaround / ETH3D houses in `internal/data/houses/` (tt-barn excluded, as in B1). Per Hunter's decision, **no Anthropic models**: the tiers use only OpenAI (GPT-5.5, GPT-5.4-mini) and xAI (Grok 4.7, Grok 4.20 NR). Code and outputs are in `internal/breadth/b3-cheap-gate/`. Raw meter answers, which contain meter numbers, are in `internal/breadth/b2/b3-raw/`; nothing outside `b2/` holds a meter number.

## 1. Verdict

**Go, CPU-only.** The recommended cascade:

1. **Gate:** zero-shot CLIP ViT-B/32 plus SigLIP2-base on every frame (0.4 s per frame on one CPU core), with a SigLIP linear probe for reading frames.
2. **Crops:** none. Meter and panel photos go full-frame at 2048 px.
3. **Tiers:**
   - Objects: mini on the gate's top-2 frames per equipment class, GPT-5.5 confirming (gas-meter and pool picks always go to GPT-5.5).
   - Panels: mini first; GPT-5.5 verifies hazard, brand and amperage claims.
   - Meters: GPT-5.5 + Grok 4.7 in parallel, unchanged.

**Cost:** about **$2.32 → $0.30 per house (−87%)**, with every measured reading and hazard metric equal to the all-VLM baseline.

| Stage | Choice | Why |
|---|---|---|
| Gate, object frames | CLIP ∪ SigLIP zero-shot, top-2 frames per equipment class per house | House-level object recall equals GPT-5.5 on every frame, with 87% of walkaround frames filtered (72% on a 3× sparser capture). OWLv2 is no better and 13× slower |
| Gate, reading frames | Checklist purpose label, or SigLIP linear probe | Probe: 208/208 meter and panel photos kept, 10% of walkaround frames sent (grouped CV by house) |
| Crop | **No-go.** Full frame, native 2048 px | Panel crops lose hazard recall (FPE p07 missed). Downscaling to 1024 px costs GPT-5.5 3 brand reads and 2 amp reads on panels, and 2 meter top-1 reads |
| Panel tier | mini @2048; escalate hazard / unprinted-brand / amp claims, or "legible but no amps", to GPT-5.5 | Same brand, amps and hazard as GPT-5.5 alone; 0 false hazard alarms vs 4; −53% to −72% per photo |
| Meter tier | GPT-5.5 + Grok 4.7 at 2048 px, both always | No cheap first tier is safe (below). The meter is 1–2 photos per house, so savings would be about $0.02 |
| **Meter auto-fill with GPT-5.5 + Grok 4.7** | **Not as safe as GPT-5.5 + Opus.** Use pre-fill with one-tap confirm, not blind auto-fill | Agreement 68/73 (93%), but only 65/68 correct (95.6%, Wilson low 87.8%), vs Opus pair 65/65 (Wilson low 94.4%) |

## 2. Inputs and outputs

- **Reading sets:**
  - Meters: 80 usable / 83 meter close-ups (PR #16 manifest), with B2's 73 hand meter-number labels and PR #16 class labels.
  - Panels: 67 PR #17 panels + 61 B2 extra panels, with B2's brand, amps and hazard labels.
  - Hazard panels: 5 (p07 FPE Stab-Lok; x0007, x0012 Zinsco; x0013 Pushmatic; x0678 FPE).
  - All 128 panel photos are electrical equipment, so all are must-keep.
- **Object sets:**
  - B1's frame labels (`labels.jsonl`): 35 Commons photos + 46 walkaround frames. c025 was lost to the Commons rate limit, as B1 lost c034.
  - House-level truth: `house.json` `visible` for meter, panel, gas meter, AC and pool on 11 houses. 18 pairs present if "partial" counts (lenient); 11 if only "yes" counts (strict); unknown is skipped.
  - Both label sets were made by AI agents, not humans.
- **All-VLM baselines (new runs, same prompts as the cascade):**
  - GPT-5.5 presence on all 1,356 walkaround + Commons frames ($0.0167 per frame at 1024 px, presence-only prompt; B1's box prompt costs $0.054).
  - B2's full-frame GPT-5.5 / Grok 4.7 / mini / Grok 4.20 answers for meters and panels.
  - GPT-5.4-mini presence on all 1,356 frames.
- **Gates run** (4-vCPU VM, no GPU):
  - Laplacian sharpness.
  - CLIP ViT-B/32 (LAION-2B) and SigLIP2-base-256: embeddings saved, zero-shot prompts, plus a linear probe.
  - OWLv2-base with 15 queries.
  - DBNet text detection (docTR `db_resnet50`, detection only).
  - OWLv2 and DBNet ran on a 721-image subset: all reading and labelled images, plus every third walkaround frame (475). All gates were compared on that subset too.
- **New VLM runs:**
  - Meters at 1024 px: GPT-5.5, Grok 4.7, mini, Grok 4.20.
  - Meters with an "identifier-context" prompt: GPT-5.5, Grok 4.7, mini.
  - Panels at 1024 px full frame (GPT-5.5, mini) and 1024 px enclosure crops (mini).
- **API spend this path:** $35.43. $22 of it is the GPT-5.5 all-frames baseline.

## 3. Metrics against ground truth

### 3.1 Hunter's question: GPT-5.5 + Grok 4.7 as the meter-number auto-fill pair (73 labelled photos)

| Pair / rule | Agree (coverage) | Correct when agree | Wilson 95% low |
|---|---|---|---|
| **GPT-5.5 + Opus 5.5 (B2, now excluded)** | 65/73 (89%) | **65/65 (100%)** | **94.4%** |
| **GPT-5.5 + Grok 4.7, B2 prompt, 2048 px** | 68/73 (93%) | **65/68 (95.6%)** | **87.8%** |
| same, 1024 px | 70/73 (96%) | 67/70 (95.7%) | 88.1% |
| same + mini must also agree | 60/73 (82%) | 59/60 (98.3%) | 91.1% |
| same + mini + Grok 4.20 all agree | 56/73 (77%) | 55/56 (98.2%) | 90.6% |
| same + both rate legibility "good" | 47/73 (64%) | 46/47 (97.9%) | 88.9% |
| identifier-context prompt, GPT-5.5 + Grok 4.7 | 67/73 (92%) | 63/67 (94.0%) | 85.6% |
| identifier prompt, and neither flags "ambiguous" | 38/73 (52%) | 38/38 (100%) | 90.8% |

- **Not as safe.** About 1 in 23 agreed reads is wrong (3 of 68).
- All three errors (m38, m61, m66) are "which identifier" choices, not misread characters:
  - m38 and m66: both models put the utility number second, behind a manufacturer serial or a long barcode string.
  - m61: both returned a longer barcode string that contains the number.
- Grok 4.7 and GPT-5.5 make the *same* wrong choice. A third voter from either provider still leaves 1 error, because the error is correlated.
- The identifier-context prompt (list every identifier with where it is printed, then apply P4's rules) fixes m61. It breaks 5 others (over-stripping long numbers, switching identifiers), so GPT-5.5 top-1 drops from 69 to 65. Don't use it.
- **Recommendation:**
  - Keep GPT-5.5's top-3 as a one-tap pre-fill (top-3 covers 71/73).
  - Treat GPT-5.5 + Grok 4.7 agreement as "likely", not auto-fill.
  - Auto-fill only if the zxing barcode also matches (B2: 10–11/73, all correct), until a larger set shows the 95% bar.
- Class label: GPT-5.5 is 22/22 US CL and 66/68 overall; unchanged.

### 3.2 Gate: recall of must-keep frames vs frames filtered

**Frame-level, strict.**
- Must-keep: 239 labelled frames (all meter and panel photos, plus any labelled electric meter, meter can, panel, gas meter, AC or pool).
- Thresholds are cross-fitted in two halves (each half uses the lowest positive score of the other half).
- "Walkaround filtered" is over the 475-frame shared subset.

| Gate (CPU s/frame) | Must-keep recall | Gas / AC / pool | Walkaround frames filtered |
|---|---|---|---|
| CLIP B/32 zero-shot (0.08) | 239/239 | 13/13, 9/9, 4/4 | 35% |
| SigLIP2-base zero-shot (0.30) | 237/239 | 13/13, 9/9, 3/4 | 33% |
| OWLv2-base (3.85) | 237/239 | 13/13, 9/9, 3/4 | 45–50% |
| DBNet text count (2.26) | 239/239 | – | 0% (useless alone) |

A frame-level filter at full recall only drops a third to a half of frames. Equipment is visible in about a third of walkaround frames (GPT-5.5 says so on 470 of 1,356 walkaround and Commons frames), and tiny wall meters force low thresholds. So filtering has to be **per-house budgeted selection**, not per-frame thresholds.

**Reading gate** (which frames go to the meter/panel reading prompts; positives = 208 photos):
- SigLIP linear probe (grouped 5-fold by house): **208/208 kept, 10% of walkaround frames sent**.
- CLIP linear probe: 208/208 kept, 16% sent.
- Zero-shot at 100% recall: CLIP sends 18%, SigLIP 35%.
- At 98% recall, the SigLIP probe sends 1%.
- In production, checklist stills carry purpose labels (`meter_close`, `panel_open`, …), and the gate only confirms them and catches mislabelled ones.

**Object tier, house level** (the gate picks the top-K frames per equipment class per house; "confirm" = mini, then GPT-5.5 on mini yes/unsure, with gas-meter and pool picks always going to GPT-5.5):

| Selection → tier | Full capture (1,321 frames): filtered, strict / lenient recall, false present, $/house | Sparse capture (every 3rd frame, 475): filtered, strict / lenient, false, $/house |
|---|---|---|
| **Every frame → GPT-5.5 (baseline)** | 0%, **11/11 / 15/18**, 6/31, **$2.00** | 0%, 11/11 / 15/18, 4/31, $0.71 |
| Every frame → mini | 0%, 11/11 / 14/18, 2/31, $0.13 | 0%, 10/11 / 13/18, 1/31, $0.05 |
| SigLIP top-1 → confirm | 96%, 11/11 / 13/18, 2/31, $0.05 | 91%, 9/11 / 11/18, 2/31, $0.05 |
| CLIP top-1 → confirm | 96%, 8/11 / 10/18, 2/31, $0.05 | 89%, 7/11 / 10/18, 0/31, $0.05 |
| OWLv2 top-1 → confirm | – | 90%, 8/11 / 10/18, 1/31, $0.06 |
| DBNet top-1 → GPT-5.5 | – | 98%, 4/11 / 5/18 |
| **CLIP ∪ SigLIP top-2 → confirm (recommended)** | **87%, 11/11 / 14/18, 2/31, $0.17** | **72%, 11/11 / 15/18, 3/31, $0.15** |
| same selection → GPT-5.5 directly | 87%, 11/11 / 14/18, 2/31, $0.25 | 72%, 11/11 / 15/18, 3/31, $0.20 |
| Random, same budget (10 frames per house) → GPT-5.5 | 92%, 9.2/11 / 11.2/18 | 77%, 9.2/11 / 11.5/18 |

- The only object the recommended cascade loses against the baseline is yt-0gotY's "AC". Its `house.json` note reads "window AC units … no ground condenser confirmed".
- Both systems miss eth3d-electro's partial panel and AC, and yt-4h8kyjnPo9s's partial AC.
- The cascade makes fewer false "present" calls (2 vs 6). GPT-5.5 per-frame over-calls AC, pool and gas.
- Frame level on B1's labelled frames, threshold gate then mini with escalation, against GPT-5.5 on every frame:
  - Gas 13/13 vs 13/13; AC 9/9 vs 9/9; panel 2/2 vs 2/2; pool 3/4 vs 3/4.
  - Electric meter 3/5 vs 4/5: house-scale meters are tiny.
  - Doors and windows drop by design; they belong to the 3D path.

### 3.3 Reading tier: resolution, crops and cheap models

**Panels** (PR #17 set: 67 photos, 19 legible brands, 9 mains, 5 ratings, 1 hazard; extra set: 60 photos, 13 brands, 3 mains, 1 rating, 4 hazards):

| Policy | Brand | Main amps | Rating | Wrong amp claims | Hazard | False hazard | $/photo (PR #17 set / extra set) |
|---|---|---|---|---|---|---|---|
| **GPT-5.5 @2048 (baseline)** | 16/19, 11/13 | 7, 3 | 5, 1 | 1, 0 | **5/5** | 3, 1 | 0.0294 / 0.0241 |
| GPT-5.5 @1024 full frame | 13/19, 7/10* | 5, 3 | 5, 0 | 1, 0 | 5/5 | 3, 1 | 0.0226 / 0.0220 |
| mini @1024 enclosure crop | 11/19, 7/13 | 4, 2 | 2, 0 | 4, 1 | **3/5** (misses FPE p07) | 3, 0 | 0.0011 / 0.0010 |
| mini @2048 alone | 15/19, 10/13 | 6, 3 | 4, 1 | 4, 1 | 5/5 | 2, 0 | 0.0021 / 0.0014 |
| **mini @2048, escalate hazard / unprinted brand / amps / legible-no-amps to GPT-5.5 @2048** | **16/19, 11/13** | **7, 3** | **5, 1** | 1, 0 | **5/5** | **0, 0** | **0.0137 / 0.0067** |
| mini, escalate any claim to GPT-5.5 | 16/19, 10/13 | 7, 3 | 5, 1 | 1, 0 | 5/5 | 1, 1 | 0.0219 / 0.0151 |

\* 40 of 60 extra photos scored at 1024 px.

- OWLv2's "breaker panel" box often covers one breaker column. On p07 the crop cuts off the printed Stab-Lok label. Crops are no-go for panels.
- Cost of the recommended policy: mini's brand names on photos with no legible brand rise from 16 to 25 (PR #17 set). Treat brand as a hint, as B2 already recommended.

**Meters:**
- GPT-5.5 top-1 at 1024 px is 67/73, vs 69/73 at 2048 px, for a $0.009 saving per photo. Stay at 2048.
- OWLv2 keeps 71/83 meter close-ups full-frame (the meter already fills ≥50% of the frame). Text-cluster crops cut identifiers off (m21, m53). No crop.
- Cheap tiers are unsafe for auto-fill:
  - mini + Grok 4.20 agreeing: 61/73 agree, only 56 correct.
  - Their agreement, with GPT-5.5 + Opus as the escalation: 70 auto-fills, 5 wrong.

### 3.4 Accuracy retained, recommended cascade vs all-VLM baseline

| Metric | Baseline | Cascade |
|---|---|---|
| Meter number top-1 / top-3 (73) | 69 / 71 | 69 / 71 (same calls) |
| Meter pair agreement correct | 65/68 | 65/68 |
| Class label, US CL / all | 22/22, 66/68 | 22/22, 66/68 |
| Panel brand (32 legible) | 27 | 27 |
| Main amps (12) / rating (6) | 10 / 6 | 10 / 6 |
| **Panel hazard recall (5)** | **5/5** | **5/5** |
| False hazard alarms | 4 | 0 |
| Object presence, house-level strict / lenient | 11/11, 15/18 | 11/11, 14/18 (the disputed window AC) |
| Gas meter / pool, house-level | 2/2, 0 present | 2/2 |

## 4. Runtime and cost per house (list prices; 4-vCPU CPU VM)

House model: 9 checklist stills (2 meter close-ups, 3 panel / disconnect photos) plus 120 walkaround keyframes (the mean of the 11 houses). Measured per-call costs.

| Line | All-VLM baseline | Cascade |
|---|---|---|
| Gate (CLIP 0.08 s + SigLIP 0.30 s per frame, 129 frames) | – | ~50 CPU-s ≈ $0.001 |
| Object presence | GPT-5.5 × 129 = $2.15 | ~17 frames → mini, ~8 GPT-5.5 confirms = $0.18 |
| Meter number (2 photos, GPT-5.5 + Grok 4.7) | $0.088 | $0.088 |
| Panels (3 photos) | $0.081 | $0.031 |
| **Total** | **$2.32** | **≈ $0.30 (−87%)** |
| API calls | ~134 | ~30 |
| Wall time (16 parallel calls) | 60–80 s | 45–80 s |

- Both wall times are bounded by Grok 4.7 on the meter (median 41 s, p90 78 s), now the latency floor because Grok 4.7 is the required second reader.
- A sparse capture (every third frame, 52 frames) costs $1.04 → $0.30.
- If the object pass used B1's box prompt ($0.054 per frame), the baseline would be about $7.1 per house.
- The old GPT-5.5 + Opus meter pair cost $0.132 per house.
- OWLv2 would add 3.85 CPU-s per frame (about 8 CPU-min per house) with no gain. DBNet adds 2.26 s per frame; it's useful only as a "text visible" tag.
- Quality tags:
  - Sharpness (Laplacian variance) separates GPT-5.5's "blurry" frames at AUC 0.75.
  - DBNet text count separates "readable equipment text" at AUC 0.86.
  - Both are good enough to rank frames and prompt a retake, not to drop frames.

## 5. Failure modes

1. **Correlated identifier choice** (meters): GPT-5.5 and Grok 4.7 agree on the same wrong identifier on multi-identifier plates. Agreement isn't independence, so no voting rule fixes it.
2. **Frame thresholds can't filter house frames**: equipment is in about a third of frames, and tiny wall meters force low thresholds. Budgeted top-K per class works; plain thresholds filter at most 35–50%.
3. **Gate choice is unstable at this n**: SigLIP wins on dense capture and CLIP on sparse. The union of both is the robust choice. House-level truth has only 11–18 positives, so one object is worth 6–9 points.
4. **Mini's "no" is unreliable for gas meters** (frame level 5/13). Hazard-class picks must bypass mini.
5. **Crops and downscaling hurt fine print**: panel labels, breaker ratings and hazard labels sit anywhere on the enclosure. OWLv2 boxes cover a column, not the enclosure.
6. **Labels**: frame and house labels are AI-made. `house.json` "partial" mixes near-misses (window AC, AC disconnect). Only 5 hazard panels exist; no CL320 plate.
7. **Store filesystem**: freshly edited scripts sometimes ran stale for a few seconds. Writes were kept off-store while appending (as B2 found).

## 6. Licence

- **Models:**
  - CLIP ViT-B/32 LAION-2B (open_clip, MIT).
  - SigLIP2-base (Apache-2.0).
  - OWLv2 (Apache-2.0).
  - docTR DBNet (Apache-2.0).
  - scikit-learn (BSD).
  - All commercial-OK.
  - VLM APIs under OpenAI and xAI terms; Anthropic is excluded by decision.
- **Images:** as in B1/B2.
  - Commons: CC BY / BY-SA.
  - Extra panels: mostly NC/ND, internal evaluation only.
  - YouTube frames: internal evaluation only.
  - ETH3D: CC BY-NC-SA.
  - Meter numbers stay in `b2/`.

## 7. Going deeper

1. **Meter auto-fill without Anthropic:** about 75+ more labelled multi-identifier plates, to test GPT-5.5 + Grok 4.7 + barcode as the auto-fill rule. Also test a structured "identifier + printed context" answer where a rule picks, rather than the model (the prompt version tried here over-strips).
2. **Real Base captures:** measure the gate on checklist stills with purpose labels and ARKit keyframes. Keyframe density changes the top-K choice (compare the dense vs sparse results).
3. **Hazard sets:** 30–50 FPE / Zinsco / Challenger panels to confirm mini's first-pass hazard recall (5/5 here), and gas-meter frames for the bypass rule.
4. **A fine-tuned gate:** the SigLIP probe already beats zero-shot. A small head trained on GPT-5.5's all-frame labels (1,356 frames exist now) could replace top-K with calibrated per-class scores. On a GPU the gate is under 10 ms per frame.
5. **Latency:** Grok 4.7 is the per-house floor (41–78 s). If that matters, run Grok 4.7 only when GPT-5.5's top-1 is not barcode-confirmed.

## Files (`internal/breadth/b3-cheap-gate/`)

- **Code:**
  - `build_images.py`, `gt.py`: image list and frame / house truth.
  - `gate_features.py`: quality, CLIP, SigLIP, OWLv2, DBNet.
  - `gate_eval.py`, `reading_gate.py`, `probe.py`: gate metrics.
  - `select_eval.py`: top-K house selection and tiers.
  - `cascade_eval.py`: frame-level cascade.
  - `crop.py`, `run_crops.py`: crops and resolution runs.
  - `vlm_presence.py`: presence tier.
  - `vlm_ids.py`: identifier-context prompt.
  - `tiers.py`: meter and panel tier policies, scored with B2's labels.
  - `images.json`, `subset.txt`: image lists.
- **Results (`results/`):**
  - Gate: `gate_eval_{all,sub}.json`, `gate_scores_{all,sub,probe}.json`, `reading_gate.json`, `probe_scores.json`.
  - Selection and cascade: `select_eval_{all,subset}[_strict].json`, `cascade_siglip.json`.
  - Tiers: `tiers_*.json` (counts only).
  - Crops: `crops_{owl,text,enclosure,full}.json`.
  - Features: `feat/` (per-frame features and embeddings).
  - Raw VLM answers: `raw/presence/` (mini and GPT-5.5 on 1,356 frames), `raw/{panel,extra}_crop_*`.
- **Meter raw answers** (contain numbers): `internal/breadth/b2/b3-raw/{meter_ids,meter_crop_encl1024}/`.
- **Reproduce:**
  - Images: fetch with `b2/fetch.py` and `b1-detection/code/fetch_images.py` into `/tmp/b3/data` and `/home/ubuntu/b1/data`.
  - Then run `build_images.py`, `gt.py`, `gate_features.py <model>`, `gate_eval.py`, `probe.py`, `select_eval.py --strict`, `tiers.py`.
