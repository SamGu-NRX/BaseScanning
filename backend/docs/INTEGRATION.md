# Integrating with the capture API

For teammates' agents calling the deployed backend. Shapes below are the v1 contract in `server/contracts/api.py` and `server/contracts/result.py`. Those routes are on `main` (PR 34, `93638bf`).

## Current status

Diff this block when the deployment changes. Keep the seven field names and their order.

| Field | Value |
| --- | --- |
| Updated UTC | 2026-09-27T03:19:47Z |
| Live URL | `https://huntertcarver-dev--capture-api.modal.run` (routes under `/v1`) |
| Health check result | `GET /v1/healthz` → 200 `{"status":"ok","storage":"gcs","state":"memory","wifTokenExpiresAt":"2026-09-29T03:14:16.000Z","version":"41a0880bc928"}`. No `Authorization` header. Checked 2026-09-27T03:19:47Z. |
| Milestone | M1. The live image is `41a0880` (`main` at PR 56), which includes the capture routes (PR 34) and WS-03 (PR 29, stage stubs in-process). PR 57 (`bcc1baf`) and PR 59 (`8421c8b`) are on `main` and are not in this image. M2 is not live. |
| What works | Health check above. A missing capture without `Authorization` returns `404 {"errors":[{"code":"capture_not_found"}]}` (auth off, SD-86), rechecked 2026-09-27T03:19:47Z. Those routes are on this live image. |
| What's placeholder | Stage handlers may be stubs until M2 (`server/orchestrator/stubs` on `main`; SD-73, SD-81). Multipart completion returns `501 multipart_not_implemented` (follow-up branch `ws-01/multipart`). No APNs push and no outbound webhook; poll `GET /events`. Live state backend is `memory`, storage is `gcs`. This image does not include PR 57 or PR 59. On `main`, dev with `HSS_AUTH_MODE` unset is auth mode `none` (PR 59). |
| Breaking changes since last update | Live version moved from `e8b7bcb87cc0` to `41a0880bc928`. The missing-capture response is unchanged. |

## Changelog

- 2026-09-27T03:19:47Z — Live health is version `41a0880bc928` (`main` at PR 56). A missing capture with no `Authorization` header is still `404 capture_not_found`. PR 57 (`bcc1baf`) and PR 59 (`8421c8b`) are on `main` and are not in this image.
- 2026-09-27T03:14:29Z — PR 34 is on `main` (`93638bf`), so the capture routes are no longer waiting on that PR. PR 29 stays on `main` (`6f5ff86`). Live health rechecked at 03:13:03Z: still version `e8b7bcb87cc0`. PR 55 is on `main` (`e941928`): kit replay sends no `Authorization` header when `CAPTURE_API_TOKEN` is unset. PR 54 is on `main` (`321e482`).
- 2026-09-27T03:06:17Z — First note. Demo auth is off. Live health: `storage=gcs`, `state=memory`, version `e8b7bcb87cc0`. Capture routes are deployed on that image and coming with PR 34 onto `main`. iOS kit is on `main` via merged PR 43. Kit replay still requires `CAPTURE_API_TOKEN` on `main`; dropping that header is open PR 55.

## Base URL and auth

Base URL: `https://huntertcarver-dev--capture-api.modal.run`

Every path below is under `/v1`. JSON bodies are UTF-8. Every response carries `X-Request-Id`.

Auth is off on this demo stack (SD-86). Send no `Authorization` header. The server treats every caller as the device principal `demo`. Hidden-reject masking on `/result` still applies. `/review` is reachable without a token. Static tokens and JWT remain in the code and are not configured here.

Errors are `ApiError`:

```json
{"errors": [{"code": "capture_not_found", "pointer": "/", "message": "capture not found"}]}
```

`pointer` is an RFC 6901 pointer into the body, or `/headers/<name>` / `/query/<name>`.

## Where the contracts live

