# WS-10 builder brief: result composer, AR guidance payload, notifications, reviewer view

Store root for every `internal/...` and `docs/...` path: `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`. Repo paths are relative to the checkout of `huntertcarver/house-scanning-server`. Plan sections: `docs/server-pipeline-build-plan.md` §5 (result contract; its snake_case examples are being rewritten to camelCase, seam decision SD-25) and §6 "WS-10". Tags: `[default chosen]` marks a choice the plan or code did not make; every one is listed in `internal/build/layout-decisions-criteria.md`. Reconciliation decisions applied on 2026-09-26 (wire casing, `ViewNeededDevice`, catalogue loading, storage keys, event data) are logged in `internal/build/decision-log-group-c.md`.

## 0. Card

- **WS-10, result composer and AR guidance.** Goal: turn the WS-09 verdicts, the WS-06 scene and the WS-08 reads into the device-facing result (`GET /captures/{id}/result` body) with the `outcome` block of plan §5 in WS-00's camelCase contract, AR anchors in ARKit world coordinates via the inverse of `frame.from_source`, customer-safe copy from WS-14's catalogue, the meter pre-fill block, `verdict_ready` / `retake_request` events with APNs and Base-webhook payloads, and a read-only reviewer HTML page.
- Builder model: Grok 4.7. Validator: a separate agent (any model), never the builder.
- Inputs: `verdicts/verdict.{A,B,C,S}.json`, `verdicts/criteria_report.json`, optional `verdicts/placement.<profile>.json` (WS-09); `scene/scene.json` + `scene/points.ply` + `scene/preview.ply` + `keyframes[]` (WS-06); `scale/scale.json` (WS-05); `reads/reads.json` (WS-08, the `reads` stage artifact of the same run); the `CaptureRecord` and `RunRecord` (`outcome_kind`, `failure_code`, `degraded`, WS-00 SD-17) from WS-01/WS-03; WS-14's prompt catalogue `ios-kit/prompts/views-needed-prompts.en-US.json`. Outputs at `captures/{captureId}/runs/{runId}/result/` (per-stage folder, no attempt folders in storage, SD-01/SD-07): `result.json` (WS-00 `ResultResponse`), `result.internal.json` (true decision, failure code, degradations), `preview.ply`, `review.html`, plus event records; consumed by WS-01 (`/result`, `/events`, review route), WS-11 (e2e), WS-14 (iOS fixtures).
- Repo: `huntertcarver/house-scanning-server`, branch `ws-10/result-and-ar`, one PR.

## 1. Start gate

Stop and report if any item is missing.

