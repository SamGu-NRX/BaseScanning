# Orchestrator

Turns a finalized capture into a verdict by running the eleven stages, early workers, prewarm and the sweeper.

Owner: WS-03. Brief: `docs/build/ws-03-orchestrator.md` in the project store. Entry points live in `server.orchestrator` and are what WS-01's hooks call.

## Run locally

```bash
export STATE_BACKEND=memory STORAGE_BACKEND=local HSS_ORCH_STAGE_STUBS=1 VISION_EXTERNAL_CALLS=off SMP_ENVIRONMENT=dev
python -m server.orchestrator.cli run-local --tar .fixtures/packets/eth3d-electro.tar --variant arkit-nonlidar --out /tmp/hss/electro
python -m server.orchestrator.cli dump-run --out /tmp/hss/electro
```

`make fixtures` comes from the skeleton. Without the tar, `run-local` exits 2 and names the missing path.

## State

Runs use `server.common.state.StateStore`. The memory backend is `MemoryState` and local files are `LocalFsStorage`, both from main. `resolve_state` still leaves `modal_dict` to WS-01. `ModalDictState` is a `MemoryState` stand-in so the suite can exercise that backend name without a second store.

## Modal

`python -m server.orchestrator.cli run-modal` and `spend` do not spawn GPU. `server/modal_app.py` is the skeleton app. `server/orchestrator/modal_functions.py` does not construct its own `modal.App`.

## Tests

```bash
uv run --frozen pytest server/orchestrator/tests -q
uv run --frozen ruff check server/orchestrator
make orchestrator-e2e
make ci
```

## Stubs

`HSS_ORCH_STAGE_STUBS=1` runs `server/orchestrator/stubs/<stage>.py` instead of `server.<stage>.run`. Stub verdicts are `manual_review` with empty `views_needed`. The stub review page says every stage is a stand-in.

### Scripted local outcomes (dev only)

With stage stubs on and `SMP_ENVIRONMENT=dev`, `HSS_LOCAL_OUTCOME` scripts the terminal state a stubbed local run reaches — the app can be walked to each state it renders, end to end, without Modal or GPU:

| `HSS_LOCAL_OUTCOME` | Capture status | Notes |
| --- | --- | --- |
| unset / `manual_review` (default) | `manual_review` | Today's stub run, byte-identical. |
| `eligible` | `complete` | Result carries a proposed placement (`boxArkitWorld` + `arkitEpoch` when the packet has one), worded as proposed. |
| `needs_more_photos` | `needs_views` | One scripted band view; the app sends a retake request. |
| `not_eligible` | `complete` | Needs `SHOW_REJECT_TO_HOMEOWNER=1`; without it the boot refuses. |
| `failed` | `failed` | The validate stub fails the run onto outcome row 1 (validation stand-in `imagesUsable: false`). |

Scripted messages and review pages always say it is a local test outcome, not a check of a wall. `HSS_LOCAL_STAGE_DELAY_S` (float, 0–30, default 0) makes each stub sleep so the app shows processing events. Both variables are refused loudly — at boot with an error naming the variable — when stage stubs are off, the environment is not dev, the value is unknown, or the number is out of range. A run keeps its scripted behavior across restart while the variables stay set.
