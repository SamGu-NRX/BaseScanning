# WS-14 builder brief: iOS contract support (spec upkeep, defaults, prompt catalogue, fixtures, replay client)

Store root for every `internal/...` and `docs/...` path: `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`. Repo paths are relative to the checkout of `huntertcarver/house-scanning-server`. Plan section: `docs/server-pipeline-build-plan.md` §6 "WS-14" and §5 (result contract). Names follow `docs/build/ws-00-skeleton-and-contracts.md` (`server/contracts`), `ws-11-eval-and-regression.md` (replay engine), `ws-10-result-and-ar.md` (result composer) and `ws-03-orchestrator.md` (event codes); where those briefs disagree with each other, WS-00 wins and the conflict is listed in `internal/build/layout-decisions-ws-14.md` §2. Items marked `[default chosen]` are decisions no brief or doc made; every one is listed in that file's §1. Reconciliation decisions applied on 2026-09-26 (camelCase result, event and code lists, confirmations route, replay flags) are logged in `internal/build/decision-log-group-c.md`; seam ids `SD-nn` refer to `internal/build/seam-decisions.md`.

## 0. Card

- **WS-14, iOS contract support.** Goal: one shippable kit, `ios-kit/`, that lets the iOS team build and test the capture app against spec v0.4 and the demo API with no phone and no help from us: the schemas, example packets and results, mini fixtures, the customer-facing prompt catalogue, a default for every open question, a replay CLI that uploads a stored packet through the real API and prints the events and the result, and the spec change process.
- Builder model: Grok 4.7. Validator: a separate agent (any model), never the builder.
- Inputs: `docs/capture-packet-spec.md` v0.4 (Appendix A, B, D); `docs/hosted-architecture.md` §2.2; `docs/scene-and-verdict-format.md` §1, §3.1, §4; plan §5; `server/contracts/` models and exported schemas (WS-00); `server/eval/replay.py` (WS-11, the upload engine); `server/result/result.json` shape and `copy.py` (WS-10); WS-03's hint and `capture_check` code lists; fixture archives `internal/data/packets/archives/*.tar` with their index `internal/data/packets/index.json` (packetgen; WS-13 rebuilds) and WS-00's `data/fixtures/manifest.json`.
- Outputs: `ios-kit/` (the iOS team); `ios-kit/prompts/views-needed-prompts.en-US.json` (read at runtime by WS-10's `copy.py`, path `HSS_RESULT_PROMPTS_PATH`); mini fixtures (WS-00 `make validate`, WS-11 smoke); `python -m ios_kit replay` (WS-01, WS-03 done-when runs; WS-11 `make e2e` keeps calling `server.eval.replay` directly).
- Repo `huntertcarver/house-scanning-server`, branch `ws-14/ios-contract`, one PR. Spec text changes never ride in that PR (§4.3).

## 1. Start gate

Stop and report if any item is missing; do not stub it yourself.

1. `docs/capture-packet-spec.md` is in the repo with `formatVersion` `"0.4"`; its Appendix A equals (as parsed JSON) `server/contracts/schemas/capture-packet.v0.4.schema.json` and `internal/packet-generator/schema/capture-packet.schema.json`; the Appendix B examples equal `data/fixtures/packets/examples/{example-packet,example-packet-photo-only}.json` (store copies checked equal on 2026-09-26).
2. WS-00 merged (`ws-00/contracts`, `ws-00/skeleton`, `ws-00/fixtures`): `server/contracts/{packet,result,api,jobs,capture_check}.py` (camelCase `ResultResponse` incl. `outcome`, SD-25/26/27; `EventType` = 10 with `confirmation` and the typed `data` of SD-19; `ConfirmationRequest`/`ConfirmationResponse`, SD-21; `CaptureCheckCode` (16) and `HintCode`, SD-20), `python -m server.contracts.export_schemas --check` green with `server/contracts/schemas/{result.v0,api.v1}.schema.json`, `python -m server.contracts.cli check-packets`, `data/fixtures/manifest.json` (`hss-fixtures/v0`), `scripts/fetch_fixtures.py`, `make validate`, `make hygiene`. `FileRole` rejects `keyframe_image` (WS-00 §4 step 5).
3. WS-11 PR `ws-11/replay-and-gates` merged: `server/eval/replay.py` with `replay(packet: Path, api: str, token: str, *, variant="arkit-nonlidar", rate=2.0, concurrency=4, finalize_at="last", until="verdict", timeout_s=900) -> ReplayRun` and the CLI `python -m server.eval.replay --packet --variant --api --token|--token-env --rate --concurrency --finalize-at --until --timeout --out [--dry-run]` (SD-36).
4. Fixture tars fetched: `scripts/fetch_fixtures.py --source $FIXTURE_SOURCE --only large` → `.fixtures/packets/eth3d-electro.tar` (37.4 MB, 303 entries, variants `arkit-lidar`, `arkit-nonlidar`, `arkit-nonlidar-2018`, `pose-free`). Committed manifests exist at `data/fixtures/packets/eth3d-electro/packet.<variant>.json`.
5. Python 3.12, `uv sync --frozen` green. The standalone part of the kit (`python -m packetgen validate`) needs only `jsonschema==4.26.0`, `numpy==2.5.3` (pins from `internal/packet-generator/requirements.txt`).
6. An API to replay against is **not** a start-gate item: until WS-01 runs, `ios_kit/fake_api.py` (§3.6) is the target and §8 item 8 is deferred, not skipped. WS-10's composer is not required either: the result examples in §4.4 are hand-built.

## 2. Files you own / must not touch

Create or modify (under `ios-kit/` unless stated; layout `[default chosen]`):
- `README.md` (iOS-facing), `VERSION` (`0.4.0`: spec minor `0.4` + kit patch `.0`), `CHANGELOG.md`, `VENDORED.md`, `requirements.txt` (standalone validator: `jsonschema`, `numpy`), `fixtures/LICENCE-NOTES.md`
- `schemas/{capture-packet.v0.4,result.v0,api.v1}.schema.json`: byte-identical copies of `server/contracts/schemas/` made by `make ios-kit`, committed, parity-tested
- `examples/packets/{example-packet,example-packet-photo-only}.json` (copies of `data/fixtures/packets/examples/`), `examples/results/{eligible,needs_more_photos,not_eligible,manual_review}.json`, `examples/events/replay-mini-arkit.jsonl`
- `prompts/views-needed-prompts.en-US.json`
- `fixtures/mini-arkit-lidar/`, `fixtures/mini-arkit/`, `fixtures/mini-photo-only/` (§6)
- `ios_kit/{__init__,__main__,replay,amend,minify,prompts,ar,schemas,fake_api}.py`; `packetgen/{__init__,conventions,validate}.py` (verbatim copies of `internal/packet-generator/packetgen/`) plus a kit-owned `packetgen/__main__.py` exposing only `validate` with packetgen's flags (`packets...`, `--no-hashes`) and JSON output
- `tests/*.py`; repo root: `.github/workflows/ws-14-ios-kit.yaml`, `Makefile` (append target `ios-kit` only), `docs/components/ws-14-ios-kit.md`, `data/fixtures/manifest.json` (append the three mini items only, `committed: true`), `pyproject.toml` (only an extra `ios-kit` with `fastapi`, `uvicorn` if they are not already dependencies)

Must not touch: `docs/capture-packet-spec.md` (spec PRs only, §4.3), `docs/hosted-architecture.md`, `docs/scene-and-verdict-format.md`, `docs/criteria-spec.md`, `docs/server-pipeline-build-plan.md`, `server/**` (all of it: `server/contracts` and `server/eval` are WS-00/WS-11, `server/result` is WS-10), `data/fixtures/**` except the manifest append, other CI workflows, the store (`internal/**`).

## 3. Interfaces

### 3.1 What the iOS team receives

| Item | Kit path | Source of truth |
|---|---|---|
| Spec v0.4 (copied into the zip by `make ios-kit` as `spec/capture-packet-spec.md`) | repo `docs/capture-packet-spec.md` | the spec |
| Packet JSON Schema (draft 2020-12) | `schemas/capture-packet.v0.4.schema.json` | spec Appendix A = `server/contracts/schemas/capture-packet.v0.4.schema.json` |
| Upload API bodies, events, errors (`CreateCaptureRequest/Response`, `RegisterFilesRequest/Response`, `CommitRequest/Response`, `FinalizeResponse`, `EventsResponse`, `CaptureStatusResponse`, `AmendmentRequest/Response`, `ConfirmationRequest/Response`, `ApiError`) | `schemas/api.v1.schema.json` | WS-00 `server/contracts/api.py` (hosted-architecture §2.2, Appendix D, SD-21) |
| Result body incl. `outcome` and AR anchors (`ResultResponse`, `Outcome`, `RecommendedPlacement`, `ViewNeededDevice`, `RejectReason`), camelCase throughout | `schemas/result.v0.schema.json`, `examples/results/*.json` | WS-00 `server/contracts/result.py` (plan §5 rewritten to camelCase, SD-25); WS-10 composes |
| Example packets per tier: `arkit` (4 keyframes, meter tap, two close-ups, every v0.4 scale input) and `photo_only`; `arkit_lidar` = `arkit` + `depth` objects + `role: depth\|confidence` files, see `fixtures/mini-arkit-lidar/packet.json` | `examples/packets/*.json`, `fixtures/mini-*/packet.json` | spec Appendix B; §6 |
| Prompt catalogue (every customer-facing string, keyed by `prompt_id` in the file, surfaced as `promptId` on the wire) | `prompts/views-needed-prompts.en-US.json` | §3.4 |
| Hint and `capture_check` code lists (closed) | `README.md` §"Live guidance codes" | WS-00 `server/contracts/capture_check.py` (`CaptureCheckCode` 16, `HintCode`; SD-20) |
| Fixtures: three committed mini packets; the full tars via `scripts/fetch_fixtures.py --source gs://hss-fixtures-<env> --only large` (WS-12 mirror; the store path for us) | `fixtures/`, `data/fixtures/manifest.json` | WS-00 §6 |
| Standalone validator, replay CLI, contract fake | `python -m packetgen validate`, `python -m ios_kit {replay, validate, minify, check-spec, check-prompts, check-ar, check-size}`, `python -m ios_kit.fake_api` | §3.5, §3.6 |
| Defaults for the 17 open questions, the change process | `README.md` §"Defaults", `CHANGELOG.md` | §12, §4.3 |

### 3.2 Upload sequence (exact requests and responses; bodies are WS-00's `api.py` models)

Base URL `$CAPTURE_API_URL` = `https://capture-api.<domain>/v1` (demo) or `http://127.0.0.1:8000/v1` (WS-01 local, `make e2e-local`). Every API call carries `Authorization: Bearer <token>` (env `CAPTURE_API_TOKEN`); storage PUTs carry none. `GET /v1/healthz` → `200 {status, storage, state, wifTokenExpiresAt, version}` (SD-36).

| # | Request | Response |
|---|---|---|
| 1 | `POST /captures`, header `Idempotency-Key: <packetId>`, body `{packetId, formatVersion: "0.4", tier, flow: "guided" (ARKit tiers) \| "photo" (photo_only), device {model, systemVersion, appVersion}, orderRef?, expected? {keyframes?, stills?}}` | `201 {captureId, status: "uploading", upload {maxSinglePutBytes: 16777216, partBytes: 8388608, urlTtlS: 3600, maxFiles: 2000, maxBatch: 50}, eventsUrl, finalizeBy}`; same key + same body → `200` same body; same key + different body → `409 idempotency_conflict`; `formatVersion` not accepted → `422 {errors: [{code: "format_version_unsupported", pointer: "/formatVersion", message}]}` (WS-02's code) |
| 2 | `POST /captures/{id}/files`, body `{files: [{path, bytes, sha256, md5 (base64 of 16 bytes), role, contentType, meta?}]}`, 1–50 per call; `role` is exactly `files[].role` (`keyframe_image` → `422 role_unknown`); `meta` only for images: `{keyframe: <keyframe record>}`, `{still: <still record>}`, `{trueDepthFrame: <record>}`; depth, confidence, features, streams, mesh carry no `meta` | `200 {files: [{path, state: "pending" \| "committed", upload? {method: "PUT" \| "MULTIPART", url, headers {Content-Type, Content-MD5}, expiresAt, uploadId?, parts?, completeUrl?}}]}`; committed path with another `sha256` → `409 sha256_conflict` |
| 3 | `PUT <upload.url>` with exactly the returned headers, body = the file bytes (`contentType`: `image/jpeg`; `application/octet-stream` for `.f32 .u8 .bin .ply`; `application/gzip` for `.csv.gz` [default chosen]) | `200`; `400 BadDigest` → re-read the file, retry once; `403` expired → re-register (step 2) |
| 4 | `POST /captures/{id}/files:commit`, body `{files: [{path, sha256}]}` (1–50) every 2 s or 10 files | `200 {committed: [], notFound: [], mismatch: []}`; `mismatch` → re-upload |
| 5 | `POST /captures/{id}/finalize`, headers `Idempotency-Key: <packetId>`, `X-Packet-Sha256: <sha256 of the body bytes>`, `Content-Encoding: gzip`, body = `packet.json` | `202 {status: "awaiting_files" \| "processing", missing: [paths], runId, etaS}`; `422 ApiError` (spec §6 codes: `schema, tier_rule, file_missing, sha256_mismatch, depth_size, tap_replay, stream_header`, WS-02 additions); `409 packet_sha_conflict` |
| 6 | `GET /captures/{id}/events?after=<seq>&wait=25` | `200 {status, next, events: [{seq, type, at, data}]}` (§3.7) |
| 7 | `GET /captures/{id}/result`; also `GET /captures/{id}` (`CaptureStatusResponse`, `ETag`) | `200 ResultResponse` (§3.3); `verdictUrl`, `previewUrl` signed 15 min |
| 8 | `POST /captures/{id}/amendments`, header `Idempotency-Key: <amendmentId>`, body `{amendmentId, answers: [view ids]}`; then steps 2–5 under `amendments/{amendmentId}/…`, finalize at `POST /captures/{id}/amendments/{aid}/finalize` with a packet whose `parentPacketId` is the parent's `packetId` and whose `formatVersion` equals the parent's [default chosen] | `201 {amendmentId, pathPrefix: "amendments/{amendmentId}/"}` |
| 9 | `POST /captures/{id}/confirmations` (SD-21; the one-tap answer to a `hint` `meter_number_confirm` or to the result's `meter` block), body `ConfirmationRequest {fieldId: meter_number \| panel_brand \| main_breaker_amps, kind: confirmed \| corrected \| none, value?, imageId, clientAt}`; `GET /captures/{id}/confirmations` lists them | `201 ConfirmationResponse {confirmationId, fieldId, imageId, kind, receivedAt, seq}`; replay `200`; `409 confirmation_conflict` / `capture_closed`; `422 value_required` / `image_unknown`; emits event `confirmation` |

Upload order (Appendix D, as `server.eval.replay` sends it): stills with `purpose` in `meter_close`, `meter_oblique`, `panel_*` first; keyframes in capture order, each JPEG immediately followed by its `depth`, `confidence`, `features`; other stills; streams and mesh; TrueDepth frames with the meter stills; finalize (may precede the last commits).

### 3.3 Result body and AR anchors (ARKit world coordinates)

`GET /captures/{id}/result` = WS-00 `ResultResponse`, **camelCase everywhere, `outcome` included** (SD-25; plan §5 is being rewritten). The full verdict v0 is **not** in the body by default (`verdict?` optional, omitted; `verdictUrl` points at the snake_case verdict file, the only snake_case document the app may ever see).

```json
{"runId": "run_7Q2", "status": "complete", "site": {"decision": "pass", "reasons": [], "profile": "C"},
 "scale": {"source": "meter_model", "status": "verified", "relBound95": 0.014},
 "criteria": [{"id": "route_length", "outcome": "pass", "measuredFt": 10.4, "plusMinusFt": 0.6, "thresholdFt": 16.0, "coverage": "observed"}],
 "viewsNeeded": [], "memberActions": [], "verdictUrl": "<signed>", "previewUrl": null, "reviewUrl": null,
 "outcome": {"kind": "eligible", "profile": "C", "message": "Your home looks eligible. Here's where the battery would go.",
   "arkitEpoch": "e1", "frameToArkit": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0.9831,-1.40,-3.25,1],
   "recommendedPlacement": {"wallId": "w1", "startSM": 1.2, "footprintM": {"w": 0.7874, "h": 1.0033, "d": 0.5588},
       "boxSceneM": {"centerM": [1.5937, 0.50165, 0.2994], "sizeM": [0.7874, 1.0033, 0.5588], "yawDeg": 0.0},
       "boxArkitWorld": {"pose": ["16 numbers, column-major, box-to-world"], "size": [0.7874, 1.0033, 0.5588]},
       "confidence": 0.8, "profile": "C"},
   "cableRoutePolylineArkit": [[0.9831, -0.153, -3.20], [2.577, -0.90, -3.20]],
   "viewsNeeded": [], "reasons": []}}
```

- `status` ∈ `uploading | awaiting_files | processing | needs_views | complete | manual_review | failed | expired`; `outcome` is null while `status ∈ uploading, awaiting_files, processing`. `outcome.kind` ∈ `eligible | needs_more_photos | not_eligible | manual_review`. `memberActions` ⊂ {`retake_meter_closeups`, `truedepth_meter_scan`}; never a tape. Hidden reject (`SHOW_REJECT_TO_HOMEOWNER=false`, the default): the device sees `outcome.kind: manual_review`, `status: manual_review`, `site.decision: manual_review`, no `reasons`; with the flag on, `not_eligible` carries `reasons: [RejectReason {criterion, measuredFt?, plusMinusFt?, thresholdFt?, comparison?: at_least | at_most, text, evidence {images, boxesPx}}]` (SD-26).
- **AR frame.** Scene v0 `frame.from_source` F is a column-major similarity (upper-left 3×3 = s·R, last column t) taking the source frame into the meter-anchored scene. `frameToArkit = F⁻¹` (upper-left `(1/s)·Rᵀ`, translation `−(1/s)·Rᵀ·t`, column-major); valid only when the scene's `source_frame` is an ARKit epoch (`arkit_world`, `arkit_world:<epoch>`, `arkit_world/<epoch>`), and `arkitEpoch` names that packet epoch. Swift: `simd_float4x4(columns:)` from the 16 numbers; `world = M * simd_float4(scene, 1)`. Photo flow or `colmap_model` source: `arkitEpoch`, `frameToArkit` and every `…Arkit…` field are null.
- `boxArkitWorld.pose`: box-centre-to-world, local +x along the wall (left → right seen from outside), +y up, +z out of the wall; `pose = frameToArkit · pose_scene(centerM, yawDeg about +y)`; `size = sizeM / s` (ARKit units). `cableRoutePolylineArkit`: metres, ≥ 2 points.
- `ViewNeededDevice` (SD-27) = `{id, kind, band?, wallId?, sM?, outM?, side?, target?, criteria, why, viewingAngleDeg?: [min, max], promptId, prompt {title, body}, targetRegionArkit? {surface: wall | ground | free, polygon: [Vec3] (≥ 3, planar)}, suggestedCameraPoseArkit?: Mat4, referenceCrop? {image, boxPx}}`. `viewingAngleDeg` is the band of camera angles from head-on the app should coach toward (`[0, 30]` wall and past_end, `[20, 60]` ground; null otherwise): coverage grading, not geometry error, is the main cause of "one more photo" (E2E). Regions: wall patch = `sM` range × heights 0–2.13 m on the wall face; ground strip = `sM` range × 0..`outM` at ground height; facing = vertical rectangle `outM` from the wall, heights 0.3–1.83 m. Camera pose: camera-to-world, ARKit camera axes, looks along −z; walls and past_end: 3.0 m out along the normal, height 1.4 m, aimed at the region centre; ground: 1.0 m out, height 1.3 m, aimed at the strip centre; facing: at the wall, 1.3 m, looking outward; close-up 1.0 m, confirm_attribute 1.5 m from the object, head-on. `referenceCrop` is always set in the photo flow. The envelope's `viewsNeeded[]` and `outcome.viewsNeeded[]` are the **same objects** (SD-26; no camelCase-only repeat, no string `prompt`).
- App rule (in `README.md`): use anchors only while the current ARKit world is the one of `arkitEpoch` (same running session, or relocalised from an `ARWorldMap` saved at that epoch's end); otherwise show `prompt` and `referenceCrop`. Multi-epoch packets: the model lives in the epoch holding the meter; regions in other epochs arrive as text prompts.

### 3.4 Prompt catalogue (`prompts/views-needed-prompts.en-US.json`)

WS-14 owns the file (content, schema, checks). WS-10's `copy.py` loads it at runtime (`HSS_RESULT_PROMPTS_PATH`, default this path; the repo's import rules forbid `server.*` importing `ios_kit`, so the file is the interface and `copy.py` mirrors `select_prompt`/`render` below), selects an entry by the `match` keys, renders `title`/`body`, and writes `promptId` + `prompt` on every `viewsNeeded` entry, `outcome.message`, and `hint.message` (`HintData.promptId`). The app renders text verbatim and never composes it; for `capture_check.missing[]` codes (WS-00 `CaptureCheckData {complete, missing, meterRead?, prompts?}`) the app looks the code up in the same catalogue.

```json
{"format": "views-needed-prompts", "version": "0.4.0", "locale": "en-US",
 "limits": {"title_chars": 40, "body_chars": 140},
 "vars": {"side": ["to the left of your meter", "to the right of your meter", "at your meter"], "s_ft": "int", "out_ft": "int", "what": "class map", "threshold_ft": "int"},
 "prompts": [
  {"prompt_id": "vn.ground.unobserved",
   "match": {"kind": ["band"], "band": ["ground"], "cause": ["unobserved", "partial", "grazing"]},
   "title": "Show us the ground",
   "body": "From chest height, point the phone down at the ground {side}, {s_ft} ft along, so we see about {out_ft} ft out from the wall.",
   "camera": {"standoff_m": 1.0, "height_m": 1.3, "pitch_deg": -50}}]}
```

Selection (`ios_kit.prompts.select_prompt`, mirrored by WS-10 `server/result/copy.py`): entries are ordered most specific first; the first entry whose every present `match` key contains the given value wins; `match` keys: `kind`, `band`, `cause`, `target_class`, `member_action`, `hint_code`, `capture_check_code`, `outcome`, `criterion`; `vn.generic` is last and matches any `viewsNeeded` entry. Prompt ids are `vn.*`, `ma.*`, `capture_check.<code>`, `hint.<code>`, `outcome.<kind>`, `reason.<criterion_id>` (SD-20: never `cc.*`). `cause` = the worst `unsure_cause` of the entry's `criteria[]` (`generated_only > unobserved > partial > grazing > unknown_attribute > margin`). Rendering: `{side}` from the sign of the `s_m` midpoint (left < −0.3 m, right > 0.3 m, seen from outside facing the wall; `_left`/`_right` code suffixes for hints and capture checks); `{s_ft}` = round(|s midpoint| × 3.2808), `{out_ft}` = round(out_m × 3.2808); `{what}` from `window → window, door → door, garage_door → garage door, gas_meter → gas meter, ac_unit → air-conditioning unit, pool → pool, panel → electrical panel, other → object`. Rendered text has no `{`; limits apply after rendering with the longest variable values.

| prompt_id | match | title | body (en-US) |
|---|---|---|---|
| `vn.wall.unobserved` | band wall; cause unobserved, partial | One more wall photo | Stand 8–12 ft back from the wall {side}, {s_ft} ft along, face it squarely and take a photo. |
| `vn.wall.grazing` | band wall; cause grazing | Face the wall straight on | We only saw this stretch from the side. From 8–12 ft back, {side}, {s_ft} ft along, photograph it head-on. |
| `vn.ground.unobserved` | band ground; any cause | Show us the ground | (block above) |
| `vn.overhead.unobserved` | band overhead | Show what's above | From about 10 ft back, tilt the phone up so the wall {side} is in frame from the ground to the eaves. |
| `vn.facing.unobserved` | band facing | What faces this wall? | Stand with your back to the wall {side}, {s_ft} ft along, and photograph straight out: fence, hedge, driveway or yard. |
| `vn.past_end` | kind past_end; side | Where does the wall end? | Walk to the {side} end of this wall and photograph the corner and what comes after it. |
| `vn.close_up.meter` | kind close_up, target meter; member_action retake_meter_closeups; capture_check_code / hint_code meter_close, meter_closeups_in_session | Two close-ups of the meter | From about 3 ft, photograph your electric meter straight on, then from one step to the side. Keep the label readable. |
| `vn.close_up.meter_oblique` | capture_check_code / hint_code meter_oblique | One more meter photo | Take one step to the side and photograph the meter again from about 3 ft, label readable. |
| `capture_check.meter_tap` | capture_check_code / hint_code meter_tap | Tap your meter | Point the phone at your electric meter and tap it on the screen once. |
| `vn.close_up.panel` | kind close_up, target panel; capture_check_code / hint_code panel_close, panel_open, main_disconnect | Electrical panel close-up | From about 3 ft, photograph the electrical panel with the door closed, then open, so the labels are readable. Touch nothing inside. |
| `vn.close_up.object` | kind close_up, other | A closer look | Take a closer photo of the {what} {side}, {s_ft} ft along, so we can see it clearly. |
| `vn.confirm.window` | confirm_attribute; class window | Does this window open? | Photograph the window {side}, {s_ft} ft along, close enough to show its frame and latch. |
| `vn.confirm.door` | confirm_attribute; class door, garage_door | A closer look at the door | Photograph the door {side} straight on from about 10 ft back, full frame and threshold in view. |
| `vn.confirm.ground` | confirm_attribute; ground | What's on the ground here? | From chest height, photograph the ground {side}, {s_ft} ft along, so we can see what it is made of. |
| `vn.generic` | kind region, or anything else | One more photo | Photograph the area {side}, {s_ft} ft along the wall, from about 10 ft back. |
| `ma.truedepth_meter_scan` | member_action | A quick scan of the meter | Turn the phone around so the screen faces the meter, hold it about a foot away and keep it still for 2 seconds. |
| `capture_check.wall_left_of_meter` / `capture_check.wall_right_of_meter` | capture_check_code / hint_code | Walk the wall | Walk slowly along the wall {side}, 8–12 ft back, keeping the wall in frame. |
| `capture_check.ground_downward_left` / `capture_check.ground_downward_right` | capture_check_code; hint_code ground_not_seen_left/right | Show the ground | From chest height, point the phone down at the ground in front of the wall {side}. |
| `capture_check.door_view_meter_side` | capture_check_code / hint_code | Photograph a door | Photograph any entry or garage door on this side of the house straight on, from about 10 ft back. |
| `capture_check.keyframe_count` / `capture_check.keyframe_cadence` / `capture_check.photo_count` | capture_check_code / hint_code (SD-20 folds `too_few_keyframes` into `keyframe_count`) | Keep going | Keep filming while you walk slowly along the wall so we get enough views. / Photograph a few more spots along the wall. |
| `capture_check.tracking_quality` | capture_check_code / hint_code | Hold steady | Move a bit more slowly and keep the wall in frame while the phone catches up. |
| `hint.meter_number_confirm` | hint_code (WS-08) | Is this your meter number? | Check the meter number we read and tap to confirm, or pick the right one. |
| `hint.meter_plate_unreadable` | hint_code (WS-08) | Step a little closer | Step a little closer so the meter label is sharp. |
| `hint.panel_label_unreadable` | hint_code (WS-08) | Panel label not readable | Move closer to the electrical panel so the breaker labels are sharp. |
| `hint.frames_blurry` | hint_code | Hold steady | Move a bit more slowly and keep the phone steady. |
| `hint.frames_dark` / `hint.frames_overexposed` | hint_code | Too dark / Too bright | Step back so more light reaches the wall. / Move so the sun is not directly in the picture. |
| `hint.wall_gap_left` / `hint.wall_gap_right` | hint_code | Missing a stretch of wall | Walk back along the wall {side} and film it again from 8–12 ft back. |
| `outcome.eligible` | outcome | Your home looks eligible | Your home looks eligible. Here's where the battery would go. |
| `outcome.needs_more_photos` | outcome | Almost there | One more photo helps us finish your check. |
| `outcome.manual_review` | outcome (also the hidden-reject text) | We have what we need | We have what we need. Our team will follow up. |
| `outcome.not_eligible` | outcome, `SHOW_REJECT_TO_HOMEOWNER=true` only | Not a fit at this spot | This spot doesn't meet the installation rules. |
| `outcome.failed` | outcome / event failed | Something went wrong | We couldn't process this capture. Please try again, or our team will follow up. |
| `reason.<criterion_id>` (one entry per criterion id of `docs/scene-and-verdict-format.md` §5) | criterion; `SHOW_REJECT_TO_HOMEOWNER=true` only (`RejectReason.text`, WS-10) | Not a fit here | One neutral sentence per criterion using `{threshold_ft}` and `{what}`, e.g. `drive_clearance`: The spot is closer than {threshold_ft} ft to the driveway. |

Rules enforced by `python -m ios_kit check-prompts`: every WS-00 `CaptureCheckCode` (16: `meter_close, meter_oblique, meter_closeups_in_session, meter_tap, door_view_meter_side, panel_close, panel_open, main_disconnect, keyframe_count, keyframe_cadence, tracking_quality, photo_count, wall_left_of_meter, wall_right_of_meter, ground_downward_left, ground_downward_right`) and every `HintCode` (the 16 above + WS-03's `frames_blurry, frames_dark, frames_overexposed, wall_gap_left, wall_gap_right, ground_not_seen_left, ground_not_seen_right` + WS-08's `meter_plate_unreadable, meter_number_confirm, panel_label_unreadable`; SD-20, read from `server.contracts.capture_check` when importable, else the list above), every `kind × band`, every `unsure_cause`, every criterion id (`reason.*`), both member actions and every outcome resolve to exactly one entry; the outcome headlines equal WS-10's; `views_needed`, hint and capture-check bodies never contain `pass`, `fail`, `reject`, `eligib`, `criteri`, `threshold`, `grazing`, `unobserved`, `candidate`, `coverage`, `s_m`, `tape`, `measure`, `ladder`, `wiring`; one action per prompt; distances in whole feet; no address, no meter number; nothing asks to move heavy objects, climb, touch anything inside a panel or enter a neighbour's yard.

### 3.5 Replay CLI and kit modules (exact signatures)

The upload engine is WS-11's `server.eval.replay` (single implementation; the kit never re-implements register/PUT/commit/finalize/poll). `ios_kit replay` wraps it and adds the iOS-facing checks.

```python
# ios_kit/replay.py
def replay_cli(argv: list[str]) -> int          # exit codes below; token from os.environ["CAPTURE_API_TOKEN"], never argv
def run(packet: Path, api: str, *, variant: str | None, rate: float, concurrency: int,
        finalize_at: str, until: str, timeout_s: float, out_dir: Path) -> ReplayRun    # calls server.eval.replay.replay(...) with token_env="CAPTURE_API_TOKEN" (SD-36)
def print_events(run: ReplayRun, jsonl: bool) -> None
def print_result(run: ReplayRun) -> None        # outcome.kind, scale.status, every prompt title/body, timings
# ios_kit/amend.py
def subset_packet(pk: dict, keep_keyframe_ids: set[str], keep_still_ids: set[str], parent_packet_id: str) -> dict
# ios_kit/ar.py  (mat4 = 16 numbers, column-major)
def frame_to_arkit(from_source: list[float]) -> list[float]
def transform_points(mat4: list[float], pts: Sequence[Sequence[float]]) -> list[list[float]]
def box_pose_arkit(from_source: list[float], center_m, size_m, yaw_deg: float) -> tuple[list[float], list[float]]
# ios_kit/prompts.py
def load_catalogue(path: Path = DEFAULT_CATALOGUE) -> Catalogue
def select_prompt(cat: Catalogue, *, kind=None, band=None, cause=None, target_class=None, member_action=None,
                  hint_code=None, capture_check_code=None, outcome=None, criterion=None) -> PromptEntry
def render(entry: PromptEntry, **vars: str | float) -> RenderedPrompt      # {promptId, title, body}; KeyError on a missing var
# ios_kit/schemas.py
def validate_result(result: dict) -> list[str]  # schemas/result.v0 + server.contracts.result.ResultResponse.model_validate
def validate_packet_schema_only(pk: dict) -> list[str]; def validate_events(body: dict) -> list[str]
```

CLI (`cd ios-kit && uv run --project .. python -m ios_kit <cmd>`): `replay --packet PATH [--variant V] --api URL [--rate 2.0] [--concurrency 4] [--finalize-at last|early] [--until processing|verdict] [--timeout 900] [--out DIR] [--jsonl] [--skip-validate]` (flags mirror `server.eval.replay`, SD-36; the kit always passes `--token-env CAPTURE_API_TOKEN`, never `--token`; `--dry-run` is forwarded); `replay --amend CAPTURE_ID --answers vn3[,vn4] --keep k00020,k00021 ...` (needs `server.eval.replay.replay(..., amend_of=, answers=)`, requested from WS-11 in §11; until it exists the command exits `7 not_supported`); `validate PATHS... [--schema-only] [--no-hashes]`; `minify --from PATH --variant V --keyframes k00020:k00025 | --stills s00020:s00025 --out DIR`; `check-spec docs/capture-packet-spec.md`; `check-prompts`; `check-ar RESULT.json`; `check-size`. Exit codes: `0` verdict received and result valid; `2` result received but invalid against `result.v0` or `ResultResponse`; `3` local packet invalid (`packetgen.validate`); `4` API 4xx (`ApiError` printed with pointers); `5` timeout; `6` network failure after the engine's retries; `7` not supported. `--out` receives `run.json` (WS-11's `ReplayRun` verbatim), `result.json`, `events.jsonl`; no token value is ever written or printed.

### 3.6 Contract fake (`uv run --project .. python -m ios_kit.fake_api --port 8765 --root DIR --result-kind needs_more_photos --verdict-delay-s 5 [--token T]`) [default chosen]

A ~200-line FastAPI double of §3.2 using WS-00's `api.py` models, for the kit's tests and for iOS development before the demo API is up; `make e2e-local` (WS-11, real API in-process) is the preferred local target once it exists. Signed URLs point at itself (`PUT /v1/uploads/{token}`; files land under `--root/uploads/{id}/{path}` and are copied to `--root/captures/{id}/{path}` at finalize, SD-30); `GET /v1/healthz` → `200 {status: "ok", storage: "local", state: "memory", wifTokenExpiresAt: null, version}`; finalize runs the vendored `packetgen.validate.validate_packet` (schema, bytes, sha256, tap replay) and maps `schema: a/b/c: msg` to `ApiError {code: "schema", pointer: "/a/b/c", message}`, other messages to `file_missing`, `sha256_mismatch`, `depth_size`, `tap_replay`, `stream_header`; implements `idempotency_conflict`, `sha256_conflict`, `packet_sha_conflict`, `role_unknown`, `format_version_unsupported`, and `POST /captures/{id}/confirmations` (`201`, replay `200`, `409 confirmation_conflict`, `422 value_required`, event `confirmation`); emits `files_committed` per commit, one `capture_check` 3 s after the first keyframe commit (`missing: ["meter_close", "door_view_meter_side"]` when the packet has no `meter_close` still and no door views; `meterRead: "absent"`), eleven `stage` events in WS-00's order `validate, poses, objects, reads, scale, dense, coverage, scene, objects_3d, criteria, result` (SD-08/SD-42) 1 s apart after `processing`, then `verdict_ready {runId, kind}`; the result is `examples/results/<result-kind>.json` with `runId`, `outcome.arkitEpoch` patched from the upload (`arkitEpoch`/`frameToArkit` null for `photo_only`). Any non-empty bearer is accepted unless `--token` is set.

### 3.7 Events

WS-00 `EventsResponse {status, next, events: [Event {seq ≥ 1, type, at, data}]}`; `EventType` = 10: `files_committed | hint | capture_check | stage | verdict_ready | retake_request | review_started | review_completed | failed | confirmation` (SD-19); typed `data` per WS-00 `api.py`, camelCase: `FilesCommittedData {paths, committed, listed?}`, `HintData {code, message, retake?, promptId?, frames?}`, `CaptureCheckData {complete, missing[], meterRead?: ok | unreadable | pending | absent, prompts?}`, `StageData {stage, status, attempt, runId, gpu?, durationS?, errorCode?}`, `VerdictReadyData {runId, kind}`, `RetakeRequestData {runId, viewsNeeded[], memberActions[]}`, `FailedData {code, message, next: manual_review | none}`, `ConfirmationData {confirmationId, fieldId, imageId, kind}`. `hint.message` is rendered by WS-10 from §3.4; `capture_check.missing[]` codes are looked up by the app in the catalogue. WS-08's one-tap candidates are not in `HintData` today: the app reads them from the result's `meter.candidates`; WS-08 proposes an additive `HintData.confirm {imageId, fieldId, mode, candidates, preselected, allowNone}` in the spec PR of §4.3 item 1.

## 4. Behaviour spec

### 4.1 `ios_kit replay`

1. **Load and pre-check.** Accept what `server.eval.replay` accepts (`.tar`, split-part prefix, folder with `packet.json`, `packetgen export` zip; `--variant` default `arkit-nonlidar`). Before uploading, run `packetgen.validate.validate_packet(manifest, check_hashes=True)`; any error → print and exit `3` unless `--skip-validate`. `_truth/` is never read or uploaded.
2. **Run** `server.eval.replay.replay(packet, api, token, variant=…, rate=…, concurrency=…, finalize_at=…, until=…, timeout_s=…)`; `token = os.environ["CAPTURE_API_TOKEN"]` (missing → exit `4` with `unauthorized`; never on argv, SD-36 `--token-env`). Map engine outcomes: `errors[]` containing an `ApiError` → `4`; timeout → `5`; transport failure → `6`.
3. **Print.** Events as one line each (`seq type at summary`) or `--jsonl`; then the result: `status`, `outcome.kind`, `scale.status`, `memberActions`, each `viewsNeeded` entry's `prompt.title`, `prompt.body` and `viewingAngleDeg`, and `timings.last_commit_to_capture_check_s` with a `≤ 30 s` flag (`CAPTURE_CHECK_TARGET_S`, informational for WS-03).
4. **Validate the result** with `validate_result` (schema + pydantic); errors → exit `2` after writing the files. Write `run.json`, `result.json`, `events.jsonl` to `--out`.
5. **`--amend`**: `subset_packet` builds an ordinary v0.4 packet: fresh `packetId` (uuid4), `parentPacketId` = the parent's, keyframes/stills/taps/doorViews filtered to `--keep`, epochs, tracking, planes, streams kept whole, `files[]` recomputed; it must pass `validate_packet`; then the engine's amendment path (§3.2 #8). Amendments answer `retake_request` and `memberActions`.

### 4.2 Mini fixtures (`minify`)

Keep keyframes in the inclusive sorted-id range, their JPEG/depth/confidence/features files, stills whose `keyframe` is kept, taps whose `keyframe` is kept (ARKit tiers: the range **must** contain a tap, refuse otherwise; `photo_only` has none), `doorViews` on kept frames, all epochs, tracking, planes and streams. Rewrite paths to the spec layout (`keyframes/<id>.features.bin`, `streams/<name>.csv.gz`, photo-only stills `stills/<id>.jpg`) [default chosen]; recompute `files[]` (`bytes`, `sha256`); `packetId = uuid5(NAMESPACE_URL, "<house>/<variant>/mini/<range>")`; keep `source.kind: "synthetic"`, append `"mini fixture: transport tests only, too few frames to reconstruct"` to `source.notes`; require `validate_packet` ok, `check_packet` ok, every file ≤ 5 MB (`check-size`, hard fail). Then append the three items to `data/fixtures/manifest.json` (`kind: "packet_mini"`, `committed: true`, `licence: "ETH3D CC BY-NC-SA 4.0, evaluation only"`).

### 4.3 Spec change process (write it into `README.md` and `CHANGELOG.md`)

1. Every change to `docs/capture-packet-spec.md` (text, Appendix A, Appendix D) or to a device-facing contract starts as a **spec PR** labelled `spec`, WS-00 §4: it edits the store doc first, then `server/contracts` (models, `export_schemas`, `SOURCES.md`), then `make ios-kit` refreshes `ios-kit/schemas`, `examples`, `CHANGELOG.md`, `VERSION`. The WS-00 owner reviews contract changes; one iOS reviewer approves anything the app reads. Never bundled with implementation. WS-14 files the first one (SD-21): `POST /captures/{id}/confirmations` + `GET …/confirmations` into `docs/hosted-architecture.md` §2.2 with the `ConfirmationRequest`/`ConfirmationResponse` bodies of §3.2 row 9, the `confirmation` event, and WS-08's proposed additive `HintData.confirm` block.
2. Versioning: `formatVersion` stays `0.x`. No bump for descriptions, examples, prompt wording, new values of open vocabularies (`stills[].purpose`, `taps[].label`, prompt ids, hint codes). Minor bump (`0.4` → `0.5`) for a new required field, a removed or renamed field, a new value in a closed enum (`files[].role`, `session.tier`, `keyframes[].reason`, `trackingState`, `outcome.kind`, `EventType`), a changed unit or convention, or a new response field the app must read.
3. Deprecation: one minor as deprecated (schema description starts `DEPRECATED (0.5): …`, validator warns, `CHANGELOG.md` lists it), removed in the next minor.
4. Compatibility window: the server accepts the current and the previous minor for 30 days after the kit release that introduced the new one [default chosen]; other versions → `422 format_version_unsupported`; validation uses the versioned schema file `capture-packet.v<minor>.schema.json` (WS-02 keeps both); amendments carry the parent's `formatVersion`.
5. Release: `VERSION = <minor>.<kit patch>`; tag `ios-kit-v<VERSION>`; `make ios-kit` builds `dist/ios-kit-<VERSION>.zip` (README, VERSION, CHANGELOG, `spec/`, `schemas/`, `examples/`, `prompts/`, `fixtures/mini-*` + LICENCE-NOTES, `packetgen/`, `requirements.txt`; no `ios_kit/`, no tests, no `_truth/`); fixtures rebuilt by packetgen (WS-13) before the tag; `CHANGELOG.md` entry = date, `formatVersion`, added / changed / deprecated / removed, one migration note for the app.
6. `check-spec` (§8 item 2) fails CI whenever Appendix A, `server/contracts/schemas`, the kit copies or the examples drift.

### 4.4 Result examples (`examples/results/*.json`)

Built on the spec's Appendix B `arkit` example: wall plane at ARKit `z = −3.25` with normal `+z`, meter tap hit `(0.9831, −0.1533, −3.251)`, ground 1.40 m below the session start (`y = −1.40`, spec §2 says 1.2–1.6 m) [default chosen]. Scene frame origin `(0.9831, −1.40, −3.25)`, axes equal to ARKit's, `s = 1`: `from_source = [1,0,0,0, 0,1,0,0, 0,0,1,0, −0.9831, 1.40, 3.25, 1]`, `frameToArkit` its inverse. Battery for profile C/S per WS-10: `footprintM {w: 0.7874, h: 1.0033, d: 0.5588}` (31 × 39.5 × 22 in; 39.5 in awaiting Base confirmation), standoff 0.02 m, so the box centre in §3.3 is `[1.2 + 0.3937, 0.50165, 0.02 + 0.2794]`. `eligible`: the block in §3.3. `needs_more_photos`: `status needs_views`, `site.decision manual_review`, two views (`vn1` ground band `w1`, `sM [3.0, 5.5]`, `outM 3.7`, cause `unobserved`, `viewingAngleDeg [20, 60]`; `vn2` wall band, `grazing`, `side right`, `viewingAngleDeg [0, 30]`) with polygons on `z = −3.25` (wall) and `y = −1.40` (ground), camera poses per §3.3, `referenceCrop` null, envelope `viewsNeeded` = the same two objects. `not_eligible`: the `SHOW_REJECT_TO_HOMEOWNER=true` shape with one `RejectReason` (`criterion drive_clearance`, `measuredFt 0.0`, `plusMinusFt 0.3`, `thresholdFt 3.0`, `comparison at_least`, `text` rendered from `reason.drive_clearance`, `evidence {images: ["k00041"], boxesPx: [[…]]}`). `manual_review`: `viewsNeeded []`, `memberActions ["retake_meter_closeups"]`, `scale.status unverified`. Every example passes `ResultResponse.model_validate` and `schemas/result.v0.schema.json`, and contains no snake_case key.

## 5. Reuse

- Vendor verbatim (source path + sha256 in `VENDORED.md`): `internal/packet-generator/packetgen/{validate,conventions,__init__}.py`. Drop the rest of packetgen (`sources.py`, `images.py`, `manifest.py`, `packet.py`, `photos.py`: pycolmap/OpenCV, synthetic generation). `packetgen/__main__.py::cmd_validate` defines the flags the kit-owned `packetgen/__main__.py` reproduces.
- Copy, do not fork: `server/contracts/schemas/{capture-packet.v0.4,result.v0,api.v1}.schema.json` and `data/fixtures/packets/examples/*.json` into the kit via `make ios-kit`.
- Call, do not port: `server.eval.replay.replay` (WS-11), `server.contracts.result.ResultResponse`, `server.contracts.packet.check_packet` (WS-00), `scripts/fetch_fixtures.py` (WS-00).
- Wording sources: WS-10 §4 step 8 headline messages (kept identical), hosted-architecture `hint` text (`Step a little closer so the meter label is sharp`), A9 §8 TrueDepth sentence (`internal/breadth/A9-no-tape-scale.md`), `docs/scene-and-verdict-format.md` §3.1 ground target, WS-09's technical `target` strings (never shown to customers). Never copy `internal/criteria/SSR_Prompts.md`.
- `internal/modal-pipeline/housepipe/validate.py`: format-0.1 semantic validator, do not port.
- Licences: ETH3D CC BY-NC-SA 4.0 (evaluation only; mini fixtures stay in the private repo and the private kit zip, `fixtures/LICENCE-NOTES.md` mirrors `data/fixtures/LICENCE-NOTES.md`); YouTube-derived frames never enter the kit.

## 6. Fixtures and data

| Fixture | Path | Size | Exercises |
|---|---|---|---|
| `eth3d-electro` (45 keyframes, 1 `reference_point` tap on `k00023`, laser LiDAR stand-in) | `.fixtures/packets/eth3d-electro.tar` → `eth3d-electro/packet.{arkit-lidar,arkit-nonlidar,arkit-nonlidar-2018,pose-free}.json`; store `internal/data/packets/archives/eth3d-electro.tar` | 37.4 MB tar; `arkit-lidar` 185 files / 34.3 MB, `arkit-nonlidar` 95 / 23.2 MB, `pose-free` 45 stills / 21.9 MB | LiDAR branch, image branch, photo flow; the real-API gate |
| `eth3d-facade` (76 keyframes, metric) | `.fixtures/packets/eth3d-facade.tar` (joined from `.part-00` + `.part-01`) | 96.6 MB | WS-10's AR round-trip fixture |
| `mini-arkit-lidar`, `mini-arkit`, `mini-photo-only` (committed) | `ios-kit/fixtures/mini-*/` from `eth3d-electro` `arkit-lidar` / `arkit-nonlidar` keyframes `k00020:k00025` (contains the tap) and `pose-free` stills `s00020:s00025`; manifest ids `ios-kit-mini-arkit-lidar`, `ios-kit-mini-arkit`, `ios-kit-mini-photo-only` (WS-00's SD-29 fixture is also called `mini-arkit`: keep the ids distinct) | 5.5 / 4.1 / 2.9 MB, every file ≤ 728 KB | transport, validation, fake-API round trip, `make validate`; too few frames for a verdict |
| WS-00 meter-stills mini (SD-29) | `data/fixtures/packets/mini-arkit/packet.json` (synthetic from `example-packet.json`: 4 keyframes, stills `s1` `meter_close`, `s2` `meter_oblique`, meter tap, one door view), copied into the kit as `examples/packets/example-packet-meter-stills.json` | ≈ 60 KB | the only committed ARKit packet whose stills carry WS-08's purposes; schema + `check_packet` |
| Example packets | `data/fixtures/packets/examples/*.json` = `ios-kit/examples/packets/*.json` | 11 + 2.4 KB | schema-only validation (no files) |

Every fixture packet has `source.kind: synthetic` and reports meter close-ups, door views, TrueDepth and address as not captured; the Appendix B `arkit` example is the only packet with those fields filled. `_truth/` sidecars are never uploaded or shipped.

## 7. Commands

```bash
uv sync --frozen && make setup                                    # repo root, Python 3.12
export FIXTURE_SOURCE=/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal   # or gs://hss-fixtures-dev
uv run python scripts/fetch_fixtures.py --source $FIXTURE_SOURCE --only large        # .fixtures/packets/*.tar
cd ios-kit
python3.12 -m venv .venv-standalone && .venv-standalone/bin/pip install -r requirements.txt   # what the iOS team installs
.venv-standalone/bin/python -m packetgen validate fixtures/mini-arkit/packet.json    # {"ok": true, ...}
uv run --project .. python -m ios_kit validate --schema-only examples/packets/example-packet.json
uv run --project .. python -m ios_kit check-spec ../docs/capture-packet-spec.md
uv run --project .. python -m ios_kit check-prompts && uv run --project .. python -m ios_kit check-size
uv run --project .. python -m ios_kit.fake_api --port 8765 --root /tmp/fake-captures --result-kind needs_more_photos &
export CAPTURE_API_TOKEN=dev-token                                 # value never printed by any command
uv run --project .. python -m ios_kit replay --packet fixtures/mini-arkit --api http://127.0.0.1:8765/v1 --out runs/mini-arkit
uv run --project .. python -m ios_kit replay --packet fixtures/mini-arkit --api http://127.0.0.1:8765/v1 --finalize-at early --out runs/early
uv run --project .. python -m ios_kit replay --packet ../.fixtures/packets/eth3d-electro.tar --variant arkit-nonlidar \
  --api $CAPTURE_API_URL --rate 2 --timeout 1260 --out runs/electro              # WS-01 local (make e2e-local) or the demo
uv run --project .. pytest tests -q                                # < 2 min CPU
uv run --project .. ruff check . && uv run --project .. ruff format --check .
make -C .. ios-kit                                                 # dist/ios-kit-0.4.0.zip
```

Runtimes on a CPU laptop: mini replay against the fake ≈ 15 s (6 keyframes at 2 files/s + 5 s verdict delay); electro `arkit-nonlidar` upload at 2 files/s ≈ 50 s plus the stack's time to verdict (p50 3 min, p95 8 min; WS-03 hard stop 20 min, hence `--timeout 1260`).

## 8. Done when

1. `uv run --project .. pytest ios-kit/tests -q` green, covering: `frame_to_arkit ∘ from_source` = identity to 1e-9 on 100 random similarities (s ∈ [0.8, 1.25]); `box_pose_arkit` size scaling by `1/s`; `subset_packet` output validates; `select_prompt` specificity and the `vn.generic` fallback; `render` raises on a missing var; every example result passes `validate_result`; the fake-API round trip incl. one `POST /captures/{id}/confirmations` (`201`, then the `confirmation` event); `409`/`422` paths (`idempotency_conflict`, `sha256_conflict`, `packet_sha_conflict`, `role_unknown`, `format_version_unsupported`, `tap_replay`, `confirmation_conflict`, `value_required`); a rendered-prompt parity test against `server.result.copy` when importable (same `promptId`, `title`, `body` for 20 sampled selections).
2. `check-spec docs/capture-packet-spec.md` prints `parity ok`: Appendix A == `server/contracts/schemas/capture-packet.v0.4.schema.json` == `ios-kit/schemas/capture-packet.v0.4.schema.json`; `ios-kit/schemas/{result.v0,api.v1}` == `server/contracts/schemas/`; `examples/packets/*` == `data/fixtures/packets/examples/*` (+ the SD-29 copy == `data/fixtures/packets/mini-arkit/packet.json`).
3. `python -m packetgen validate fixtures/mini-*/packet.json` → `ok: true` ×3, zero errors and zero warnings, `max_tap_replay_error ≤ 1e-3` on the two ARKit minis; `uv run python -m server.contracts.cli check-packets ios-kit/fixtures/mini-*/packet.json --files` ok; `make validate` still green with the three manifest entries.
4. `check-ar examples/results/eligible.json` and `needs_more_photos.json`: box centre and every polygon vertex map scene → ARKit → scene with max error < 1e-6 m; wall polygon on `z = −3.25 ± 1e-6`, ground polygon on `y = −1.40 ± 1e-6`; the wall camera pose sits at `z ≈ −0.25`, `y ≈ 0.0` (1.4 m above ground), the ground camera at `y ≈ −0.10`.
5. `check-prompts` exits `0` (every rule of §3.4, including WS-00's two code lists, the `reason.*` entries and headline equality with WS-10).
6. Fake round trip: both `replay` commands of §7 against `fake_api` exit `0`; `runs/mini-arkit/result.json` validates and has no snake_case key; `events.jsonl` has `files_committed`, `capture_check`, eleven `stage` events in the SD-42 order and `verdict_ready {runId, kind}`; `runs/early/events.jsonl` shows `awaiting_files` before `processing`; `run.json` is a valid `ReplayRun` with `timings.last_commit_to_capture_check_s` set.
7. `minify` regenerates the three minis byte-identically from `.fixtures/packets/eth3d-electro.tar` (deterministic `packetId`, sorted `files[]`).
8. Real API (deferred until WS-01 exists, then mandatory): `eth3d-electro` `arkit-nonlidar` via `ios_kit replay` against `make e2e-local` or `$CAPTURE_API_URL` exits `0`; the result validates; `timings.finalize_to_verdict_s` set; `last_commit_to_capture_check_s ≤ 30` reported (WS-03's gate, informational here).
9. `check-size`: no file in `ios-kit/` over 5 MB, `dist/ios-kit-0.4.0.zip` under 30 MB; `make hygiene` green; `rg -n "keyframe_image" ios-kit/` returns nothing outside `CHANGELOG.md`; `rg -n "(sk-|xai-|Bearer [A-Za-z0-9])" ios-kit/ docs/components/ws-14-ios-kit.md` returns nothing; no `_truth/` under `ios-kit/`; the zip contains no `ios_kit/` or `tests/`.
10. Regression gates that must stay green: `make ci` (WS-00) and `make gates-offline` (WS-11) unchanged; `make e2e` still calls `server.eval.replay` (this WS adds no second uploader); 0 unsafe passes untouched (this WS writes no verdicts).

## 9. Validator checklist

- Run §8 items 1–7 and 9 verbatim on a clean checkout; record each command and its last output line. Item 8 only if WS-01 is available; otherwise write `deferred: WS-01 absent`.
- Inspect by hand: `examples/results/needs_more_photos.json` — both `outcome.viewsNeeded` entries carry `promptId`, `prompt.title`, `prompt.body`, `viewingAngleDeg`, `targetRegionArkit`, `suggestedCameraPoseArkit`; the envelope `viewsNeeded` holds the same two objects; no snake_case key and no key `verdict` in the body; no `reasons` in `manual_review.json`. Read 5 random catalogue entries aloud for tone (a homeowner can act on each without a tool). Run `replay --dry-run` on a `.tar` fixture and check the printed order against Appendix D. `rg -n "$CAPTURE_API_TOKEN" runs/` finds nothing.
- Diff review: `git diff --stat main..ws-14/ios-contract` touches nothing under `server/`, contract docs, other workflows, or `data/fixtures/` beyond three appended manifest items; `VENDORED.md` sha256s match `sha256sum internal/packet-generator/packetgen/{validate,conventions,__init__}.py`; the kit schemas are byte-identical to `server/contracts/schemas/`.
- Fail if: any §8 command fails; any prompt breaks a §3.4 rule; any file > 5 MB; a second upload engine exists in `ios_kit/`; a schema in the kit differs from WS-00's export.
- Report at `internal/build/validation/ws-14.md` (store frontmatter): table `item | command | last output line | pass/fail`, deferred items, hand-inspection notes, and the `[default chosen]` items the validator disagrees with.

## 10. Constraints

- Vision models anywhere in this repo are GPT-5.5 and Grok 4.7 only (no Anthropic); WS-14 itself makes no model calls.
- Never commit real meter numbers, Base SSR prompt text (`internal/criteria/SSR_Prompts.md`, `ssr-quick-reference-notion.md`), API keys or tokens (env names only: `CAPTURE_API_TOKEN`, `CAPTURE_API_URL`), fixture `_truth/` sidecars, or any file over 5 MB. Never paste a secret value into docs, logs, `run.json` or the PR.
- Target device is a regular non-Pro iPhone (no LiDAR); the kit's default fixture and examples are the `arkit` tier. LiDAR-first when present (`arkit_lidar` mini fixture), image fallback otherwise. Photo flow: up to ~60 ordinary photos in one packet; prompts and README never assume LiDAR or a Pro phone.
- No customer tools: no prompt, README line or default may ask the homeowner to measure, use a tape, a ladder or any tool; `memberActions` are only `retake_meter_closeups` and `truedepth_meter_scan`.
- Cost: this WS spends no VLM or GPU budget; each replay against the demo costs one pipeline run (measured $0.05–0.15 GPU + ≈ $0.30 VLM with the B3 cascade, hard cap $1.50 per run, SD-38); do not loop replays against the demo, use the fake or `make e2e-local`.
- Privacy: fixture packets are synthetic and public-dataset derived; real customer packets never enter `ios-kit/` or a test; the README tells the app to ask for consent before capture (spec §5 rule 7) and to delete local files only after finalize returns `processing` and every file is committed.

## 11. Out of scope and handoffs

- API handlers, storage and state backends, signed URLs, `/v1/healthz`, the confirmations route (SD-21): WS-01 (the fake in §3.6 is a contract double). Server-side packet validation and `format_version_unsupported`: WS-02. Stub verdict, stages, `capture_check` timing: WS-03. The hint and `capture_check` code lists: WS-00 `server/contracts/capture_check.py` (SD-20; WS-14 mirrors them in the catalogue and confirms the wording with the iOS team).
- Result composition, `frameToArkit`, polygons and camera poses at runtime, `copy.py` (loads the catalogue from `HSS_RESULT_PROMPTS_PATH`, mirrors `select_prompt`/`render`, writes `promptId` + `prompt {title, body}` and `viewingAngleDeg`), APNs, webhook, reviewer view: WS-10; the headline texts of §3.4 stay identical on both sides.
- Replay engine, `ReplayRun`, gates, `make e2e`, `make e2e-local`, `--token-env` (SD-36, done): WS-11. Remaining request to WS-11: `replay(..., amend_of: str | None = None, answers: list[str] = [])`.
- Contracts, schemas, `data/fixtures` policy, `make validate`, `make hygiene`: WS-00 (spec PRs go through it). Fixture rebuilds, meter close-up fixtures, the real-house packet: WS-13 / packetgen. Fixture mirror `gs://hss-fixtures-<env>` and the demo URL: WS-12.
- Decisions owed by Base (plan §9): whether the homeowner sees `reject`; the rules profile per utility; whether photos may go to OpenAI/xAI. Owed by Hunter: photo flow inside the app; GitHub app access.

## 12. Open questions and defaults

Spec §7 questions, the default the app assumes, and what the server does when the app deviates:

| # | Default to assume | Server behaviour on deviation |
|---|---|---|
| 1 originals leave the phone | yes: keyframes and stills upload during the scan; local copies kept until acknowledged | listed files never committed → finalize answers `202 awaiting_files`, the capture expires after 24 h; uploaded files not in `files[]` are ignored; photos go only to the bucket and the vision APIs Base approves |
| 2 minimum iOS | iOS 16+; below, omit `exif` and `highResolution` | omitted fields are optional; nothing fails |
| 3 high-res stills | `captureHighResolutionFrame` for `meter_close`, `meter_oblique`, `panel_*`, linked to a keyframe | plain stills accepted; a close-up without `keyframe` → warning `scale_no_keyframe_link`, weaker scale |
| 4 location and compass | full-accuracy location, Core Motion `xTrueNorthZVertical` | absent streams are optional; never used for distances |
| 5 `CLHeading` meaning | trust `CMDeviceMotion.heading`; `CLHeading` is a cross-check | ignored when inconsistent |
| 6 half-pixel | continuous coordinates, (0, 0) = corner of the top-left pixel | tap replay > 1e-3 → `422 tap_replay`; the mini fixtures are the reference |
| 7 distortion | pinhole, no distortion | none |
| 8 LiDAR | raw `sceneDepth` + confidence on every keyframe; mesh optional | `sceneDepthEnabled` with a keyframe lacking `depth` → `422 depth_missing`; `smoothed: true` accepted |
| 9 resets | keep earlier keyframes with their epoch id | fewer frames, more `views_needed` |
| 10 taps | record `frozen` and `hit.target` for every tap | optional; the `meter` tap is required when the flow reached it |
| 11 motion rate | 100 Hz, 50 Hz acceptable | `imuRaw.rateHz < 50` fails the schema |
| 12 feature points | export `rawFeaturePoints` per keyframe | optional |
| 13 admission records | optional and reserved | ignored |
| 14 clocks | `ARFrame.timestamp` and Core Motion share device uptime | stream/keyframe time mismatch → warning, streams down-weighted |
| 15 photo-only fallback | `stills[].gravity` and `stills[].location` attached | optional; photo flow runs on EXIF alone (`intrinsics_assumed` when no focal) |
| 16 TrueDepth and ARKit | pause, scan ~2 s, resume; a new epoch is fine | scan frames without world pose are expected |
| 17 raw IMU cost | 100 Hz `imuRaw` on top of `motion`; drop `motion` to 50 Hz first | `imuRaw` is required in ARKit tiers; a packet without it fails the schema |

WS-14 defaults beyond the spec (all `[default chosen]`, listed in `internal/build/layout-decisions-ws-14.md`): kit layout and the standalone `packetgen validate`; the contract fake; `application/gzip` for streams; amendments carry the parent's `formatVersion`; compatibility window one previous minor for 30 days; ground 1.40 m below the session start in the examples; catalogue limits 40/140 characters and the `{side}` threshold ±0.3 m; the app saves an `ARWorldMap` at session end. Fixed by WS-00 and the seam decisions and followed here: `flow: guided|photo`, `SHOW_REJECT_TO_HOMEOWNER=false`, `RULES_PROFILE_DEFAULT=C`, `format_version_unsupported`, `pathPrefix`, camelCase for the whole result including `outcome` (SD-25), one `ViewNeededDevice` shape with `viewingAngleDeg` (SD-27), `RejectReason` with `measuredFt`/`evidence {images, boxesPx}` (SD-26), 10 event types with camelCase `data` (SD-19), WS-00's code lists (SD-20), the confirmations route (SD-21), `--token-env` (SD-36), optional omitted `verdict`. If WS-00 or Hunter overrules any of these, change the kit and the layout-decisions file, never `server/`.
