---
cursor:
  subagentId: "bc-edf41cf7-deb1-547c-8cf8-b1de02deffa1"
---

# Path handoff notes: B2 equipment close-up reading (production reads stage)

## 1. One-paragraph summary
- **What it is.** Path B2 reads equipment close-ups on the server: meter number, CL/class label, meter-can type and rust, panel brand, main/bus amperage and hazard line. All photos are real (83 Commons meters from PR #16, 67 panels from PR #17, 61 new Openverse panels).
- **Headline numbers.** On 73 hand-labelled meters, top-1 meter number is GPT-5.5 69/73 (95%) and Grok 4.7 66/73 (90%), against 64% held-out for Apple Vision + PR #16 ranking. Both read 22/22 US CL labels.
- **Production pair: GPT-5.5 + Grok 4.7 agreement** (replacing GPT-5.5 + Opus; no Anthropic). It accepts 68/73 (93%) at 65/68 (96%) precision, Wilson low 87.8%. That is below the 95% auto-fill bar, so the pair **pre-fills with one-tap confirm**, never blind auto-fills.
- **Panels.** Every model flags all 5 real hazard panels. Grok 4.7 raises 0 false hazard alarms; GPT-5.5 raises 4 (p20, p48, p78, x0149). A hazard needs both models to flag it, and still goes to review.
- **Verdict.** Go for meter number (confirm UI), class label, panel hazard flag, and printed amps. No-go for can type and rust from close-ups, and no-go for server OCR as the reader. **CL320 recall is unmeasured**: the data holds no CL320 plate.
- **Where things are.**
  - Report: `internal/breadth/B2-equipment-reading.md`. Code: `internal/breadth/b2/` in the store.
  - Nothing is in `house-scanning-server` yet; port `vlm.py`, `engines.py` (the zxing part), the scorers and `vendor/meter_eval/`.

## 2. Environment that worked
- **Machine:** 4 vCPU / 15 GB RAM cloud VM, no GPU, Ubuntu, Python 3.12.3 via `uv`.
- **Install:**
```sh
sudo apt-get update -qq && sudo apt-get install -y tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0
curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH=$HOME/.local/bin:$PATH
mkdir -p /tmp/b2 && cd /tmp/b2 && uv venv -p 3.12 .venv && source .venv/bin/activate
uv pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
uv pip install python-doctr paddlepaddle paddleocr zxing-cpp opencv-python-headless pillow pytesseract requests openai anthropic numpy
```
- **Versions used:** openai 3.19.2, anthropic 1.8.0, zxing-cpp 3.1.1 (Apache-2.0), paddleocr 3.7.0 + paddlepaddle 3.3.1, python-doctr 1.1.0, torch 2.14.0+cpu, Tesseract 5.3.4 (all Apache-2.0). Install takes about 2 min.
- **Install gotchas:**
  - A bare `apt-get install` failed ("Unable to locate package tesseract-ocr") until `apt-get update` was run.
  - `python-doctr[torch]` warns that the extra doesn't exist; plain `python-doctr` is fine.
- **Weights download on first use:**
  - PaddleX goes to `~/.paddlex/official_models` (PP-OCRv6 medium det/rec). Set `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`.
  - docTR goes to `~/.cache/doctr` from `doctr-static.mindee.com`, about 200 MB.
- **API keys are referenced only by env name:**
  - `OpenAIChatGraphPooledProdApiKey`
  - `XAI_CHATGRAPH_PROD_API_KEY`, for Grok via the OpenAI SDK with `base_url="https://api.x.ai/v1"`.
  - Anthropic `AnthropicChatGraphPooledProdApiKey` is **out of credit**, and Gemini is unavailable (Vertex 403).

## 3. Reproduce the headline result
```sh
S=/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2; B=$S/internal/breadth/b2; cd $B
source /tmp/b2/.venv/bin/activate
export PYTHONPATH=$B/vendor PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True B2_RAW=/tmp/b2raw
python fetch.py meter --manifest inputs/meter_manifest.csv      # 83 photos, ~3 min (1 s sleep per file)
python fetch.py panel --manifest inputs/panel_screened.csv      # 67 photos
python merge_raw.py raw /tmp/b2raw                              # seed local copy from stored runs (skip to rerun)
python vlm.py --model gpt-5.5  --set meter --workers 6          # ~2 min
python vlm.py --model grok-4.7 --set meter --workers 12         # ~10 min (median 41 s/photo)
python vlm.py --model gpt-5.5  --set panel; python vlm.py --model grok-4.7 --set panel
python engines.py --engine doctr --set meter                    # writes barcodes too; ~5 min
python score_meter.py | grep -E "gpt-5.5|grok-4.7"
python analyze_meter.py | grep -E "GPT-5.5 \+ Grok 4.7 \|"
python score_panel.py panel; python score_panel.py extra
```
- **Expected snippets:**
  - `vlm_gpt-5.5 | 73 | 68/73 = 93% ... | 69/73 = 95% (87%–98%) | 71/73 = 97%`
  - `vlm_grok-4.7 | 73 | 69/73 ... | 66/73 = 90% (82%–95%) | 72/73 = 99%`
  - `GPT-5.5 + Grok 4.7 | 68/73 = 93% | 65/68 = 96% (88%–98%) | 87.8%`
- **Non-determinism:** VLM answers vary run to run at temperature defaults, so expect ±1–2 photos. Stored outputs are in `raw/{meter,panel,extra}/*.jsonl`.
- **Extra panel set:**
  - Photos are already downloaded as `c*.jpg` under `$B2_DATA/extra_cand/` by `screen_panels.py inputs/openverse_extra_keep.json`.
  - Copy the 61 ids in `inputs/panels_extra.csv` to `$B2_DATA/extra/images/x<n>.jpg`, where `x0007` = `c0007`.
  - Or refetch with `fetch.py extra --manifest inputs/panels_extra.csv`.

## 4. Parameters that matter
- **Image sent to the VLM:**
  - EXIF-transposed and RGB, with the long side capped at **2048 px** (`MAX_SIDE`), JPEG q90, and `detail: "high"` for OpenAI/xAI.
  - **No crop, sharpening or contrast enhancement was applied** to model inputs. None was needed for the 95% result.
  - Resolution does matter. Four panel amps (p12, p33, p39, p63/64) are legible only at full resolution; PR #17 missed them on thumbnails, and the models got them at 2048 px. Don't go below 2048. Tiling and crops were not tested.
- **Fetch:** width is capped at 3840 px, as in PR #16/#17; OCR runs at that resolution.
- **Prompts** (`METER_PROMPT`, `PANEL_PROMPT` in `vlm.py`), the parts that mattered:
  - "Never guess characters; null if unreadable".
  - "Prefer the utility's meter number (large print, next to barcode, after No./Nr./#) over manufacturer serial/model".
  - Return `number_candidates` up to 5.
  - For panels: a `hazard_line` enum, a `brand_evidence` enum, and `text_seen[]`.
- **Output limits:** `max_completion_tokens=8000` (OpenAI/xAI). Keep it high, because GPT-5.5 spends about 660 output tokens including reasoning.
- **Ranking:** the ranked list is `[meter_number] + number_candidates`, deduplicated on `meter_eval.match.core()`. A pick is right when the core matches (PR #16 rule).
- **Agreement rule:** accept when both models' top-1 cores are equal, else show the union top-3 for a tap.
  - GPT-5.5 + Grok 4.7: 68/73 accepted, 65 correct. The 3 accepted errors (m38, m61, m66) are both models choosing the same wrong identifier.
  - The 5 disagreements (m30, m33, m57, m64, m77) all go to tap-to-confirm.
  - Adding Grok 4.20-non-reasoning as a third voter gives 59/73 accepted, 58/59 correct, Wilson low 91.0%.
  - GPT-5.5 + PaddleOCR top-1 gives 50/73 accepted, 49/50 correct, low 89.5%.
- **zxing barcode path** (`engines.py barcodes()`):
  - `zxingcpp.read_barcodes` on grayscale at scale 1.0 and 0.5; payloads are deduplicated, each with a normalized box.
  - To confirm, compare `core(normalize(payload))` with the VLM's top-1 core.
  - It decodes something on 20/83 photos, and confirms GPT-5.5's top-1 on 10/73 and Grok 4.7's on 10/73, **10/10 correct each**.
  - Use it as a free precision boost: barcode equal to top-1 means auto-fill. Don't use it as a recall source.
- **Hazard rule:**
  - Flag when `hazard_line ∈ {federal_pacific_stab_lok, zinsco_sylvania_magnetrip, challenger}`. Pushmatic is **not** on Base's reject list.
  - GPT-5.5 AND Grok 4.7: 5/5 real hazards caught, 0 false alarms across both sets (derived from stored runs; Grok 4.7 alone had none).
- **OCR (baselines only):**
  - PaddleOCR: `use_textline_orientation=True`, doc orientation and unwarping off, **`enable_mkldnn=False`** (otherwise paddlepaddle 3.3 crashes). Paddle internally resizes above 4000 px.
  - docTR: `db_resnet50` + `parseq`, `assume_straight_pages=False`, `export_as_straight_boxes=True`.
  - Tesseract: `--oem 1 --psm 11` or `3`.

## 5. Dead ends
- **Server OCR + PR #16 ranking.** Reading is fine (Paddle 95%, docTR 92%) but top-1 is poor (68%, 59%). Tesseract gets 22–25% top-1 on curved or glossy plates. Don't port OCR as the reader.
- **PaddleOCR speed.** 35 s/photo idle on 4 vCPU (11–45 s), 50–100 s under contention, against 2.9 s for docTR and 2.3 s for Tesseract. The panel run was abandoned.
- **docTR 1.1** raises `ValueError: negative stride` inside its rotated-crop path on some photos. `engines.py` falls back to a straight-page predictor.
- **Meter-can type from close-ups.** Models disagree: GPT-5.4-mini and Grok 4.20 call about 50/83 "ringless", GPT-5.5 calls 6. Every model tried calls rust on 29–87% of photos with none visible.
- **Cheap models as sole readers.** They invent brands and amps (Grok 4.20 put "200" on 13 photos) and invent `printed_text` for FPE on non-FPE panels (x0476, p27). The `brand_evidence` field doesn't separate real reads from invented ones.
- **Openverse anonymous API:** `page_size` above 20 returns 401.
- **Store sync:** appending to jsonl inside the synced store creates `*.conflict.jsonl` copies, and edits show up with a lag of a few seconds.
  - Always write runs to `B2_RAW=/tmp/b2raw`, then `python merge_raw.py /tmp/b2raw raw`.
  - `merge_raw.py` folds conflict copies back in, keyed on photo id.
- **Anthropic pooled key ran out mid-run**, so there are no Claude results on the extra set.

## 6. Data quirks
- **Meter set:** 83 photos, 80 usable. 73 have an agreed number (hand labels checked against PR #16's key-holder lengths, 73/73 match). All 73 are reproduced exactly by at least one non-Claude reader.
- **No CL320 plate in the data** (21 × CL200, 1 × CL20). Only the false-CL320 rate is measured: 0 for every engine.
- **Hidden or faint numbers:**
  - m35 (number under a sticker) and m36 (off frame): GPT-5.5 and Grok 4.7 correctly return null; the cheap models return the seal number.
  - m83 (faint plate, no label): Grok 4.7 answers confidently, and the answer is unverifiable.
- **Multi-identifier plates cause every flagship miss:**
  - utility number against manufacturer serial (m33, m38, m50–52, m57);
  - long barcode string against its 8-digit core (m61, m66).
- **PR #17 labels were wrong 4 times** (fixed in `labels/panel_hand.csv`): p12 main 100, p33 100, p39 150, p63/p64 MAIN 50.
- **Hazard photos:**
  - p60 carries a Federal Pioneer badge (Canadian FPE affiliate) and is scored as unknown hazard.
  - p20 is a modern Eaton label that prints "Challenger" as a compatible breaker. It is **not** a hazard, but models flag it.
- **Extra set:**
  - Mostly BY-NC-SA / BY-NC-ND, so internal evaluation only.
  - x0761 duplicates p22 and is excluded.
  - x0678 FPE was found by the models, not by any title.
  - x0151's brand is unverifiable.
- **Files containing real meter numbers — excluded from repo:**
  - `labels/meter_numbers_hand.csv`
  - `inputs/hand_numbers_raw.txt`
  - `inputs/meter_locate_per_image.csv`
  - `mosaics/num_*.png`
  - `raw/meter/*.jsonl`
  - `results/meter_per_image.csv`
  - `results/meter_analysis.md`
  - `results/meter_table.md`

## 7. Integration notes for the pipeline
- **Inputs:** the checklist close-ups (meter close-up, main breaker box, main disconnect), original resolution, EXIF intact. No scene geometry is needed.
- **Outputs** as verdict v0 `criteria[]` facts with `coverage[].region = {element_ids, images}`:
  - `meter_number`:
    - agreement or barcode match → pass with `fact.value`;
    - disagreement → unsure `unknown_attribute` + a `views_needed` `confirm_attribute` carrying the top-3 union;
    - both null → unsure `unobserved` + a `close_up` request.
  - `cl320`: `fact.value` = the CL string as read. Text CL320 → pass, other CL → fail per the criteria spec, none legible → unsure `unobserved`.
  - `panel_recall`: a hazard flag from both models → unsure `rule_requires_review`, never an automatic fail. Include `text_seen` and the image id so a reviewer can check the quote.
  - Main amps → `fact.value` (int), only when `main_breaker_amps` is non-null from both models or matches printed `text_seen`.
- **Don't emit** `meter_type`, `meter_mount`, `oversized_can` or rust from close-ups. Mark them `not_evaluated` and leave them to the surroundings photo and 3D paths.
- **Error codes to surface:** `vlm_timeout` (Grok 4.7 p90 is 78 s; set about 120 s and fall back to GPT-5.5 alone plus confirm), `vlm_parse_error` (no JSON object), `provider_quota`.
- **Cost per house** (1 meter + 2 panel photos, list prices, no batch):
  - GPT-5.5: about $0.036 per meter photo and $0.029 per panel photo.
  - Grok 4.7: about $0.009 and $0.007 (a lower bound, since reasoning tokens may be unreported).
  - Pair total: about **$0.12 per house**.

## 8. Open risks
- **CL320 recall.** Cheapest test: collect 20 real CL320 plates (Commons/Openverse "CL320", Itron/L+G 320 A) and rerun `score_meter.py`.
- **Real iPhone captures (non-Pro, glare, oblique, dim)** are untested. Commons photos are curated. Cheapest test: 30 in-house phone shots of real meters and panels.
- **Correlated identifier errors** between GPT-5.5 and Grok 4.7 (m38, m61, m66). Test an "all identifiers with context" prompt plus a rule-based pick on the 73.
- **Hazard set** is 5 positives, all old labelled panels; breaker-only views are untested. Cheapest test: 30–50 FPE/Zinsco/Challenger photos, including no-label shots.
- **Grok 4.7 latency and cost** accounting (hidden reasoning). Measure xAI billing on 50 calls.
- **The 95% auto-fill bar** needs about 150 labelled meters to show a 95% lower bound for any pair.

## 9. What you would do differently
- Write runs outside the synced store from the start, since sync conflicts cost a restart.
- Ask the VLM for every identifier with its printed context and pick with a rule, instead of asking it for "the" meter number.
- Collect CL320 and hazard-panel positives before running any model.
- Skip PaddleOCR and Tesseract baselines on CPU. docTR alone shows the OCR ceiling.
- Label can type only from wide surroundings shots, with US-specific classes.
