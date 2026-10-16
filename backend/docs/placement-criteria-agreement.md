# Where placement and criteria agree on the committed cases

This report measures what the live placement path and the live criteria engine each decide on every committed synthetic capture case, and quotes each engine's own reasons. It feeds one open decision: whether the placement decision may control device eligibility (the "Placement decision controls eligibility" entry in `status-report/basescanning.json` `sam_decisions`, tracked in `plans/basescanning-backend-w9.md`, status "not started" when this work began). The result compose path reads criteria verdicts, not `placement/placement.json`, so placement controls eligibility only if the two engines can be trusted to agree. This report measures; it adds no evaluator, no threshold, and no policy number.

The script is `backend/scripts/placement_criteria_agreement.py`. It imports both engines and never edits them. The measurement ran on base `alpha` at `4bcbedba7ffd3d8c4d7f725395e33ed5a9d27d6c`.

## The headline

The two engines cannot be compared on these cases, because the criteria engine refuses all 11 of them. Its own precondition rejects a scene whose meter object does not name its wall:

```python
if meter.get("wall_id") not in {w["id"] for w in scene["walls"]}:
    raise CriteriaInputError("meter_wall_missing")
```

(`backend/server/criteria/run.py`, lines 304-305.) Every committed capture case anchors the meter by `frame.anchor.element_id` under the `meter_anchored/v0` convention instead, and the meter object carries no `wall_id` field. That is not a malformed input: the placement bridge accepts the same documents as schema-valid scenes. The two engines simply do not yet speak the same scene convention on any committed case.

So the report answers the premise behind the measurement directly: on the committed cases, placement is the only engine that produces a decision at all. Measured agreement between the engines is zero rows, not because they disagree, but because no case exercises both. Until a committed case exercises both engines on one input, letting placement control eligibility would rest on no cross-check at all.

## How to reproduce

From `backend/`, on any fresh checkout:

```sh
uv sync --frozen --extra api
uv run --frozen --extra api python scripts/placement_criteria_agreement.py
```

The full run took 1.1 s on one CPU core, exit code 0. The script verifies every source file against its manifest sha256 pin first, refuses drifted fixtures, and fails loudly (exit 1) if either engine raises an unexpected exception. The JSON table carries the rules sha256 (`539daff6...`) and profiles sha256 (`3c762608...`) it measured under.

Both engines ran with their shipped defaults, with two documented settings: C2 and the C3 judge are off, because no committed case ships the coverage grid C2 sweeps and the judge is the only networked step; `allow_missing_occluders` is on, because the committed scenes ship no occluder geometry, and the engine marks those reports provisional itself.

## Case by case

| Case | Manifest | Kind | Placement | Criteria | Agreement |
| --- | --- | --- | --- | --- | --- |
| hazard-right-ground-gap | capture_cases_v1 | positive | solved: `manual_review` | refused: `meter_wall_missing` | criteria refused |
| hazard-left-ground-gap | capture_cases_v1 | positive | solved: `manual_review` | refused: `meter_wall_missing` | criteria refused |
| multiwall-source-unsupported | capture_cases_v1 | negative | refused: `placement_scene_unsupported` at `/walls` | refused: `meter_wall_missing` | neither evaluated |
| generated-object-evidence-unsupported | capture_cases_v1 | negative | refused at `/objects/1/evidence` | refused: `meter_wall_missing` | neither evaluated |
| standoff-meter-anchor-unsupported | capture_cases_v1 | negative | refused at `/objects/0/geometry/standoff_m` | refused: `meter_wall_missing` | neither evaluated |
| footprint-hazard-unsupported | capture_cases_v1 | negative | refused at `/objects/1/geometry/kind` | refused: `meter_wall_missing` | neither evaluated |
| generated-regions-unsupported | capture_cases_v1 | negative | refused at `/generated_regions` | refused: `meter_wall_missing` | neither evaluated |
| hazard-right-ground-gap | cases | positive | refused at `/objects/0/geometry/standoff_m` | refused: `meter_wall_missing` | neither evaluated |
| hazard-left-ground-gap | cases | positive | refused at `/objects/0/geometry/standoff_m` | refused: `meter_wall_missing` | neither evaluated |
| multiwall-source-unsupported | cases | negative | refused at `/walls` | refused: `meter_wall_missing` | neither evaluated |
| generated-hazard-unsupported | cases | negative | refused at `/generated_regions` | refused: `meter_wall_missing` | neither evaluated |

The confusion table has no entries: both engines produced a decision pair on zero cases. The refusal counts are 2 cases where placement solved and criteria refused, and 9 cases where neither engine evaluated the source.

## What each engine said, in its own words

### The two cases placement solved

On the `capture_cases_v1` positives, the placement path (the production bridge conversion, then the unchanged top-level solver) returned `manual_review` with the exact spot the manifest derives by hand from the authored dimensions:

- hazard-right-ground-gap: spot at wall `side`, segment 0, span [7.0, 9.583333] ft, outcome `unsure`. `gas_clearance` is `unsure` with cause `margin` (measured exactly 3.0 ft against 0.59 ft of uncertainty), `ground_surface` is `unsure` with cause `unobserved`, and `meter_working_space`, `wall_equipment_above` and `route_length` pass. The solver's own reason: "The best spot has checks nobody can settle from this scan: ground under the battery; distance from gas equipment; distance from ac units; ..." and it asks for a ground band plus the demo-policy clearances.
- hazard-left-ground-gap: spot at span [-9.583333, -7.0] ft, same unsure causes, plus `opening_clearance` unsure unobserved.

These match the manifest's declared expectations check for check.

### The refusals placement declared

Six of the seven source-only refusals land exactly where their manifests declare, code and pointer. The one that does not is `cases/generated-hazard-unsupported`: the W7 manifest declares `/objects/1/evidence`, but the bridge's first refusal is `/generated_regions`. The newer `capture_cases_v1` manifest says this out loud about its byte-identical twin: "declared at its actual first refusal `/generated_regions` (generated regions are unknown space, not observed geometry) rather than the `/objects/1/evidence` pointer its W7 manifest entry declared." The W7 declaration is stale, not the bridge.

The two `cases/` positives also refuse at the bridge, at `/objects/0/geometry/standoff_m`. Their manifest solves positives on the wire scene, not on the source, and describes the stand-off anchor as outside the bridge's scope; the source-side refusal is that limit, recorded here because this measurement runs both engines on the same input, the committed source, for every case.

### Cases neither engine could evaluate

Nine cases produced no decision from either engine: all five `capture_cases_v1` negatives, the two `cases/` positives on their sources, `cases/multiwall-source-unsupported`, and `cases/generated-hazard-unsupported`. In each, the bridge refuses the source and the criteria engine refuses the same source at its meter-wall precondition. They are counted as unevaluated, never as agreement.

## What this does not measure

- No criteria verdict exists for any committed case, so profile differences (A, B, C, S) are unmeasured here.
- Whether the criteria engine evaluates the merged scene a real capture run produces (where detection may add the meter's `wall_id`) is open; no committed case carries that shape.
- C2 never ran: no committed case ships the coverage grid it sweeps.
- The criteria refusals are the engine's declared precondition, not crashes. The script still fails the run if an engine crashes, and a test pins that behavior.

The measurement is deterministic: same bytes in, same table out, no timestamps.
