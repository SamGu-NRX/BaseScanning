# Every state the app renders, over a real socket

The iOS processing UI renders eight states while a scan runs. This page names
each one, the exact command that produces it on `make local-api`, and what the
API serves; the e2e suite `backend/tests/test_local_app_states_e2e.py` drives
all of them over TCP in CI, and `evidence/burst11-bs-local-app-states-run/`
carries a recorded terminal walk per state (`walk-<state>.gif`) plus
reviewer-page screenshots. Screen needs come from
`ios/HouseScanKit/Sources/HouseScanKit/Processing/PhotoProcessingStatus.swift`
and the copy in `ios/HouseScan/UI/Copy/ProcessingCopy.swift`.

## The journey every state goes through

One server, one capture, the documented front door — with the app token from
`backend/runs/local-api/session.json` (`TOKEN`, below) and a retagged copy of
the committed packet (`data/fixtures/packets/mini-arkit`):

```bash
PID=local-app-states-eligible
CAPTURE_ID=$(curl -s -X POST http://127.0.0.1:$PORT/v1/captures \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $PID" \
  -H "Content-Type: application/json" -d @create.json | jq -r .captureId)

curl -s -X POST http://127.0.0.1:$PORT/v1/captures/$CAPTURE_ID/files \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d @files.json                          # register: sealed hashes per file
# ... one signed PUT per file, then:
curl -s -X POST http://127.0.0.1:$PORT/v1/captures/$CAPTURE_ID/files:commit \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d @commit.json

curl -s -X POST http://127.0.0.1:$PORT/v1/captures/$CAPTURE_ID/finalize \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $PID" \
  -H "Content-Type: application/json" -H "X-Packet-Sha256: $SHA" \
  --data-binary @packet/packet.json       # 202 {runId, status: processing}

curl -s "http://127.0.0.1:$PORT/v1/captures/$CAPTURE_ID/events?after=0&wait=5" \
  -H "Authorization: Bearer $TOKEN"       # long-poll to a terminal status
curl -s http://127.0.0.1:$PORT/v1/captures/$CAPTURE_ID/result \
  -H "Authorization: Bearer $TOKEN"       # what the result screen reads
curl -s http://127.0.0.1:$PORT/v1/captures/$CAPTURE_ID/review \
  -H "Authorization: Bearer $REVIEWER_TOKEN"   # the reviewer page
```

`create.json`, `files.json`, `commit.json` and the PUT loop are exactly what
the e2e test and `evidence/burst11-bs-local-app-states-run/walk-*.gif` run;
the recorded walks are the source of truth for both command and output.

## The eight states

| App screen | Command (on `make local-api`) | Capture status when settled | Served outcome |
|---|---|---|---|
| Processing your scan | `HSS_LOCAL_STAGE_DELAY_S=2 make local-api` | `processing` → `manual_review` | stage events stream while the twelve stubs work |
| A possible battery spot | `HSS_LOCAL_OUTCOME=eligible make local-api` | `complete` | `eligible` with a proposed `recommendedPlacement` |
| One more look needed | `HSS_LOCAL_OUTCOME=needs_more_photos make local-api` | `needs_views` | `needs_more_photos` with `viewsNeeded` |
| Needs an installer's review | `HSS_LOCAL_OUTCOME=manual_review make local-api` | `manual_review` | `manual_review` |
| No possible spot found | `HSS_LOCAL_OUTCOME=not_eligible SHOW_REJECT_TO_HOMEOWNER=1 make local-api` | `complete` | `not_eligible`, no placement |
| (reject hidden from the homeowner) | `HSS_LOCAL_OUTCOME=not_eligible make local-api` | `manual_review` | `manual_review` |
| Processing didn't finish | `HSS_LOCAL_OUTCOME=failed make local-api` | `failed` | failed event naming the stage, plain message |
| The processing service refused this scan | any; send a bad packet or token | — (never runs) | 4xx with an error code |
| (answer not ready yet) | any; read before any run | pre-run | not-ready envelope / 404 `result_not_ready` |

## What each screen needs from the API

### Processing your scan

While the run works the events stream carries `stage` events
(`{runId, stage, status, ...}`); the capture status stays `processing` until a
terminal event lands. `HSS_LOCAL_STAGE_DELAY_S=2` paces the twelve stubs at two
seconds each so the working state is observable — and pollable: a long-poll
mid-run returns `status: "processing"` with the stage events so far.

### A possible battery spot (`eligible`)

`complete` capture; `result` answers:

```json
{
  "status": "complete",
  "outcome": {
    "kind": "eligible",
    "message": "Local test stand-in: the proposed placement is scripted, not measured. This is a local test outcome, not a check of a wall.",
    "recommendedPlacement": {
      "wallId": "w1",
      "profile": "C",
      "footprintM": {"w": 0.7874, "h": 1.0033, "d": 0.5588},
      "boxSceneM": {
        "centerM": [0.6937, 0.50165, 0.2994],
        "sizeM": [0.7874, 1.0033, 0.5588],
        "yawDeg": 0.0
      },
      "confidence": 0.5
    }
  },
  "reviewUrl": "/v1/captures/<captureId>/review"
}
```

Worded as proposed: the service's own message says the placement is scripted,
not measured — the answer stays provisional, the AR box and the card render it
as a candidate. The committed pin is
`backend/tests/fixtures/app_states/eligible/result.json`.

### One more look needed (`needs_more_photos`)

`needs_views` capture; `result` carries `viewsNeeded`, each with `id`,
`kind` (`"band"`), a `prompt` (`title`, `body`) and a `why` — the screen shows
the prompt verbatim. Pinned at
`backend/tests/fixtures/app_states/needs_more_photos/result.json`.

### Needs an installer's review (`manual_review`)

`manual_review` capture; `outcome.kind` `manual_review`. This is also where a
`not_eligible` run lands when `SHOW_REJECT_TO_HOMEOWNER` is unset — the reject
screen never shows to the homeowner without the flag.

### No possible spot found (`not_eligible`, flag on)

`SHOW_REJECT_TO_HOMEOWNER=1` lets the capture complete with `outcome.kind`
`not_eligible`, a service message, and no placement.

### Processing didn't finish (`failed`)

The events stream ends with a `failed` event carrying `code`, a plain
`message` (the screen shows it verbatim) and `next: "none"`, after a `stage`
event with `status: "failed"` naming the stage that failed. `result` answers
`status: "failed"`. Pinned at
`backend/tests/fixtures/app_states/failed/result.json`.

### The processing service refused this scan (`refused`)

Two refusals, both before any run exists:

- a malformed finalize body → 4xx with
  `errors: [{code, pointer, message}]` — retrying the same bytes won't change it;
- a missing or wrong bearer → 401 with the same envelope.

### Answer not ready (`not ready`)

Before any run: `result` answers 200 with the not-ready envelope
(`status` still pre-run, `outcome: null`, `viewsNeeded: []`), and the reviewer
page answers 404 `result_not_ready`.

## Regenerating the evidence

`evidence/burst11-bs-local-app-states-run/` holds one recorded walk per state
plus the three reviewer-page screenshots. Each walk is a real asciinema
recording of the journey above against a real `make local-api` server; session
tokens are masked in the recordings. The e2e suite re-proves the same states in
CI without recordings.
