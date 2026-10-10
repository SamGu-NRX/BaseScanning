# Coverage-extent witnesses: baseline, pipeline witnesses, schema receipts

The record of the witness pass on top of ea38351 ("Coverage stops at the fitted extent and
samples the fitted ground"), which fixed the two coverage defects the handoff files as issues
#95 and #96. Everything here ran offline on synthetic bundles: no real home capture, no private
rule, no depth model, no server call.


## Baseline (head ea38351, the three-file repair untouched)

Run in `recon/`, 2026-10-10:

| Command | Result |
| --- | --- |
| `uv sync --locked` | Resolved 17 packages; environment up to date |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 20 files already formatted |
| `uv run pytest -q` | 90 passed, 50 warnings, 11.7 s |
| `uv run pytest -q tests/test_coverage_fit.py` | 9 passed, 1.8 s |


## Pipeline witnesses (tests/test_coverage_pipeline.py + tests/witness_bundle.py)

Eight witnesses, each through `pipeline.run` (the caller a real bundle takes) on a synthetic
bundle rendered from analytic planes in `witness_bundle`: LiDAR depth only, no model, no
network, no real home capture. The oracle is the pipeline's own written scene.json,
coverage.json and geometry.json, plus refusals it raises:

| Witness | Pins |
| --- | --- |
| observed stops at the fitted extent when the baseline runs past it | every exported span within `[lo, hi]` of geometry.json while the phone's baseline claims 1.9/0.6 m more wall; grid starts at `lo`, boundary cells clipped |
| a hole in the wall stays unobserved through the pipeline | fit bridges a 0.8 m opening, cells across it stay unobserved, neighbours observed, exported spans agree |
| ground observed on a falling fitted plane (0.35 m/m) | 2 ft ground entries exist - impossible for the old horizontal-plane sampler, whose samples sit 0.21 m below the surface at 2 ft out |
| a short wall track (0.6 m) refuses | `RuntimeError: no straight vertical wall` |
| an empty wall track (floor only) refuses | same refusal |
| two frames 5 cm apart observe no wall or ground | under the 0.25 m two-position bar nothing exports (free space keeps its own weaker bar - known defect, commented in the test) |
| a bundle with no keyframes refuses | `ValueError: ... has no keyframes` |
| one LiDAR frame cannot drive coverage | `ValueError: ... only 1 of 1 keyframes carry LiDAR depth` |

`uv run pytest -q tests/test_coverage_pipeline.py` - 8 passed.

## Owned CLI smoke (exact command, offline)

    uv run python tests/witness_bundle.py /tmp/witness-smoke/bundle
    uv run python -m recon /tmp/witness-smoke/bundle --out /tmp/witness-smoke/out \
        --work /tmp/witness-smoke/work --depth lidar --no-server

Output sha256 (head obv/basescanning-004 + these tests):

    30a7ea0…  scene.json
    3e0cf8f…  coverage.json
    ac5e607…  geometry.json
    b28eb3b…  report.md
    438d3af…  model.glb

## Which coverage changed (same bundle, pre-change code at c813182 vs head)

| Field | c813182 | head ea38351 + witnesses |
| --- | --- | --- |
| coverage.json cells_s_ft[0] | -3.5 (grid line beyond the fit) | -3.035 (the fitted extent) |
| coverage.json cells_end_ft | absent | present; last cell 5.0→5.332, clipped to the fit |
| scene.json wall span_ft | [-3.0, 5.0] (under-covers the fit) | [-3.0, 5.332] |
| scene.json overhead span_ft | [-3.5, 5.5] (claims past the fit) | [-3.035, 5.332] |
| wall_observed last cell | false (phantom grid cell) | true (cell ends at the real fit end) |
| ground_out_ft | unchanged on flat ground (0.664 tail) | unchanged (slope case is the pytest witness above) |

Full suite with the witnesses: `uv run pytest -q` - 98 passed, 56 warnings, 25.8 s; ruff check
and format clean (23 files).
