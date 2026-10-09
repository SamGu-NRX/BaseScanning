# The code behind `score`

The `scoring` package lives in `src/scoring/`. It scores capture-pipeline runs against an independent tape survey of the same house. Python 3.12, no runtime dependencies, one console script (`score = "scoring.cli:main"`, `pyproject.toml:10`). The component README one level up ([README.md](../README.md)) documents the experiment and links the scoring protocol, which reports each house as a case series and counts unsafe passes as the number that matters most. This README documents the code. Five modules, about 2,300 lines, plus a one-line `__init__.py`. Every `file:line` reference below was checked against the commit that added this file.

## The scorer reads results files. It does not run pipelines.

Each pipeline writes a results file, and `score` reads that file. A study takes three kinds of JSON input: one rules file, one survey per house, and one results file per pipeline run. A fourth command, `score import-measure-lab`, turns a Measure Lab session zip into a results file. [FORMATS.md](../FORMATS.md) specifies every input field by field. [METRICS.md](../METRICS.md) explains every number the scorer prints.

## Run it against the fixtures

After `uv sync --locked`, from `experiments/scoring`:

```sh
uv run score \
  --rules fixtures/rules.json \
  --truth fixtures/truth/synthetic-01.json \
  --results fixtures/results/ar-taps.json fixtures/results/photo-depth.json \
  fixtures/results/mesh-scaled.json \
  --out results/synthetic-01
```

Ran at this commit. You should see:

- exit code 0;
- the markdown summary on stdout;
- three `score: wrote <path>` lines on stderr, one per CSV;
- `measurements.csv`, `checks.csv` and `runs.csv` in the out directory.

Rerunning rewrites those three files byte for byte identically, so pointing `--out` at the committed `results/synthetic-01` leaves the working tree clean. The component README saves stdout with `> results/synthetic-01/summary.md`; the committed summary is exactly what the command prints. Real surveys and results live in git-ignored `data/`, the default `--out` (`.gitignore:25`).

The summary opens with the rules name and sha256 and a count of houses, candidate spots and runs: 1 house, 2 candidate spots, 3 pipeline runs. Each house then gets a Distances table (median and maximum absolute error in inches; 3.48, 12.60 and 1.20 for the three fixture runs), a Checks table (ar-taps agrees on 4 of 7 checks and makes 1 unsafe pass; mesh-scaled makes no decisions), a Timing table, and a list naming every unsafe pass, missed review and decision made without its measurement. A closing line says what the data is not. These numbers test the scorer, not any pipeline.

The three CSVs differ in grain. `measurements.csv` has one row per survey measurement per run, `checks.csv` one row per check per run, `runs.csv` one row per run; the row builders are `measurement_row`, `check_row` and `run_row` (`report.py:134`, 157, 192). Cells hold the figure or stay empty when it does not apply, and booleans are written `true` or `false` (`report.py:1-5`).

## How data flows through the code

`main` (`cli.py:13`) is the whole scoring path.

1. An argv starting with `import-measure-lab` goes straight to `measure_lab.main` (`cli.py:15-16`), which parses its own arguments (`measure_lab.py:600-617`).
2. argparse parses `--rules`, `--truth` (one or more surveys), `--results` (one or more runs) and `--out`, which defaults to `data` (`cli.py:24-36`).
3. `refuse_output_over_inputs` (`cli.py:41-48`) checks the three CSV paths from `csv_paths` (`report.py:243`) against every input path before anything is loaded or printed, so a colliding `--out` never touches an input.
4. `load_study` (`cli.py:49`) loads and validates every file and pairs runs with surveys (`inputs.py:688`): `load_rules` (`inputs.py:314`), `load_truth` for each survey (`inputs.py:380`), `load_results` for each run (`inputs.py:573`), each run joined to the survey listing its capture (`inputs.py:710-717`), then `_require_run_matches_survey` (`inputs.py:725`) and `_require_same_capture_time` (`inputs.py:726`). Out comes a `Study` (`inputs.py:130`). A results file names its capture, and only a survey listing that capture accepts it; a run that answers questions the survey does not ask, or skips one it does, is rejected there.
5. `score_run` (`metrics.py:362`) runs once per house and run pair (`cli.py:54-58`). It is pure: `score_measurement` (`metrics.py:65`) for each survey measurement, `score_check` (`metrics.py:226`) for each check.
6. `markdown` (`report.py:300`) goes to stdout (`cli.py:59`). `write_csvs` (`report.py:247`) writes the three CSVs (`cli.py:60`), echoing each written path to stderr (`cli.py:61`). Then exit 0 (`cli.py:62`).
7. An `InputError` from either step prints `score: <message>` to stderr and returns 2 (`cli.py:50-52`), before any CSV is written.

