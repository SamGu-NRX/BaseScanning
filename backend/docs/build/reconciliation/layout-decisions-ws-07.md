---
cursor:
  subagentId: "bc-daaca27d-97fb-5942-a61c-ddfbd6fff59a"
---

# Layout decisions: WS-07 object understanding

Every `[default chosen]` in `docs/build/ws-07-object-understanding.md`, with the reason and where the plan, code or contract was silent. Reconcile against `internal/build/layout-decisions-contracts.md` (WS-00) when WS-00's owner reviews. Second table: inconsistencies found while writing the brief (nothing was edited).

## Defaults chosen

| # | Decision | Where in the brief | Why (source silent or ambiguous) |
|---|---|---|---|
| 1 | Dependencies land as `pyproject.toml` optional group `objects` (`torch` CPU, `transformers`, `open_clip_torch`, `openai`, `scipy`) | §2 | WS-00 owns `pyproject.toml`; an extras group is the smallest edit that keeps `uv sync --frozen` working for other WS |
| 2 | `max_selected_frames = 20`; houses with ≤ 20 frames select everything | §3 config, §4 step 4 | B3 measured ≈ 17 frames per house for `top-2/class ∪ two gates`; the cap bounds cost when placement stills are added |
| 3 | GDINO-tiny at 0.35 stays the `window`/`door` proposer beside OWLv2 (`gdino_openings = True`) | §3 config, §4 step 5 | the plan's lift acceptance (4/6) was measured with GDINO-tiny boxes (B1 `lift3d.py`); OWLv2's facade run was 40/76 and unused |
| 4 | Object ids `obj_<class>_<n>`, never `meter` | §3 table | WS-06 owns the anchor `meter`; no id scheme exists in the contract |
| 5 | Sidecars `objects/{presence.json,detections.jsonl,view_requests.json,report.json}` next to `objects.json` | §3 | WS-00 §3.9 lists only `objects.json = list[Object]`; presence, view requests and cost have no other home |
| 6 | VLM-only extra classes `disconnect, basement_well, bin, hot_tub` | §4 step 1 | criteria need wells (B/C), disconnect (P1/P9), movable bins; WS-09's C1 map already takes `hot_tub → pool`; not in the plan's list |
| 7 | Detector prompt strings for the non-B1 classes (garage door, gas pressure regulator, gas pipe on a wall, basement window well, shrub, tree, trash bin, gate in a fence, wooden deck, outdoor stairs, roof eave overhang, porch roof, electrical disconnect switch box, hot tub) | §4 step 1 | untested classes (B1 dump §8); B3 `OWL_QUERIES` covers only water heater, vent, utility box |
| 8 | Gate embeddings may run in the early worker as frames commit; selection at finalize | §4 step 2, §12 | B3 timing (0.38 s/frame CPU) makes it free to hide during capture; plan only says "CLIP/SigLIP frame gate" |
| 9 | Stills with `purpose` in `meter_surround, meter_left, meter_right, adjacent_wall, behind_fence, house_overview` are always selected | §4 step 4 | Base's checklist frames are informative by design (C3 §2); B3 had no checklist stills to measure |
| 10 | Hazard frames (gas_meter, pool picks) and the escalation order are B3's; the `unsure+gate` escalation reuses the stored cross-fitted thresholds | §4 steps 3, 7 | B3 `cascade_eval.py --esc unsure+gate` default; thresholds otherwise unused |
| 11 | GPU OWLv2 budget ≤ 30 s per 150 frames | §4 step 5 | unmeasured on L4 (hosted-architecture bundles OWLv2 into ~2 GPU-min); CPU figures are B1's |
| 12 | OWLv2 seed threshold 0.3, support threshold 0.2 | §3 config, §12 | plan says 0.2, B1 says 0.2 over-fires and recommends ~0.3 as proposer; both kept with distinct roles |
| 13 | Grok 4.7 = second reader per decision-bearing object (best view, box prompt), ≤ 8 calls per house; geometry classes get no second read | §4 step 9 | Hunter's constraint (GPT-5.5 + Grok 4.7 agreement pair for object confirmation) vs B3's measured single-confirmer cascade; per-object calls keep the pair within the plan's $0.85 |
| 14 | Confidence ladder 0.9 / 0.75 / 0.5 / 0.3 and the WS-09 threshold 0.75 for pass/fail support | §3 table, §11 | contract defines `confidence` as recognition confidence only; no shared scale existed |
| 15 | Disagreement keeps the object at 0.5 (never drops a hazard class on one `no`) | §4 step 9 | safety direction: a missed gas meter can pass an unsafe spot |
| 16 | Grok's box joins `source.views` only at IoU ≥ 0.3 with the OWLv2 seed | §4 step 9, §12 | B1: VLM boxes are loose |
| 17 | Non-LiDAR wall casts are `inferred` with `plus_minus_m = max(0.8, spread)`; triangulation `max(1.0, spread)`; `agree_m = 0.5` | §4 step 10 | refined-pose lift untested (B1 0/6 under non-LiDAR drift; gate decision §8 item 7); 0.8 m = B1's match radius |
| 18 | `location_unsure` sentinel: `point` at anchor + `[0, 1.0, 2.0]` m, `plus_minus_m = 10.0`, `inferred`; WS-09 tests `≥ 9.99` | §4 step 10, §11 | schema requires a geometry; plan says "plus_minus_m wide → present, location unsure" without a value |
| 19 | Opening vertical bound `+0.2 m` | §3 table, §4 step 11 | B1: detector boxes include lintels/sills, ~0.2 m of the 0.35 m error |
| 20 | `registration_report.registered_fraction < 0.6` → every lift `inferred`, `plus_minus_m ≥ 1.0` | §4 step 16 | WS-04's walkaround gate is ≥ 60 % registered; below it poses are not trusted for placement |
| 21 | Planned per-call costs: mini $0.0011, GPT-5.5 presence $0.0167, GPT-5.5 boxes $0.054, Grok 4.7 $0.029 | §3 config | B3 §4 and B1 §4 measurements; Grok's presence-prompt cost was not measured, so its box-prompt cost is used |
| 22 | `gpt-5.5`: omit `temperature`, send `reasoning_effort: "low"`; mini and Grok get `temperature=0, seed=0` | §4 step 14, §12 | the user's determinism requirement vs a reasoning endpoint that rejects `temperature`; cache is the real guarantee |
| 23 | Cache under `captures/{id}/runs/{run}/objects/vlm-cache/<key>.json` (+ `.fixtures/vlm-cache/` locally), key = model + prompt sha256 + image sha256 + max_side + detail | §4 step 14 | plan asks for cached responses keyed by image hash; storage layout follows WS-00 §3.9 prefixes |
| 24 | `VISION_EXTERNAL_CALLS` default `on` in `SMP_ENVIRONMENT=dev`, required explicit in `prod` | §12 | Base has not decided on photos leaving to OpenAI/xAI; same env name as WS-09 |
| 25 | `OBJECTS_OWL_TILE_2X2=off` | §12 | B1 suggests tiling for tiny meters but never tested it |
| 26 | `early.meter_candidates(packet, top_k=3)` for WS-04's photo flow | §11 | WS-04 accepts `meter_candidates` but names no producer; plan's fragment policy needs "tap or detection" |
| 27 | Read `poses/cameras.json` (WS-04's file), geometry from `scene.keyframes[]` | §12 | WS-00 says `poses/poses.json`; WS-04 writes `cameras.json` |
| 28 | `run_presence` (steps 2–10) may start at finalize in parallel with `poses`; `run` waits for `scene.json` | §3, §12 | hosted-architecture runs object confirmation in parallel with SfM; WS-00 has one `objects` stage after `scene` |
| 29 | `OBJECTS_*` and `VISION_EXTERNAL_CALLS` read from `os.environ` in `ObjectsConfig` until WS-00 adds them to `Settings` | §12 | WS-00 `Settings` lists neither |
| 30 | Lift acceptance keeps B1's `MATCH_M = 0.8 m` and ROI; packet-depth (256 × 192) and oracle-depth numbers are both reported if the first misses 4/6 | §12, §9 item 8 | the plan's number came from 1024 px oracle depth |

## Inconsistencies found (reported, not edited)

| # | Where | What | How the brief handles it |
|---|---|---|---|
| 1 | Plan §6 WS-07 cascade: "crops → cheap tier (GPT-5.4-mini presence) → GPT-5.5 + Grok 4.7 confirmation on 5–10 selected frames" vs B3 §1 (final): no crops (panel crops lose FPE hazard recall; text crops cut identifiers), mini on the gate's top-2 frames per class with GPT-5.5 confirming, Grok 4.7 only on meter reads | B3 is later and measured; the brief follows B3 for frames, crops and tiers, and keeps the GPT-5.5 + Grok 4.7 pair per decision-bearing object (Hunter's constraint, gate decision header) | plan §6 WS-07 needs a rewrite of its cascade line |
| 2 | Plan §6 WS-07 "OWLv2 proposals (`run_detectors.py`, thr 0.2)" vs B1 §3/§7 "OWLv2 over-fires at 0.2; use ~0.3 as a proposer" | seed 0.3 / support 0.2 | plan should say 0.3 seed |
| 3 | Plan §6 WS-07 done-when "3D lift on facade fixture ≥ 4/6 openings with LiDAR stand-in depth" was measured with GDINO-tiny at 0.35 and 1024 px scan depth (B1 §3); OWLv2's facade run was 40/76 and "not used"; packets carry 256 × 192 depth | GDINO-tiny kept for openings; both depth sources reported | plan should name the detector and depth resolution |
| 4 | Plan §6 WS-07 "B3 prelim: margin 0.8 keeps 239/239 relevant frames, filters ~34 %" vs B3 §3.2/§5.2 final: per-frame thresholds filter only 33–50 %; the mechanism that works is per-house top-2/class selection (87 % filtered, 72 % sparse) | brief uses top-K selection; thresholds only for escalation | plan line is stale |
| 5 | Gate decision §3 VLM cost table ("confirmation 5–10 frames × ($0.054 + $0.029) ≈ $0.42–0.83; total $0.75–1.65 per house; B3 pending") vs B3 §4 ($2.32 → $0.30 per house, object presence $2.15 → $0.18) | brief cites B3 | gate decision §3 says B3 "will replace these figures"; it has |
| 6 | Hosted architecture §3 latency table "Object confirmation (VLM pair, 5–10 frames) p95 60 s" vs Grok 4.7 p90 78 s (B1, B3) | objects stage target ≤ 90 s | table's p95 is optimistic by Grok's tail |
| 7 | WS-00 §3.9 artifact `poses/poses.json` (+ `poses/registration.json`) vs WS-04 §3 `poses/cameras.json` + `registration_report.json` | brief reads WS-04's names | one of the two briefs must change |
| 8 | WS-00 `Settings` has no `VISION_EXTERNAL_CALLS` (WS-09 §3 uses it) and no `OBJECTS_*` knobs | `ObjectsConfig` reads `os.environ` | WS-00 should add `external_vision` and per-stage knobs |
| 9 | WS-00 §3.9: `objects.json = list[Object]` only | sidecars under `objects/` added | WS-00 table should list them |
| 10 | WS-09 §4 step 3 C1 class map has no `other` (water heater → P12 obstruction) and none of `bin, shrub, tree, gate, deck, stairs, eave, porch_roof` | brief hands `other/water_heater` to WS-09; the rest reach C2/C3 only | WS-09 map to be extended or the plan's class list trimmed |
| 11 | Scene v0 `object_class` enum lacks `water_heater` and `fence` while the plan's WS-07 class list includes `water_heater` and B1's `fence` | `other` + `label: water_heater`; fence as a wall-role hint | format doc §2.5 could add `water_heater` |
| 12 | B1 §1 "presence recall ≥ 0.8 on every class" holds for GPT-5.5 (≥ 0.83) but Grok 4.7 is 0.73 on downspout and 0.75 on pool (B1 image-level table); B3's presence prompt on the 81 labelled frames gives GPT-5.5 pool 3/4, hose bib 2/3 | acceptance pinned to the pair's cached answers and to B3's cascade row | plan's "≥ 0.8 per class" needs the model and prompt named |
| 13 | B1 §2 counts 48 labelled house frames; B3 §2 counts 46 walkaround frames from the same labels | none (evaluation bookkeeping) | minor; B3 excludes two frames it did not embed |
| 14 | Gate decision header: "cheap tiers from OpenAI or xAI only" and B3 tested Grok 4.20 NR as a cheap tier, but B3's recommended cascade uses only GPT-5.4-mini (OpenAI) | brief uses mini only | none needed |