- WS-00 merged: `server/result/` stub + README; `server/contracts/result.py` with `ResultResponse`, `Outcome`, `RecommendedPlacement`, `ViewNeededDevice`, `RejectReason`, `MeterBlock`, `CaptureStatus`, `EventType` (10, incl. `confirmation`) and the typed event data (`docs/build/ws-00-skeleton-and-contracts.md` §3, camelCase per SD-25/26/27) plus the exported `server/contracts/schemas/result.v0.schema.json`; `server/common/` `Storage` (`put_bytes`, `signed_get_url`) and `StateStore` (`append_event`, `set_status`; SD-15/16) protocols with `LocalFsStorage` / `MemoryState`; `server/eval/` vendored harness (`frame.py`, `sceneio.py`, `chain.py`, `coverage.py`).
- WS-14's catalogue file `ios-kit/prompts/views-needed-prompts.en-US.json` exists at `HSS_RESULT_PROMPTS_PATH` (WS-10 never imports `ios_kit`: the repo's import rules forbid `server.*` → `ios_kit`, so the JSON file is the interface and `server/result/copy.py` mirrors the selection rules of WS-14 §3.4), or the coordinator has accepted the test-only fallback table (§4.8).
- WS-09's branch merged or its golden outputs available: `verdicts/verdict.{A,B,C,S}.json` for `eth3d-facade`, `eth3d-electro`, `eth3d-delivery` plus the synthetic flat-wall run (`server/criteria/tests/data/synthetic_flatwall/`), regenerable with `make criteria-golden`.
- Fixtures (SD-28): `data/fixtures/scenes/<site>/gt-laser/{scene.json,coverage.npz}` + `.fixtures/gt/<site>/gt-laser/laser.ply` (WS-00 `make fixtures ONLY=all`; ETH3D PLYs never committed) and one guided-flow packet with ARKit epochs for the epoch test (`data/fixtures/packets/eth3d-facade/packet.arkit-nonlidar.json`).
- `docs/hosted-architecture.md` §2.2 (result example, event types, push and webhook shapes; the coordinator's spec PR adds `POST /captures/{id}/confirmations`, `GET /v1/healthz`); `docs/capture-packet-spec.md` v0.4 for the pose/mat4 convention.
- Python 3.12 repo-wide (WS-00 D-16), `uv sync --frozen --extra result` with the optional group `result` = `numpy jsonschema jinja2 pillow httpx` (no ML packages).

## 2. Files you own / must not touch

Create or modify:

- `server/result/{__init__,compose,ar,copy,meter,preview,notify,config}.py` (`__init__.run(ctx)` = stage `result`, SD-24; `copy.py` = catalogue loader + selector mirroring `ios_kit.prompts`, never importing it)
- `server/result/review/{render.py,template.html,style.css}`
- `server/result/tests/**`, `server/result/README.md`, `docs/components/ws-10-result.md`
- `Makefile` target `result-golden` (append only)
- A separate contracts PR `ws-10/contracts-result-ext` only if a field below is missing from `server/contracts/result.py` (additive, WS-00 reviews); no schema of your own.

Forbidden: `server/contracts/**` outside that PR, `server/criteria/**` (WS-09; request `placement.<profile>.json` fields through the layout-decisions file), `server/api/**` and `server/common/**` (WS-01: you call their interfaces, you do not edit them), `server/eval/**`, `server/scene/**`, `ios-kit/prompts/**` (WS-14 owns the wording), `docs/*spec*.md`, `docs/hosted-architecture.md`, `docs/scene-and-verdict-format.md`, CI config, `data/fixtures/**`, `internal/**`.

## 3. Interfaces

```python
# server/result/compose.py
def compose_result(capture: CaptureRecord, run: RunRecord, scene_dir: Path, verdict_dir: Path, out_dir: Path, *,
                   reads: Path | None, scale: Path | None, catalogue: Catalogue,
                   config: ResultConfig = ResultConfig()) -> ComposeResult:
    """RunRecord = WS-00 jobs.py (SD-17); WS-10 reads run_id, outcome_kind, failure_code, degraded.
    Reads verdict.<profile>.json for config.profile (+ every profile for the reviewer), builds the
    ResultResponse (server.contracts.result), writes out_dir/result.json, result.internal.json,
    preview.ply, review.html; returns {response, internal, events: list[(EventType, data)]}."""
def emit_events(result: ComposeResult, state: StateStore) -> list[int]      # seq numbers, once per run
def run(ctx: StageContext) -> StageResult                                  # stage `result` (SD-24): compose_result + emit_events + set_status
# CLI: python -m server.result.compose --capture CAPTURE.json --run RUN.json --scene S/ --verdicts V/ --out OUT/
#      [--reads reads.json] [--scale scale.json] [--catalogue PATH] [--profile C] [--show-reject]
# server/result/copy.py  (mirror of ios_kit.prompts, WS-14 §3.4; same selection semantics, tested for parity by WS-14)
def load_catalogue(path: Path) -> Catalogue
def select_prompt(cat: Catalogue, *, kind=None, band=None, cause=None, target_class=None, member_action=None,
                  hint_code=None, capture_check_code=None, outcome=None, criterion=None) -> PromptEntry
def render(entry: PromptEntry, **vars: str | float) -> RenderedPrompt        # {promptId, title, body}
```

```python
# server/result/ar.py  (all mat4 are 16 numbers column-major, packet convention)
def scene_to_arkit(frame: dict) -> np.ndarray | None      # inv(mat4_from_list(frame["from_source"])) if the
                                                          # source_frame is an ARKit epoch, else None
def box_to_arkit(T_sa: np.ndarray, center_m, size_m, yaw_deg) -> dict   # {"pose": mat4, "size": [w, h, d]}
def points_to_arkit(T_sa: np.ndarray, pts_m: np.ndarray) -> list[list[float]]
def camera_pose_for_view(view: dict, scene: dict) -> np.ndarray   # camera-to-scene mat4 (ARKit camera axes)
def region_polygon_scene(view: dict, scene: dict) -> tuple[str, np.ndarray]   # ("wall"|"ground"|"free", 4x3), WS-00 `surface`
def best_reference_crop(region_pts_m: np.ndarray, scene: dict) -> dict | None   # {"image", "boxPx"} via frame.project_arkit
```

```python
# server/result/config.py  (WS-00 Settings names first, HSS_RESULT_* for this WS's own knobs)
@dataclass(frozen=True)
class ResultConfig:
    profile: str = "C"                       # RULES_PROFILE_DEFAULT (WS-00 Settings)
    show_reject: bool = False                # SHOW_REJECT_TO_HOMEOWNER (WS-00 Settings; Base's call)
    max_views: int = 6                       # HSS_RESULT_MAX_VIEWS (device list; WS-09 caps at 8)
    signed_url_ttl_s: int = 900              # HSS_RESULT_SIGNED_URL_TTL_S (hosted-architecture: 15 min)
    preview_max_points: int = 200_000        # HSS_RESULT_PREVIEW_MAX_POINTS
    meter_autofill: str = "barcode"          # HSS_RESULT_METER_AUTOFILL: barcode|never (B3 §3.1; never "agreement")
    threejs_url: str = "https://unpkg.com/three@0.160.0/build/three.module.js"   # HSS_RESULT_REVIEW_THREEJS_URL
    catalogue_path: Path = DEFAULT_CATALOGUE  # HSS_RESULT_PROMPTS_PATH (WS-14's file; one name, WS-14 §3.4)
    webhook_url: str | None = None           # BASE_WEBHOOK_URL; secret in WEBHOOK_HMAC_SECRET (WS-00 names; values never in repo)
```

**Device payload** = WS-00's `ResultResponse` (`server/contracts/result.py`, `docs/build/ws-00-skeleton-and-contracts.md` §3.7): **camelCase everywhere on the wire, `outcome` included** (SD-25; the plan §5 snake_case examples are being rewritten). Python identifiers, the verdict file and `result.internal.json` stay snake_case. Restated here as WS-10 fills it:

```json
{"runId": "run_7Q2", "status": "complete|needs_views|manual_review|failed",
 "site": {"decision": "pass|manual_review|reject", "reasons": ["..."], "profile": "C"},
 "scale": {"source": "meter_model", "status": "verified|single_reference|unverified", "relBound95": 0.014},
 "criteria": [{"id": "route_length", "outcome": "pass", "unsureCause": null, "measuredFt": 10.4,
               "plusMinusFt": 0.4, "thresholdFt": 16, "coverage": "observed"}],
 "viewsNeeded": ["<the same ViewNeededDevice objects as outcome.viewsNeeded, SD-26>"],
 "memberActions": ["retake_meter_closeups"], "meter": {"status": "prefill_confirm", "value": "…", "candidates": ["…"],
   "barcodeConfirmed": false, "readersAgree": true, "clean": false, "evidence": {"image": "s01", "boxPx": null}},
 "outcome": {"kind": "eligible|needs_more_photos|not_eligible|manual_review", "profile": "C",
   "message": "customer-safe headline", "arkitEpoch": "e1", "frameToArkit": [16],
   "recommendedPlacement": {"wallId": "w1", "startSM": -3.6, "footprintM": {"w": 0.7874, "h": 1.0033, "d": 0.5588},
      "boxSceneM": {"centerM": [0, 0, 0], "sizeM": [0, 0, 0], "yawDeg": 0}, "boxArkitWorld": {"pose": [16], "size": [0, 0, 0]},
      "confidence": 0.8, "profile": "C"},
   "cableRoutePolylineArkit": [[0, 0, 0]],
   "viewsNeeded": [{"id": "vn3", "kind": "band", "band": "ground", "wallId": "w1", "sM": [3.0, 5.5], "outM": 3.7, "side": null,
      "target": "downward photo of the ground in front of this wall, from chest height", "criteria": ["ground_surface"], "why": "…",
      "viewingAngleDeg": [20, 60], "promptId": "vn.ground.unobserved", "prompt": {"title": "…", "body": "…"},
      "targetRegionArkit": {"surface": "ground", "polygon": [[0, 0, 0]]}, "suggestedCameraPoseArkit": [16], "referenceCrop": null}],
   "reasons": [{"criterion": "drive_clearance", "measuredFt": 0.0, "plusMinusFt": 0.3, "thresholdFt": 3.0,
      "comparison": "at_least", "text": "…", "evidence": {"images": ["k00041"], "boxesPx": [[0, 0, 0, 0]]}}]},
 "verdictUrl": "<signed>", "previewUrl": "<signed>", "reviewUrl": "<signed or relative>"}
```

`MeterBlock` (WS-00, B3 §3.1 policy): `status: auto_filled|prefill_confirm|unreadable|not_captured`, `value`, `candidates (≤ 3)`, `barcodeConfirmed`, `readersAgree`, `clean`, `evidence {image, boxPx|null}`; filled from WS-08's `reads.json` `meter` (`MeterRead {status: auto_fill|prefill|choose|unreadable|single_reader|disabled, value, core, candidates[{value, core, sources, rank, flags}], confidence, agree, barcode_match}`) and `confirm.still_id`. `ViewNeededDevice` (SD-27) = `{id, kind, band?, wallId?, sM?, outM?, side?, target?, criteria, why, viewingAngleDeg?: [min, max], promptId, prompt {title, body}, targetRegionArkit? {surface: wall|ground|free, polygon}, suggestedCameraPoseArkit?, referenceCrop? {image, boxPx}}`; the envelope's `viewsNeeded` and `outcome.viewsNeeded` are the same objects, no second shape (the earlier string `prompt` is withdrawn). `result.internal.json` (never served to the device): `{decision, outcome_kind_internal, failure_code, degraded, judge_flags, c2_vetoes, decidability}`.

Units and frames: scene metres; verdict feet; device payload metres for coordinates (`…M`, `sM`) and feet for criteria values (`…Ft`) as in the verdict; ARKit world is right-handed, +y up, origin at the epoch's session start, `mat4` column-major (`docs/capture-packet-spec.md` §2). `boxArkitWorld.pose` is box-to-ARKit-world with box local +x along the wall (left to right seen from outside), +y up, +z out of the wall; `size` in ARKit units (metres when `source_frame` is an ARKit epoch, scaled by `scale_of(inv(T))` otherwise). Camera poses are camera-to-world with ARKit camera axes (+x right, +y up, looking along −z).

Errors (`ResultError.code`, stage `failed`): `verdict_missing` (run record says `criteria` succeeded but no verdict for `config.profile`), `verdict_invalid` (fails `server.eval.sceneio.validate`), `scene_invalid`, `payload_invalid` (fails `ResultResponse` validation: a bug, never ship). Not errors: a run whose `criteria` stage failed or hit `hard_stop` still gets a payload (§4.2). Soft (payload written, `warnings[]` in `result.internal.json`): `no_arkit_frame` (photo flow: anchors null, crops used), `no_route_geometry`, `preview_skipped`, `reads_missing`, `catalogue_missing` (fallback table).

## 4. Behaviour spec

1. **Load and pick the profile.** Validate every present verdict; the device payload uses `config.profile` (`RULES_PROFILE_DEFAULT`, then `C`); the reviewer page shows all profiles. Read `criteria_report.json` for cost, timings and degradations, and the run record for `failure_code` / `degraded`.
2. **Outcome lattice (plan §5).** `site.decision == pass` → `outcome.kind = eligible`, `status = complete`. `manual_review` with non-empty `views_needed` → `needs_more_photos`, `status = needs_views`. `manual_review` with empty `views_needed` → `manual_review`, `status = manual_review`, message "we have what we need". `reject` → `not_eligible`, `status = complete` when `show_reject`; when off (default) the device sees `outcome.kind = manual_review`, `status = manual_review`, `outcome.reasons = []`, `site.decision` reported as `manual_review`, and the true decision goes only to `result.internal.json`, the reviewer page and the webhook `decision`. Run-level overrides (WS-03 invariant "a degraded run never ends eligible"): `RunRecord.degraded` non-empty or `failure_code` set → never `eligible`; a verdict `pass` becomes `manual_review` with `site.reasons += ["degraded:<stage>"]` per stage; no verdict at all (criteria failed, `hard_stop`) → `manual_review` payload with `site = {decision: manual_review, reasons: [failure_code], profile}`, `criteria = []`, `outcome.kind = manual_review`. `memberActions` from `scale.json`: `status unverified` or `conflict` → `retake_meter_closeups` when the close-ups did not register or the model is unknown, else `truedepth_meter_scan`; never a tape. `site.reasons` are the verdict's codes, not customer text.
3. **Scene → ARKit mapping.** `T = mat4_from_list(scene.frame.from_source)` (source → scene, similarity), `T_sa = inv(T)`, `s = scale_of(T_sa)`. The mapping is valid only when `scene.frame.source_frame` names an ARKit epoch: accepted spellings `arkit_world`, `arkit_world/<epoch>`, `arkit_world:<epoch>` `[default chosen]`; `outcome.arkitEpoch` = that epoch id (or the packet's single epoch when unnamed; `null` otherwise) and `outcome.frameToArkit = mat4_to_list(T_sa)`. Any other `source_frame` (`colmap_model`, `eth3d_world`) → `no_arkit_frame`: every `…Arkit…` field is `null` and views carry `referenceCrop`. Points: `apply(T_sa, p)`; directions: `apply_dir`; poses: `transform_pose(T_sa, pose)`.
4. **Recommended placement** (only for `eligible`; wire keys `recommendedPlacement {wallId, startSM, footprintM, boxSceneM {centerM, sizeM, yawDeg}, boxArkitWorld {pose, size}, confidence, profile}`, SD-26). Candidate = `site.chosen_candidate` (fallback: the passing run with the shortest `start_s_m` distance to the meter). Battery size from the profile (`footprintM` = `{w: 0.7874, h: 1.0033, d: 0.5588}` for C/S = 31 × 39.5 × 22 in; A/B `{0.6096, 1.0033, 0.6096}`; height always 39.5 in as in WS-09, awaiting Base confirmation). `startSM` = midpoint of the run's range when the range is wider than the battery, else its start. Wall geometry from `server.eval.chain.Chain`: `p = plan_at(s_mid)`, direction `d = (dx, dz)`, outward `n = (−dz, dx)`; `centerM = [p.x + n.x·(d/2 + standoff), ground_y + h/2, p.z + n.z·(d/2 + standoff)]` with `standoff = 0.02 m`; `sizeM = [w, h, d]`; `yawDeg = degrees(atan2(−dz, dx))`. If WS-09 wrote `placement.<profile>.json` with `box_scene_m`, use it instead. `boxArkitWorld = box_to_arkit(T_sa, …)`. `confidence` `[default chosen]` = `clamp(min_k (mou_k − 1) / 2, 0, 1)` over the metric checks at the spot (`margin_over_uncertainty`), 1.0 when none binds; documented as heuristic.
5. **Cable route polyline** (`cableRoutePolylineArkit`). Prefer `placement.<profile>.json.route_polyline_scene_m`; else build it from the chain: points at the meter foot `[0, 0.3048, 0.02]`, each chain corner between s = 0 and the spot at route height 0.3048 m (PR #11 `route.height_ft 1.0`) offset 0.02 m out of the wall, the spot's near edge, then the box centre top; transform with `points_to_arkit`. `null` without an ARKit frame.
6. **viewsNeeded with AR guidance.** For each verdict entry (up to `max_views`, in the verdict's order) build one `ViewNeededDevice` (SD-27): verdict fields in camelCase (`wallId`, `sM`, `outM`, `side`, `target`, `criteria`, `why`); `viewingAngleDeg` = the verdict's `view_angle_deg` when present, else derived `[0, 30]` for `band: wall`/`past_end` and `[20, 60]` for `band: ground` (degrees from head-on; `null` for `facing`, `overhead`, `region`, `close_up`, `confirm_attribute`), because coverage grading, not geometry error, is the main cause of undecided outcomes (E2E §4); `targetRegionArkit = {surface, polygon}` from `region_polygon_scene`: `band wall`/`past_end` → `surface wall`, rectangle at `sM` × heights `[0, 2.13]` (past_end: 2 m of wall continuation beyond the end point); `band ground` → `surface ground`, strip `sM` × `out [0, outM]`; `band facing`/`overhead`/`region` → `surface free`, vertical rectangle at `sM`, `outM` from the wall, heights `[0.3, 1.83]` (overhead: `[1.22, 2.13]`); `close_up`/`confirm_attribute` → the object's `wall_rect`/box face (`surface wall`). `suggestedCameraPoseArkit` from `camera_pose_for_view` (WS-14 §3.3 numbers): walls and past_end: 3.0 m out along `n`, height 1.4 m, looking at the region centre (camera −z toward the wall); ground: 1.0 m out, height 1.3 m, pitched to look at the strip centre (`outM/2`); facing: at the wall, height 1.3 m, looking along `+n`; close-up: 1.0 m from the object centre head-on; confirm_attribute: 1.5 m. Build the camera-to-world as `z_cam = normalize(cam − target)`, `x_cam = normalize(up × z_cam)`, `y_cam = z_cam × x_cam`, then `transform_pose(T_sa, …)`. `promptId` + `prompt` from step 8. Photo flow: `referenceCrop = best_reference_crop(polygon, scene)` = the keyframe with the smallest incidence angle whose projection (`frame.project_arkit`) lies inside the image, `boxPx` = projected bbox grown 10 %; `null` if none.
7. **Envelope lists.** `criteria[]` = the verdict's criteria at the chosen candidate plus site-scope facts, mapped `id, outcome, unsureCause, measuredFt, plusMinusFt, thresholdFt, coverage` (the worst `coverage[].status`); `viewsNeeded[]` = exactly the `ViewNeededDevice` objects of `outcome.viewsNeeded` (same list, SD-26); `scale` from `scale.json` (`relBound95 = rel_bound_95`) or `{"source": "none", "status": "unverified", "relBound95": 0.5}` when missing.
8. **Copy** (`copy.py`). The customer-safe wording is WS-14's catalogue (`ios-kit/prompts/views-needed-prompts.en-US.json` at `HSS_RESULT_PROMPTS_PATH`), loaded by `copy.load_catalogue`, selected by `copy.select_prompt` (the mirror of `ios_kit.prompts.select_prompt`: entries ordered most specific first, first entry whose every present `match` key contains the given value wins, `vn.generic` last; `cause` = the worst `unsure_cause` of the entry's `criteria[]` in the order `generated_only > unobserved > partial > grazing > unknown_attribute > margin`) and rendered by `copy.render(entry, **vars)` → `{promptId, title, body}`; WS-10 never composes ids or text: `promptId` is the selected entry's id and the app renders `title`/`body` verbatim. Selection inputs per surface: views → `kind, band, cause, target_class`; headline → `outcome` (`manual_review` also for a hidden reject); reject reasons → `criterion` (`show_reject` only; WS-14's `reason.<criterion_id>` entries, one per criterion id of `docs/scene-and-verdict-format.md` §5); WS-03's events → `hint_code` / `capture_check_code` (WS-00's closed lists, SD-20) and `member_action`. Variables (WS-14 §3.4 names): `side` (from the sign of the `sM` midpoint, ±0.3 m dead band), `s_ft`, `out_ft` (`round(· × 3.2808)`), `what` (class map), `threshold_ft`. Every selection must resolve; a missing entry is a test failure, and a missing catalogue file uses `copy.FALLBACK` (English, tests only) with warning `catalogue_missing`. Rendered `title` ≤ 40 and `body` ≤ 140 characters (WS-14 limits); bodies never contain `grazing`, `unobserved`, `s_m`, `candidate`, `coverage`, `reject`, `fail`, nor any measurement instruction.
9. **Meter block.** From WS-08's `MeterRead`: `auto_fill` → `auto_filled` (only WS-08 may set it: barcode match, or a confirmation; zxing 10–11/73 in B2, all correct); `prefill`, `choose`, `single_reader` → `prefill_confirm` with `candidates` = `MeterRead.candidates[].value` (≤ 3, preselected first), `readersAgree = agree` (GPT-5.5 + Grok 4.7 agreement is "likely", 65/68, Wilson low 87.8 %, never auto-fill), `barcodeConfirmed = barcode_match`; `unreadable` → `unreadable` (WS-09 already carries WS-08's `close_up` view); `disabled` or no meter still → `not_captured`; `clean = fact.clean`; `evidence.image = confirm.still_id`. `meter_autofill == never` downgrades `auto_fill` to `prefill_confirm`. Never log the value: log `sha256(value)[:8]` and the status.
10. **Preview.** Use WS-06's `scene/preview.ply` when present (≤ 200 000 points, written by WS-06); else load the first `point_cloud` asset (`server.eval.sceneio.load_points`), stride-subsample to `preview_max_points`, write `preview.ply` (binary LE, `write_ply`). Either way add or keep a `generated` uint8 field (1 where the asset's `generated_field` is set or the point lies inside a `generated_regions[]` box); skip with `preview_skipped` when no cloud.
11. **Events, push, webhook** (`notify.py`, WS-00 typed data, camelCase, SD-19). After writing the payload: `state.append_event(captureId, "verdict_ready", VerdictReadyData {runId, kind})` once per run (the coordinator verifies exactly one final event per run); `append_event(captureId, "retake_request", RetakeRequestData {runId, viewsNeeded: [ids], memberActions})` when `needs_more_photos` or `memberActions` non-empty; `state.set_status(captureId, status)` (WS-10 writes `complete | needs_views | manual_review`, SD-18). APNs payload exactly `{"aps": {"alert": {"title": "Your site check is ready"}}, "captureId": …, "type": "verdict_ready"|"retake_request"}` (no decision, no address). Webhook `POST BASE_WEBHOOK_URL`, header `X-Capture-Signature: t=<unix>,v1=<hex HMAC-SHA256(WEBHOOK_HMAC_SECRET, t + "." + body)>`, body `{"id": "evt_<uuid>", "type": "verdict.ready", "captureId", "orderRef", "runId", "decision", "verdictUrl"}` with the true decision; retries with exponential backoff 1 s doubling to a 3600 s cap for 24 h `[default chosen]`, idempotent on `id`. Delivery uses `server.common` HTTP/APNs clients; WS-10 owns the payload builders, signing and the retry schedule.
12. **Reviewer page** (`review/render.py`, Jinja2, one self-contained HTML per capture, demo scope): header (captureId, runId, per-profile decisions A/B/C/S with the device profile marked, true decision even when hidden from the device, `failure_code`/`degraded`, scale block, timings, VLM cost, `criteria_report.decidability` per profile with the hybrid's `by_engine` split); photo strip (thumbnails by signed URL, purposes labelled, meter close-ups first); criteria table per profile (id, outcome, `unsure_cause`, measured ± bound ft, threshold, margin/uncertainty, coverage status / best angle / median views, evidence images with boxes, C2 veto marks, judge flags from `criteria_report`); candidate strip per wall (inline SVG: runs coloured green/amber/red along s, meter at 0, openings drawn); views needed (target, prompt, AR pose numbers, reference crop); meter block with value and candidates and a "confirm" form posting WS-00's `ConfirmationRequest {fieldId: meter_number, kind: confirmed | corrected | none, value?, imageId, clientAt}` to WS-01's `POST /captures/{id}/confirmations` (SD-21; WS-01 passes `by="reviewer"` to WS-08's `policy.confirm`; reviewer sees the value; page is behind the API's auth); three.js viewer of `preview.ply` (module import from `threejs_url`, generated points red; a notice when offline); links to raw JSON (`verdict.<p>.json`, `criteria_report.json`, `result.json`, `result.internal.json`). Prod features (queue, overrides, retake request, re-run, audit log) are listed as TODO links only.
13. **Determinism.** No randomness; subsampling uses a fixed stride. `result.json` is byte-stable for the same inputs except `generatedAt` and signed URLs.

## 5. Reuse

- `internal/eval-harness/evalharness/frame.py` (vendored as `server/eval/frame.py`): `mat4_from_list`, `mat4_to_list`, `apply`, `apply_dir`, `scale_of`, `transform_pose`, `project_arkit`, `ray_arkit`; `chain.py`: `Chain`, `plan_at`, `outward`, `ground_y`; `sceneio.py`: `load_points`, `write_ply`, `validate`. Use them unchanged; do not re-implement the frame math.
- `internal/eval-harness/evalharness/adapters/hs11.py::_views_needed_for` for the technical `target` strings the copy table maps from (do not copy technical strings into customer prompts).
- Result envelope and event shapes: `docs/hosted-architecture.md` §2.2 (result example, `status` enum, event types, push and webhook bodies, retry table); the typed models in `server/contracts/result.py` (WS-00 brief §3: `ResultResponse`, `Outcome`, `RecommendedPlacement`, `ViewNeededDevice`, `RejectReason`, `MeterBlock`, `CaptureStatus`, event data). Outcome block: `docs/server-pipeline-build-plan.md` §5. Customer wording and the AR numbers the iOS kit documents: `docs/build/ws-14-ios-contract.md` §3.3–3.4 (catalogue schema and selection rules mirrored in `copy.py`, wall camera 3.0 m out at 1.4 m, ground 1.0 m out at 1.3 m).
- Meter policy: `internal/breadth/B3-cheap-gate.md` §3.1 (GPT-5.5 + Grok 4.7 agreement is "likely", not auto-fill; auto-fill only on barcode match; keep GPT-5.5 top-3 as one-tap pre-fill) and `internal/breadth/B2-equipment-reading.md` §3.1 (zxing 10–11/73 confirmations, all correct; misses are identifier choice, not misread characters). `internal/context-dumps/full/b2-equipment-reading.md` if present for field names.
- Judge/downgrade semantics you display: `internal/context-dumps/full/c3-vlm-baseline.md` §8 (judge flags are review triggers; never show judge numbers). Placement box and lattice: `internal/context-dumps/full/c1-rules-3d.md` §4 (decision rule) and `internal/context-dumps/full/c2-volumetric.md` §4 (box 31 × 22 × 39.5 in; C2 spots are `unsure` reasons, never customer text).
- PR #11's demo `docs/eli5.html`-style rendering is not reused (different scene format); three.js (MIT) via pinned CDN; Jinja2 (BSD).

## 6. Fixtures and data

| Fixture | Path | Exercises | Expected outcome |
|---|---|---|---|
| synthetic flat wall verdicts | `make criteria-golden` output for `server/criteria/tests/data/synthetic_flatwall/` (`scene.json` has `source_frame: arkit_world/e1`, `from_source` = a known similarity) | eligible path, AR box and route | `eligible`, `boxArkitWorld` present, round trip ≤ 1 cm |
| eth3d-facade GT verdicts (`angle80`) | `data/fixtures/scenes/eth3d-facade/gt-laser/` + `verdicts/` | needs_more_photos with wall and ground views; `source_frame: eth3d_world` (not ARKit) → crops path | `needs_more_photos`, anchors `null`, every view has `referenceCrop` |
| eth3d-facade with a forged ARKit frame | test helper rewrites `source_frame` to `arkit_world/e1` (same matrix) | AR guidance geometry on a real scene | polygons within 0.5 m of w2 / the courtyard strip; camera 2–4 m from the wall |
| eth3d-delivery GT verdicts (C) | `data/fixtures/scenes/eth3d-delivery/gt-laser/` + `verdicts/` | not_eligible path; hidden vs shown reject | `not_eligible` with `show_reject`, `manual_review` without; reviewer shows `reject` in both |
| eth3d-electro GT verdicts | `data/fixtures/scenes/eth3d-electro/gt-laser/` | manual_review with views | `needs_more_photos` or `manual_review` per the verdict |
| reads samples | `server/result/tests/data/reads.{auto_fill,prefill,choose,unreadable,disabled}.json` written by you in WS-08's `Reads` shape (values `000000000`, `111111111`) | meter block states | `auto_filled`, `prefill_confirm` ×2, `unreadable`, `not_captured` |
| capture + run samples | `server/result/tests/data/capture.sample.json` (`captureId cap_test`, `orderRef base-order-0`, `flow guided`, epochs `[e1]`), `run.{ok,degraded,failed}.json` as WS-00 `RunRecord` (`degraded: ["objects"]`, `failure_code: "hard_stop"`) | envelope, run-level overrides | ok → per verdict; degraded → never `eligible`; failed → `manual_review` with `reasons: ["hard_stop"]` |
| prompt catalogue | `ios-kit/prompts/views-needed-prompts.en-US.json` (WS-14) at `HSS_RESULT_PROMPTS_PATH` | copy | every selection WS-10 makes resolves to one `promptId` |

## 7. Commands

```bash
uv sync --frozen --extra result                  # Python 3.12 (WS-00 D-16)
make fixtures ONLY=all && make criteria-golden   # WS-00 GT + PLYs, WS-09 outputs under /tmp/crit/<site>/
T=server/result/tests/data; G=data/fixtures/scenes
uv run python -m server.result.compose --capture $T/capture.sample.json --run $T/run.ok.json \
   --scene $G/eth3d-facade/gt-laser --verdicts /tmp/crit/facade --out /tmp/res/facade --profile C
uv run python -m server.result.compose --capture $T/capture.sample.json --run $T/run.ok.json \
   --scene $G/eth3d-delivery/gt-laser --verdicts /tmp/crit/delivery --out /tmp/res/delivery --show-reject
uv run python -m server.result.compose --capture $T/capture.sample.json --run $T/run.ok.json --scene /tmp/crit/synthetic \
   --verdicts /tmp/crit/synthetic/verdicts --out /tmp/res/synthetic --reads $T/reads.auto_fill.json
uv run python -c "from server.contracts.result import ResultResponse; import json
for d in ('facade','delivery','synthetic'): ResultResponse.model_validate(json.load(open(f'/tmp/res/{d}/result.json'))); print('ok', d)"
uv run pytest server/result/tests -q              # < 2 min CPU
uv run ruff check server/result && uv run ruff format --check server/result
make result-golden                                # composes the three fixtures, validates, renders review pages
```

Runtime: compose < 3 s per capture on a laptop (preview subsampling of a 25 MB PLY ~2 s); no GPU, no network except the optional three.js CDN when a browser opens the page.

## 8. Done when

1. `result.json` for synthetic, facade, delivery and electro validates as `server.contracts.result.ResultResponse` and against `server/contracts/schemas/result.v0.schema.json`; `viewsNeeded` equals `outcome.viewsNeeded` object by object (SD-26); every `promptId` resolves in the catalogue; no snake_case key anywhere in `result.json` (SD-25).
2. Outcomes: synthetic → `eligible` with `recommendedPlacement.boxArkitWorld` and `cableRoutePolylineArkit` non-null; facade (`angle80`, C) → `needs_more_photos` with ≥ 1 ground view whose `promptId` starts with `vn.ground.`, `viewingAngleDeg == [20, 60]` and `referenceCrop` non-null (no ARKit frame); delivery C → `not_eligible` with `--show-reject` and `outcome.reasons[]` naming `drive_clearance` with `measuredFt`, `plusMinusFt`, `thresholdFt`, `text`; the same without the flag → `manual_review`, `status manual_review`, `outcome.reasons == []`, `result.internal.json.decision == "reject"`; `run.degraded.json` on the synthetic verdict → `manual_review` with `degraded:objects` in `site.reasons`; `run.failed.json` with no verdicts → `manual_review`, `reasons == ["hard_stop"]`.
3. **AR round trip:** `pytest -k roundtrip` maps 1,000 random scene points through `T_sa` and back with 5 random similarities (scale 0.9–1.1) → max error < 1e-9 m; the synthetic box centre and the 8 box corners mapped scene → ARKit → scene differ by ≤ 0.01 m; `boxArkitWorld.size == sizeM × scale_of(T_sa)` within 1e-9.
4. Camera poses: for every facade view under the forged ARKit frame, the camera is 2.5–3.5 m from the wall face and 1.3–1.5 m high (wall/past_end), 0.8–1.2 m out and 1.2–1.4 m high pitched down for ground, and its −z axis points at the region centre within 5°; `det(R) = +1`, columns orthonormal within 1e-9.
5. Copy: every selection WS-10 can make (all `(kind, band, cause)` combinations, every `outcome`, every criterion id in `docs/scene-and-verdict-format.md` §5 for reasons, WS-00's 16 `capture_check` codes and every `HintCode`, both member actions) resolves to exactly one catalogue entry; no rendered `title` exceeds 40 or `body` 140 characters or contains `grazing`, `unobserved`, `s_m`, `candidate`, `coverage`, `reject`, `fail`; the rendered headlines equal WS-14's table.
6. Meter block: the five reads samples give `auto_filled` / `prefill_confirm` / `prefill_confirm` / `unreadable` / `not_captured`; `readersAgree` alone never yields `auto_filled`; `HSS_RESULT_METER_AUTOFILL=never` turns the `auto_fill` sample into `prefill_confirm`; the test log contains no 9-digit value.
7. Events: `verdict_ready` exactly once per run; `retake_request` exactly when `needs_more_photos` or `memberActions` non-empty; data validate as WS-00's `VerdictReadyData {runId, kind}` / `RetakeRequestData {runId, viewsNeeded, memberActions}` (SD-19); APNs payload equals the §4.11 literal with no decision or address; webhook signature verifies with `hmac.compare_digest` and the documented formula; retry schedule = `[1, 2, 4, …, 3600, 3600, …]` until 24 h.
8. Reviewer page: `review.html` renders for the three golden results; each shows 4 profile decisions, a criteria table with one row per criterion of the device profile, the candidate SVG, the views list and the meter block; `grep -c "<tr" review.html` ≥ criteria count; the page loads with the network disabled except for the three.js notice.
9. Preview: `preview.ply` ≤ `preview_max_points` vertices, binary little-endian, has a `generated` property; `server.eval.sceneio.read_ply` parses it.
10. Unit tests ≥ 25 covering steps 2–11 plus `no_arkit_frame`, `verdict_missing`, `payload_invalid`, `catalogue_missing`, degraded/failed runs, hidden-reject leakage (no `reject` string anywhere in the device payload when hidden).
11. No file > 5 MB in the diff; no Anthropic import; `grep -rn "SSR" server/result` empty.

## 9. Validator checklist

Fresh clone, `make fixtures ONLY=all && make criteria-golden`, then §7 yourself. Then:

- Open `/tmp/res/synthetic/result.json`: `outcome.kind == eligible`; recompute `apply(T, boxArkitWorld.pose[:3,3])` in Python and compare with `boxSceneM.centerM` (≤ 1 cm); check `frameToArkit` is the inverse of the scene's `from_source`; `rg -n "_[a-z]+\":" result.json` finds no snake_case key.
- Open `/tmp/res/facade/result.json`: every `outcome.viewsNeeded[i]` has `prompt.title`/`prompt.body` in plain language, `viewingAngleDeg` set for wall and ground bands and `referenceCrop.image` naming a real keyframe; open that image (fixture) and confirm the crop box covers the wall/ground region by eye for 3 entries.
- Open `/tmp/res/delivery/result.json` without `--show-reject`: search the file for `reject`, `not_eligible`, `drive_clearance` reason text → absent; present in `result.internal.json` and `review.html`.
- Open the three `review.html` in a browser: candidate strip colours match the verdict runs (spot check 3 runs), meter block shows candidates, three.js loads the preview (or shows the offline notice).
- Run the round-trip and camera-pose tests and read their printed max errors.
- Report at `internal/build/validation/ws-10.md`: validator `cursor.subagentId` frontmatter, commit sha, the 11-row gate table with observed values, screenshots of the three reviewer pages (PNG < 1 MB each) under `internal/build/validation/media/`, hand checks, open issues.

## 10. Constraints

- Vision models: none are called by WS-10 (GPT-5.5 and Grok 4.7 only elsewhere; no Anthropic). Never display judge or VLM distance estimates as measurements.
- Never commit real meter numbers, Base SSR prompt text, API keys (`WEBHOOK_HMAC_SECRET`, `APNS_KEY_P8`, GCS signer: names only), ETH3D-derived assets, YouTube frames, or any file over 5 MB. Never log meter values in plain text.
- No customer tools: `memberActions` are only `retake_meter_closeups` and `truedepth_meter_scan`; prompts never ask to measure.
- Privacy: the device payload contains no address and no other capture's data; the push contains no decision; signed URLs expire in 15 min; whether the homeowner sees `not_eligible` is Base's product call (`SHOW_REJECT_TO_HOMEOWNER`, default false).
- Cost: $0 VLM; CPU only.

## 11. Out of scope and handoffs

- Verdict content, lattice, `views_needed` selection and caps, `placement.<profile>.json`: WS-09.
- Contract models (`server/contracts/result.py`, `ViewNeededDevice.viewingAngleDeg`, event data) and schema export: WS-00 (additive fields via a contracts PR); the verdict-side `view_angle_deg` spec PR: coordinator (SD-27).
- API routes (`GET /result`, `GET /events`, `GET /captures/{id}/review`, `POST /captures/{id}/confirmations` with `ConfirmationRequest`, SD-21), state store, signed-URL issuance, APNs/HTTP client wiring: WS-01 (WS-10 provides pure builders and calls the interfaces).
- Stage scheduling, `RunRecord`, early `capture_check` and `hint` events during capture: WS-03 (renders their messages through WS-10's `copy.select_prompt` by `hint_code` / `capture_check_code`; codes are WS-00's, SD-20).
- Prod reviewer app (queue, overrides, retake request, re-run, audit log): WS-12/later; WS-10 ships the read-only page and the meter confirm form only.
- Prompt wording, catalogue schema, `reason.<criterion>` entries and checks, iOS rendering of anchors: WS-14 (`ios-kit/prompts/`; `ios_kit.prompts` is the kit's own copy of the selection rules, never imported by `server/`).

## 12. Open questions and defaults

| Question | Default to assume |
|---|---|
| Homeowner sees `reject` (Base) | no: `SHOW_REJECT_TO_HOMEOWNER=false`, device sees `manual_review` |
| Envelope camelCase vs plan §5 snake_case | resolved (SD-25): camelCase for the whole `ResultResponse`, `outcome` included; the plan's examples are being fixed |
| `viewsNeeded[].prompt` string vs `{title, body}` | resolved (SD-26/27): one `ViewNeededDevice` shape with `promptId` + `prompt {title, body}` in both lists |
| Viewing-angle band on views | `viewingAngleDeg` from the verdict's `view_angle_deg` when present, else `[0, 30]` wall / `[20, 60]` ground (SD-27) |
| Which epoch the anchors refer to | the scene's `source_frame` epoch (`arkit_world:<epoch>` per WS-06); anchors null when the source is not ARKit |
| Photo flow guidance | text prompt + `referenceCrop`; no anchors |
| Meter auto-fill rule | barcode match or confirmation only (B3 §3.1, WS-08 `decide_meter`); pair agreement = pre-fill with one-tap confirm |
| `verdict_ready` / `retake_request` data | resolved (SD-19): `VerdictReadyData {runId, kind}`, `RetakeRequestData {runId, viewsNeeded, memberActions}`, camelCase |
| Wall camera height | 1.4 m (WS-14 §3.3 and its `check-ar` test); ground 1.3 m |
| Placement confidence formula | `clamp(min (mou − 1) / 2, 0, 1)`, heuristic, documented |
| Route geometry source | `placement.<profile>.json` when WS-09 writes it, else chain reconstruction at 0.3048 m |
| Webhook retry schedule | exponential 1 s → 3600 s cap, 24 h |
| three.js delivery in the demo | pinned CDN module URL; offline notice fallback |
| Reviewer auth | none in the demo page itself; served behind the API's bearer token (WS-01) |