| What | Repo path |
| --- | --- |
| HTTP bodies (create, files, commit, finalize, events, amendments, confirmations, devices, health) | `server/contracts/api.py`, exported `server/contracts/schemas/api.v1.schema.json` |
| Capture packet (the `packet.json` body) | `docs/capture-packet-spec.md` (v0.4) and `server/contracts/schemas/capture-packet.v0.4.schema.json` |
| Result, including AR anchors | `server/contracts/result.py`, `server/contracts/schemas/result.v0.schema.json` |
| iOS kit copies of those schemas | `ios-kit/schemas/` |
| Scene and verdict documents linked from the result | `docs/scene-and-verdict-format.md`, `server/contracts/schemas/scene.v0.schema.json`, `server/contracts/schemas/verdict.v0.schema.json` |

Regenerate the check with `uv run python -m server.contracts.export_schemas --check`. The kit zip copies the packet spec and schemas; do not hand-edit `ios-kit/schemas/`.

## Capture flow

Checked against `main` at `a917d39`. The live image `41a0880` serves these routes. It does not include later `main` commits PR 57 and PR 59.

Upload order that the replay client uses: meter and panel stills first; each keyframe JPEG immediately followed by its depth, confidence, and features; other stills; streams and mesh; TrueDepth with the meter stills; finalize (finalize may run before the last commits).

### 1. Create

`POST /v1/captures`. Header `Idempotency-Key` must equal `packetId`.

```json
{"packetId": "6f3c1d7e-2b0a-4a4e-9a51-0f1e2d3c4b5a", "formatVersion": "0.4", "tier": "arkit",
 "flow": "guided", "device": {"model": "iPhone16,1", "systemVersion": "18.2", "appVersion": "1.4.0"},
 "orderRef": "example-order", "expected": {"keyframes": 150, "stills": 10}}
```

`flow` is `guided` for ARKit tiers and `photo` for `photo_only`. `formatVersion` other than `"0.4"` → `422 format_version_unsupported`.

```json
201 {"captureId": "cap_01J9Z6K4", "status": "uploading",
     "upload": {"maxSinglePutBytes": 16777216, "partBytes": 8388608, "urlTtlS": 3600, "maxFiles": 2000, "maxBatch": 50},
     "eventsUrl": "/v1/captures/cap_01J9Z6K4/events", "finalizeBy": "2026-09-28T03:06:17Z"}
```

Same key and same body → `200` with the same body. Same key and a different body → `409 idempotency_conflict`. Missing key → `422 idempotency_key_missing`. Too many open captures → `429 too_many_open_captures`.

`GET /v1/captures/{id}` returns `CaptureStatusResponse` (`captureId`, `packetId`, `status`, `tier`, `flow`, `createdAt`, `finalizeBy`, `filesRegistered`, `filesCommitted`, `runId`) plus `ETag`. `If-None-Match` can yield `304`. Unknown id → `404 capture_not_found`. Deleted → `410 capture_deleted`.

### 2. Register files

`POST /v1/captures/{id}/files`. 1–50 files.

```json
{"files": [
  {"path": "stills/s01.jpg", "bytes": 3120448, "sha256": "<64 hex chars>", "md5": "<base64 of 16 bytes>",
   "role": "still", "contentType": "image/jpeg",
   "meta": {"still": {"id": "s01", "purpose": "meter_close", "img": "stills/s01.jpg"}}}
]}
```

`role` is the packet's `files[].role`: `keyframe`, `depth`, `confidence`, `features`, `still`, `stream`, `truedepth`, `mesh`, `compat`, `other`. `keyframe_image` → `422 role_unknown`. `meta` is exactly one of `keyframe`, `still`, or `trueDepthFrame`, and only for images. Depth, confidence, features, streams, and mesh omit `meta`.

```json
200 {"files": [
  {"path": "stills/s01.jpg", "state": "pending",
   "upload": {"method": "PUT", "url": "<signed url>", "headers": {"Content-Type": "image/jpeg", "Content-MD5": "<md5>"}, "expiresAt": "<IsoTime>"}}
]}
```

A committed path registered again with the same `sha256` comes back `state: "committed"` (refresh is unnecessary). A different `sha256` → `409 sha256_conflict`. Other `422` codes include `batch_too_large`, `path_invalid`, `path_duplicate`, `bad_md5`, `content_type_invalid`, `meta_mismatch`, `file_too_large`, `capture_too_large`, `too_many_files`. `409` also includes `not_in_manifest` and `invalid_state`.

Files larger than `maxSinglePutBytes` (16 MiB) are meant to be multipart. On `main` that path returns `501 multipart_not_implemented`. Do not depend on `method: "MULTIPART"` until `ws-01/multipart` lands.

