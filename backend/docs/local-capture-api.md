# The local capture API: one command, loopback only

`make local-api` gives a reviewer, the iOS simulator and `tools/hunter-local-ingest` a working
capture API on `127.0.0.1` from a fresh checkout. It replaces the hand-assembled uvicorn command in
`server/api/README.md`, which ran with `HSS_INGEST_HOOKS=null` — so finalize never reached the
orchestrator and no run ever started.

## Fresh checkout

```bash
git clone https://github.com/SamGu-NRX/BaseScanning.git && cd BaseScanning
git checkout alpha
cd backend
uv sync --frozen --extra api
make local-api            # PORT=8000 default; override with PORT=8123 make local-api
```

Wait for the banner:

```
local capture API ready: http://127.0.0.1:8000
session file: .../backend/runs/local-api/session.json
stand-in stages: validate, poses, objects, reads, scale, dense, coverage, scene, objects_3d, criteria, result
real stage: placement
```

Stop with Ctrl-C. Every run generates fresh tokens; nothing persists across restarts except the
uploaded bytes under `runs/local-api/volume/`.

`make local-api-smoke` proves the same launcher end to end: it starts the script as a subprocess on
an ephemeral port and drives the committed `data/fixtures/packets/mini-arkit` packet over real TCP
with httpx — create, register, PUT to the signed URLs, commit, finalize, long-poll events to
terminal `manual_review`, read the result and the reviewer page — plus the auth and error
boundaries. No TestClient, no `drain_finalize`: the orchestrator is driven inline by `start_run`.

## Environment

| Variable | Value set by `make local-api` | Why |
|---|---|---|
| `SMP_ENVIRONMENT` | `dev` | dev posture; no cloud credentials needed |
| `HSS_AUTH_MODE` | `static` | bearer tokens from `CAPTURE_API_TOKENS` (explicit, because dev otherwise defaults to `none`) |
| `CAPTURE_API_TOKENS` | `device:<app-token>,reviewer:local-review:<reviewer-token>` | generated per run; tokens are ≥32 chars (`server/api/auth.py`) |
| `UPLOAD_URL_HMAC_SECRET` | fresh hex per run | signs the volume upload tokens |
| `STATE_BACKEND` | `memory` | no Modal, no database |
| `STORAGE_BACKEND` | `volume` | signed PUT URLs a client can actually fetch: `{CAPTURE_API_PUBLIC_URL}/v1/uploads/{token}` (`storage_volume.py`), unlike `storage_local.py`'s `file://` URLs |
| `VOLUME_ROOT` | `backend/runs/local-api/volume` | uploaded bytes live under gitignored `runs/` |
| `CAPTURE_API_PUBLIC_URL` | `http://127.0.0.1:<port>` | derived from the bind address, so upload URLs are reachable on this machine |
| `HSS_INGEST_HOOKS` | `auto` | finalize reaches the orchestrator (this is the point) |
| `HSS_ORCH_STAGE_STUBS` | `1` | committed stage stubs instead of GPU/vision work |
| `HSS_ORCH_PREWARM` | `0` | no prewarm work on capture create |
| `VISION_EXTERNAL_CALLS` | `off` | no networked model calls |
| `BASE_WEBHOOK_URL` / `WEBHOOK_HMAC_SECRET` | unset | webhooks off |

Pass through without interpretation: `HSS_LOCAL_OUTCOME` and `HSS_LOCAL_STAGE_DELAY_S`. The stage
stubs honor them to produce every state the app renders — `docs/local-app-states.md` names each
state, its command and what gets served; this launcher never reads or changes the variables.

## The session file

`backend/runs/local-api/session.json`, mode `0600` (the directory `runs/` is gitignored):

```json
{
  "baseUrl": "http://127.0.0.1:8000/v1",
  "appToken": "<bearer for the app and harness>",
  "reviewerToken": "<bearer for the review page>",
  "pid": 12345,
  "gitSha": "<git rev-parse HEAD at start>",
  "stageStubs": true,
  "startedAt": "<UTC ISO-8601>"
}
```

The shape is pinned verbatim by `tests/test_local_capture_api.py` because the iOS run and later
packets read it. Two choices to note: the tokens are written **inline** (`appToken`, not
`appTokenFile`) because a single self-contained file is one less path for every consumer to resolve
against an unknown working directory; and `gitSha` is the full head SHA so a captured session always
identifies the code that served it. Tokens appear nowhere else — not in stdout, not in the uvicorn
access log (which keeps upload URLs as `/v1/uploads/{token}` route shapes anyway).

## What is a stand-in

`HSS_ORCH_STAGE_STUBS=1` runs every one of the twelve orchestrator stages as its committed stub
**except `placement`**, which is `stubs_exempt` (deterministic CPU work, no GPU/network/credentials)
and solves for real. A terminal `manual_review` verdict therefore proves the whole HTTP + finalize +
orchestrator path, not the quality of the vision stages.

## The iOS simulator

The simulator shares the Mac's loopback, so it talks to `http://127.0.0.1:8000/v1` directly — set
the app's capture API base URL and paste the `appToken` from the session file. **A physical phone
does not share the Mac's loopback** and is out of scope: the server refuses any non-loopback bind,
by design.

## tools/hunter-local-ingest

Point it at the session: read `backend/runs/local-api/session.json`, use `baseUrl` as the API base,
`appToken` as the bearer on every request, and register uploads from the packet directory exactly as
the smoke test does. The review page needs the `reviewerToken` bearer:
`GET {baseUrl}/captures/{captureId}/review` with `Authorization: Bearer <reviewerToken>` returns the
rendered HTML.
