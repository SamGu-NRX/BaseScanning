# WS-01 ingest API

## Purpose

`/v1` capture ingest: create a capture, register files, PUT bytes to a signed URL, commit, finalize `packet.json`, long-poll events, and accept one-tap confirmations. The placement decision is not in this service. Vision models are not called.

## Owner WS and brief

WS-01. Brief: `docs/build/ws-01-ingest-api.md`. Seam rows that name this WS: SD-32 (`capture-api`), SD-43 (`mini-arkit` path), SD-52 (`ConfirmationData.by`). SD-44 (packet.json before `packet.<variant>.json`) is implemented in `server/api/replay.py` even though that row's applies-to list does not name WS-01.

## Inputs / outputs

Inputs: bearer token (`CAPTURE_API_TOKENS`), a capture create body, file bytes, gzip `packet.json` on finalize, confirmation bodies.

Outputs: capture id `cap_` + ULID, upload URLs, commit and finalize bodies, events (`files_committed`, `confirmation`, `failed`), `run_` + sha256 of the packet bytes. Objects land in `uploads/{captureId}/…` and, after acceptance, `captures/{captureId}/…`.

Storage backends: `volume` (this gate), `gcs` (keyless V4 via WIF), `r2`. State: in-process memory for tests, `modal.Dict` when that backend is selected. Terraform modules: `infra/terraform/modules/capture_bucket`, `infra/terraform/modules/modal_wif`.

## How to run on a fixture

```bash
export SMP_PRODUCT_ID=house-scanning SMP_ENVIRONMENT=dev SMP_SERVICE_NAME=capture-api \
  STORAGE_BACKEND=volume VOLUME_ROOT=/tmp/hss-vol \
  CAPTURE_API_PUBLIC_URL=http://127.0.0.1:8000 STATE_BACKEND=memory HSS_INGEST_HOOKS=null \
  HSS_AUTH_MODE=static \
  CAPTURE_API_TOKENS="replay:$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  UPLOAD_URL_HMAC_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
export CAPTURE_API_TOKEN=${CAPTURE_API_TOKENS#replay:}
uv run --extra api uvicorn server.api.app:create_app --factory --port 8000
uv run --extra api python -m server.api.replay \
  --packet .fixtures/packets/eth3d-electro/packet.arkit-nonlidar.json \
  --api http://127.0.0.1:8000/v1 --token-env CAPTURE_API_TOKEN --until processing
```

`create_app` turns off uvicorn's access log. The JSON access line records `/v1/uploads/{token}` and never the token. Pass `--no-access-log` as well if an older uvicorn still prints the raw path.

## How to test

```bash
uv sync --extra api
uv run --extra api ruff check .
uv run --extra api ruff format --check .
uv run --extra api lint-imports
uv run --extra api pytest -q
export PATH="$HOME/.local/bin:$PATH"
for m in capture_bucket modal_wif; do
  terraform -chdir=infra/terraform/modules/$m fmt -check -recursive
  terraform -chdir=infra/terraform/modules/$m test
done
```

`pytest -m live` is the GCS/R2 contract test. It skips when those buckets are unset.

## Rebuild from scratch

The API does not depend on a VM image. `uv sync --extra api` and the commands above are the whole local rebuild. `modal` is imported only inside `server/api/modal_entry.py` and is not required to import `server.api` or to pass the tests. Terraform uses the mock Google provider (`terraform test`); do not apply, and do not create a service-account key. `GCP_AUTH_MODE=sa_key` is documented debt only.

WS-00 contracts from `main` (`66ee387`) are the models this API uses. `server/common` stays in this branch: that PR did not ship storage, state, config, logging, or geometry.

## Last gate table

Observed 2026-09-26 on the volume backend, `HSS_INGEST_HOOKS=null`, after merging `origin/main`. `GIT_SHA` was `dev`. The gate server listened on `127.0.0.1:8766` and was stopped afterward.

