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
| `HSS_ORCH_MODAL_REMOTE` | `0` | a 'local' run never dispatches to deployed Modal functions — remote workers cannot see this process's memory state or volume, and remote-on also flips state to a Modal Dict and storage to GCS (`server/common/deploy_switch.py`) |
| `VISION_EXTERNAL_CALLS` | `off` | no networked model calls |
| `BASE_WEBHOOK_URL` / `WEBHOOK_HMAC_SECRET` | unset | webhooks off |

Pass through without interpretation: `HSS_LOCAL_OUTCOME` and `HSS_LOCAL_STAGE_DELAY_S`. The stage
stubs honor them when the burst11-bs-local-outcome-states packet lands; this launcher never reads
or changes them, so `HSS_LOCAL_OUTCOME=pass make local-api` just works.

### What the launcher refuses from the shell

Everything above pins **where work runs and where bytes go**. The same audit applied to the rest of
the environment turned up eleven keys a developer's shell could export that would do exactly that,
so the launcher drops them before uvicorn imports and prints one line naming what it refused
(key names only, never values):

| Refused key | Why |
|---|---|
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | Modal credentials any code path reaching for Modal would authenticate with |
| `MODAL_IDENTITY_TOKEN` | a Modal identity token that authenticates this process |
| `MODAL_APP_NAME` | names the deployed Modal app a Modal call would target |
| `MODAL_ENVIRONMENT` | selects which Modal environment the Modal client targets |
| `MODAL_TASK_ID` | marks the process as inside Modal and flips packet-cache behavior (`orchestrator/artifacts.py`) |
| `STATE_DICT_NAME` | names the shared Modal Dict state would land in |
| `HSS_PACKET_CACHE` | redirects packet-cache bytes away from the local volume |
| `HSS_ORCH_TEST_FAIL_STAGE` / `HSS_ORCH_TEST_HANG_STAGE` / `HSS_ORCH_TEST_KILL_COORDINATOR_AFTER` | fault-injection hooks a stray export would turn into a mysterious local failure or hang |

Reviewed and left alone, with the reasons: `HSS_LOCAL_OUTCOME` / `HSS_LOCAL_STAGE_DELAY_S` are the
deliberate harness pass-throughs above; `HSS_OBJECTS_CACHE_DIR`, `HSS_READS_CACHE_DIR` and
`HSS_SCALE_METER_MODELS_PATH` are local-disk read paths for stages that run as committed stubs
locally (only `placement` is real, and it reads none of them); `HF_HOME` / `HUGGINGFACE_HUB_CACHE`
are weight-cache locations no local stage downloads into; `CAPTURE_API_URL` / `CAPTURE_API_TOKEN`
are client-side harness variables the server never reads; and `HSS_MODAL_SCHEDULE` and the Modal
runtime keys are only read inside Modal images, which a loopback-only memory-state run never
reaches. `BASE_WEBHOOK_URL` / `WEBHOOK_HMAC_SECRET` stay unset (webhooks off), as before.

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

One session file belongs to one live server. A second `make local-api` against the same session
directory exits with an error while the first is still running (its `pid` is alive and its `baseUrl`
port accepts connections), so clients reading the file keep working credentials, even when the
second launch asks for a different `PORT`. Stop the first server, or pass the launcher a different
`--session-dir`. A file left behind by a server that has exited is replaced.

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
