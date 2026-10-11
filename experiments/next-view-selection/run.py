"""Run the next-view selection study, or replay committed results against a fresh run.

    uv run --project experiments/next-view-selection \
        python experiments/next-view-selection/run.py \
        --manifest experiments/next-view-selection/manifest.json

Add ``--replay experiments/next-view-selection/results`` to regenerate every
CSV and figure in a scratch directory, compare them byte-for-byte with the
committed results, and write replay-report.md into the results directory.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path

PKG_PARENT = Path(__file__).resolve().parent
sys.path.insert(0, str(PKG_PARENT))

from next_view_selection.figures import FIGURE_SPECS, write_figures  # noqa: E402
from next_view_selection.model import StudySpec, load_manifest, spec_from_manifest  # noqa: E402
from next_view_selection.study import (  # noqa: E402
    RunResult,
    impossible_table,
    run_study,
    summarize,
    write_results,
)

CSV_FILES = ("runs.csv", "summary.csv", "impossible_views.csv")
FIGURE_FILES = tuple(spec[0] for spec in FIGURE_SPECS)
DEFAULT_RESULTS = PKG_PARENT / "results"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def print_summary(summary_rows: list[dict[str, object]]) -> None:
    print(f"{'policy':<22}{'resolved':>9}{'rate':>7}{'mean cost':>11}{'unsupported':>13}")
    for row in summary_rows:
        print(
            f"{row['policy']:<22}{row['resolved']:>9}{row['resolution_rate']:>7.3f}"
            f"{row['mean_cost']:>11.3f}{row['unsupported']:>13}"
        )


def run_and_write(out_dir: Path, manifest: dict) -> tuple[StudySpec, list[RunResult]]:
    spec = spec_from_manifest(manifest)
    results = run_study(spec)
    write_results(out_dir, results, spec)
    write_figures(summarize(results, spec), out_dir / "figures")
    return spec, results


def replay(results_dir: Path, manifest: dict) -> int:
    fresh = Path(tempfile.mkdtemp(prefix="nvs-replay-"))
    run_and_write(fresh, manifest)
    checks: list[tuple[str, bool]] = []
    report_lines = [
        "# Replay report",
        "",
        "Fresh regeneration compared byte-for-byte with the committed results.",
        "",
        "| file | committed sha256 | fresh sha256 | agreement |",
        "|---|---|---|---|",
    ]
    names = [*CSV_FILES, *(f"figures/{name}" for name in FIGURE_FILES)]
    for name in names:
        committed, fresh_path = results_dir / name, fresh / name
        if not committed.exists():
            checks.append((name, False))
            report_lines.append(f"| {name} | missing | {sha256(fresh_path)} | MISSING |")
            continue
        agree = committed.read_bytes() == fresh_path.read_bytes()
        checks.append((name, agree))
        report_lines.append(
            f"| {name} | {sha256(committed)} | {sha256(fresh_path)} "
            f"| {'MATCH' if agree else 'DIFF'} |"
        )
    agreement = "FULL" if all(ok for _, ok in checks) else "FAILED"
    report_lines += [
        "",
        f"Regeneration agreement: {agreement}.",
        "",
        "Replay re-runs the study from the frozen manifest only; no cached state is consulted.",
        "Results describe synthetic acquisition, never field improvement or electrical safety.",
        "",
    ]
    (results_dir / "replay-report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print("\n".join(report_lines))
    return 0 if all(ok for _, ok in checks) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="next-view selection study runner")
    parser.add_argument("--manifest", required=True, help="path to the frozen manifest.json")
    parser.add_argument(
        "--out", default=str(DEFAULT_RESULTS), help="output directory for runs/summary/figures"
    )
    parser.add_argument(
        "--replay",
        default=None,
        help="regenerate into a scratch dir and verify byte agreement with this results dir",
    )
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.replay:
        return replay(Path(args.replay), manifest)
    out_dir = Path(args.out)
    spec, results = run_and_write(out_dir, manifest)
    summary_rows = summarize(results, spec)
    print(f"study: {len(spec.worlds)} worlds x {len(spec.policy_names)} policies -> {out_dir}")
    print_summary(summary_rows)
    impossible = [
        str(row["world"]) for row in impossible_table(results, spec) if not row["oracle_resolvable"]
    ]
    print(f"impossible-view worlds (oracle): {impossible}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
