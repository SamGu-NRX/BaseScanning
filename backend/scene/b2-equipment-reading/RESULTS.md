---
cursor:
  subagentId: "bc-edf41cf7-deb1-547c-8cf8-b1de02deffa1"
---

# B2: server-side reading of equipment close-ups

All numbers are on **real photos** (Wikimedia Commons, Flickr via Openverse). Nothing is synthetic. Code, labels and raw outputs are in `internal/breadth/b2/` (see "Files" at the end).

## 1. Verdict

**Go for VLM-via-API on meter number, class label and panel brand/amperage/hazard line, with confirmation rather than blind auto-fill. No-go for server OCR (docTR, PaddleOCR, Tesseract) as the reader. No-go for meter-can type and rust from close-ups.**

| Approach | Meter number | CL / class label | Can type, rust | Panel brand, amps, hazard |
|---|---|---|---|---|
| Flagship VLM (GPT-5.5, Claude Opus 5.5) | **Go**: pre-fill with one-tap confirm. Two-model agreement gets close to the auto-fill bar | **Go**: 22/22 US CL labels | **No-go** | **Go** for hazard flag and brand as a review hint. Amps: go where printed |
| Grok 4.7 | Go as second opinion (top-1 90%) | Go (22/22) | No-go | Go: zero false hazard alarms on both sets. Slow (median 41 s per photo) |
| Cheap VLM (GPT-5.4-mini, Grok 4.20 NR, Haiku 4.5) | Top-3 list only (top-1 78–84%) | Mini and Grok 4.20 22/22 or 21/22; Haiku 20/22 | No-go | No-go alone: invented brands and amps, and false FPE calls quoting "printed" text that isn't there |
| Server OCR + PR #16 ranking | **No-go**: top-1 docTR 59%, PaddleOCR 68% (held-out 66% / 74%, about Apple Vision's 64% on-device) | docTR 19/22, PaddleOCR 17/22 | – | No-go: keyword rule reads a brand on 8–26% |
| zxing-cpp barcode confirmation | Keep as a free precision check: 10–11/73 photos, all correct | – | – | – |

Key numbers: GPT-5.5 and Opus 5.5 each pick the right meter number first on **69/73 (95%)**, against 64% for PR #16's Vision + ranking. **GPT-5.5 + Opus agreement: 65/65 correct at 89% coverage** (Wilson lower bound 94.4%, just under PR #16's 95% auto-fill bar). No reader ever claimed CL320, but the data holds no CL320 plate, so CL320 recall is **unmeasured**. All four real hazard panels in the extra set (Zinsco ×2, Pushmatic, FPE Stab-Lok), plus PR #17's FPE photo, were flagged by every model tested.

## 2. Inputs and outputs

