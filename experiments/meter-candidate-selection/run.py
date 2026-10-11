"""Run the candidate-selection experiment, or replay its presentation tables.

Run (each arm reads its frozen inputs, candidates and ranking score every case, and the
raw reader output plus the scored tables land in results/):

    uv run --project experiments/meter-candidate-selection python \
        experiments/meter-candidate-selection/run.py \
        --manifest experiments/meter-candidate-selection/manifest.json

Replay (recompute cases.csv, summary.json and tables.md from results/raw alone — no
reader runs, no input files — and overwrite the presentation files in place; the commit
must stay byte-identical, which test_replay checks):

    uv run --project experiments/meter-candidate-selection python \
        experiments/meter-candidate-selection/run.py \
        --replay experiments/meter-candidate-selection/results

The meterocr arm (Apple Vision) is recorded as not-run: it needs macOS, and a mock is not
OCR evidence.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from meter_candidates import gold, scoring, tables, tesseract

RAW_DIR = "raw"


def _experiment_dir(manifest_path: Path) -> Path:
    return manifest_path.resolve().parent


def _read_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text())
    ids = [case["id"] for case in manifest["cases"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("manifest.json: duplicate case ids")
    return manifest


def run(manifest_path: Path) -> None:
    experiment = _experiment_dir(manifest_path)
    manifest = _read_manifest(manifest_path)
    results_dir = experiment / "results"
    raw_dir = results_dir / RAW_DIR
    if raw_dir.exists():
        shutil.rmtree(raw_dir)  # a fresh run never inherits a previous run's raw output
    raw_dir.mkdir(parents=True, exist_ok=True)

    engine = tesseract.available()
    records: list[dict] = []
    for case in manifest["cases"]:
        arm = case["arm"]
        if arm == "observed":
            result = json.loads((experiment / case["observations"]).read_text())
            result["reader"] = {"name": "hand-authored observation set", "file": case["observations"]}
        elif arm == "tesseract":
            if engine is None:
                print(f"skipping {case['id']}: tesseract not available", file=sys.stderr)
                continue
            binary, version = engine
            result = tesseract.read_image(binary, version, str(experiment / case["image"]))
        else:
            print(f"skipping {case['id']}: arm {arm} is not runnable here", file=sys.stderr)
            continue
        result["case_id"] = case["id"]
        result["arm"] = arm
        out = raw_dir / arm / f"{case['id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(tables.dumps(result))
        records.append(scoring.evaluate_case(case, result, gold.load()[case["id"]]))
    _present(results_dir, records, manifest)


def replay(results_dir: Path) -> None:
    """Recompute the presentation files from results/raw alone."""
    experiment = results_dir.resolve().parent
    manifest = _read_manifest(experiment / "manifest.json")
    raw_dir = results_dir / RAW_DIR
    if not raw_dir.is_dir():
        raise SystemExit(f"--replay: no {raw_dir}")
    labels = gold.load(experiment / "gold" / "labels.json")
    cases = {case["id"]: case for case in manifest["cases"]}
    records = []
    for path in sorted(raw_dir.glob("*/*.json")):
        result = json.loads(path.read_text())
        case = cases[result["case_id"]]
        records.append(scoring.evaluate_case(case, result, labels[case["id"]]))
    _present(results_dir, records, manifest)


def _present(results_dir: Path, records: list[dict], manifest: dict) -> None:
    # One ordering for run and replay alike: the commit stays byte-identical either way.
    records = sorted(records, key=lambda r: (r["arm"], r["id"]))
    (results_dir / "cases.csv").write_text(tables.cases_csv(records))
    summary = tables.build_summary(records, manifest["arms"])
    (results_dir / "summary.json").write_text(tables.dumps(summary))
    (results_dir / "tables.md").write_text(tables.tables_md(summary, records))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="the experiment manifest.json")
    parser.add_argument("--replay", type=Path, help="results dir to replay from raw output")
    args = parser.parse_args()
    if not args.replay and not args.manifest:
        parser.error("one of --manifest or --replay is required")
    if args.replay:
        replay(args.replay)
    else:
        run(args.manifest)


if __name__ == "__main__":
    main()