The importer path is `measure_lab.main` then `import_session` (`measure_lab.py:565`): `refuse_output_over_inputs` on the out file (`measure_lab.py:574`), `load_rules` (583), `load_truth` (584), `load_session` (585), `build_results` (586-588), which validates the map from `load_map` (`measure_lab.py:329`) against the session. The built JSON is then loaded through `load_study` in a temp directory, so a file that would not score is never written (591-594); only then is it written to `--out` (595-596). Exit 0 with one stderr line (630-634), or exit 2 on `InputError` (627-629).

## What each module holds

### `inputs.py`: load and validate the three input files

The biggest module, 732 lines. Everything here raises `InputError` (`inputs.py:41`), a ValueError subclass that names the file, the field and the fix.

- Frozen dataclasses: `Threshold` (45), `Rules` (53), `Candidate` (61), `SurveyMeasurement` (68), `Check` (81), `Truth` (91), `PipelineMeasurement` (102), `Results` (110), `House` (124), `Study` (130).
- `Fields` (150) validates one JSON object: required and optional text (180, 186), lengths (189), enum choices (207), lists (213, 222), and the `format: 1`, `unit: "ft"` header (233).
- `read_json` (264) and `parse_json` (272) reject what JSON would otherwise swallow: duplicate keys, NaN and Infinity, and numbers written as strings. Floats become Decimal.
- Loaders: `load_rules` (314), `load_truth` (380), `load_results` (573), `load_study` (688).
- `refuse_output_over_inputs` (247).
- Cross-file consistency helpers: `_require_same_checks_at_every_spot` (461), `_validate_check` (493), `_validate_review_band` (530), `_require_run_matches_survey` (634), `_require_same_capture_time` (672).

### `metrics.py`: score one run against its survey

Pure functions over the loaded inputs. No I/O.

- `score_run` (`metrics.py:362`) builds a `RunScore` (286) from a `Truth` and a `Results`: one `MeasurementScore` (46) per survey measurement and one `CheckScore` (185) per check.
- A `MeasurementScore` pairs a survey measurement with the run's value: status, the signed error in feet, whether the survey value sits inside the run's reported ± (54), and the error converted to inches (57-62; `INCHES_PER_FOOT` at 23).
- Status buckets (25-34): `scored`, `scale_reference`, `not_surveyed`, `missing_unsupported`, `missing_failed`, `false_absent`, `phantom`, `absent_agreed`.
- A `CheckScore` carries what the survey supports (`truth_outcome`, 97), the run's answer, and the comparison: `agrees`, `unsafe_pass`, `missed_review`, `over_caution`, `false_rejection`, `decided_without_measurement`, `abstention`.
- `truth_outcome` (97) and `decide` (124) apply the survey's strict rule: pass when the margin clears the uncertainty, fail when it misses by more, otherwise borderline or review. `margin_ft` (140) signs margins so positive means passing.
- `error_to_margin` (163) divides the absolute error by the larger of the survey's margin and its ±; `could_flip` (177) treats a ratio of exactly 1 as able to flip the check.
- `RunScore` counters: `count` (293), `denominator` (296), `scored_errors_in` (301), `median_abs_error_in` (305), `max_abs_error_in` (310), `within_reported` (315), `makes_decisions` (321), `judged` (325), `agreements` (330), `unsafe_passes` (334), `missed_reviews` (338), `over_cautious` (342), `decided_without_measurement` (346), `false_rejections` (350), `could_flip` (354), `abstentions` (358).

### `report.py`: markdown summary and three CSVs

- `markdown` (`report.py:300`) renders the summary. Per house: a Distances table (`_distances`, 339), a Checks table (`_checks`, 387), a Timing table (`_timing`, 440), and `_named_lists` (487), which name every unsafe pass, missed review and decision without a measurement.
- `write_csvs` (247) writes `measurements.csv`, `checks.csv` and `runs.csv` (`CSV_NAMES`, 240) through `csv_paths` (243) and `_write` (232).
- Row builders `measurement_row` (134), `check_row` (157) and `run_row` (192) map scores to the column tuples at lines 17, 37 and 69.
- `fixed` (102) rounds half up for display only; every comparison used the exact value (module docstring, 3-5).
- Escaping: `_one_line` (264) removes line breaks, `_text` (280) escapes Markdown punctuation, `_cell` (285) escapes pipes, `_code` (268) builds code spans that survive embedded backticks.

