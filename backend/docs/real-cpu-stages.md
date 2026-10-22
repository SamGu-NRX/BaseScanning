# Real CPU stages: `HSS_ORCH_REAL_STAGES`

With `HSS_ORCH_STAGE_STUBS=1` a local run answers every stage with its stand-in
from `server/orchestrator/stubs/<stage>.py`, so the result never depends on the
scan. `HSS_ORCH_REAL_STAGES` adds the middle setting: it names the stages whose
**real** implementations run even with stubs on, so a local or dev run can
exercise one real stage at a time against stand-in upstreams.

```sh
export SMP_ENVIRONMENT=dev HSS_ORCH_STAGE_STUBS=1 HSS_ORCH_REAL_STAGES=validate
# or every stage whose real path needs no GPU, weights or paid call:
export HSS_ORCH_REAL_STAGES=cpu
```

The value is a comma-separated list of stage names, or the alias `cpu`.
Whitespace and a trailing comma are tolerated; stages run real on top of
whatever their upstreams produced, which under stubs are stand-in artifacts.

## The `cpu` alias, and why `objects` and `dense` are excluded

`cpu` selects every stage whose real path is deterministic CPU work with no
GPU, no model weights and no paid call:

`validate`, `poses`, `reads`, `scale`, `coverage`, `scene`, `objects_3d`,
`placement`, `criteria`, `result`

Two stages stay out:

- `objects` loads the OWLv2 and SigLIP weights (`server/objects/run.py`), which
  a CPU dev box does not carry. Listing `objects` explicitly is refused unless
  `HSS_OBJECTS_DEVICE=cpu` is also set to run those weights locally.
- `dense` falls back to π³/MoGe/DA3 weights on a CUDA device whenever the
  packet has no LiDAR and no points were injected (`_acquire_cloud` in
  `server/dense/run.py`), and fails with `input_missing:weights` without
  `HSS_DENSE_WEIGHTS`. List it by name (`cpu,dense`) when the packet or the
  machine can serve it.

`reads` and `criteria` stay in the alias because their real paths make no
vision call while `VISION_EXTERNAL_CALLS` is off, which is the default. The
alias is refused when that variable is on.

## Refusals

The variable is read by `server/orchestrator/stage_selection.py`, validated
when the orchestrator runner first loads, and refused with a message that names
the variable and the bad value when:

- a listed stage name is unknown;
- `HSS_ORCH_MODAL_REMOTE` is true in any spelling `load_orch` accepts (`1`,
  `true`, `yes`, `on`, any case): a remote run already runs every stage for
  real;
- `SMP_ENVIRONMENT` is anything other than exactly `dev`, including unset or
  blank. `Settings` reads those values as `prod`, so the selection does too;
- `objects` is listed without `HSS_OBJECTS_DEVICE=cpu`;
- the `cpu` alias is used while `VISION_EXTERNAL_CALLS` is true (`on`, `1`,
  `true`, `yes`).

A stage selected this way can still fail at run time: a real stage fed by
stand-in artifacts can meet inputs it was not built for (the stub poses stage
produces one identity camera, for example). Select upstream stages together
when a downstream real stage needs their real shape.

## What the `impl/` artifacts mean

For every stage a run publishes, `server/orchestrator/runner.py` `run_stage`
also publishes one marker at the run-relative key `impl/<stage>.json` — stored
at `captures/{capture_id}/runs/{run_id}/impl/<stage>.json` — with exactly:

```json
{"schema": "hss-stage-impl/v1", "stage": "<stage name>", "implementation": "real" | "stand-in", "callable": "<dotted module>.<function>"}
```

- `implementation` is `"stand-in"` when a module under
  `server.orchestrator.stubs` ran, `"real"` otherwise (including `placement`,
  which is `stubs_exempt` and always real).
- `callable` names what actually executed, read off the resolved callable —
  `server.packet.run`, `server.orchestrator.stubs.poses.run` — so a per-run
  selection override (the capture-placement runner's) is recorded as what it
  ran.
- A marker exists only for stages that succeeded; a failed stage publishes no
  artifacts, this one included.

The result stage's declared inputs include `impl/`, so the composer receives
every marker of its run. The contract is shared with
`burst14-bs-result-names-stand-ins`; change it in both places or not at all.
