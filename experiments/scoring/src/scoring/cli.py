"""The score command: load the inputs, print a markdown summary, write the CSV tables."""

import argparse
import sys
from pathlib import Path

from scoring import measure_lab
from scoring.inputs import InputError, load_study, refuse_output_aliases, refuse_output_over_inputs
from scoring.metrics import score_run
from scoring.report import csv_paths, markdown, write_csvs


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["import-measure-lab"]:
        return measure_lab.main(argv[1:])
    parser = argparse.ArgumentParser(
        prog="score",
        description="Score pipeline runs against a tape survey. Prints a markdown summary and "
        "writes measurements.csv, checks.csv and runs.csv.",
        epilog="To turn a Measure Lab session into a results file, run "
        "`score import-measure-lab --help`.",
    )
    parser.add_argument("--rules", type=Path, required=True, help="rules file (JSON)")
    parser.add_argument(
        "--truth", type=Path, nargs="+", required=True, help="one survey file per house"
    )
    parser.add_argument(
        "--results", type=Path, nargs="+", required=True, help="one file per pipeline run"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data"),
        help="directory for the CSV tables (default: data, which git ignores)",
    )
    args = parser.parse_args(argv)

    try:
        refuse_output_aliases(csv_paths(args.out))
        # Before loading or printing anything, so a colliding --out never touches an input.
        refuse_output_over_inputs(
            csv_paths(args.out),
            [
                ("rules", args.rules),
                *(("survey", path) for path in args.truth),
                *(("results", path) for path in args.results),
            ],
        )
        study = load_study(args.rules, args.truth, args.results)
    except InputError as error:
        print(f"score: {error}", file=sys.stderr)
        return 2

    runs = [
        score_run(house.truth, results, study.rules.thresholds)
        for house in study.houses
        for results in house.runs
    ]
    sys.stdout.write(markdown(study, runs))
    for path in write_csvs(runs, args.out):
        print(f"score: wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