### `measure_lab.py`: session zip to results file

Imports a Measure Lab session zip into a results file. The session is the app's format 2 (`measure_lab.py:42`). The map is a file written by hand after the walk; it ties each survey measurement to a session measurement, a refusal, or the words `absent` or `unsupported`.

- `load_session` (215) hashes the zip and checks the hash against the survey's `captures` before decompressing anything (218-223). `session.json` is read with a 16 MiB cap (`MAX_SESSION_JSON_BYTES`, 71; enforced at 232-236).
- `load_map` (329) parses map entries into `FromSession` (309), `FromRefusal` (316) or a bare missing reason, all held in a `Map` (321).
- `build_results` (499) validates the map against the session (`_check_map`, 390) and converts meters with `meters_to_feet` (79): exact division by 0.3048 (`METERS_PER_FOOT`, 46), half up to a millionth of a foot (`FEET_PLACES`, 50). `capture_s` runs from the session's start to its last measurement (513-520). `processing_s` is null, because the app shows each value as it is tapped (530-532). `scale_source` is `ar_poses` (54).
- `--decide` (611-616) computes outcomes by applying the survey's rule to the run's own values (`_rule_outcome`, 472; `_outcomes`, 485) and appends `+rule` to the pipeline id (`RULE_SUFFIX`, 56).
- `dumps` (536) writes Decimals exactly, never through a float.

### `cli.py`: the entry point

`main` (13), 66 lines: the dispatch, the argparse setup, the try block, the scoring loop, the outputs. `score --help` ends with a pointer to `score import-measure-lab --help` (21-22).

## Words the code uses

- **Scale reference.** The one measured distance some pipelines use to set scale (`Truth.scale_reference`, `inputs.py:96`). Never scored, and the one measurement a run may leave out.
- **Denominator.** Every survey measurement except the scale reference, whether or not the run answered (`RunScore.denominator`, `metrics.py:296`). The Distances table reports scored counts out of it.
- **Judged.** Checks whose survey outcome is not `unknown` (`metrics.py:325-328`). The Checks table reports agreements out of that.
- **Unsafe pass.** A run's pass where the survey fails (`metrics.py:203-204`). The scoring protocol counts these as the number that matters most.
- **Missed review.** A run's pass where the survey is borderline or inside a review band (`metrics.py:205-206`).
- **Over-caution.** An unsure or fail where the survey passes; the fails among them are also false rejections (`metrics.py:207-209`).
- **Could flip.** The run's error is at least as large as both the survey's distance to the nearest line and its ± (`error_to_margin`, `metrics.py:163`; `could_flip`, 177). A warning sign, not a replay of the decision (`METRICS.md:39-41`).

## Rules the code enforces, and why

Exact arithmetic, so ties resolve identically. Numbers are parsed as Decimal (`parse_float=Decimal`, `inputs.py:295`) and comparisons are strict (`decide`, `metrics.py:124-137`): a value exactly on a line, or exactly the uncertainty away from it, never passes or fails. In floats, 3.1 - 3.0 is 0.10000000000000009, which would pass a 3.1 ± 0.1 ft clearance against a 3 ft rule that is exactly borderline (`inputs.py:6-8`).

Every input problem is a hard stop. Unknown fields (`inputs.py:159-164`), duplicate keys (`inputs.py:278-287`), NaN and Infinity (`inputs.py:289-290`, wired in at 296), negative lengths (`inputs.py:194-195`), and numbers written as strings (`inputs.py:192-193`) are all rejected. The reason is the module docstring (`inputs.py:3-4`): a silently skipped row changes the score without anyone seeing it.

Errors name the file, the field and the fix. `Fields.error` (`inputs.py:167-170`) prefixes each message, `main` prints it to stderr and exits 2 (`cli.py:50-52`), and no CSV is written. Feeding the rules file back as a results file gives, verbatim, `score: fixtures/rules.json: unknown field 'name', 'thresholds'; allowed fields are capture, format, measurements, outcomes, pipeline, rules_sha256, scale_source, timing, unit` and exit 2. A `NaN` where a length belongs gives `NaN is not a measurement; write null for a missing value`.

