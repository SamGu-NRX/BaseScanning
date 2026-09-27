# WS-07 object understanding

## Purpose

Recognise equipment, openings, and movable obstacles on a house walk, then place the ones that belong in the scene. Placement is ordinary geometry. Clearance numbers stay in the criteria rules.

## Owner WS and brief

WS-07. The brief on disk is the store copy `docs/build/ws-07-object-understanding.md` (final pass SD-43, SD-47, SD-54, SD-56). This page records the build.

## Inputs and outputs

Inputs: `packet_dir` as WS-03 hydrates it (`packet.json` plus the committed image files). A `frames` input and a sibling `normalized.json` are not the frame list. The 3D stage then reads `scene/scene.json` and `objects/objects.json` from its work dir. `run` and `run_3d` return `server.contracts.jobs.StageResult` with work-dir relative §3.9 keys.

Outputs:

- `objects/objects.json` (`hss-objects/v0`): 2D candidates plus optional `ground_type` votes. Each vote is `{image, material, confidence}`. Materials are the scene enum: `grass` becomes `lawn`, `concrete_or_paving` becomes `concrete`, `mulch_or_soil` becomes `mulch`, `gravel` and `deck` stay, `other` becomes `unknown`. `pavers`, `dirt`, `drive`, and `walkway` are kept only when the model names them.
- `objects/objects_3d.json` (`hss-objects-3d/v0`): `server.contracts.scene.SceneObject` records (the wire field is `class`). `fence`, `gate`, and `driveway` stay in `presence.json` and are not objects. `shrub`, `tree`, and `bin` are emitted. Profile blocking is left to the criteria engine. `electric_meter` is not emitted; the scene owns the meter anchor.
- Sidecars: `presence.json`, `frame_scores.json`, `detections.jsonl`, `report.json`, `vlm-cache/`. `run_3d` writes `objects/view_requests.json` and returns that key on `StageResult` (WS-09 reads it there).

## How to run on a fixture

The commands in `server/objects/README.md` use the committed scores and presence answers. `gate-eval` does not re-encode images. `lift-eval` scores the facade packet with clean truth poses. The mini-arkit command names `data/fixtures/packets/mini-arkit/packet.json` and does not search for a variant file.

`VISION_EXTERNAL_CALLS` defaults off (SD-67), including when `SMP_ENVIRONMENT=prod` and when WS-00 Settings has filled `on` because the variable was unset. An explicit `on` plus `OPENAI_API_KEY` and `XAI_API_KEY` is what authorizes a vendor call. Those are the only key names the client reads. `off` still writes `objects.json` from detector boxes, with confidence at most 0.5. Live tests (`pytest -m live`) skip unless the switch is on and both keys are set. They call GPT-5.5 and Grok 4.7 and refuse the next vendor call once planned spend would meet `HSS_OBJECTS_LIVE_MAX_USD` (default 3). This builder did not run them.

## How to test

```bash
uv run pytest server/objects/tests -q -m "not live"
uv run ruff check server/objects
```

`pytest server/objects/tests -m "not live"` on this build: 72 passed, 2 deselected (the live tests). Live tests were not executed.

## Rebuild from scratch

The package is `server/objects/`. Optional weights are the `objects` extra in `pyproject.toml` (`torch`, `transformers`, `open_clip_torch`, `openai`). Acceptance does not download them: class scores, selection, and presence answers are the committed JSON and JSONL files.

## Last gate table

Observed on this branch, cached answers only.

| Check | Bar | Observed |
|---|---|---|
| `pytest -m "not live"` | N ≥ 30, exit 0 | 72 passed, 2 deselected (`-m live` not run) |
| `class_scores` vs 20 cached embeddings | within 1e-4 | `test_class_scores_match_cached_embeddings` on `data/fixtures/objects/b3/emb20.json`. Text towers download with the `objects` extra |
| Selection, 11 houses | exact top-2 union | 166/1321 frames, matches `selection_top2_union.json` |
| OWLv2 boxes, 10 images | within 2 px and 0.01 score | `test_owlv2_boxes_match_b1_on_ten_images`. Weights download with the `objects` extra; the ten images stay in the eval store |
| CLIP cross-fit | 239/239 at margin 0.8 | 239/239 |
| SigLIP cross-fit | ≥ 237/239 at margin 0.5 | 237/239 |
| `filtered_frac` | ≥ 0.85 | 0.874 |
| Strict house recall | 11/11, false present ≤ 2/31 | 11/11, 2/31 |
| Lenient house recall | ≥ 14/18 | 14/18 |
| Planned mini + confirm | ≤ $0.20 / house | $0.168 |
| SigLIP frame cascade | meter 3/5, panel 2/2, gas 13/13, AC 9/9, pool 3/4 | same |
| Pair presence, n ≥ 3 | ≥ 0.8 | ac 9/9, door 20/20, downspout 11/11, meter 5/6, gas 13/13, hose bib 3/3, panel 2/2, meter can 4/4, pool 4/4 |
| `lift-eval` facade depth | ≥ 4/6, 0 false, median ≤ 0.5 m | 5/6, false_3d 0, median 0.328 m, match 0.8 m. Noise `nonlidar_modern` seeds 0–4: observed 0 |
| Facade `cli run` and mini-arkit packet | both stages / still routing | `cli check` ok, 54 objects, 0 observed openings (scene cameras are `DSC_*`, packet frames are `k#####`). mini-arkit writes scores for all 6 images; `s1`/`s2` are not selected |
| Budget | lower of stage cap and `vlm_budget_usd`; hazard first | `effective_budget` takes the min. Hazard confirms run before mini |
| Ground votes | scene enum | mapping above, plus explicit `pavers` / `dirt` / `drive` / `walkway` |
| Movable obstacles | emit the class only | `bin`, `shrub`, `tree` emitted with no `attrs`. `gate` is a sidecar |

## Inconsistencies

The brief still says `VISION_EXTERNAL_CALLS` defaults on in dev and is required in prod (missing raises). SD-67 supersedes that for this client: unset is off in every environment, and a WS-00 Settings value of `on` that was filled in because the variable was unset does not authorize a call. `server/common` is unchanged. The brief's live-test cap is $5; the handoff adds `HSS_OBJECTS_LIVE_MAX_USD` default 3, which the client enforces on the live-test tally only.

## Known limits

`run` reads keyframes and stills from `packet_dir/packet.json` (image paths relative to that directory, or `files[]` rows with role `keyframe`/`still` when the arrays are empty) and returns `objects/objects.json`. `run_3d` reads `scene/scene.json` and `objects/objects.json` from the stage work dir and returns `objects/objects_3d.json` and `objects/view_requests.json`. It casts boxes onto scene walls when keyframe ids match the 2D views; the facade GT scene uses `DSC_*` ids, so a `cli run` against that scene emits `location_unsure`. `lift-eval` places openings from the packet's clean poses instead. Class-score parity uses the committed `emb20.json` embeddings and downloads the text towers. OWLv2 parity downloads its weights and reads the ten eval images from the local breadth store when that store is present. The response cache is the reproducibility path. A missing vendor call degrades the stage. It does not fail it, except for a missing packet or a missing gate weight.