### 3. PUT the bytes

`PUT` the `upload.url` with exactly the returned headers and the file bytes. Storage PUTs do not use the API bearer (and this demo does not use a bearer at all).

- `200` — stored.
- `400 BadDigest` — re-read the file and retry once.
- `403` with an expired signature — register the path again, then PUT the new URL.
- Volume backend only: the URL is `PUT /v1/uploads/{token}` on this API. The token is the credential. `GET /v1/uploads/{token}` is on `main`; a PUT token answered as GET returns `403 token_invalid`.

Signed PUTs write `uploads/{captureId}/{path}`. After the packet is accepted, the server copies each file to `captures/{captureId}/{path}`, which is what the pipeline reads.

### 4. Commit

`POST /v1/captures/{id}/files:commit`, about every 2 seconds or 10 files. 1–50 paths.

```json
{"files": [{"path": "stills/s01.jpg", "sha256": "<64 hex chars>"}]}
```

```json
200 {"committed": ["stills/s01.jpg"], "notFound": [], "mismatch": []}
```

`mismatch` means re-upload that path. `409 sha256_conflict` or `409 invalid_state` are also possible.

### 5. Finalize with `packet.json`

`POST /v1/captures/{id}/finalize`. Headers: `Idempotency-Key: <packetId>`, optional `X-Packet-Sha256` (sha256 of the body bytes), optional `Content-Encoding: gzip`. Body is the capture packet (`docs/capture-packet-spec.md`).

```json
202 {"status": "awaiting_files", "missing": ["keyframes/k00148.jpg"], "runId": "run_7Q2", "etaS": 200}
202 {"status": "processing", "missing": [], "runId": "run_7Q2", "etaS": 180}
```

`status` is `awaiting_files` until every listed file is committed, then `processing`. Manifest failures are synchronous `422` (`schema`, `tier_rule`, `format_version_unsupported`, `sha256_mismatch`, `file_size_mismatch`, `tap_replay`, `depth_size`, `confidence_size`, `features_size`, and the WS-02 manifest codes). A different packet under the same idempotency key → `409 packet_sha_conflict`. Body over the limit → `413 payload_too_large`. Checks that need the bytes (hashes, image headers, stream contents) fail later as a `failed` event, not a late 422.

### 6. Events long-poll

`GET /v1/captures/{id}/events?after=<seq>&wait=<0-25>`

```json
200 {"status": "uploading", "next": 43, "events": [
  {"seq": 42, "type": "hint", "at": "2026-09-27T03:06:17Z",
   "data": {"code": "meter_plate_unreadable", "message": "<catalogue text>", "retake": "meter_close", "promptId": "hint.meter_plate_unreadable"}}
]}
```

Pass `after` as the previous `next` (the last seq you have). `wait` is seconds, capped at 25. Too many concurrent polls → `429 too_many_polls`.

Event `type` is one of: `files_committed`, `hint`, `capture_check`, `stage`, `verdict_ready`, `retake_request`, `review_started`, `review_completed`, `failed`, `confirmation`. `data` is camelCase. This long-poll is how the demo reports progress. There is no push.

### 7. Result

`GET /v1/captures/{id}/result` → `200` `ResultResponse` (next section), or a minimal body while the run has not written one. `404` / `410` match status. Signed `verdictUrl` and `previewUrl` last 15 minutes.

`GET /v1/captures/{id}/review` → `200 text/html` when a review page exists, else `404 result_not_ready`.

### 8. Amendments

Only while status is `needs_views` or `manual_review`. One open amendment at a time.

`POST /v1/captures/{id}/amendments` with `Idempotency-Key: <amendmentId>`.

```json
{"amendmentId": "6f3c1d7e-2b0a-4a4e-9a51-0f1e2d3c4b5a", "answers": ["vn3"]}
```

```json
201 {"amendmentId": "6f3c1d7e-2b0a-4a4e-9a51-0f1e2d3c4b5a", "pathPrefix": "amendments/<amendmentId>/"}
```

Replay of the same key → `200`. `answers` must name ids from `viewsNeeded` or `memberActions` (`422 answers_unknown`). Another amendment already open → `409 amendment_open`.

