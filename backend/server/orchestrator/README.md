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

`HSS_ORCH_STAGE_STUBS=1` runs `server/orchestrator/stubs/<stage>.py` instead of `server.<stage>.run`. Stub verdicts are `manual_review` with empty `views_needed`.
