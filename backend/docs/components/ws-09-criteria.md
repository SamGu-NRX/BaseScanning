# WS-09 criteria engine

## Purpose

One contract-valid verdict per profile A, B, C, and S. The 3D rules engine decides and is the only path to a placement pass. The chain solver fills unsure meter-wall cells. C2 vetoes. C3 downgrades.

## Owner WS and brief

WS-09. Brief: store `docs/build/ws-09-criteria-engine.md`. Branch `ws-09/criteria-engine`.

## Inputs / outputs

Inputs: `scene.json`, `coverage.npz`, keyframes, optional point-cloud PLY, `scale.json`, `reads.json`, `coverage/coverage_report.json`, `objects/view_requests.json`.

Outputs: `verdicts/verdict.{A,B,C,S}.json`, `criteria_report.json` (includes `decidability`), `placement.<profile>.json`, sidecars `c1.<profile>.raw.json`, `c1.<profile>.chain.json`, `c2.sweep.json`, `c3.facts.json`, `c3.judge.json`.

## How to run on a fixture

```bash
uv run pytest server/criteria/tests -q
make criteria-golden
```

`make criteria-golden` runs the unit suite, then writes `/tmp/crit/facade`, `/tmp/crit/delivery`, and `/tmp/crit/electro` with C3 off. Laser PLYs come from `make fixtures`.

## How to test

`server/criteria/tests/test_criteria.py`. Incoming scenes are checked with `server.contracts.scene.Scene`. Outgoing verdicts are checked with `server.contracts.verdict.Verdict` and `verdict_contract_errors`. The synthetic flat wall is the hard positive pass: site `pass`, `views_needed == []`, and with C2 on `c2.vetoes == 0` plus at least one C2 pass spot.

## Rebuild from scratch

```bash
uv sync
uv run pytest server/criteria/tests -q
```

No VM-only state. The eval harness loads from `server.eval` (WS-00 skeleton, on `main`).

## Last gate table

| # | Gate | Result |
|---|---|---|
| 1 | 12 golden verdicts validate | PASS. `validate` is `ok` on facade, delivery, and electro A/B/C/S, the gravel `footprint3in` verdict, and `colmap-nonlidar` A/B/C/S. |
| 2 | unsafe passes 0, c5 empty | PASS. `unsafe_passes == 0`, `coverage_contract_unsafe_passes == 0`, `c5_margin_errors == []` on all 12 scored verdicts. |
| 3 | facade / electro / delivery site decisions | PASS for the decision lattice: facade and electro A/B/C/S `manual_review`; delivery A/B/C/S `manual_review` (C/S may also be `reject`). `site.agreement == 1.0`. Nine delivery w1 runs fail other checks while `drive_clearance` stays `unsure` because that strip is unobserved. |
| 4 | facade + gravel C1 pass in [-3.95, -3.30] | PASS. `test_gravel_window_passes_under_footprint3in` (profile C, `footprint3in`, C2 on): a w1 run inside [-3.95, -3.30] has `failing == []` and `outcome == pass`, and the site passes. The near-wall ground probe was sitting on the wall skin (share about 0.90). The C2 sample on the window jamb failed `wall_backing`; the recess sample in the gap passes. |
| 5 | synthetic C2 ≥ 1 pass and every C1 pass stays a pass | PASS on the synthetic wall, profile C, C2 on: `c2.vetoes == 0`, `n_pass >= 1`, site `pass`, views `[]`. |
| 6 | delivery agreement and facade C2 agreement ≥ 0.9 | Facade `c2.agreement_with_c1` is 1.0. Delivery C start-range agreement with C2 off is 0.6312 (712 agree, 416 over_caution, 0 unsafe passes) against the 0.85 bar. The earlier figure was 0.6073, including with C2 on, which clears the 0.5 bar. C2 finds no delivery pass spots, so it does not move this score. The shortfall is over-caution on fail labels: no candidate, or a ground check left unsure on an unobserved strip. |
| 7 | audit-verdict decisive_on_false_claims 0 | PASS. `decisive_on_false_claims == 0` and `contract_errors == []` on all 12 golden verdicts. |
| 8 | facade angle80 views_needed | PASS. Eight views, each with `id`, `kind`, `criteria`, and `why`. Ground entries use the chest-height target. |
| 9 | path is `C1-rules-3d+hs11-chain` (or `C1-rules-3d` with hybrid off) | PASS. Golden verdicts use `C1-rules-3d+hs11-chain`. |
| 10 | unit tests ≥ 34 | PASS. `pytest server/criteria/tests`: 100 passed. `make ci`: 333 passed. |
| 11 | ≤ 60 s per golden scene, C3 off | PASS. Facade 53.424 s, delivery 51.271 s, electro 20.872 s. |
| 12 | no anthropic/claude, no file > 5 MB, no VM paths | PASS. A case-insensitive search of `server/criteria` for anthropic and claude is empty. |
| 13 | decidability facade ≥ 0.38, electro ≥ 0.75; photo row WARN below 0.29 | PASS on the laser rows. Facade C share 0.3981. Electro C share 0.8668. Photo rows stay WARN below 0.29 and were not re-scored on this head. |

