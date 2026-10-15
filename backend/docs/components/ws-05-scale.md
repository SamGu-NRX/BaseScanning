# WS-05 scale

## Purpose

Turn a registered model into metres with a stated 95% bound. LiDAR supplies the scale when depth and confidence are present. Otherwise the meter's make and model, looked up in WS-13's dimension table, supplies a cover diameter measured in the two close-ups. ARKit session scale is a gate. Doors, garage doors and footprints are consistency checks. The stage writes `scale/scale.json`.

Photo-flow budget at 26 ft (A10): about 3 / 7 in median / p90 at true scale. ARKit-only scale on a no-depth session adds about 6–13 in and never verifies a placement. The meter make/model term is the photo flow's route toward the 2–6 in bar (about 1.9 / 6.2 in, calculated, rescoring pending).

## Owner WS and brief

WS-05. Brief: `docs/build/ws-05-scale-layer.md`. The meter dimension table data belongs to WS-13. This package owns the loader and the fusion.

## Inputs/outputs

Inputs: `poses/cameras.json`, `poses/sparse.ply`, `poses/tracks.npz`, `poses/registration_report.json`, the packet (`packet.json` when a directory is given), `reads/reads.json` or `early/meter_identity.json`, and `data/meter-models/meter_models.v1.json` (or the test seed until that file exists).

Outputs: `scale/scale.json`, `scale/report.json`, `scale/meter_models.pending.jsonl` when a model is unknown, `scale/contour_overlay_*.jpg`. `--emit-metric-copies` also writes `sparse_metric.ply` and `cameras_metric.json` for the scorer.

## How to run on a fixture

WS-00 fixtures and `make fixtures` are not on main yet. Until they land, run the synthetic tests. When a packet exists:

```bash
uv run python -m server.scale.run \
  --packet data/fixtures/packets/mini-arkit/packet.json \
  --poses /tmp/ws04/mini/poses \
  --reads server/scale/tests/fixtures/reads/m28.reads.json \
  --out runs/mini/scale
```

The simulation reproduction (N=20000, seed 7):

```bash
uv run python -m server.scale.sim.no_tape_mc --n 20000 --out runs/mc
```

## How to test

```bash
uv run pytest server/scale/tests -q
uv run ruff check server/scale
```

The suite includes a small simulation smoke (N=80). The published-row comparison is the N=20000 command above.

## Rebuild from scratch

`server/scale/` is the stage. `server/contracts/jobs.py` and `server/contracts/verdict.py` are WS-00's. `server/contracts/scale.py` is WS-00's `ScaleBlock` plus the optional extension (origin, reason codes, ARKit gate, evidence, close-up request); `schemas/scale.v0.schema.json` is the export of that model. `server/common/geometry.py` matches the names and signatures on WS-00's skeleton PR 38. That pull request is still open, so this copy stays until it merges, and then only WS-00's file remains. Point `HSS_SCALE_METER_MODELS_PATH` at WS-13's table.

## Last gate table

| Item | Expected | Observed | Result |
|---|---|---|---|
| 1 pytest, 15 reason codes, verified impossibilities, socket blocked | green, no network | `uv run pytest server/scale/tests -q` | see the PR; socket test passes |
| 2 ETH3D LiDAR score-cloud | scale error within ±1.0 | fixtures and WS-04 poses are not on main | pending |
| 3 synthetic LiDAR 0.85 and 1.15 | agree within 0.5% | unit test passes | pass |
| 4 fallthrough modern / 2018 / pose-free | bounds in [0.08, 0.11] and [0.25, 0.33]; pose-free `no_scale_source` | unit test passes | pass |
| 5 identity handoff | CENTRON only on m28 and m39; m28 mixture 0.5/0.5 | unit test passes | pass |
| 6 table pins | six CENTRON metres; unusable rows refused | unit test passes | pass |
| 7 synthetic contours | 20/20 labels; two-view within 0.5%; one-view within 1% | unit tests pass | pass |
| 8 real-photo experiment | labels and diameter on real meters | capture day not run | pending |
| 9 simulation N=20000 seed 7 | 0.6/2.0/3.1 ±0.1, conflict 0.02±0.01, ARKit 2.4/5.7/6.9, reference 0.4/1.0/1.2 | two close-ups 0.60/2.02/3.10, conflict 0.02045, ARKit modern 2.42/5.73/6.85, marked-length reference 0.35/0.95/1.24 | pass |
| 10 make validate / schemas / hygiene / lint-imports / score-verdicts | green | `make schemas` passes; validate, hygiene, lint-imports and score-verdicts are not on main yet | blocked |
| 11 no VLM client | `rg openai\|xai\|httpx\|requests server/scale` empty outside tests | checked before push | pass if the scan is empty |
| 12 hygiene | no second table, no raw text, files under 5 MB | seed table is under tests/fixtures only | pass |
| 13 mini packet.json | `vn-scale-1` and a retake; detail is an edge or registration miss | unit test passes | pass |

`meter_verified_enabled` defaults to false. No block in the tests has `source == meter_model` and `status == verified`.

## Known limits

- Walk-frame measurement is not implemented. An unregistered close-up returns `meter_closeup_unregistered`.
- Door and garage leaves are emitted as unused checks. They are not measured from the packet.
- TrueDepth fusion is modelled in the simulation and not wired into `run_scale`.
- The contour fitter keeps a clean `fitEllipse` when its inliers clear 0.6, and refines only the weaker candidates.
- Family matching treats the table's model-family tokens as a subset of the identity tokens. That is a builder default so "Itron OpenWay CENTRON" hits the CENTRON rows.
- The product fusion bound includes the 1.1 factor. The simulation's conflict rule uses the half-width without that factor, matching the published study.
- m28's source answer named a polycarbonate cover. The replayed stub uses `cover_material: unknown` so item 5's 0.5/0.5 mixture holds.
- `arkit_fit.scale_pct` is read as a scale factor when it sits between 0.2 and 5.
- Provisional contracts will collide with WS-00's modules when that PR merges.
