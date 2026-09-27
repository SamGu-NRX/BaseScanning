# server/orchestrator

Owner: WS-03. Brief: `docs/build/ws-03-orchestrator.md`.

Stubs: `start_run`, `prewarm`, `dispatch_early`, `on_confirmation(..., *, by)`, `cancel_capture`, `sweep`, and `validate_manifest` re-exported from `server.packet.validate`.

Inputs: a committed packet at `captures/{captureId}/packet.json` and `CommittedFile` records.

Outputs: `run/timings.json`, `run/cost.json`, `run/stages.json`. `legacy_run_packet.py` is the vendored housepipe reference and is not the stage entry point.
