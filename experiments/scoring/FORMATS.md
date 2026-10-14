# Scoring input files

`score` reads three kinds of JSON file: one rules file, one survey file per house, and one results file per pipeline run. `score import-measure-lab` writes a results file from a Measure Lab session. [METRICS.md](METRICS.md) explains what the scorer computes from them.

Everything in `fixtures/` is synthetic. Keep real surveys and results beside the capture in `captures/` or `data/`, never in git.

## Rules shared by all three files

Every file starts with `"format": 1` and `"unit": "ft"`.

The scorer reads numbers as exact decimals, so a tie with a threshold resolves the same way every time. In floats, 3.1 − 3.0 is 0.10000000000000009, which would pass a 3.1 ± 0.1 ft clearance against a 3 ft rule that is exactly borderline.

The scorer rejects unknown fields, duplicate keys, `NaN`, negative lengths and numbers written as strings. Fields that may be null must still be present. Any input problem stops the run with exit code 2 and a message that names the file, the field and the fix, and the scorer writes no CSV. The same happens when `--out` would put a CSV on top of an input file, whether by the same path, a symlink or a hard link.

## Rules file

The rules file holds named thresholds that use `rules.yaml` parameter names. It carries public or clearly synthetic values only. Base's unpublished values never go in a tracked file.

```json
{
  "format": 1, "unit": "ft", "name": "Synthetic scoring fixture",
  "thresholds": {
    "gas_clearance_ft": {"value_ft": 3, "pass_when": "at_least", "source": "Public: Base help page"},
    "max_route_ft": {"value_ft": 20, "pass_when": "at_most", "source": "Public: Base help page"}
  }
}
```

A threshold name must end in `_ft`. `pass_when` is `at_least` for a clearance, or `at_most` for a limit such as route length. Each results file carries the sha256 of the exact rules file it ran under (`shasum -a 256 rules.json`), so the scorer refuses a run made under different rules.