Upload the new files under `pathPrefix` with the same register, PUT, and commit calls. Then `POST /v1/captures/{id}/amendments/{aid}/finalize` with a v0.4 packet whose `parentPacketId` is the parent's `packetId` and whose `formatVersion` matches the parent. Response is the same `202 FinalizeResponse`. Unknown amendment → `404 amendment_not_found`.

### 9. Confirmations

`POST /v1/captures/{id}/confirmations`

```json
{"fieldId": "meter_number", "kind": "confirmed", "value": "EXAMPLE", "imageId": "s01", "clientAt": "2026-09-27T03:06:17Z"}
```

`fieldId` is `meter_number`, `panel_brand`, or `main_breaker_amps`. `kind` is `confirmed`, `corrected`, or `none`. `value` is required unless `kind` is `none`, and must match `^[A-Za-z0-9 ./-]{1,32}$`. Do not send a real meter number in examples, logs, or commits.

```json
201 {"confirmationId": "cfm_01J9Z6K4", "fieldId": "meter_number", "imageId": "s01",
     "kind": "confirmed", "by": "device", "receivedAt": "2026-09-27T03:06:17Z", "seq": 44}
```

The same body replays as `200`. A different answer → `409 confirmation_conflict`. Capture already `complete`, `failed`, or `expired` → `409 capture_closed`. Missing value → `422 value_required`. Unknown image → `422 image_unknown`. `by` is set by the server (`device` on this demo).

`GET /v1/captures/{id}/confirmations` → `200 {"confirmations": [ ...same fields plus value ]}`.

### 10. Devices

`POST /v1/devices` → `204`.

```json
{"token": "<apns device token>", "platform": "ios", "bundleId": "example.bundle"}
```

Registration is stored. This demo does not send pushes. The app learns that a result is ready from `/events`.

### 11. Delete

`DELETE /v1/captures/{id}` → `204`, including when the capture is already gone. Later reads return `410 capture_deleted`.

### Health

`GET /v1/healthz` → `200` or `503`.

```json
{"status": "ok", "storage": "gcs", "state": "memory", "wifTokenExpiresAt": "<IsoTime or null>", "version": "<git sha>"}
```

`status` is `ok` or `degraded`. `503` uses `status: "degraded"`.

## Result format and AR anchors

`GET /v1/captures/{id}/result` is `ResultResponse`: camelCase, `outcome` included. The full verdict file is not embedded; `verdictUrl` points at the snake_case verdict. The device must not be given `result.internal.json` or `verdicts/*`.

`status` is `uploading`, `awaiting_files`, `processing`, `needs_views`, `complete`, `manual_review`, `failed`, or `expired`. `outcome` is null while status is `uploading`, `awaiting_files`, or `processing`.

`outcome.kind` is `eligible`, `needs_more_photos`, `not_eligible`, or `manual_review`. With the default hidden reject, a reject is shown as `manual_review` with empty `reasons`, failing criteria shown as `unsure` without measurements, and `verdictUrl` null (SD-69).

AR anchors are in ARKit world coordinates (gravity-aligned, +y up, origin at session start, column-major 4×4). They are valid only while the phone's current ARKit world is `outcome.arkitEpoch` (same running session, or an `ARWorldMap` saved at that epoch's end). Otherwise render `prompt` and `referenceCrop` and ignore the matrices.

- `outcome.frameToArkit` — 16 numbers, column-major. Inverse of the scene's `frame.from_source` similarity. Maps scene metres into that ARKit epoch. Null when the scene source is not an ARKit epoch (`arkit_world`, `arkit_world/<epoch>`, or `arkit_world:<epoch>`).
- `outcome.recommendedPlacement.boxArkitWorld` — `{pose: [16], size: [w, h, d]}`. `pose` is box-centre-to-world: local +x along the wall (left to right from outside), +y up, +z out of the wall. `size` is metres in ARKit units. Present for `eligible`.
- `outcome.cableRoutePolylineArkit` — metres, at least two `[x, y, z]` points, or null.
- `viewsNeeded[]` and `outcome.viewsNeeded[]` are the same objects. Each may include `targetRegionArkit` (`surface`: `wall`, `ground`, or `free`; `polygon`: at least three points, planar, ARKit world) and `suggestedCameraPoseArkit` (camera-to-world, camera looks along −z). `viewingAngleDeg` is `[min, max]` degrees from head-on. `prompt.title` and `prompt.body` are rendered text; show them verbatim. Photo flow sets `referenceCrop` (`image`, `boxPx`) instead of anchors.