- **Meters:** PR #16's 83 Commons photos (80 usable). Their class labels come from PR #16's manifest (22 US CL plates: 21 × CL200, 1 × CL20; 46 non-US current ratings). Meter-number ground truth is `labels/meter_numbers_hand.csv`: **73 hand transcriptions**, each checked against PR #16's key-holder lengths (full and core length, 73/73 match). Meter-can class and rust are hand-labelled on all 83 (`labels/meter_can_condition_hand.csv`).
- **Panels, PR #17 set:** 67 unique photos. Labels are PR #17's legibility screen and notes plus my eye checks (`labels/panel_hand.csv`, 25 rows). The eye checks **corrected PR #17 four times**: p12, p33 and p39 main-handle amps are legible at full resolution, and p63/p64's breaker marked MAIN reads 50. Of the 67, 19 have a legible maker, 9 a legible main rating and 5 a panel rating. One is a hazard line (p07 FPE Stab-Lok); p60 carries a Federal Pioneer (Canadian FPE) badge and is scored as unknown.
- **Panels, extra set (new):** 796 Openverse hits for PR #17's missing hazard terms. gpt-5.4-mini screened them for equipment type only (not legibility), which left 83. After deduplication against PR #17 and an eye pass removing non-panels (HVAC coils, inspection cards and similar), **61 photos** remain, listed in `inputs/panels_extra.csv` with licence, author and landing URL. Labels are `labels/panel_extra_hand.csv`. Brands and amps are labelled only where the title or visible text confirms them. There are **4 verified hazard panels**: x0007 (Zinsco), x0012 (Zinsco/Magnetrip), x0013 (Pushmatic, 70 A), and x0678 (FPE Stab-Lok, found by the models and confirmed by eye). Most are NC/ND licences, so this set is for internal evaluation only. x0761 turned out to be the same photo as p22 and is excluded.
- **Engines:**
  - VLMs, one JSON answer per photo, prompts in `vlm.py`, long side capped at 2048 px: GPT-5.5, GPT-5.4-mini, Claude Opus 5.5, Claude Haiku 4.5, Grok 4.7 and Grok 4.20-0309-non-reasoning.
  - OCR (`engines.py`), fed through PR #16's unchanged `locate.candidates`/`ranked`: docTR 1.1 (db_resnet50 + parseq), PaddleOCR 3.7 (PP-OCRv6 medium), Tesseract 5.3.4 (psm 11 and psm 3). Barcodes are read with zxing-cpp.
- **Not run:**
  - Gemini: the only Google credential has Vertex disabled (403), and enabling it would be an external write.
  - Claude on the extra set: **the Anthropic pooled key ran out of credit** ("credit balance is too low") during this run.

## 3. Metrics against ground truth

### 3.1 Meter number (73 labelled photos; held-out = even ids, the half PR #16 didn't tune on)

| Engine | Read anywhere | Top-1 | Top-3 | Top-1 held-out | Answered |
|---|---|---|---|---|---|
| Apple Vision + PR #16 ranking (reference, PR #16) | 97% | – | 85% held-out | 64% | – |
| **GPT-5.5** | 68 (93%) | **69 (95%, 87–98)** | 71 (97%) | 33/35 (94%) | 73/73 |
| **Claude Opus 5.5** | 69 (95%) | **69 (95%, 87–98)** | 73 (100%) | 33/35 (94%) | 73/73 |
| Grok 4.7 | 69 (95%) | 66 (90%) | 72 (99%) | 31/35 (89%) | 73/73 |
| GPT-5.4-mini | 69 (95%) | 61 (84%) | 70 (96%) | 30/35 (86%) | 73/73 |
| Grok 4.20 NR | 68 (93%) | 61 (84%) | 72 (99%) | 30/35 (86%) | 73/73 |
| Claude Haiku 4.5 | 65 (89%) | 57 (78%) | 67 (92%) | 27/35 (77%) | 73/73 |
| docTR + PR #16 ranking | 67 (92%) | 43 (59%) | 58 (79%) | 23/35 (66%) | – |
| PaddleOCR + PR #16 ranking | 69 (95%) | 50 (68%) | 64 (88%) | 26/35 (74%) | – |
| Tesseract psm 3 / psm 11 + ranking | 45% / 38% | 25% / 22% | 29% / 27% | 29% / 29% | – |

