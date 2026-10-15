# Orchestrator

## Purpose

Run the eleven capture stages, early meter and panel reads, image QA, pose-frusta hints, GPU prewarm and the sweeper. The placement decision stays in the criteria and result stages. This package only schedules them and records state.

## Owner WS and brief

WS-03. Brief: project store `docs/build/ws-03-orchestrator.md`. Branch `ws-03/orchestrator`.

## Inputs/outputs

Inputs: `start_run`, `prewarm`, `dispatch_early`, `on_confirmation`, `cancel_capture` from WS-01; `StateStore` and `Storage` from WS-00; `server.<stage>.run(ctx)` from WS-02 through WS-10, or the stubs in `server/orchestrator/stubs/` when `HSS_ORCH_STAGE_STUBS=1`.

Outputs: `RunRecord`, `StageRecord`, `EventRecord`; `runs/{runId}/run/{timings,cost,stages}.json`; early blobs under `captures/{id}/early/`; `RunRecord.artifacts` naming every stage output.

## How to run

```bash
export STATE_BACKEND=memory STORAGE_BACKEND=local HSS_ORCH_STAGE_STUBS=1 VISION_EXTERNAL_CALLS=off
python -m server.orchestrator.cli run-local --tar .fixtures/packets/eth3d-electro.tar --variant arkit-nonlidar --out /tmp/hss/electro
python -m server.orchestrator.cli dump-run --out /tmp/hss/electro
```

`run-modal` reports Modal unavailable until the SDK, a `dev` token and `server.modal_app` are present. No GPU is spawned from this package on import or on push.

## How to test

```bash
uv run pytest server/orchestrator/tests -q
make orchestrator-e2e
```

The suite covers the state contract on memory and the local Modal Dict stand-in, the ready-set, GPU attempts, outcome rows, staleness, escalation, hard stop and sweeper reattach.

## Rebuild

`server/contracts` and `server/common` (`StateStore`, `MemoryState`, `LocalFsStorage`) come from main. `resolve_state` leaves `modal_dict` to WS-01. `ModalDictState` is a `MemoryState` stand-in so the suite can run that backend name without a second store.

## Last gate table

Contracts are on `main`. Fixture tars and `make validate` / hygiene / score-verdicts are still absent, so §8.2, §8.7 and the fixture-dependent §8.10 targets stay pending. GPU rows stay blocked until `server.modal_app` (WS-12) exists. See the pull request for observed values.

## Known limits

- GPU containers are not spawned here. `server.modal_app` is WS-12 and is not on main.
- Stage packages that have not merged stay on stubs. Stub verdicts are `manual_review`.
- `merge_objects` is an identity no-op until `server.scene` exists.
- Keep-warm reserve uses the L4 rate in `internal/modal-pipeline/app.py` (`0.000222` USD/s), not the brief's `$0.34`.