Threshold names follow the [scoring protocol](https://github.com/SamGu-NRX/house-scanning-master/blob/9737e3f0eefe90f2a12a190bf8750e7fed64413f/docs/research/t3-scoring-protocol.md). `server/rules.yaml` is in PR #11 and not on `main`. In the fixture, `facing_gap_ft`, `pool_clearance_ft` and `review_route_ft` are synthetic placeholders. The format uses feet to match `rules.yaml` and `scene.json`. The protocol records surveys in meters, so convert once when you type a survey up.

## Survey file

Write one survey file per house.

```json
{
  "format": 1, "unit": "ft", "house": "synthetic-01",
  "captures": ["synthetic-01-walk-1"],
  "scale_reference": "scale",
  "candidates": [{"id": "c1", "marker": "Blue painter's tape outline", "location": "South wall, 7 ft 5 in from the SW corner"}],
  "measurements": [
    {"id": "c1-gas", "candidate": "c1", "from": "footprint edge", "to": "gas regulator",
     "status": "measured", "value_ft": 4.500, "plus_minus_ft": 0.010,
     "method": "tape", "measured_by": ["surveyor-a", "surveyor-b"]}
  ],
  "checks": [
    {"candidate": "c1", "check": "gas", "measurement": "c1-gas", "threshold": "gas_clearance_ft"},
    {"candidate": "c1", "check": "route", "measurement": "c1-route",
     "threshold": "max_route_ft", "review_threshold": "review_route_ft"}
  ]
}
```

| Field | Meaning |
|---|---|
| `captures` | Recording ids this survey applies to, ideally each recording's sha256. Each id belongs to one house. |
| `scale_reference` | The one measurement some pipelines may use to set scale. It must be measured, cannot decide a check, and is never scored. |
| `candidates[]` | Spot `id`, its physical `marker`, and its `location` as tape offsets from permanent corners. |
| `measurements[]` | `candidate` (null for house-level distances such as wall length), `from` and `to` in words, `status` (`measured`, `absent` or `not_measured`), `method`, `measured_by`. Only `measured` entries have `value_ft` and `plus_minus_ft`. |
| `checks[]` | The spot, the check name, the deciding measurement and its `threshold`. Every spot needs the same check names with the same thresholds, so the denominator cannot shrink. An absent feature may decide only an `at_least` check. |
| `checks[].review_threshold` | Optional. It makes a band: values that clear `review_threshold` pass, values past `threshold` fail, and values between go to review. Both thresholds must point the same way, with the review line strictly on the passing side. Equal values are rejected, because they leave no band to review. |

## Results file

Each pipeline run writes one results file.

```json
{
  "format": 1, "unit": "ft",
  "pipeline": "ar-taps", "capture": "synthetic-01-walk-1",
  "rules_sha256": "<64 hex digits>", "scale_source": "ar_poses",
  "measurements": [
    {"id": "c1-gas", "value_ft": 4.7, "plus_minus_ft": 0.3},
    {"id": "c2-facing", "value_ft": null, "missing": "failed"}
  ],
  "outcomes": [{"candidate": "c1", "check": "gas", "outcome": "pass"}],
  "timing": {"capture_s": 420, "processing_s": 12.5}
}
```

| Field | Meaning |
|---|---|
| `pipeline` | Row name. Each variant is its own pipeline id. |
| `capture` | Must appear in exactly one survey's `captures`. |
| `scale_source` | `native_metric`, `scale_reference` or `ar_poses`. |
| `measurements[]` | One entry per survey measurement. The scale reference is optional. An entry has `value_ft` with `plus_minus_ft` (a number, or null if the run reports none), or `value_ft` null with `missing` set to `unsupported`, `failed` or `absent`. |
| `outcomes` | One `pass`, `unsure` or `fail` per survey check, exactly as the pipeline decided. Null for a run that makes no decisions. |
| `timing` | `capture_s` and `processing_s` in seconds, or null. Runs on the same capture must report the same `capture_s`. |

## Measure Lab sessions

`score import-measure-lab` turns a session zip shared from Measure Lab into a results file. It reads session format 2, which `experiments/measure-lab/SESSION-FORMAT.md` (PR #7) documents. The team writes a map file after the walk that ties each survey measurement to a session measurement.

```sh
uv run score import-measure-lab fixtures/measure-lab/synthetic-session-01.zip \
  --map fixtures/measure-lab/map.json --rules fixtures/rules.json \
  --truth fixtures/truth/synthetic-01.json --out data/measure-lab.json
```

This excerpt shows each kind of map entry. `fixtures/measure-lab/map.json` is a complete map. Every survey measurement id except the scale reference needs a key.

```json
{
  "format": 1, "unit": "ft",
  "pipeline": "measure-lab",
  "session": "synthetic-session-01",
  "notes": "plus_minus_ft_by_key is the plan's untested 0.3 ft error bar for AR taps.",
  "plus_minus_ft_by_key": {"straight": 0.3, "alongWall": 0.3},
  "measurements": {
    "wall-length": {"session_measurement": "m2", "key": "alongWall"},
    "c1-gas": {"session_measurement": "m3", "key": "straight", "plus_minus_ft": 0.25},
    "c2-facing": {"refusal": "r1"},
    "c1-route": "unsupported",
    "c1-pool": "absent"
  }
}
```

| Map entry | Becomes |
|---|---|
| `{"session_measurement": id, "key": k}` | `values[k]` in feet. `k` must be the measurement's `compared` quantity, the one `accepted` vouches for. A measurement with `accepted: false` becomes null, missing `failed`. |
| `{"refusal": id}` | Null, missing `failed`. |
| `"absent"` or `"unsupported"` | Null, missing `absent` or `unsupported`. |

A session measurement has only the quantities its endpoints allow, as `experiments/measure-lab/SESSION-FORMAT.md` lists. Point to point gives `straight`, `horizontal` and `vertical`, plus `alongWall` when a reference wall is chosen. Point to wall gives `gapToWall` and `heightAboveGround`. The importer rejects a session whose measurement has any other set of values, an endpoint missing from `points` or `walls`, or a `compared` quantity it does not hold.

Map a route as `"unsupported"`. Measure Lab records no routed cable path, and an along-wall distance leaves out vertical legs and detours, so it is not a route length. The importer rejects any other entry for a survey measurement that decides a check named `route`, or a check that uses `max_route_ft` or `review_route_ft`.

`"absent"` is the operator's assertion that the feature does not exist. Write it only after someone has looked over the whole area the check needs and found no such feature. Never infer it from a missing measurement or a refusal. In the scorer, an absent feature passes an `at_least` clearance, so a wrong `"absent"` can become an unsafe pass.

Every value needs a stated uncertainty: its entry's `plus_minus_ft`, or its key's value in `plus_minus_ft_by_key`. There is no default. The ±0.3 ft in the example is the plan's untested error bar for an AR tap, listed in `docs/00-overview.md` under "Conventions the code relies on". It is not a measured error bar. `session` must match the zip's session id.

The importer converts meters exactly (1 ft = 0.3048 m) and rounds half up to a millionth of a foot. The capture id is the zip's sha256, which must already be in the survey's `captures`. The importer checks that before it unpacks anything, and it refuses a `session.json` larger than 16 MiB. That limit is a chosen safety bound, not calibrated against real captures. `scale_source` is `ar_poses`. `capture_s` runs from the session's start to its last capture attempt (measurement or refusal). `processing_s` is null, because the app shows each value as it is tapped. A map or session problem stops the import with a specific message. The importer loads its output through the scorer before it writes the file.

`--decide` also writes a row named `<pipeline>+rule`. Its outcomes apply the survey's strict rule, described in [METRICS.md](METRICS.md), to the run's own values and uncertainties. With no value the row says unsure, except that an absent feature passes an `at_least` check. The row emulates the check rule in `docs/00-overview.md` ("Every check answers PASS, FAIL or UNSURE") until a real solver exists.

## Rebuild the fixture session zip

`tests/helpers.py` builds `fixtures/measure-lab/synthetic-session-01.zip` reproducibly from `fixtures/measure-lab/synthetic-session-01/session.json`. After you edit that file, run `uv run python tests/helpers.py rebuild-session-zip`. Then put the printed sha256 in the survey's `captures`.