| # | Check | Observed |
|---|---|---|
| 1 | `uv run --extra api pytest -q` | 149 passed in 7.89s (includes `server/api/tests`, 65 passed in 5.14s). Catalogue in `server/api/tests/test_ingest.py` and `test_more.py`, including SD-52: device `by=device`, reviewer supersede 201 with `by=reviewer`, event `data.by` set, event `data` has no `value`. `ruff check` and `ruff format --check` pass. `lint-imports` kept the API/packet contract (58 files, 167 deps). |
| 2 | Replay, local volume | `eth3d-electro` `arkit-nonlidar`: committed 95/95, `status=processing`, `runId=run_e7373872be8b63b6`, 0.347 s. Second run: same `captureId` `cap_01M3G4Y3FJSKN2RPV92QSKKQXM` and same `runId`, `replayed=true`, 0.003 s. `arkit-lidar`: 185/185, `runId=run_1c7258c15698d01b`, replay 0.469 s, finalize HTTP 106 ms. `yt-12Ks1idWj40` `photo-only`: 6/6, `runId=run_2bbdb9fc84114c13`, `--finalize-at early` → `earlyStatus=awaiting_files` then `processing`, 0.05 s. `data/fixtures/packets/mini-arkit/packet.json` (SD-43) is not on `main`; that replay was not run. |
| 3 | Terraform | `terraform test` pass for `capture_bucket` (1 passed) and `modal_wif` (1 passed). `terraform fmt -check` pass on both. No GCP project: live GCS was not run. |
| 4 | Unauthorized | `POST /v1/captures` with `{}` and no bearer → 401 `unauthorized`. `GET /v1/healthz` → 200 `{"status":"ok","storage":"volume","state":"memory","wifTokenExpiresAt":null,"version":"dev"}`. |
| 5 | Latency, volume | `POST /files` with 50 entries: 3.3 ms. `files:commit` max 10 ms. Lidar finalize 106 ms. GCS p50 was not measured. |
| 6 | Logs | `rg` of `/tmp/ws01/api-gate.log` for `Bearer `, `X-Goog-Signature`, `serviceAddress`, `rawText`, and `/v1/uploads/ey` → 0 hits. JSON `http.route` for uploads is `/v1/uploads/{token}`. Uvicorn's access logger is disabled. |
| 7 | Modal `dev` | Not run. The Modal CLI is not installed here, and `modal deploy` needs `hss-dev-api` / `hss-dev-gcp`. `server.api` imports without `modal`. |
| 8 | WS-00 `make ci` | `make ci` is not a target. `make schemas` (`export_schemas --check`) passed. Full pytest is row 1. No committed file over 5 MB. |

## Known limits

- Multipart (`files:complete` and the part URLs) returns 501 `multipart_not_implemented`. Follow-up branch: `ws-01/multipart`.
- `STORAGE_BACKEND=volume` until the `house-scanning` GCP project exists. Modules are validated with `terraform test` only.
- `ConfirmationData` matches WS-00 (`confirmationId`, `fieldId`, `imageId`, `kind`, `by`). With a static token or JWT, `by` is `reviewer` when that principal is a reviewer and `device` otherwise (SD-52). When `SMP_ENVIRONMENT=dev` and `HSS_AUTH_MODE` is unset, Settings defaults it to `none` (SD-86): every request is the device principal `demo`, `/result` still masks a hidden reject, and `/review` is reachable with no `Authorization` header. Set `HSS_AUTH_MODE=static` or `jwt` to turn auth back on. Those paths stay in the code and are unused while the mode is `none`. `ResultResponse` has no `profile`; a not-ready result returns empty `viewsNeeded` and `memberActions` and `outcome: null`.
- A second replay of a capture that is already past `uploading` returns the existing ids and does not re-register. Registration itself is rejected with 409 `invalid_state` once status is `processing`.
- `max_containers=1` while state is `modal.Dict`.
- `POST /captures/{id}/finalize` returns 202 after the fast checks (schema, hashes, parent, declared size). Manifest validation, promotion, and `start_run` run on a daemon thread in the same container, off the request path. Status moves to `processing` and a `stage` event (`validate` / `running`) is emitted before that response when every listed file is committed. A manifest problem sets status `failed` and emits `failed` (with `pointer`). A storage 503 restores the previous status so finalize or commit can be retried.
- Hooks are null while `server.orchestrator` is the SD-23 stub (`HSS_INGEST_HOOKS=null`).
