# server/api

Owner: WS-01. Brief: `docs/build/ws-01-ingest-api.md`.

`create_app()` is the HTTP app. This package imports `server.orchestrator` and does not import `server.packet`.

Inputs: ingest API v1 requests (`server/contracts/api.py`).

Outputs: capture status, upload targets, events, and the device result served from `result/result.json`.
