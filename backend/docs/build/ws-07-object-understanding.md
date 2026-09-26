# WS-07 builder brief: object understanding (cheap gate, OWLv2 proposals, GPT-5.5 / Grok 4.7 confirmation, 3D placement)

Store root `$S` = `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`. Plan: `$S/docs/server-pipeline-build-plan.md` §6 WS-07. Names align to `$S/docs/build/ws-00-skeleton-and-contracts.md` (WS-00) and its siblings `ws-04-pose-backbone.md`, `ws-09-criteria-engine.md`. Evidence: B1 `$S/internal/breadth/B1-detection.md` + `$S/internal/context-dumps/full/b1-detection.md`; B3 `$S/internal/breadth/B3-cheap-gate.md` (code `internal/breadth/b3-cheap-gate/`); C3 `$S/internal/breadth/C3-vlm-baseline.md`; gate decision `$S/docs/gate-decision.md` §3, §5.4. Every `[default chosen]` is listed in `$S/internal/build/layout-decisions-ws-07.md`; the reconciliation decisions applied on 2026-09-26 (two-stage split, artifact shapes, env names, Grok timeout) are logged in `$S/internal/build/decision-log-group-c.md`.

## 0. Card

- **WS-07 `object-understanding`.** Goal: for one run, find the objects Base's criteria depend on in the capture's images (CPU gate → OWLv2 proposals → GPT-5.4-mini screen → GPT-5.5 confirmation → Grok 4.7 second read on decision-bearing objects) as the `objects` stage right after `validate`, then place each one in the scene frame according to the capture tier (LiDAR depth lift; taps and wall-plane casts; `location_unsure` + a view request) as the `objects_3d` stage after `scene`, and write scene-v0 `Object` records with recognition confidence, a position bound, evidence image ids and boxes, at ≤ $0.85 of vision-API spend per house (seam decisions SD-06, SD-08).
- Builder: Grok 4.7. Validator: a separate agent (any model), never the builder.
- Inputs consumed: `packet/normalized.json` + packet files (WS-02; images, `*.depth.f32`, `*.confidence.u8`, taps); `poses/cameras.json`, `poses/registration_report.json` (WS-04); `scene/scene.json` with `walls`, `chain`, `ground`, `frame`, `keyframes[]` (WS-06); `Settings` (WS-00: `OPENAI_API_KEY`, `XAI_API_KEY`, `SMP_ENVIRONMENT`, `VISION_EXTERNAL_CALLS`). Outputs produced: `objects/objects.json` (2D detections, `hss-objects/v0`) → WS-06 scene builder; `objects/objects_3d.json` (`hss-objects-3d/v0`, `list[Object]` in the scene frame) → `server.scene.merge_objects` (WS-06, called by WS-03) → WS-09; sidecars `objects/{presence.json,frame_scores.json,detections.jsonl,view_requests.json,report.json}` → WS-09 (view requests, C3 photo ranking), WS-10 (reviewer crops), WS-11/12 (cost); `server.objects.early.meter_candidates` → WS-03/WS-04 (photo flow without a meter tap).
- Repo `huntertcarver/house-scanning-server`, branch `ws-07/object-understanding`, one PR.

## 1. Start gate

Stop and report if any item is missing.