Outputs never land on inputs. `refuse_output_over_inputs` (`inputs.py:247-261`) resolves paths (252-254) and falls back to `os.path.samefile` (256), so a symlink or a hard link to an input is refused, not just a matching spelling of the path.

The scale reference is never scored. A check may not use it as its deciding measurement (`inputs.py:506-511`), `score_measurement` marks it `scale_reference` (`metrics.py:81-82`), it is the one measurement a run may leave out (`inputs.py:650-651`), and every error figure excludes it (`report.py:311-312`). A run given the reference can match it exactly, so its error says nothing about accuracy.

Ids in the markdown are escaped. Ids may be any non-empty string (`METRICS.md:3`), so the report flattens line breaks, escapes Markdown punctuation, escapes pipes in table cells, and sizes code-span fences past any backtick run (`report.py:257-291`). The CSVs hold ids unchanged (`METRICS.md:3`).

## Where the package plugs into the repository

- `make scoring` (`Makefile:59-65`) runs `uv sync --locked`, `ruff check`, `ruff format --check` and `pytest -q`. Its comment calls it the commands of `.github/workflows/scoring.yml`, and that workflow runs them on every push or PR touching `experiments/scoring/**` (lines 4-12 and 37-40).
- `experiments/evals`: `make field` (`experiments/evals/Makefile:77`) with `TRUTH`, `MAP` and `RULES` runs `uv run score import-measure-lab` and then `uv run score` (`experiments/evals/Makefile:103-106`) from a scoring checkout pinned at commit 77ce830dd08acf0c7982edcec91cd3240e0565ea (line 73), which must be at that commit and unmodified (lines 87-91). `evals/field.py` writes its rows as this package's results files, with no uncertainty and no decisions (`field.py:20-22`).
- The Measure Lab flow: the app shares a session zip whose sha256 is the capture id. The survey's `captures` must list that hash before the importer unpacks anything (`measure_lab.py:218-223`). The map file is written by hand after the walk (`FORMATS.md:94`). `tests/helpers.py` rebuilds the committed fixture zip reproducibly (`session_zip` at 169, the `__main__` block at 191-194): run `uv run python tests/helpers.py rebuild-session-zip`, then put the printed sha256 in the survey's `captures` (`FORMATS.md:139-141`).

## Known limits

What the data can show (`METRICS.md:59-71`): paired results at the listed spots, a case series. It cannot show an accuracy rate for homes in general, validated error bars, safety, homeowner effort, or installation eligibility. Not scored at all (`METRICS.md:73-76`): spot placement error, and latency beyond one processing time per run.

Limits in the code:

- A session's `session.json` is capped at 16 MiB decompressed (`measure_lab.py:71`, enforced at 232-236). The comment calls it a chosen safety bound, not calibrated against real captures (`measure_lab.py:69-70`).
- Lengths are capped at 1000000000 with at most 12 decimal places (`inputs.py:196-201`), which keeps derived inches and error ratios inside Decimal's 28-digit reporting precision.
- Session format 2 only (`measure_lab.py:42`); any other version is refused with its value in the message (`measure_lab.py:244-248`).
- The `+rule` row is an emulation of the survey's strict rule applied to the run's own values, until a real solver exists (`measure_lab.py:55-56`, 472-482; `FORMATS.md:137`).

## Runnable examples in src/examples/

`src/examples/` holds three scripts. Run each from `experiments/scoring`:

```sh
uv run python src/examples/score_fixtures.py
uv run python src/examples/import_measure_lab.py
uv run python src/examples/library_use.py
```

`score_fixtures.py` scores the committed fixtures through the CLI. `import_measure_lab.py` imports the fixture session zip into a results file. `library_use.py` calls the loaders, `score_run` and `markdown` from Python instead of through the CLI.

## Where to look next

- [FORMATS.md](../FORMATS.md): the three input files and the map file, field by field.
- [METRICS.md](../METRICS.md): every number the summary and the CSVs report.
- The component README ([README.md](../README.md)): the experiment, the protocol, and where real data goes.
- [tests/](../tests/): 347 tests. `test_fixtures.py` scores the committed fixtures against hand-worked answers, `test_inputs.py` checks that every input problem names file, field and fix, `test_metrics.py` works each metric by hand, `test_measure_lab.py` runs the importer on a synthetic format-2 session, and `test_report.py` holds the summary's shape under hostile ids.
