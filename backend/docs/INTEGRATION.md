# Integrating with the capture API

For teammates' agents calling the deployed backend. Shapes below are the v1 contract in `server/contracts/api.py` and `server/contracts/result.py`. Those routes are on `main` (PR 34, `93638bf`).

## Current status

Diff this block when the deployment changes. Keep the seven field names and their order.

| Field | Value |
| --- | --- |
| Updated UTC | 2026-09-27T14:40:54Z |
| Live URL | `https://huntertcarver-dev--capture-api.modal.run` (routes under `/v1`) |
| Health check result | `GET /v1/healthz` → 200 `{"status":"ok","storage":"gcs","state":"modal_dict","version":"bc8438301ab6"}` (selected fields). No `Authorization` header. Checked 2026-09-27T14:26:32Z and again 2026-09-27T14:40:54Z. `bc8438301ab6` is the first 12 hex chars of main `bc8438301ab662170c49ba5c5eddd0fcb6521e4b`. |
| Milestone | Real runs are live. One no-token electro `arkit-nonlidar` capture, with dev fixture meter pixels `k00023:989.573,712.173` and anchor `reference_point`, returned a schema-valid `ResultResponse` (`manual_review`, outcome `needs_more_photos`). Every stage completed on attempt 1. Client elapsed, upload through that result, was 662.8 s. Capture `cap_01M3HM8XBNBA7HRTZ8BKZS6ENZ`, run `run_29ef8133acb82c6f`. Create through finalize was 3 min 35 s (expect about 5 min; WS-01 is fixing uploads). The pipeline, finalize accept through the result stage, was 7 min 31 s (expect about 8 min). Completed `GET /result` reads stayed at or under 1.5 s (median about 0.9 s). |
| What works | Health 200 with `storage: gcs`, `state: modal_dict`, and version `bc8438301ab6`, main `bc84383` ([PR 143](https://github.com/huntertcarver/house-scanning-server/pull/143)). `HSS_ORCH_MODAL_REMOTE=1`, `HSS_ORCH_STAGE_STUBS=0`, GPU image `im-744i3sKYjXb3UoVuBs8XoV`. One electro upload returned a schema-valid real result. Result reads on that capture stayed fast. |
| What's placeholder | Create through finalize is still the slow part (this run 3 min 35 s; expect about 5 min). WS-01 is fixing that upload path. Older notes that say an upload only proves stub stages are stale. Multipart completion is `501 multipart_not_implemented`; no APNs push or outbound webhook, so poll `GET /events`. |
| Breaking changes since last update | Live version moved from `3e97f031d109` to `bc8438301ab6`. Real stage spawning is on and stage stubs are off. A capture on this image runs the pipeline instead of the stub review page. |

## Switch for real runs

Deployed. The live image in **Current status** was built with the second step below (`HSS_ORCH_MODAL_REMOTE=1`, `HSS_ORCH_STAGE_STUBS=0`, `HSS_GPU_IMAGE_ID=im-744i3sKYjXb3UoVuBs8XoV`) from main `bc84383`.

Live capture-api has `HSS_ORCH_MODAL_REMOTE=1`, so the coordinator spawns each stage and the container timeout is 1560 s. Poses and dense spawn on the GPU image (`Pipeline` on `GPU_IMAGE`, L4 when `HSS_GPU_IMAGE_ID` is the prebuilt CUDA image). Objects placement is the objects stage's own spec. The coordinator still lives in capture-api and has to outlive the 20-minute hard stop while those calls run.

Capture-api may run more than one container (`max_containers` defaults to 4, one spare `buffer_containers`, `@modal.concurrent` with `target_inputs` 1). Every capture-api container shares one Modal Dict (`STATE_BACKEND=modal_dict`, SD-72) and GCS, so a poll on a second container sees the run the first container created. Create claims the packet idempotency key with one atomic dict insert, so two containers cannot both return 201 for the same packet.

Two steps. From a main checkout:

1. Remote spawn and the GPU image. This bakes `HSS_ORCH_MODAL_REMOTE=1` into capture-api, raises its timeout from 600 s to 1560 s (the same budget as `coordinate_run`), and sends poses and dense to `Pipeline` on `GPU_IMAGE`. The same step bakes `STATE_BACKEND=modal_dict` and `STORAGE_BACKEND=gcs` into capture-api, pipeline, and sweeper, so a worker hydrates the packet the API wrote. Dev still writes `HSS_ORCH_STAGE_STUBS=1` until step 2.

```
HSS_ORCH_MODAL_REMOTE=1 HSS_GPU_IMAGE_ID=im-744i3sKYjXb3UoVuBs8XoV \
SMP_ENVIRONMENT=dev GIT_SHA=$(git rev-parse HEAD) \
uv run modal deploy -m server.modal_app -e dev
```

2. Stubs off. Set `HSS_ORCH_STAGE_STUBS=0` in the same deploy shell. Dev writes `0` only when `HSS_ORCH_MODAL_REMOTE=1` and an explicit `HSS_ORCH_STAGE_STUBS=0` are both set. That value is baked into capture-api, pipeline, and sweeper. Any other dev deploy writes `1`.

```
HSS_ORCH_MODAL_REMOTE=1 HSS_ORCH_STAGE_STUBS=0 HSS_GPU_IMAGE_ID=im-744i3sKYjXb3UoVuBs8XoV \
SMP_ENVIRONMENT=dev GIT_SHA=$(git rev-parse HEAD) \
uv run modal deploy -m server.modal_app -e dev
```

Client wait. With the remote flag on, finalize's processing `etaS` is 1500 s. `awaiting_files` stays 200 s. With the flag unset, processing `etaS` stays 180 s. `scripts/e2e_dev.py --timeout` defaults to 1500 s so a real run fits; pass `--timeout` to override. The kit replay `--timeout` default stays 900 s; pass `--timeout 1500` there for a real run.

`scripts/e2e_dev.py` gives `files:commit` and `finalize` a 900 s read timeout (capture-api allows 1200 s) and retries the same body on a timeout or 503. A 95-file commit can finish on the server after the old 150 s client read has already raised. The commit route returns an already-committed path in `committed`. Finalize of the same packet is not posted again once `GET /v1/captures/{id}` shows a run.

## Fixture meter pixels

Golden-house replays score like an M2 driver run when coverage sees `models['meter_frames']` and `models['anchor_label']`. The live client passes those pixels on create. The server applies them only when `SMP_ENVIRONMENT=dev`. Production accepts the field and does not store it, so a missing meter tap still closes on the first attempt and is not retried (SD-97).

`POST /v1/captures` takes optional `fixtureMeter`:

```json
{"fixtureMeter": {"meterFrames": "k00023:989.573,712.173", "anchorLabel": "reference_point"}}
```

`meterFrames` is the same `id:u,v` text as `HSS_COVERAGE_METER_FRAMES` (commas separate the pixel and the next frame). `anchorLabel` is the packet tap label when it is not `meter`. Electro's tap is `reference_point`.

`scripts/e2e_dev.py` sends that object from `--meter-frames` and `--anchor-label`, or from `HSS_E2E_METER_FRAMES` and `HSS_E2E_ANCHOR_LABEL`.

The process env vars `HSS_COVERAGE_METER_FRAMES` and `HSS_COVERAGE_ANCHOR_FROM_TAP` are the same override for every capture in that process, and only when `SMP_ENVIRONMENT=dev`. Prefer `fixtureMeter` for one replay.

## Changelog

- 2026-09-27T14:40:54Z — `house-scanning` dev is on main `bc84383` with real runs live (`HSS_ORCH_MODAL_REMOTE=1`, stubs off, GPU image `im-744i3sKYjXb3UoVuBs8XoV`). Health version `bc8438301ab6`. One electro non-lidar capture returned a schema-valid `manual_review` in 662.8 s (`cap_01M3HM8XBNBA7HRTZ8BKZS6ENZ`, `run_29ef8133acb82c6f`). Create through finalize was 3 min 35 s (expect about 5 min; WS-01 owns that upload path). The pipeline was 7 min 31 s (expect about 8 min). `GET /result` stayed at or under 1.5 s.
- 2026-09-27T12:10:00Z — `scripts/e2e_dev.py` retries commit and finalize with a 900 s read timeout. Dev replays can pass meter pixels on `fixtureMeter` (or `HSS_E2E_METER_FRAMES`). Prod ignores that override (SD-97). No deploy.
- 2026-09-27T09:40:00Z — The real-run switch still is not deployed. With `HSS_ORCH_MODAL_REMOTE=1`, capture-api, pipeline, and sweeper share Modal Dict state and GCS. Capture-api can scale past one container, and prewarm uses `.spawn.aio` from an async request. Objects placement is not part of this switch.
- 2026-09-27T09:25:18Z — `house-scanning` dev redeployed from main `04be308` (PR 126 and PR 124). Stubs stay on and remote stage spawning stays off. No-auth `GET /v1/healthz` returned 200 with `storage: gcs` and version `04be3089a3d0`. One no-token `mini-arkit` replay returned a schema-valid `manual_review` in 168.0 s (`cap_01M3H24K8PK10GSK00AVATFKCQ`, `run_06cd631bae08c30f`). That run id differs from `run_4dee057d229c1c21`. The client elapsed is longer than the 114.9 s replay on `1468206`. This is not an M2 pass.
- 2026-09-27T08:57:24Z — Prepared the real-run switch in code and left it off. No deploy. Live capture-api still has remote stage spawning unset and a 600 s timeout. The M2 result decides when to set `HSS_ORCH_MODAL_REMOTE=1` with `HSS_GPU_IMAGE_ID` and redeploy, which raises the capture-api timeout to 1560 s.
- 2026-09-27T08:25:32Z — `house-scanning` dev redeployed from main `1468206` (PR 91, including PR 115). No-auth `GET /v1/healthz` returned 200 with `storage: gcs` and version `14682067c0d3`. One no-token `mini-arkit` replay returned a schema-valid `manual_review` in 114.9 s (`cap_01M3GZCCRVHPRS830RYVQCW5C9`, `run_4dee057d229c1c21`). That client elapsed is longer than the 70 s replay on `f1f0b63`. Stubs stay on, so this is not an M2 pass.
- 2026-09-27T07:03:34Z — `house-scanning` dev redeployed from main `f1f0b63` (PR 111). No-auth `GET /v1/healthz` returned 200 with `storage: gcs` and version `f1f0b6394f93`. One no-token `mini-arkit` replay returned a schema-valid `manual_review` in 70 s (`cap_01M3GTQPAS864253AEMFGVYWHM`, `run_4dee057d229c1c21`). Stubs stay on, so this is not an M2 pass. This deploy replaced an intervening unmerged PR 91 image `7803f67d6ee5`.
- 2026-09-27T06:45:07Z — Dev app redeployed from [PR 111](https://github.com/huntertcarver/house-scanning-server/pull/111) head `8875483b0a93` (main `be01020` plus stage package data). No-auth `GET /v1/healthz` returned 200 with `storage: gcs` and that version. Inside the CPU stage image, Jinja rendered `template.html` with the review CSS inlined; the GPU image had the same template, schemas, `profiles.yaml`, and meter-model files. Stubs stay on, so this is not a capture replay and not an M2 pass. Historical M1 evidence stays on `41a0880`.
- 2026-09-27T05:33:50Z — Read-only, no-auth probes observed draft PR 91 image `c786f76cb7af`, health 200, and missing-capture 404. GCS storage and memory state are unchanged. Remote lifecycle findings remain under review; historical M1 evidence stays tied to `41a0880`, and no M2 or new latency measurement is asserted.
- 2026-09-27T04:57:09Z — Read-only, no-auth probes observed image `ed257c3231b8` (main at PR 86), health 200, and missing-capture 404. The image includes WS-12 composition, retry-finalize guidance, and the amendment state fix. Historical M1 evidence remains tied to `41a0880`; no M2 or new finalize timing is asserted.
- 2026-09-27T04:04:37Z — Read-only, no-auth probes observed image `b25cd7e365a8`, health 200, and missing-capture 404. Main at PR 44 includes PR 72 background finalize; distinguish its asynchronous manifest errors from synchronous request checks. The prior M1 measurement remains evidence for `41a0880`; no new end-to-end milestone is claimed.
- 2026-09-27T03:30:22Z — M1 passed on the live URL. No-token `make e2e-dev` against image `41a0880` returned a schema-valid `ResultResponse` (`manual_review`, stub stages, 59 s). Finalize currently takes about 60 s to respond; a fix is coming. PR 53 is on `main` (`87b7c60`) and is not in this image.
- 2026-09-27T03:28:30Z — PR 35 is on `main` (`0a7878b`, scale layer). The live image is still `41a0880bc928` and was not rebuilt.
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

The live version is the one in **Current status**: main `bc84383`, real stages on. Create through finalize is still slow (about 5 minutes; the electro run on this image was 3 min 35 s). WS-01 is fixing that upload path. A finished upload does exercise the real result renderer.

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

`status` is `awaiting_files` until every listed file is committed, then `processing`. A 202 accepts the packet; it does not prove validation or reconstruction succeeded. JSON/schema/tier checks and comparisons against registered file digests/sizes can still return synchronous `422`. Manifest validation, promotion, and run startup execute in the background (PR 72); manifest errors such as `tap_replay`, `depth_size`, `confidence_size`, `features_size`, and WS-02 manifest codes arrive as `failed` events after acceptance. Continue polling status/events. A different packet under the same idempotency key → `409 packet_sha_conflict`; body over the limit → `413 payload_too_large`. Checks that need uploaded bytes (hashes, image headers, stream contents) also fail through events.

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