"Read" for a VLM means its listed candidates contain the exact full string. For top-1/top-3 a pick is right when its core matches (PR #16's rule), so VLM top-1 can exceed read. Brackets are 95% Wilson intervals.

- **Label bias check.** The hand labels were made by Claude Opus 5.5, the same model as one engine. Yet **all 73 labels are reproduced exactly by at least one non-Claude reader** (GPT, Grok or docTR), so the confirmed subset is the full set. On the 8 photos where GPT-5.5 and Opus disagree, Opus is outvoted 4 times (m50–m52, m57: other models back the label) and GPT-5.5 4 times (m33, m38, m61, m66). Opus's score is not inflated by the labels.
- **What the misses are.** For the flagship models they are almost never misread characters; they are choices of identifier. Every GPT-5.5 and Opus miss had both plate identifiers read correctly, but the other one ranked first: the manufacturer serial against the utility number (m33, m38, m50–m52, m57), or the long barcode string against its 8-digit core (m61, m66). Character-level misreads occur only on cheap models: Grok 4.20 and Haiku read `50489940` for `50489240`, a secondary identifier, and Haiku also dropped a digit. So a **top-3 list with a one-tap choice** covers 97–100%.
- **Abstention.** Where the number is hidden (m35 under a sticker, m36 off frame), GPT-5.5, Opus and Grok 4.7 return null. GPT-5.4-mini, Grok 4.20 and Haiku return another string instead, the seal number or a model code. On the very faint Austrian plate (m83, no agreed label) Opus and Grok 4.7 give a confident, unverifiable number.
- **Agreement rules for auto-fill** (accept only when the readers' top-1 cores agree; PR #16's bar is precision ≥95%):

| Rule | Coverage | Precision | Wilson low |
|---|---|---|---|
| **GPT-5.5 + Opus 5.5** | 65/73 (89%) | 65/65 (100%) | 94.4% |
| GPT-5.5 + Grok 4.7 + Opus 5.5 | 62/73 (85%) | 62/62 (100%) | 94.2% |
| GPT-5.5 + Grok 4.7 (no Claude) | 68/73 (93%) | 65/68 (96%) | 87.8% |
| GPT-5.5 + PaddleOCR top-1 | 50/73 (68%) | 49/50 (98%) | 89.5% |
| GPT-5.5 + docTR top-1 | 43/73 (59%) | 42/43 (98%) | 87.9% |
| GPT-5.5 alone | 73/73 | 69/73 (95%) | 86.7% |
| Any flagship top-1 = decoded barcode | 10–11/73 | 100% | 72–74% |

  With 73 photos, even 100% observed precision can't clear a 95% lower bound until about 75 agreed photos. **GPT-5.5 + Opus with zero errors is as good as this data can show.** GPT-5.5 + Grok 4.7 misses on m38, m61 and m66, where both models chose the same wrong identifier, so the two readers aren't independent on "which identifier". Pair different providers only after checking this.

### 3.2 Class label (PR #16's plaintext labels)

| Engine | US CL (22) | All class labels incl. current ratings (68) | False CL320 claims (83) |
|---|---|---|---|
| GPT-5.5 / Opus 5.5 / Grok 4.7 / Grok 4.20 | 22/22 each | 66 / 68 / 66 / 63 | 0 |
| GPT-5.4-mini / Haiku 4.5 | 21 / 20 | 60 / 51 | 0 |
| docTR / PaddleOCR / Tesseract psm11 | 19 / 17 / 7 | 63 / 60 / 29 | 0 |
| Apple Vision (PR #16) | 20/22 | – | – |

**CL320 recall is untestable here: there is no CL320 plate in the data** (only CL200 and CL20). No engine ever turned a CL200 into CL320.

### 3.3 Meter can type and rust (83 photos, my coarse hand labels)

Labels are coarse because ring versus ringless is rarely visible in these close-ups: socket 22, bolt-in/plug-in (non-US A-base and DIN) 32, not visible 17, cabinet/cutout 7, multi-meter bank 5.

| Model | 5-way class | Family (round / bolt-in / not visible) | Bolt-in recall | Says rust (light/heavy) | Rust on 3 labelled rusty |
|---|---|---|---|---|---|
| Opus 5.5 | 61/83 (73%) | 78% | 28/32 | 29/83 | 2/3 |
| GPT-5.5 | 38/83 (46%) | 55% | 10/32 | 35/83 | 3/3 |
| Grok 4.7 | 36/83 (43%) | 53% | 6/32 | 38/83 | 3/3 |
| GPT-5.4-mini / Grok 4.20 / Haiku | 35–42% | 51–53% | 5–11/32 | 24–72/83 | 3/3 |

Models disagree wildly: GPT-5.4-mini and Grok 4.20 call about 50 of 83 "ringless", GPT-5.5 calls 6. Every model calls rust on 29–87% of photos where I see none at thumbnail scale. Opus's lead is partly the same-model labelling bias. **No-go**: Base's enclosure/mount criterion needs the full can and mount, i.e. the "meter surroundings" photo, not the close-up, plus US-specific labels.

### 3.4 Panels, PR #17 set (67 photos)

| Model | Brand right (19 legible) | Brand wrong | Brand named with none legible (43) | Main amps right (9) | Panel rating right (5) | Wrong amp claims | FPE caught (1) | False hazard alarms |
|---|---|---|---|---|---|---|---|---|
| **Opus 5.5** | 18 (95%) | 0 | 13 | 8 | 5 | 0 | 1 | 1 |
| **GPT-5.5** | 16 (84%) | 3 | 16 | 7 | 5 | 1 | 1 | 3 |
| **Grok 4.7** | 12 (63%) | 0 | 9 | 6 | 5 | 0 | 1 | **0** |
| Grok 4.20 NR | 16 (84%) | 3 | 32 | 6 | 4 | 14 | 1 | 2 |
| GPT-5.4-mini | 15 (79%) | 3 | 25 | 6 | 4 | 4 | 1 | 2 |
| Haiku 4.5 | 8 (42%) | 1 | 3 | 1 | 2 | 0 | 1 | 0 |
| docTR + keyword rule | 3 (16%) | 1 | 0 | 1 | 1 | 22 | 1 (reads "STAB-LOK") | 0 |
| Tesseract psm11 + keyword rule | 5 (26%) | 1 | 7 | 0 | 1 | 4 | 0 | 0 |

### 3.5 Panels, extra set (60 scored photos; no Claude, key out of credit)

| Model | Brand right (13 confirmed) | Brand wrong | Main amps right (3) | Hazard caught (4) | False hazard alarms (verified by eye) |
|---|---|---|---|---|---|
| GPT-5.5 | 11 | 0 | 3 | **4** | 1 (x0149) |
| Grok 4.7 | 10 | 0 | 3 | **4** | **0** |
| GPT-5.4-mini | 10 | 1 | 3 | **4** | 0 |
| Grok 4.20 NR | 10 | 1 | 2 | **4** | 3 (x0476, x0638, x0773) |
| docTR + keyword rule | 2 | 0 | 1 | 1 | 0 |
| Tesseract psm11 + keyword rule | 1 | 0 | 0 | 0 | 0 |

- **Hazard recall is 5/5 for all models across both sets** (p07, x0007, x0012, x0013, x0678). x0678 was not in any title; the models found it and I confirmed it by eye.
- **False hazard alarms come with invented evidence.**
  - Grok 4.20 reports `printed_text` "Federal Pacific / Stab-Lok" on a plug-fuse panel (x0476) and a Russian panel (p27).
  - GPT-5.5 read the handwriting "Chiller" on a Square D switch as "Challenger" (p48), called an unbranded panel FPE from breaker style (p78), and called a modern Eaton label "Challenger" (p20). Opus made the same call on p20; that label does print "Challenger" as a compatible breaker type.
- OCR plus a keyword rule catches the FPE photo from printed "STAB-LOK" text, but reads a brand on only 16–26% of legible panels, and its amp rule mostly catches branch-breaker numbers. So **a hazard flag should trigger review, never an automatic reject**, and the claimed text needs checking against the crop.
- **Brand naming without legible evidence is common**, mostly "Square D QO" on generic panels. Grok 4.7 does it least (9 + 7 across the two sets) and Grok 4.20 most (32 + 21). The `brand_evidence` field does not separate real from invented reads reliably.
- **Amperage:** GPT-5.5, Opus and Grok 4.7 read printed main and bus ratings well (7–8 of 9 mains, 5/5 ratings). Grok 4.20 invents "200" on 13 photos. Note that Pushmatic is not on Base's reject list; FPE, Challenger, Zinsco/GTE-Sylvania/Magnetrip are.

## 4. Runtime and cost (list prices, September 2026; no batch or caching)

| Engine | Median s/photo (p90) | Tokens in / out (meter) | $/meter photo | $/panel photo |
|---|---|---|---|---|
| GPT-5.5 ($5 / $30 per M) | 7.2 (9.1) | 3.2k / 0.66k | 0.036 | 0.029 |
| Opus 5.5 ($4 / $20) | 8.5 (11.6) | 4.3k / 0.68k | 0.031 | 0.024 |
| Grok 4.7 ($2 / $6) | 41 (78) | 3.9k / 0.13k* | 0.009* | 0.007* |
| Grok 4.20 NR ($1.25 / $2.50) | 2.1 (2.9) | 2.8k / 0.15k | 0.004 | 0.003 |
| GPT-5.4-mini ($0.75 / $4.50) | 1.4 (1.6) | 3.2k / 0.11k | 0.003 | 0.002 |
| Haiku 4.5 ($1 / $5) | 2.5 (4.3) | 2.0k / 0.20k | 0.003 | 0.003 |
| docTR (CPU) | 2.9 (idle, 4 photos) | – | ~0 | ~0 |
| PaddleOCR (CPU) | 35 (idle, 4 photos; 11–45) | – | ~0 | ~0 |
| Tesseract psm11 (CPU) | 2.3 (idle, 4 photos) | – | ~0 | ~0 |

\*xAI's reported completion tokens may exclude hidden reasoning; Grok 4.7's 41 s median suggests heavy reasoning, so its cost is a lower bound.

- Per house (1 meter + 2 panel photos): about **$0.09 for GPT-5.5**, and **$0.17 for GPT-5.5 + Opus** agreement.
- VLM wall times are single calls from a 4-vCPU cloud VM; photos are independent, so a house is one parallel round (about 10 s for GPT-5.5/Opus).
- OCR ran on the same 4 vCPUs with no GPU; the idle timings use 4 meter photos (m02, m30, m45, m70) at full resolution. Batch runs under contention were 2–5× slower. PaddleOCR on panels was dropped at the wrap-up (10/25 done, too slow). A GPU would speed up PaddleOCR and docTR detection but would not fix their top-1 problem, which is ranking, not reading.

## 5. Failure modes

1. **Wrong identifier, not wrong characters.** Multi-identifier plates (utility number, manufacturer serial, barcode string) cause every flagship miss. Two models from different providers often pick the same wrong one.
2. **Invented evidence on panels.** Cheap models, and occasionally GPT-5.5, report "printed" brand or hazard text that isn't there. Opus and Grok 4.7 do it least.
3. **Confident reads on faint plates** (m83) with no ground truth: agreement rules help, but can't prove correctness.
4. **Close-ups don't show the can.** Can type and mount are mostly invisible, and models guess rather than abstain. Rust is over-called.
5. **Server OCR reads the text but can't choose the identifier.** PaddleOCR and docTR read rates (95%, 92%) match Vision's 97%, but their top-1 under PR #16's ranking is 68% and 59%. PaddleOCR is about 12× slower than docTR on CPU (35 s against 2.9 s per photo). Tesseract fails on curved and glossy plates.
6. **Data gaps.** There's no CL320 plate, so CL320 recall is unknown. Only 5 real hazard panels exist, all old, and most extra-set photos are NC/ND.

## 6. Licence

- **VLM APIs:** commercial use under each provider's terms (OpenAI, Anthropic, xAI); photos leave the device. The Anthropic pooled key needs a top-up before any further Claude use.
- **OCR:** docTR Apache-2.0, PaddleOCR/PaddlePaddle Apache-2.0, Tesseract Apache-2.0, zxing-cpp Apache-2.0.
- **Photos:** meter photos are Commons CC/PD (per PR #16's manifest). PR #17's panels are CC BY / BY-SA / CC0 / PD. The extra set is mostly BY-NC-SA / BY-NC-ND, so it is for **internal evaluation only**: record URLs, don't redistribute.
- `labels/meter_numbers_hand.csv` and `raw/meter/*` hold plaintext meter numbers from public photos. They are fine in the store; **never copy them into the public repo**.

## 7. Going deeper

1. **CL320:** find real CL320 plates (Commons/Openverse "CL320", "320 A meter", Itron/Landis+Gyr 320 A self-contained). Without them the check is unmeasured.
2. **Auto-fill bar:** about 40–75 more labelled meter photos would show whether GPT-5.5 + Opus agreement clears a 95% lower bound. Test a prompt that returns *all* identifiers with their printed context (next to "No.", barcode, large print) and let a rule pick, since the failure is which identifier, not reading.
3. **Hazard detection:** a purpose-built set of 30–50 real FPE/Zinsco/Challenger panels, including breaker-only close-ups without labels, to measure recall and false alarms properly. Add a crop-verification step that re-asks the model to quote and locate the claimed text.
4. **Can and mount:** use Base's "meter surroundings" photo, with US labels (ringless / ring / meter-main / bank / pole) on US photos.
5. **Gemini:** needs the Vertex API enabled on `smp-bootstrap-admin` (external write; not done).
6. **Top up the Anthropic pooled key**, then run Opus/Haiku on the extra set.

## 8. Recommendation for the depth phase

**Take VLM-via-API forward as the server reader for meter number, class label and panel brand/amps/hazard, and stop investing in server OCR.**

1. **Meter number.** Use GPT-5.5 as the primary reader with a top-3 one-tap confirm; it covers 97% of photos. Add Opus 5.5 as the second reader and auto-fill only on agreement (100% precise at 89% coverage here). Extend the labelled set to about 150 photos to prove the 95% bar, and try the "list every identifier with its printed context" prompt.
2. **CL320.** Gather real CL320 plates first; this is the one Base criterion here with zero recall evidence.
3. **Panels.** Run the hazard flag (FPE / Zinsco / Challenger) with GPT-5.5 or Grok 4.7 plus a quote-and-locate verification pass, routing any flag to review. Build a 30–50 photo hazard set. Treat brand without printed evidence as a hint only.
4. **Can type, mount and rust.** Move these to the surroundings photo and the 3D tracks, with US-specific labels; close-ups can't answer them.
5. **Operational.** Top up the Anthropic key, enable Vertex for a Gemini comparison, and keep zxing barcode confirmation (free, 100% precise when it fires).

## Files (all in `internal/breadth/b2/`)

- **Code:** `fetch.py`, `engines.py`, `vlm.py`, `score_meter.py`, `analyze_meter.py` (bias check, miss categories, agreement rules), `score_can.py`, `score_panel.py` (`panel` / `extra`), `screen_panels.py`, `merge_raw.py`, and the vendored PR #16 `vendor/meter_eval/`.
- **Labels:** `meter_numbers_hand.csv`, `meter_can_condition_hand.csv`, `panel_hand.csv`, `panel_extra_hand.csv`.
- **Inputs:** `panels_extra.csv` (61 extra photos with licence, author and URL), plus the handoff inputs.
- **Results:** `results/meter_summary.json`, `meter_per_image.csv`, `meter_analysis.md`, `can_table.md`, `panel_table.md`, `extra_table.md`.
- **Raw outputs:** `raw/{meter,panel,extra}/*.jsonl`.
- **Reproduce:** `export B2_RAW=/tmp/b2raw` while runs are writing (store sync makes conflict copies of appended files), then `python merge_raw.py /tmp/b2raw raw`.
