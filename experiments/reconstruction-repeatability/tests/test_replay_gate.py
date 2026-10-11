"""The replay CLI is a gate: it must exit 0 only when every committed case record
reproduces byte-for-byte, and nonzero when one does not. Both controls below drive the
actual CLI (``run.py --replay``) as a subprocess, so what they test is what a reviewer
would run. Each replay re-executes the full manifest, which is why these take about a
minute each; the slowness is the evidence."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parent.parent
RUN = EXPERIMENT / "run.py"
RESULTS = EXPERIMENT / "results"


def run_cli(results_dir: Path) -> subprocess.CompletedProcess:
    """Run the real CLI against a results dir; cwd is the repo root because the committed
    record stores the manifest path relative to it. Popen, not subprocess.run: conftest's
    stub replaces subprocess.run for the model, and the point here is the real process."""
    proc = subprocess.Popen(
        [sys.executable, str(RUN), "--replay", str(results_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=EXPERIMENT.parent.parent,
    )
    out, err = proc.communicate(timeout=600)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def copy_results(tmp_path: Path) -> Path:
    dest = tmp_path / "results"
    shutil.copytree(RESULTS, dest)
    return dest


def test_clean_witnesses_replay_to_zero(tmp_path: Path):
    """Clean control: the committed pipeline/cache witness receipts reproduce and the CLI
    accepts them with exit 0."""
    proc = run_cli(copy_results(tmp_path))
    doc = json.loads((tmp_path / "results" / "replay.json").read_text())
    assert doc["agreed"] == doc["total"], proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_mutated_witness_replays_to_nonzero(tmp_path: Path):
    """Negative control: a deliberately mutated cached result — one witness record edited
    in place — must make the actual CLI exit nonzero, naming the disagreement."""
    dest = copy_results(tmp_path)
    record_path = dest / "reconstruction_repeatability.json"
    record = json.loads(record_path.read_text())
    baseline = next(c for c in record["cases"] if c["name"] == "baseline-repeat")
    # ground_height_ft sits inside the first run's cached wall metrics — a cached result
    # quietly rewritten in place.
    baseline["observed"]["wall_metric_runs"][0]["ground_height_ft"] += 0.125
    record_path.write_text(json.dumps(record, indent=1))

    proc = run_cli(dest)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "baseline-repeat" in proc.stderr
    replay_doc = json.loads((dest / "replay.json").read_text())
    marked = next(c for c in replay_doc["cases"] if c["name"] == "baseline-repeat")
    assert marked["agreement"] == "DIFFER"


def test_manifest_drift_refuses_the_replay(tmp_path: Path):
    """A results dir whose record no longer matches its manifest digest is not evidence:
    the CLI must refuse and exit nonzero rather than silently replay the wrong contract."""
    dest = copy_results(tmp_path)
    record_path = dest / "reconstruction_repeatability.json"
    record = json.loads(record_path.read_text())
    record["manifest_sha256"] = "0" * 64
    record_path.write_text(json.dumps(record, indent=1))

    proc = run_cli(dest)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "refused" in proc.stderr