`memberActions` is only `retake_meter_closeups` and `truedepth_meter_scan`.

Illustrative `eligible` fragment (identity pose, not a measured house):

```json
{"runId": "run_example", "status": "complete",
 "outcome": {"kind": "eligible", "profile": "C", "message": "<catalogue text>",
   "arkitEpoch": "e1",
   "frameToArkit": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],
   "recommendedPlacement": {"wallId": "w1", "startSM": 1.2,
     "footprintM": {"w": 0.7874, "h": 1.0033, "d": 0.5588},
     "boxSceneM": {"centerM": [1.59, 0.50, 0.30], "sizeM": [0.7874, 1.0033, 0.5588], "yawDeg": 0},
     "boxArkitWorld": {"pose": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1], "size": [0.7874, 1.0033, 0.5588]},
     "confidence": 0.8, "profile": "C"},
   "cableRoutePolylineArkit": [[0.0, 0.3, 0.0], [1.0, 0.3, 0.0]],
   "viewsNeeded": [], "reasons": []}}
```

Swift: `simd_float4x4(columns:)` from the 16 numbers. Scene point to world: `world = frameToArkit * simd_float4(scene, 1)`.

## iOS kit: fake API and replay

PR 43 is merged. The kit is `ios-kit/` on `main`. Commands run from that directory against the repo checkout.

Fake API (local double of the upload sequence; files land under `--root`):

```bash
cd ios-kit
uv run --project .. python -m ios_kit.fake_api --port 8765 --root /tmp/fake-captures --result-kind needs_more_photos
```

On `main` (PR 55, `e941928`), the fake defaults to no token. Do not pass a real credential. `--token T` requires that exact bearer. Optional knobs: `--verdict-delay-s` (default 5), `--stage-interval-s`, `--hint-delay-s`, `--capture-check-delay-s`. `--result-kind` selects `ios-kit/examples/results/<kind>.json`.

The fake signs URLs at itself (`PUT /v1/uploads/{token}`), runs the vendored packet check on finalize, and then emits `files_committed`, one `capture_check`, the eleven stage events, and `verdict_ready`.

Replay client (uploads a packet file by file, then polls):

```bash
cd ios-kit
uv run --project .. python -m ios_kit replay \
  --packet fixtures/mini-arkit/packet.json \
  --api http://127.0.0.1:8765/v1 \
  --until processing
```

Against the demo, `--api` is `https://huntertcarver-dev--capture-api.modal.run/v1`. On `main` (PR 55), an unset `CAPTURE_API_TOKEN` sends no `Authorization` header, which matches this demo (SD-86). Do not commit a token. Prefer `--dry-run` to print the upload plan without calling the network.

Useful flags: `--rate` (default 2), `--concurrency` (default 4), `--finalize-at last|early`, `--until processing|verdict`, `--timeout` (default 900), `--out DIR`, `--jsonl`, `--skip-validate`. `--amend` exits `7` on `main` (the engine hook is not in the kit yet). Exit `0` means a valid result; `2` invalid result; `3` bad local packet; `4` API 4xx; `5` timeout; `6` network failure.

There is also a server-side replay entry point on `main` (PR 34): `python -m server.api.replay`.

## Limits and client retries

At most 2,000 files per capture, 50 per register or commit call, 16 MiB per single PUT, 256 MiB per file, 1 GiB per capture, URL lifetime 60 minutes, finalize within 24 hours, 20 open captures per principal.

| Response | What to do |
| --- | --- |
| Network error, timeout, 408, 429, 5xx | Back off with jitter, honour `Retry-After` |
| Storage 403, expired signature | Register the path again, PUT the new URL |
| Storage 400 `BadDigest` | Re-read the file and retry once |
| 409 on create or finalize | Conflicting idempotency replay; do not retry the same key with a different body |
| 422 on finalize | Fix the packet; the server keeps the files |
| 501 `multipart_not_implemented` | Keep the file under 16 MiB, or wait for `ws-01/multipart` |
