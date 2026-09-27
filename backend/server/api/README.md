# server/api

Owner: WS-01. Brief: `docs/build/ws-01-ingest-api.md`.

`create_app()` is the HTTP app. This package does not import `server.packet`. Hooks call `server.orchestrator` only when `HSS_INGEST_HOOKS=auto`. The default is `null`, because the orchestrator stubs still raise.

`/v1` ingest for a house scan: create a capture, PUT files to signed URLs, commit, finalize `packet.json`, long-poll events.

Local volume backend (no GCP project required):

```bash
SMP_ENVIRONMENT=dev STORAGE_BACKEND=volume VOLUME_ROOT=/tmp/hss-vol \
  CAPTURE_API_PUBLIC_URL=http://127.0.0.1:8000 STATE_BACKEND=memory HSS_INGEST_HOOKS=null \
  HSS_AUTH_MODE=static \
  CAPTURE_API_TOKENS="replay:$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  UPLOAD_URL_HMAC_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
  uv run uvicorn server.api.app:create_app --factory --port 8000
```

`server.api` imports without calling Modal. `modal deploy -m server.api.modal_entry` is for a machine that has the Modal CLI and the `hss-dev-api` / `hss-dev-gcp` secrets. The app label is `capture-api`.

Multipart signed URLs answer `501 multipart_not_implemented` in this PR. The follow-up branch is `ws-01/multipart`.

Tests: `uv run --extra api pytest server/api/tests -q`.