## Known limits

- Delivery C start-range agreement with C2 off is 0.6312, under the 0.85 bar (remeasured on the merged engine; the earlier figure was 0.6073). Profile C, `HSS_CRITERIA_C2=off`, `HSS_CRITERIA_C3=off`, `VISION_EXTERNAL_CALLS=off`. Four labels, all truth fail, 1128 samples at 2 in: placement w1 185 agree / 16 over-caution, drive w1 101 / 100, placement w2 222 / 141, drive w2 204 / 159. Ranges are w1 [-9.3002, 0.8598] m and w2 [0.8598, 19.2798] m. The 416 misses are 62 samples with no candidate (w1 [0.098, 0.860] m and w2 [18.538, 19.249] m), 102 drive samples on candidates that already fail another check while `drive_clearance` stays unsure (w1 runs 1–6, w2 runs 52–53), and 252 samples on w2 runs 35, 37, 54, and 55, whose outcome is unsure. Contract 3.1 leaves a drive fail unsure when the ground in front of the footprint is under 90% observed. Forcing those strips to fail would violate the coverage contract.
- Facade gravel `footprint3in` passes when `facade_gravel.py` adds the observed gravel strip. The ground strip starts 0.15 m off the wall so the skin voxel is not a probe. C2 places a candidate in the window recess and keeps the cable on the wall face.
- `server.criteria.run(ctx)` reads the §3.9 work-dir keys (`scene/scene.json`, `objects/objects_3d.json`, `objects/view_requests.json`, `reads/reads.json`, `coverage/*`, `scale/scale.json`), calls `evaluate_scene`, and writes `verdicts/`. View requests come from `objects/view_requests.json`, not `scene/objects/`.
- The placement sidecar is metres. The box centre is `depth/2 + 0.02 m` out from the face, at the shortest-route station inside the run, not the run's left edge.
- A missing laser or recon PLY, or a declared occluders asset, raises `occluders_missing`. `--allow-missing-occluders` marks the report `provisional: true`, and the pass gates reject it.
- SD-67: the C3 client reads only `OPENAI_API_KEY` and `XAI_API_KEY`. `VISION_EXTERNAL_CALLS` defaults off.
- `view_angle_deg` is omitted (SD-57).
- Battery height is 39.5 in in every profile. Profile S depth is 2.0 ft. `ac_ft` is 0; AC still blocks through the footprint and the route.
- A C2 veto is counted only when it pulls a passing candidate to `unsure`. Cells on a candidate that was already fail or unsure do not increment `c2.vetoes`.
- A sub-second C2 budget returns `skipped` before the sweep, because the first numba compile takes longer than that.