- WS-00 PRs `ws-00/contracts`, `ws-00/skeleton`, `ws-00/fixtures` merged: `server/contracts/scene.py` (`Object`, `Source`, `Scene`), `server/contracts/jobs.py` (`StageContext` with `vlm_budget_usd` and `heartbeat`, `StageResult`, `StageName` includes `objects` **and** `objects_3d`, SD-08), `server/common/{config,storage,geometry,logging}.py` (`Settings.VISION_EXTERNAL_CALLS`, SD-11), `server/eval/**`, the stub `server/objects/__init__.py` (`run`, `run_3d`), `make validate` green, `data/fixtures/packets/eth3d-facade/packet.arkit-lidar.json`, `data/fixtures/packets/mini-arkit/packet.json` (SD-29: 4 keyframes, stills `s1` `meter_close`, `s2` `meter_oblique`, meter tap) and `data/fixtures/scenes/eth3d-facade/gt-laser/scene.json` present.
- `make fixtures ONLY=all` done: `.fixtures/packets/eth3d-facade.tar` (93 MB, 76 keyframes, laser-rendered 256 × 192 LiDAR depth + confidence per keyframe), `.fixtures/packets/eth3d-electro.tar` and `.fixtures/packets/yt-12Ks1idWj40.tar` (6 `photo_only` stills, SD-29) extracted.
- Store readable with: `internal/breadth/b1-detection/{code,labels,results,eval_images_batch1.json,facade76_images.json}`, `internal/breadth/b3-cheap-gate/{*.py,images.json,subset.txt,results/}`, `internal/data/houses/<id>/{frames,house.json}` for the 11 B3 houses, `internal/packet-generator/packetgen/noise.py` (drift profiles, tests only).
- Machine: Python 3.12, `uv`, 4 CPU cores, 8 GB RAM, ~3 GB disk for weights (`HF_HOME`), internet to Hugging Face. GPU optional (OWLv2 on 150 frames). `OPENAI_API_KEY` + `XAI_API_KEY` only for the two live-call tests in §8 (≤ $5 total); no Anthropic key is ever read.
- WS-04 and WS-06 outputs are **not** required to start: the facade GT scene carries 76 laser-registered `keyframes[]` in the scene frame (B1's "clean poses" case) and the `arkit-lidar` packet carries the depth files; the lift acceptance runs on those (§6). The `objects` stage (steps 2–9) needs only `packet/normalized.json`; `objects_3d` (steps 10–11) needs `scene/scene.json`.

## 2. Files you own / must not touch

Create or modify: `server/objects/{__init__,run,config,gate,select,proposals,prompts,vlm,confirm,depth,lift,emit,early,cli}.py` (`__init__` re-exports `run` for stage `objects` and `run_3d` for stage `objects_3d`, SD-24), `server/objects/tests/**`, `server/objects/README.md`, `data/fixtures/objects/**` (every file < 5 MB), `docs/components/ws-07-object-understanding.md`, and in `pyproject.toml` only the new optional-dependency group `objects` (`torch` CPU wheel, `transformers`, `open_clip_torch`, `openai`, `scipy`) `[default chosen]`.

Must not touch: `server/contracts/**`, `server/common/**` (a new `Settings` field goes through a spec PR to WS-00, §12), `server/eval/**`, `server/{scene,criteria,reads,poses,scale,dense,coverage,result,api,orchestrator}/**`, `docs/*.md` contracts, schemas, CI config, `internal/**`, `internal/criteria/SSR_Prompts.md` (never copy any of its text), `internal/breadth/b2/**` (meter numbers).

## 3. Interfaces

Units: metres in the scene frame (`meter_anchored/v0`), continuous pixels of the **stored** image with (0, 0) at the top-left corner (keyframes are never rotated, spec §5 rule 1; stills are loaded with `PIL.ImageOps.exif_transpose`, as B1 `common.load_image`), confidence 0–1, USD, seconds. Detections in `detections.jsonl` use normalised `[x0, y0, x1, y1]` in [0, 1] of that image (B1 convention); `Source.views[].box_px` uses `[u0, v0, u1, v1]` pixels. Poses read from `scene.keyframes[]` are camera-to-scene, ARKit camera axes; `server.common.geometry.pixel_ray_arkit` / `server.eval.frame.{ray_arkit, project_arkit, apply}` do the maths.

Read from `NormalizedPacket` (WS-02): `session.tier`, `keyframes[] {id, img, w, h, intrinsics, pose, epoch, depth?{file, confidenceFile, w, h}, purpose?}`, `stills[] {id, purpose, img, w, h, orientation, keyframe?}`, `taps[] {id, label, keyframe, pixel, hit?{position, target}}`, `root`. From WS-04: `poses/cameras.json` (`{image, keyframe_id, epoch, pose, intrinsics, w, h, pose_source}` per registered frame) and `registration_report.json` (`fragments[]`, `source_frame`, `meter.frames`). From WS-06 `scene.json`: `frame.{anchor, source_frame, from_source}`, `walls[] {id, role, baseline_m, height_m, plus_minus_m, evidence, observed_fraction?}`, `chain`, `ground.surface`, `keyframes[] {id, image, pose, intrinsics, w, h}`, existing `objects[].id` (WS-06 writes the anchor `meter`).

```python
# server/objects/config.py  (per-area knobs HSS_OBJECTS_*, SD-11; shared switch from WS-00 Settings)
@dataclass(frozen=True)
class ObjectsConfig:
    external_vision: bool = True            # Settings.VISION_EXTERNAL_CALLS=on|off (WS-00; default on in dev, required in prod)
    budget_usd: float = 0.85                # HSS_OBJECTS_VLM_BUDGET_USD_PER_HOUSE (plan §6 WS-07); ctx.vlm_budget_usd caps it lower
    gate_models: tuple[str, ...] = ("clip", "siglip")   # HSS_OBJECTS_GATE_MODELS
    top_k_per_class: int = 2                # HSS_OBJECTS_GATE_TOP_K (B3 recommended)
    max_selected_frames: int = 20           # HSS_OBJECTS_MAX_SELECTED [default chosen]
    proposer_seed_thr: float = 0.3          # HSS_OBJECTS_OWL_SEED_THR (B1 §7)
    proposer_support_thr: float = 0.2       # HSS_OBJECTS_OWL_SUPPORT_THR (plan: OWLv2 thr 0.2)
    gdino_openings: bool = True             # HSS_OBJECTS_GDINO_OPENINGS: GDINO-tiny for window/door at 0.35 [default chosen]
    second_reader: str = "grok-4.7"         # HSS_OBJECTS_SECOND_READER=grok-4.7|off
    max_side_px: int = 1024                 # detectors and presence calls (B1/B3 setting)
    openai_timeout_s: float = 90.0          # per gpt-5.4-mini / gpt-5.5 call; 3 tries
    grok_timeout_s: float = 120.0           # per grok-4.7 call; 3 tries (measured p90 78 s; reconciled with WS-08, hosted-architecture updated)
    cache_dir: str = "objects/vlm-cache"    # under the run's storage prefix
    device: str = "auto"                    # cuda|cpu|auto
    seed: int = 0
MODELS = {"cheap": "gpt-5.4-mini", "confirm": "gpt-5.5", "second": "grok-4.7"}   # xAI base_url https://api.x.ai/v1
PLANNED_USD = {"gpt-5.4-mini": 0.0011, "gpt-5.5": 0.0167, "gpt-5.5:boxes": 0.054, "grok-4.7": 0.029}  # B3 §4, B1 §4 [default chosen]
```

```python
# server/objects/gate.py ... select.py ... proposals.py
def embed_frames(frames: list[FrameRef], model: Literal["clip", "siglip"], *, max_side: int = 1024) -> dict[str, np.ndarray]
def class_scores(emb: dict[str, np.ndarray], model: str, prompts: dict[str, list[str]] = GATE_PROMPTS) -> dict[str, dict[str, float]]
def select_frames(scores: dict[str, dict[str, dict[str, float]]], order: list[str], *, k: int, min_gap: int = 2,
                  always: list[str] = ()) -> FrameSelection      # .frames, .hazard_frames (gas_meter/pool picks), .filtered_frac
def run_owlv2(frames: list[FrameRef], *, queries: dict[str, str] = OWL_QUERIES, store_thr: float = 0.08) -> dict[str, list[Detection]]
def run_gdino(frames: list[FrameRef], *, classes=("window", "door"), thr: float = 0.35) -> dict[str, list[Detection]]
# server/objects/vlm.py ... confirm.py
class VisionClient:  # OpenAI SDK for both vendors; temperature 0 where accepted; response cache keyed by cache_key()
    def presence(self, model: str, image: Path, prompt: PromptSpec) -> PresenceAnswer   # present{cls: yes|no|unsure}, ground_type, quality
    def boxes(self, model: str, image: Path, prompt: PromptSpec) -> BoxAnswer           # objects[{label, box, confidence}], present{}, ground_type
def cache_key(model: str, prompt_sha256: str, image_sha256: str, max_side: int, detail: str) -> str
def run_cascade(sel: FrameSelection, client: VisionClient, budget: Budget) -> CascadeResult   # steps 6-10 of §4
def agreement(gpt: Literal["yes","no","unsure"], grok: Literal["yes","no","unsure","unavailable"]) -> tuple[str, float]
# server/objects/depth.py ... lift.py ... emit.py ... run.py
def load_lidar(frame: FrameRef) -> tuple[np.ndarray, np.ndarray] | None              # port of housepipe.lidar.read_depth
def lift_depth(dets, cams, depths, *, pct: int = 30, radius_m: float = 0.6, min_views: int = 2) -> list[Cluster]
def lift_wall_cast(dets, cams, scene, *, agree_m: float = 0.5) -> list[Cluster]
def lift_triangulate(dets, cams, *, max_gap: int = 6, epi_px: float = 25, vote_min: int = 3, reproj_px: float = 30) -> list[Cluster]
def place_taps(taps, cams_by_frame, depths, scene) -> list[dict]
def to_objects(clusters, presence, tier: str, scene: dict, cfg: ObjectsConfig) -> tuple[list[dict], list[dict]]   # objects, view_requests
def run(ctx: StageContext) -> StageResult      # stage `objects` (2D, steps 2-9) right after validate, parallel with poses/reads (SD-08)
def run_3d(ctx: StageContext) -> StageResult   # stage `objects_3d` (steps 10-11) after scene; optional, CPU, 120 s budget (SD-08)
```

`FrameRef {id, image: Path, w, h, kind: keyframe|still, purpose?, epoch?, sha256}`; `Detection {label, score, box}` (normalised xyxy); `Cluster {cls, pos_m, views[{frame, box, score}], spread_m, method}`. `python -m server.objects.cli run --packet DIR/packet.json --scene S/scene.json --cameras poses/cameras.json --out OUT [--stage objects|objects_3d|both] [--no-external] [--tier-override arkit|photo_only]` (default `both`, in order), `cli gate-eval`, `cli lift-eval`, `cli check OUT/objects_3d.json --scene S/scene.json` (validates each `Object` with the contract model, merges into a copy of the scene, runs `server.eval.cli validate`).

Outputs (SD-06). Stage `objects` writes `objects/objects.json` = `{"format": "hss-objects/v0", "objects": [{id, class, label?, confidence, views: [{image, box_px, score}], attrs?}]}`: the 2D detections after the cascade (steps 2–9), one row per candidate object, `views[]` = every supporting frame with its pixel box and score, `class` from the table in §4 step 1; WS-06's scene builder reads it for opening and equipment boxes, and `presence.json` for the `fence`/`driveway` hints and `ground_type` votes. Stage `objects_3d` writes `objects/objects_3d.json` = `{"format": "hss-objects-3d/v0", "objects": list[Object]}` of scene-v0 `Object` dicts in the scene frame; the coordinator hands it to `server.scene.merge_objects(scene_path, objects_3d_path)`. `Object` field rules:

| Field | Rule |
|---|---|
| `id` | `obj_<class>_<n>` (`n` from 1 per class) `[default chosen]`; never `meter` (WS-06 owns the anchor); ids unique against `scene.objects[]` |
| `class` / `label` | scene enum via the class table (§4 step 1); `label` = the detector query or VLM label text; water heater → `class: other, label: water_heater` |
| `geometry` | `wall_rect` for wall-mounted classes when a wall is assigned (openings: `s_m` from box left/right rays, `bottom_m`/`top_m` from box bottom/top); `point` otherwise; `box` only for `ac_unit`/`pool` from LiDAR clusters (`size_m` from the cluster extent, `yaw_deg` 0) |
| `confidence` | recognition only `[default chosen]`: 0.9 both readers `yes`; 0.75 GPT-5.5 `yes` (class with no second reader); 0.5 one reader or readers disagree; 0.3 mini-only or OWLv2-only. WS-09 treats < 0.75 as review-only evidence (§11) |
| `plus_minus_m` | a position bound from geometry, never from confidence: LiDAR cluster `max(0.35, spread_m)`; tap on LiDAR depth `0.10`; tap ray-cast on a wall `max(0.15, 3 × wall fit rms) + wall.plus_minus_m`; wall cast from ≥ 2 views `max(0.8, spread_m)`; triangulation `max(1.0, spread_m)`; `location_unsure` sentinel `10.0`; openings add `+0.2` vertical (B1: lintel/sill bias) recorded in the sidecar; all values `[default chosen]` except the B1-measured 0.35 |
| `source` | `{kind: detector|vlm|tap|lidar, model: "owlv2-base+gpt-5.5+grok-4.7" style string, views[]: every supporting view {image: frame id, box_px, score}}` — the evidence image ids WS-10 renders |
| `attrs` | absent (unknown); WS-09's C3 fills `operable/ground_level/well/movable` |
| `evidence` | `observed` only for LiDAR depth clusters with ≥ 2 views and for taps (LiDAR depth or ray-cast onto an `observed` wall); everything else `inferred` (fail or unsure only, never pass) |

Sidecars `[default chosen]`: `objects/presence.json` `{frames: {frame_id: {cheap: {...}, confirm: {...}|null, second: {...}|null, ground_type, quality}}, class_summary: {class: {state: present|unknown, frames_yes: [...], best_frame}}}` (WS-07 never asserts absence, B1 dump §7); `objects/frame_scores.json` `{frame_id: {clip: {class: score}, siglip: {class: score}}}` (the step 3 class scores; WS-09's C3 ranks photos with it); `objects/detections.jsonl` one row per frame `{id, model, seconds, size, dets[]}` (B1 `run_detectors` shape); `objects/view_requests.json` `[{object_id, class, kind: "region", wall_id?, s_m?, target, why, reference: {image, box_px}}]` for WS-09's `views_needed`; `objects/report.json` gate/cascade/cost numbers (§4 step 15). Errors `ObjectsError(code)`: `inputs_missing:<name>`, `gate_model_missing`, `vlm_unavailable`, `vlm_parse_error`, `budget_exhausted`, `no_depth_for_lift`, `pose_quality_low`, `single_view_object`, `external_vision_disabled` (B1 dump §7 codes). Only `inputs_missing` and `gate_model_missing` fail a stage; the rest degrade and are listed in `StageResult.warnings`. Both stages are optional in the graph (SD-08): a failed `objects` leaves `scene` without detections and `objects_3d` skipped; a failed `objects_3d` leaves the scene unmerged and `criteria` runs on WS-06's anchor and openings only.

## 4. Behaviour spec

1. **Class list** (plan §6 WS-07 = B1's 12 + missing criteria classes; VLM-only extras `[default chosen]`: `disconnect, basement_well, bin, hot_tub`). Detector prompt strings for the 12 B1 classes are `internal/breadth/b1-detection/code/common.py::CLASSES`; B3 `gate_features.OWL_QUERIES` adds "water heater", "vent on a wall", "utility box on a wall"; the remaining prompts are `[default chosen]` and untested.

| WS-07 class → scene `class` | Criterion served (`docs/criteria-spec.md`, `docs/scene-and-verdict-format.md` §5) | Detector query | Tier (§4 step 9) |
|---|---|---|---|
| `electric_meter → meter` (views only; anchor is WS-06's) | anchor of every distance; P2–P4, P10–P12; `meter_working_space`, `route_length` | electric meter | decision |
| `meter_can → meter_can` | P2 `oversized_can`, P11 `meter_type`; scale anchor (WS-05) | electric meter socket box | decision |
| `main_panel → panel`, `disconnect → disconnect` | P1/P9/P12/P13; `wall_equipment_above` (C: never under a wall-mounted electrical box) | electrical breaker panel; electrical disconnect switch box | decision |
| `gas_meter`, `gas_regulator`, `gas_pipe` (same names) | `gas_clearance` 5/5/3 ft; P12 review when ≤ 3 ft from the meter | gas meter; gas pressure regulator; gas pipe on a wall | decision, hazard |
| `ac_condenser → ac_unit` | `ac_clearance` (fixed footprint); P12 `meter_not_accessible` (`yes` = reject) | air conditioner condenser unit | decision |
| `water_heater → other` (`label: water_heater`) | P12 fixed obstruction; garage cue | water heater | decision |
| `wall_box`, `vent` | `wall_equipment_above` (C: never directly under a comms box or vent) | utility box on a wall; vent on a wall | decision |
| `window`, `door`, `garage_door`, `basement_well` | `opening_clearance` (B: 3 ft each side; C: not under/blocking), `route_path` (A: windows block; garage prohibited; over-door B never / C review) | window; door; garage door; basement window well | geometry |
| `downspout`, `hose_bib` | non-blocking (B/C); informational for the reviewer | downspout; outdoor water faucet | geometry |
| `pool`, `hot_tub` | `pool_clearance` 5/5/10 ft; pool equipment tells → review (C) | swimming pool; hot tub | decision, hazard |
| `fence` (presence only → `walls[].role` hint for WS-06), `gate` | `facing_gap`, `wall_backing` (fence-only fails), `requires_member_action` | fence; gate in a fence | sidecar |
| `driveway` (presence + `ground_type` votes → WS-06 region `drive`) | `drive_clearance`, `route_path` (driveway crossing prohibited) | driveway | sidecar |
| `shrub`, `tree`, `bin` | movable for placement (B/C) but fixed P12 obstruction (conflict #12) | shrub; tree; trash bin | geometry |
| `deck`, `stairs`, `eave`, `porch_roof` | `headroom` (< 6 ft fail, 6–7 review), `ground_surface` (decks fail), meter access | wooden deck; outdoor stairs; roof eave overhang; porch roof | geometry |

2. **Gate embeddings** (`gate.py`, port of B3 `gate_features.{Clip,Siglip,load}`): CLIP ViT-B/32 `laion2b_s34b_b79k` (open_clip) and SigLIP2 `google/siglip2-base-patch16-256` (transformers), image long side 1024 px, L2-normalised fp16 embeddings per frame, `torch.set_num_threads(2)`. Measured 0.08 s + 0.30 s per frame on one CPU core (B3 §4). Runs on every keyframe and still; WS-03 may run it in the early worker as files commit `[default chosen]`. OWLv2 is **not** a gate: B3 §3.2 measured it no better (237/239 must-keep recall, 45–50 % filtered) at 3.85 s/frame, ~13× SigLIP.
3. **Zero-shot class scores** (port of B3 `gate_eval.{PROMPTS, text_emb, embedding_scores}`): text prompts per class from `gate_eval.PROMPTS` (meter 3, panel 4, gas_meter 2, ac_condenser 2, pool 1, water_heater 1, plus the `other` negatives). CLIP: `softmax(logit_scale · sims)` over all prompts, class score = sum of its prompts. SigLIP: `sigmoid(logit_scale · sims + logit_bias)`, class score = max over its prompts. Cross-fitted per-frame thresholds keep 239/239 (CLIP, margin 0.8) or 237/239 (SigLIP, margin 0.5) must-keep frames but filter only 35 % / 33 % of walkaround frames (B3 §3.2, `results/gate_eval_all.json`), so thresholds are **not** used for filtering: they are stored in `report.json` and used only for the escalation flag in step 7.
4. **Frame selection** (port of B3 `select_eval.select`): for each gate and each equipment class (`meter, panel, gas_meter, ac_condenser, pool`) rank the house's frames by class score and take the top `k = 2`, skipping a frame within 2 positions (capture order) of one already picked for that class; `S` = union over both gates and all classes; gas_meter and pool picks are `hazard_frames`. Add stills with `purpose` in `meter_surround, meter_left, meter_right, adjacent_wall, behind_fence, house_overview` `[default chosen]`; cap `S` at 20 frames by dropping the lowest-ranked non-hazard extras `[default chosen]`; houses with ≤ 20 frames select everything. Stills with `purpose` in `meter_close, meter_oblique, panel_close, panel_open, main_disconnect, panel_surround` are WS-08's and never enter `S`. Every frame outside `S` never reaches a VLM: B3 measured 87 % of walkaround frames filtered on the 11 houses (1,321 frames, mean 120 per house) and 72 % on a 3× sparser capture, with house-level recall 11/11 strict, 14/18 lenient and 2/31 false-present against GPT-5.5 on every frame at 15/18 and 6/31 (B3 §3.2 table, `results/select_eval_all_strict.json`). Record `filtered_frac`.
5. **Proposals** (`proposals.py`, port of B1 `run_detectors.{Owl,GDino,norm_box}`): OWLv2 `google/owlv2-base-patch16-ensemble` on every registered keyframe and every still at long side 1024, all queries of step 1 in one pass, `post_process_grounded_object_detection(threshold=0.04, target_sizes=[(side, side)])` with `side = max(w, h)` (boxes are in padded-square pixels), renormalise by the real `w, h`, store detections with score ≥ 0.08, same-class NMS at IoU 0.7 (B1 `evaluate.nms_same_class`). GDINO-tiny `IDEA-Research/grounding-dino-tiny` for `window`/`door` at 0.35 with B1's per-class token-span scoring (the stock post-processing mislabels overlapping phrases) `[default chosen]`. Frames not registered by WS-04 get proposals for presence only, never for the lift. Runtime CPU: OWLv2 2.0–2.6 s, GDINO 3.5 s per frame (B1 §4); GPU (L4): unmeasured, budget ≤ 30 s per 150 frames `[default chosen]`.
6. **Cheap presence** (`confirm.py`, port of B3 `vlm_presence`): `gpt-5.4-mini` on every frame in `S`, full frame (no crops, B3 §1: crops cut fine print and cost hazard recall), long side 1024, `detail: high`, prompt `PROMPT_PRESENCE_V1` = B3 `vlm_presence.PROMPT` with the class list of step 1 (our wording, never SSR text; reply JSON `{present: {class: yes|no|unsure}, readable_equipment_text, quality}`), parsed with the `{...}` regex, 3 tries with `sleep(3 · attempt)` (B3), 90 s timeout. Planned cost $0.0011/frame, median 0.97 s (B3 `cascade_siglip.json`).
7. **Confirmation** (`gpt-5.5`, same prompt and settings): on every frame in `S` where mini answered `yes` or `unsure` for any decision class, and on **every** `hazard_frame` regardless of mini (mini's `no` on gas meters is 5/13 at frame level, B3 §5 item 4), plus frames where a gate class score is above its stored threshold while mini said `no` (`unsure+gate` escalation, B3 `cascade_eval.py` default) `[default chosen]`. GPT-5.5's answer is final for the frame; mini's stands where GPT-5.5 was not asked. Planned $0.0167/frame, median 4.4 s; ≈ 8 frames per house (B3 §4).
8. **Candidate objects**: for each class with ≥ 1 confirmed `yes` frame, seed boxes = that class's OWLv2 (or GDINO) boxes with score ≥ 0.3 in confirmed frames; support boxes = score ≥ 0.2 in any registered frame; association across frames is done by the lift (step 10), never by 2D IoU alone. A class with a `yes` but no box in any frame becomes one candidate with its best frame and no box (Grok's box in step 9 fills it).
9. **Second reader** (`grok-4.7`, xAI `base_url https://api.x.ai/v1`, OpenAI SDK): for each candidate of a decision class (table step 1), one call on its best view (highest OWLv2 seed score, else highest gate score) with `PROMPT_BOXES_V1` = B1 `run_vlm.PROMPT` with the class list of step 1 (`box_2d [ymin, xmin, ymax, xmax]` in 0–1000, `present{}`, `ground_type`); ≤ 8 calls per house `[default chosen]`, run in parallel (Grok 4.7 median 41–54 s, p90 78 s: B1 §4, gate decision §3), 120 s timeout (`grok_timeout_s`; same figure as WS-08), 3 tries. Geometry classes get no second read. **Agreement rule** (`agreement()`): both `yes` → confidence 0.9; GPT-5.5 `yes` + Grok `no|unsure|unavailable` → 0.5 (`disagree`, object kept: dropping a hazard object is the unsafe direction); GPT-5.5 `unsure` + Grok `yes` → 0.5; both `no` → candidate dropped and logged; `second_reader == off` → 0.75 `[default chosen]`. Grok's box is added to `source.views` when its IoU with the seed box ≥ 0.3 `[default chosen]`, else recorded in the sidecar only (VLM boxes are loose, B1 §5).
10. **3D placement per tier** (stage `objects_3d`, `run_3d`, after `scene`; `lift.py`, ports of B1 `lift3d.{lift_depth, cluster, lift_triangulate}` and `housepipe.lidar.{_sample, _unproject, place_taps}`; cameras = `scene.keyframes[]`; reads `objects/objects.json` and `presence.json` from the `objects` stage of the same run, never re-runs a VLM call):
   - `arkit_lidar`: for every box in a frame with LiDAR depth, depth = 30th percentile of confidence ≥ 1 samples in the box's central half (0.2 m < z ≤ 5 m); back-project the box centre; greedy per-class clustering, radius 0.6 m, ≥ 2 views → `observed`, `plus_minus_m = max(0.35, spread)`. B1 measured 4/6 facade openings, 0 false, 0.35 m 3D / 0.25 m horizontal median with clean poses, 4/6 at 0.52 m under `lidar_modern` drift. Taps: `place_taps` on LiDAR depth (`observed`, ±0.10 m), else `hit.position` when `hit.target == sceneDepth` and the tap's epoch is the scene's `source_frame` epoch.
   - `arkit` (no LiDAR): taps first (`meter, gas_meter, ac_unit, door_corner, window_corner, fence, driveway_edge`): ray-cast the tap pixel onto the nearest chain wall plane in front of the camera (`server.eval.extract.extract_scene`'s `cast` logic; `t > 0.3 m`); `observed` when the wall is `observed`. Non-tapped wall-mounted classes: cast the box centre and box edges of every supporting view onto the wall planes; views agreeing within 0.5 m (`agree_m`) form one object → `wall_rect`, `inferred`, `plus_minus_m = max(0.8, spread)` `[default chosen]` (refined-pose lift is untested: B1 0/6 under `nonlidar_modern` raw ARKit drift, gate decision §8 item 7). Free-standing classes (`ac_unit, pool, hot_tub, shrub, tree, bin, water heater`): `lift_triangulate` on the refined poses (`max_gap 6, epi_px 25, vote_min 3, reproj_px 30`; B1 clean poses 3/6 at 0.59 m) → `point`, `inferred`, `plus_minus_m = max(1.0, spread)`. Anything with < 2 supporting registered views → `location_unsure`.
   - `photo_only`: as `arkit` using WS-04's image-only poses; frames outside the primary fragment count as unregistered. Objects that end `location_unsure` are still emitted (present, place unknown): `point` at the anchor + `[0, 1.0, 2.0]` m `[default chosen]`, `plus_minus_m = 10.0` (the `location_unsure` sentinel WS-09 tests with `>= 9.99`), `inferred`, and one `view_requests.json` entry `{kind: region, target: "photo of the <class> from 2-4 m, head-on, with the wall around it", reference: best view + box}` (WS-09 turns it into `views_needed`; WS-10 attaches the reference crop in the photo flow).
11. **Openings**: `window/door/garage_door/basement_well` boxes lifted onto the assigned wall; `s_m` from the left/right box edges' wall intersections, `bottom_m/top_m` from bottom/top edges; vertical bound `+0.2 m` (detector boxes include lintels and sills, B1 §3); `attrs` absent, so every window counts as an opening (contract §2.5). GDINO and OWLv2 boxes of the same opening are merged by their 3D cast (agree_m 0.5).
12. **Ground type and fence**: per selected frame, the VLM `ground_type` (B1 enum) is written to `presence.json`; mapping to scene materials `grass → lawn, concrete_or_paving → concrete|pavers, mulch_or_soil → mulch|dirt, gravel → gravel, deck → deck, other → unknown` (B1 dump §7) is WS-06's to apply. `fence` and `driveway` presence go to `class_summary` only.
13. **Budget and degradation** (`Budget`): running tally of planned + measured `cost_usd` (xAI returns `usage.cost_in_usd_ticks / 1e10`; OpenAI from tokens × list price as in B3 `PRICES`). Spend order: (a) GPT-5.5 on `hazard_frames`, (b) mini on `S`, (c) GPT-5.5 confirms, (d) Grok second reads, (e) nothing else. Before each call, if `tally + planned ≥ budget_usd` skip the remaining stages in reverse order (e → d → c → b, never a); every skipped stage is a warning `budget_exhausted:<stage>`, affected objects get confidence ≤ 0.5 (review-only downstream), and `report.json` records `skipped_stages`. `external_vision == False` (`Settings.VISION_EXTERNAL_CALLS=off`): steps 6, 7, 9 are skipped, objects come from OWLv2 only (`source.kind: detector`, confidence 0.3–0.5), `class_summary` states are all `unknown`, warning `external_vision_disabled`; the pipeline still completes. `StageContext.vlm_budget_usd` (SD-17), when lower than `budget_usd`, is the cap; every call is logged with the WS-00 keys `vlm_provider, vlm_model, tokens_in, tokens_out, vlm_cost_usd` (SD-13).
14. **Determinism**: `temperature=0` and `seed=0` on every call where the endpoint accepts them (`gpt-5.4-mini`, `grok-4.7`); `gpt-5.5` rejects `temperature`, so omit it and send `reasoning_effort: "low"` `[default chosen]`; prompts are frozen constants with a `PROMPT_*_V1` version and sha256 recorded per answer; every answer is cached at `cache_key(model, prompt_sha256, image_sha256, max_side, detail)` in `Storage` under `captures/{id}/runs/{run}/objects/vlm-cache/<key>.json` (and `.fixtures/vlm-cache/` locally) `[default chosen]`; a cache hit costs $0 and is counted in `cache_hits`; replays and CI run with a warm cache and make zero external calls. Detectors run with fixed seeds and `torch.use_deterministic_algorithms(True)` on CPU; on GPU, box scores may differ by ≤ 0.01 and boxes by ≤ 2 px, so acceptance tests compare counts, not raw scores.
15. **Reports and metrics**: `report.json` `{frames_total, frames_selected, filtered_frac, hazard_frames, calls: {model: n}, cost_usd: {model: usd, total}, planned_usd, cache_hits, skipped_stages, objects: {emitted, observed, inferred, location_unsure}, lift: {method: n}, timings_s: {gate, owlv2, gdino, cheap, confirm, second, lift}}`; `StageResult.metrics` mirrors `vlm_cost_usd`, `vlm_calls`, `filtered_frac`, `objects_emitted` (the log line's `vlm_cost_usd` key, WS-00 §3.10). Sensitive: never log prompt text with images, never persist API keys, never write meter-plate text (WS-08 owns reads).
16. **Failure modes**: a vendor down (3 tries failed) → `vlm_unavailable`, that reader counts as `unavailable` in the agreement rule; both vendors down → confidence 0.3 objects + warning, stage still `done`; JSON parse failure after 3 tries → `vlm_parse_error`, treated as `unsure`; no depth file for a LiDAR frame → that frame lifts as `arkit`; `scene.keyframes` empty → every object `location_unsure` + warning `pose_quality_low`; `registration_report.registered_fraction < 0.6` → all lifts `inferred` with `plus_minus_m ≥ 1.0` `[default chosen]`; a frame's image unreadable → skipped and warned. The stage never raises on model disagreement or empty detections.

Per-house cost (B3 §4 house model: 9 checklist stills + 120 keyframes; list prices; WS-08 lines shown for the whole-house figure):

| Line | All-VLM baseline | Cascade (measured, B3) | This brief (expected) | Cap |
|---|---|---|---|---|
| Gate, 129 frames × (0.08 + 0.30) s CPU | – | ≈ 50 CPU-s ≈ $0.001 | same | – |
| Object presence: mini on ~17 frames, ~8 GPT-5.5 confirms | GPT-5.5 × 129 = $2.15 (≈ $7.1 with B1's box prompt) | $0.18 | $0.18 | – |
| Grok 4.7 second reads, ≤ 8 objects × $0.029 | – (not in B3's cascade) | – | ≤ $0.23, typical $0.09–0.15 | – |
| **WS-07 total** | $2.15 | $0.18 | **$0.27–0.41** | **$0.85** (`budget_usd`) |
| Meter (2 photos, GPT-5.5 + Grok 4.7 @2048) + panels (3) — WS-08 | $0.088 + $0.081 | $0.088 + $0.031 | same | WS-08 |
| **House total** | **$2.32** | **≈ $0.30 (−87 %)** | ≈ $0.40–0.55 | – |
| API calls / wall time | ~134 / 60–80 s | ~30 / 45–80 s | ~38 / ≤ 120 s (Grok tail, `grok_timeout_s`) | – |

Reads and hazard metrics are unchanged by the cascade (B3 §3.4: meter top-1 69/73, panel hazard 5/5, false hazard alarms 4 → 0); a sparse 52-frame capture costs $1.04 → $0.30 (B3 §4).

## 5. Reuse

| Store source | Repo destination | Keep / drop |
|---|---|---|
| `internal/breadth/b3-cheap-gate/gate_features.py` (`load`, `Clip`, `Siglip`, `OWL_QUERIES`) | `server/objects/gate.py`, `proposals.py` | keep CLIP/SigLIP/OWLv2 classes; drop `quality` (Laplacian; ranking only, B3 §4), `DBNet` (docTR; "text visible" tag only), the `/tmp/b3` paths |
| `b3-cheap-gate/gate_eval.py` (`PROMPTS`, `text_emb`, `embedding_scores`, `owl_scores`) | `server/objects/gate.py` | keep scoring; drop `evaluate` folds into `cli gate-eval` |
| `b3-cheap-gate/select_eval.py` (`select`, `HAZARD_PICKS`, `house_eval`) | `server/objects/select.py`, `tests/` | keep top-K selection; `house_eval` becomes the acceptance test |
| `b3-cheap-gate/cascade_eval.py` (`escalate`, `final`) | `server/objects/confirm.py` | keep the escalation rule; drop scoring tables |
| `b3-cheap-gate/vlm_presence.py` (`CLASSES`, `PROMPT`, `call`, `encode`, `PRICES`) | `server/objects/prompts.py`, `vlm.py` | keep; extend the class list; add cache, timeout, `temperature=0`; drop `grok-4.20` prices |
| `b3-cheap-gate/{crop.py,run_crops.py,vlm_ids.py,tiers.py,reading_gate.py,probe.py}` | not ported | crops are a no-go (B3 §1); identifier prompt rejected (B3 §3.1); tiers/reading gate/probe are WS-08's |
| `internal/breadth/b1-detection/code/common.py` (`CLASSES`, `GROUND_TYPES`, `MATCH_IOU`, `load_image`) | `server/objects/prompts.py`, `tests/b1_eval.py` | keep; remove `STORE`/`WORK` hardcoded paths |
| `b1-detection/code/run_detectors.py` (`Owl`, `GDino`, `norm_box`, `STORE_THRESHOLD`) | `server/objects/proposals.py` | keep OWLv2 + GDINO with the token-span fix; drop `Florence`, `Sam3`, `mmgdino` (B1 no-go / gated / broken) |
| `b1-detection/code/run_vlm.py` (`PROMPT`, `parse`, `call`) | `server/objects/prompts.py`, `vlm.py` | keep the box prompt and parser; **delete** the `anthropic` import, `claude-*` branches and prices |
| `b1-detection/code/evaluate.py` (`nms_same_class`, `presence`, `match_counts`) | `server/objects/tests/b1_eval.py` | keep as the acceptance scorer |
| `b1-detection/code/lift3d.py` (`lift_depth`, `cluster`, `lift_triangulate`, `dlt`, `reproj`, `score`) | `server/objects/lift.py`, `tests/lift_eval.py` | keep; replace `eth3d.py` scan depth with packet depth / scene keyframes; `noisy_poses` (packetgen `noise.py`) tests only |
| `internal/modal-pipeline/housepipe/lidar.py` (`read_depth`, `_sample`, `_unproject`, `place_taps`, `_tap_depth`, `LIDAR_*`, `MIN_CONFIDENCE`) | `server/objects/depth.py` (copy with header comment) | keep; stage packages may not import `server.scale` (WS-00 import rules) — consolidation into `server/common/depth.py` is a later WS-00 change |
| `internal/eval-harness/evalharness/{frame,extract}.py` | `server.eval.frame`, `server.eval.extract` | import, do not copy (`ray_arkit`, `project_arkit`, `apply`; the wall `cast` logic is re-implemented in `lift.py` because `extract_scene` does not expose it) |

Licences: OWLv2 Apache-2.0; Grounding DINO tiny Apache-2.0; CLIP ViT-B/32 via open_clip (MIT code, LAION-2B weights); SigLIP2 Apache-2.0; no Florence-2, SAM 3, GDINO-base or Gemini. Weights download from Hugging Face without a token and are never committed. Eval data: ETH3D CC BY-NC-SA and YouTube frames = evaluation only, never public; Commons CC BY / BY-SA (`commons_houses.csv`).

## 6. Fixtures and data

| Fixture | Path | Size | Exercises |
|---|---|---|---|
| B1 eval list + labels | `$S/internal/breadth/b1-detection/{eval_images_batch1.json,labels/labels.jsonl}` → `data/fixtures/objects/b1/` | 46 KB, 49 KB (163 labelled images: 48 house frames in the store, 35 Commons + 80 meter close-ups VM-only) | per-class presence recall; Commons/meters re-fetched with `b1-detection/code/fetch_images.py` (Commons API, `iiurlwidth=1600`; 4 files lost to rate limits) |
| B1 detector outputs | `b1-detection/results/dets/{owlv2-base,gdino-tiny}.jsonl`, `results/facade76/gdino-tiny.jsonl`, `results/metrics.md` | 1.4 MB, 2.4 MB, 1.7 MB | parity check of the ported proposer (same boxes at score ≥ 0.08 within 2 px) |
| B1 3D GT + lift results | `b1-detection/results/{eth3d_gt.json,lift3d_gdino-tiny.json}` | 7 KB, 82 KB | 6 GT openings (5 w1 windows + door) in ETH3D scan coordinates; expected `depth 4/6, 0 false, 0.35 m` |
| B3 image list + truth | `b3-cheap-gate/{images.json,subset.txt,results/gt.json}` | 327 KB, 14 KB, 139 KB | 1,321 walkaround frames of 11 houses (`internal/data/houses/<id>/frames/`), house-level truth (`house.json` `visible`) |
| B3 gate scores + selection results | `b3-cheap-gate/results/{gate_scores_all.json,gate_eval_all.json,select_eval_all_strict.json,cascade_siglip.json}` | 720 KB, 4 KB, 5 KB, 4 KB | parity of `class_scores` and `select_frames` (same frames picked per house) |
| B3 cached VLM answers | `b3-cheap-gate/results/raw/presence/{gpt-5.4-mini,gpt-5.5}.jsonl` | 574 KB, 559 KB | seed the response cache by frame sha256 so the cascade acceptance runs with **zero** API calls |
| Facade packet + GT scene | `.fixtures/packets/eth3d-facade.tar` (`packet.arkit-lidar.json`, 76 kf, 256 × 192 laser-rendered depth + confidence), `data/fixtures/scenes/eth3d-facade/gt-laser/scene.json` (76 keyframes, scene frame) | 93 MB, 59 KB | LiDAR branch lift (`arkit_lidar`); `packet.arkit-nonlidar.json` = `arkit` tier (wall casts, triangulation); `packet.pose-free.json` = `photo_only` (`location_unsure` + view requests). Packet ids ↔ `DSC_*` stems via `_truth/truth.json`, read by tests only |
| Electro packet + GT scene | `.fixtures/packets/eth3d-electro.tar`, `data/fixtures/scenes/eth3d-electro/gt-laser/scene.json` | 36 MB, 43 KB | doors on two walls; synthetic gas meter labelled `SYNTHETIC` (never a detection target) |
| Stills packets (SD-29) | `data/fixtures/packets/mini-arkit/packet.json` (synthetic, committed: stills `s1` `meter_close`, `s2` `meter_oblique`, meter tap, one door view); `.fixtures/packets/yt-12Ks1idWj40.tar` with `data/fixtures/packets/yt-12Ks1idWj40/packet.photo-only.json` (6 stills: `meter_surround`, `panel_surround`, `house_overview`) | 60 KB; 4.4 MB | step 4 still routing (WS-08 purposes never enter `S`; `meter_surround`/`house_overview` always do); the golden ETH3D packets all have `stills: null`, so this is the only still path in CI |
| Walkaround houses | `internal/data/houses/{yt-5-M-7f35Tyg,yt-0gotY_syWWU,yt-4h8kyjnPo9s,yt-12Ks1idWj40}/` | frames in store | gas meter 1.5 ft from the meter (C3 misjudged it as 5+ ft); the disputed "window AC"; partial AC; the photo-only 6-still Base install (Houston `yt-12Ks1idWj40` leaks the installed battery: presence evaluation only, never placement scoring, WS-00 D-22) |

Copy only the small JSON/JSONL files into `data/fixtures/objects/` (all < 5 MB); images, tars and weights are fetched (`make fixtures`, `HF_HOME`). No fixture holds a meter number, Base prompt text or a customer photo.

## 7. Commands

```bash
uv sync --frozen --extra objects && export HF_HOME=.fixtures/hf   # CPU laptop, ~5 min incl. 2.6 GB of weights
uv run python -m server.objects.cli gate-eval --images data/fixtures/objects/b3/images.json \
  --gt data/fixtures/objects/b3/gt.json --out /tmp/ws07/gate            # embeddings ≈ 9 min on 4 cores, then < 10 s
uv run python -m server.objects.cli cascade-eval --presence-cache data/fixtures/objects/b3/raw/presence \
  --scores /tmp/ws07/gate/gate_scores.json --out /tmp/ws07/cascade      # no API calls, < 30 s
uv run python -m server.objects.cli lift-eval --packet .fixtures/packets/eth3d-facade/packet.arkit-lidar.json \
  --scene data/fixtures/scenes/eth3d-facade/gt-laser/scene.json --gt data/fixtures/objects/b1/eth3d_gt.json \
  --detector gdino-tiny --thr 0.35 --out /tmp/ws07/lift                 # GDINO on 76 frames ≈ 5 min CPU, lift < 10 s
uv run python -m server.objects.cli run --packet .fixtures/packets/eth3d-facade/packet.arkit-lidar.json \
  --scene data/fixtures/scenes/eth3d-facade/gt-laser/scene.json --out /tmp/ws07/run --no-external   # both stages, ≈ 8 min CPU
uv run python -m server.objects.cli run --packet data/fixtures/packets/mini-arkit/packet.json --stage objects \
  --out /tmp/ws07/mini --no-external                                                  # still routing, < 1 min
uv run python -m server.objects.cli check /tmp/ws07/run/objects_3d.json --scene data/fixtures/scenes/eth3d-facade/gt-laser/scene.json
OPENAI_API_KEY=... XAI_API_KEY=... uv run pytest server/objects/tests -m live -q     # 2 tests, ≤ $5, ≈ 3 min
uv run pytest server/objects/tests -q -m "not live" && uv run ruff check server/objects && uv run lint-imports
```

Modal run (after WS-12): `modal run server/modal_app.py::objects_smoke --packet eth3d-facade --variant arkit-lidar`, expected `objects` ≤ 120 s wall time after `validate` (Grok tail) and `objects_3d` ≤ 120 s CPU after `scene.json` (SD-08 budget).

## 8. Done when

- `pytest server/objects/tests -m "not live"` prints `N passed`, N ≥ 30, including: gate parity (`class_scores` on 20 cached B3 embeddings within 1e-4 of `gate_scores_all.json`), selection parity (`select_frames` picks exactly the frames `select_eval_all_strict.json` implies for `clip+siglip union top-2/class` on all 11 houses), OWLv2 parity (10 B1 eval images: same labels and boxes within 2 px, scores within 0.01 of `dets/owlv2-base.jsonl`), agreement-rule table, budget skip order, `location_unsure` sentinel, cache round trip (second call makes zero requests), `VISION_EXTERNAL_CALLS=off` path, `Object` records validate.
- `cli gate-eval` reports must-keep recall 239/239 (CLIP) and ≥ 237/239 (SigLIP) with the cross-fitted thresholds (B3 §3.2), and `filtered_frac ≥ 0.85` for `top-2/class` on the 1,321 frames.
- `cli cascade-eval` with B3's cached answers reproduces house-level recall `11/11` strict, `≥ 14/18` lenient, false present `≤ 2/31`, planned cost `≤ $0.20` per house for the mini + GPT-5.5 part (B3 `select_eval_all_strict.json`).
- Presence recall ≥ 0.8 per class (plan §6 WS-07), pinned to cached answers so it is reproducible: (a) pair presence (`yes` from GPT-5.5 or Grok 4.7) on the 83 house + Commons B1-labelled images using `b1-detection/results/vlm/{gpt-5.5,grok-4.7}.jsonl` ≥ 0.8 for every class with n ≥ 3 (B1 image-level table: GPT-5.5 alone is ≥ 0.83 on every class; Grok alone dips to 0.73 on downspout and 0.75 on pool); (b) the cascade's final per-frame presence on B3's 81 labelled frames equals the `cascade` row of `cascade_siglip.json` (gas 13/13, AC 9/9, panel 2/2, pool 3/4, electric meter 3/5 — house-scale meters are tiny, B3 §3.2).
- `cli lift-eval` on the facade `arkit-lidar` packet with GT-scene poses: `recovered ≥ 4/6`, `false_3d == 0`, `centre_err_m_median ≤ 0.5` within the w1–w3 ground-floor ROI (B1: 4/6, 0, 0.35 m). Same command with `--noise nonlidar_modern --seeds 5` (packetgen drift on the poses) must report every object `inferred` or `location_unsure` (0 `observed`).
- `cli run` on the facade `arkit-lidar`, `arkit-nonlidar` and `pose-free` variants writes `objects.json` (`hss-objects/v0`, every `views[].image` a packet frame id) and `objects_3d.json` that `cli check` validates; `arkit-lidar` emits ≥ 4 `observed` openings; `arkit-nonlidar` emits 0 `observed` detector objects; `pose-free` emits ≥ 1 `view_requests.json` entry and only `plus_minus_m ≥ 0.8` objects. `cli run --stage objects` on `mini-arkit` selects `s1`/`s2` into no VLM set (WS-08 purposes) and writes `frame_scores.json` for all 6 images.
- Live tests (`-m live`): one house (`yt-5-M-7f35Tyg`, 150 frames) through steps 2–9 finds `gas_meter` and `electric_meter` present with `report.json` `cost_usd.total ≤ 0.85` and `frames_selected ≤ 20`; a repeat run reports `cache_hits == calls` and `cost_usd.total == 0`.
- Regression gate that stays green: `make validate` (merged scenes still validate), `score-verdicts` on the reference verdicts reporting 0 unsafe passes after `merge_objects` takes `objects_3d.json` from the facade run, `make hygiene`, `lint-imports` (no import of `server.scale`/`server.scene`), `rg -n "anthropic|claude" server/objects` → nothing.
- `docs/components/ws-07-object-understanding.md` exists with the rebuild commands, the pasted gate table and per-class recall table; every `[default chosen]` above appears in `internal/build/layout-decisions-ws-07.md`.

## 9. Validator checklist

File the report at `internal/build/validation/ws-07.md` (frontmatter per the store rule; sections: environment, commands + exit codes, artifacts inspected, hand checks, verdict PASS/FAIL, defects). Run on a fresh clone, never with the builder's caches except the committed `data/fixtures/objects/`.

1. `uv sync --frozen --extra objects && uv run pytest server/objects/tests -q -m "not live"` → exit 0; paste the summary line and the parity test names.
2. Run every §7 command except the live tests; paste `gate-eval`, `cascade-eval` and `lift-eval` summary JSON. Fail if any §8 threshold is missed.
3. Open `/tmp/ws07/run/objects_3d.json` (facade `arkit-lidar`): every object has `source.views[].image` naming a packet keyframe id and a `box_px` inside that image's `w × h`; `evidence: observed` objects have ≥ 2 views; no `attrs`; no id `meter`; ids unique against the scene's objects.
4. Hand-sample 3 objects: render `box_px` on the named frames (`PIL.ImageDraw`) and confirm the class by eye; render the `pose-free` run's `view_requests.json` reference crop and confirm it shows the object.
5. `rg -n "temperature|reasoning_effort|seed" server/objects/vlm.py` → temperature 0 and seed set for mini and Grok, omitted for `gpt-5.5`; `rg -n -i "anthropic|claude|SSR" server/objects` → nothing; `rg -n "sk-|xai-" server/objects data/fixtures/objects` → nothing.
6. Cache: run `cli run` twice on the live test house (needs keys, ≤ $2): the second `report.json` has `cost_usd.total == 0` and `cache_hits == calls`; the two `objects.json` files are byte-identical.
7. `VISION_EXTERNAL_CALLS=off uv run python -m server.objects.cli run ...` → exit 0, `report.json.skipped_stages` lists `cheap, confirm, second`, all objects `source.kind == detector`, confidence ≤ 0.5.
8. Fail the WS if: any Anthropic reference; any crop sent to a VLM; OWLv2 used as a frame filter; a `plus_minus_m` derived from confidence (grep `confidence` in `emit.py` bound code); `objects_3d.json` fails `cli check`; `run_3d` makes any network call; the lift acceptance is met only by lowering `MATCH_M` (0.8 m) or the ROI; a `[default chosen]` missing from the layout-decisions file; any tracked file > 5 MB.

## 10. Constraints

- Vision models: GPT-5.5 and Grok 4.7 as the agreement pair, GPT-5.4-mini as the only cheap tier; OpenAI and xAI endpoints only; **no Anthropic** (the ported B1/B3 code drops every `claude-*` branch). Local models: OWLv2, GDINO-tiny, CLIP, SigLIP2 only (§5 licences).
- Never commit: real meter numbers (`internal/breadth/b2/**`, `b3-raw/**` never synced), Base SSR prompt text (all prompts are our own wording from the class table; `SSR_Prompts.md` is never opened by this WS), API keys or secret values (env names only; `make hygiene`), files over 5 MB (weights, tars, images, embeddings), customer photos.
- Cost caps: `HSS_OBJECTS_VLM_BUDGET_USD_PER_HOUSE = 0.85` enforced in code (inside the run-wide $1.50 hard cap, SD-38); expected $0.27–0.41 (§4 table); live tests ≤ $5 total; Modal spend $0 for this WS (GPU runs are WS-12's benchmark).
- Privacy: whether customer photos may leave to OpenAI/xAI is Base's decision (gate decision §10, Base 8); `VISION_EXTERNAL_CALLS` is the switch and every call is logged with image sha256, model and cost, never with pixels; cached answers live in the capture's own storage prefix and are deleted with `DELETE /captures/{id}`.
- No customer tools anywhere: taps and photos only; `view_requests` ask for photos, never measurements.
- Target device is the non-Pro iPhone: the `arkit` and `photo_only` paths must run and validate without any depth file; LiDAR is used first when present (`Branch = lidar`).
- Store docs are read-only; inconsistencies go into the component page.

## 11. Out of scope and handoffs

- WS-06: the scene builder reads the 2D `objects/objects.json` (SD-06) for opening spans, `fence`/`driveway` hints and `ground_type` votes; `server.scene.merge_objects(scene_path, objects_3d_path)` (ids, re-validation after merge) is called by WS-03 after `objects_3d`; ground regions and their materials, `walls[].role: fence`, the anchor `meter` object stay WS-06's.
- WS-03: stage graph `poses, objects, reads ← validate` in parallel, `objects_3d ← scene, objects` (optional, CPU, 120 s), `criteria` after `merge_objects` (SD-08); dispatch by stage name to `server.objects.run` / `run_3d` (SD-24).
- WS-09: consumes `confidence` (`≥ 0.75` may support pass/fail per `evidence`; `< 0.75` review-only), the `location_unsure` sentinel (`plus_minus_m ≥ 9.99` → every clearance against that object `unsure/unobserved` + a `views_needed` `region` entry built from `view_requests.json`), `attrs` via C3, `objects/frame_scores.json` for C3 photo ranking; its C1 class map now carries `other/water_heater`, `deck`, `stairs` as obstacles, `eave`/`porch_roof` as overheads and `bin`/`shrub`/`tree` as movable obstacles (WS-09 brief §4 step 3).
- WS-08: meter/panel reads, the reading gate (SigLIP linear probe, B3 §3.2), reading-frame crops/resolution (none; 2048 px), the meter auto-fill policy; `embed_frames` is exported for its probe.
- WS-04: consumes `early.meter_candidates(packet, top_k=3)` (OWLv2 "electric meter" ≥ 0.3 on `meter_*` stills and keyframes, ranked by the CLIP meter score) when the photo flow has no meter tap `[default chosen]`; WS-03 wires the call (the `objects` stage itself already runs in parallel with `poses`).
- WS-05: scale, meter-can contour, `meter_can` as the scale anchor (WS-07 only reports its boxes).
- WS-10: reviewer rendering of `source.views` boxes and `view_requests` reference crops; customer-safe copy.
- WS-11/12: gate tables in CI, Modal image with the four local models on the volume, per-house cost report from `StageResult.metrics`.
- WS-13: real-house validation that may promote non-LiDAR wall casts from `inferred` to `observed`, per-class US examples (panels in context n = 2, hose bibs 3, pools 4, cans 6, meters in context 8: B1 §3), the untested classes (`disconnect, wall_box, vent, gas_regulator, gas_pipe, basement_well, gate, deck, stairs, eave, porch_roof, water heater`: B1 dump §8).

## 12. Open questions and defaults

| Question | Default until answered |
|---|---|
| May customer photos go to OpenAI/xAI? (Base) | WS-00 `Settings.VISION_EXTERNAL_CALLS` (SD-11): `on` in `SMP_ENVIRONMENT=dev`; **required explicit** in `prod` (missing → startup error) |
| Plan says "GPT-5.5 + Grok 4.7 confirmation on 5–10 selected frames" and "crops → cheap tier"; B3 measured mini → GPT-5.5 with no crops and Grok only on reads | follow B3 for frames and crops; Grok 4.7 is the second reader per decision-bearing object (≤ 8 calls), not per frame; plan §6 WS-07 to be updated by the coordinator |
| Plan's acceptance "≥ 4/6 openings with LiDAR stand-in depth" was measured with GDINO-tiny boxes and 1024 px oracle depth, not OWLv2 and 256 × 192 packet depth | GDINO-tiny stays the opening proposer; if the packet-depth run misses 4/6, report both the packet-depth and the B1 oracle-depth numbers in the component page and do not lower `MATCH_M` |
| OWLv2 seed threshold: plan 0.2, B1 recommends ~0.3 | seed 0.3, support 0.2 (`proposer_seed_thr`, `proposer_support_thr`) |
| Should non-LiDAR wall casts ever be `observed`? | no, until WS-13 measures the refined-pose lift on a real house |
| Tiny meters at house scale (B1: electric meter frame-level 3/5) | no tiling; `HSS_OBJECTS_OWL_TILE_2X2=off` `[default chosen]`; WS-13 tests 2 × 2 tiling at 2048 px |
| `gpt-5.5` and `temperature=0` | omit `temperature`, send `reasoning_effort: "low"`; if the endpoint rejects that too, send neither and rely on the cache |
| Where the Grok box goes when it disagrees with the seed box (IoU < 0.3) | sidecar only; the OWLv2 seed stays the `box_px` of record |
| Env names | resolved (SD-11): `VISION_EXTERNAL_CALLS` is a WS-00 `Settings` field; every WS-07 knob is `HSS_OBJECTS_*`, read by `ObjectsConfig.from_env()` through `server.common.config` |
| Pose artifact name | resolved (SD-03): `poses/cameras.json` + `poses/registration_report.json`; WS-07 uses `scene.keyframes[]` for geometry anyway |
| Grok 4.7 timeout 90 s (hosted-architecture §3) or 120 s? | 120 s (`grok_timeout_s`; measured p90 78 s; WS-08 uses the same; hosted-architecture is being updated by the coordinator) |
| Should the `objects` stage wait for `scene` or start at finalize? | resolved (SD-08): stage `objects` (steps 2–9, `run`) starts right after `validate` in parallel with `poses` and `reads`; stage `objects_3d` (steps 10–11, `run_3d`) runs after `scene` and reuses `objects.json` + `presence.json` |
| Lift acceptance depth source | keep B1's `MATCH_M = 0.8 m` and ROI; report packet-depth (256 × 192) and oracle-depth numbers side by side when the first misses 4/6 `[default chosen]` |
