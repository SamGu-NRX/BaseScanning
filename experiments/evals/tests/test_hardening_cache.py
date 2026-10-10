"""Regressions for the evals cache and staleness findings (#106, #170, #173): each failed
before its fix. No model weights load anywhere here."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from evals import field, triangulate
from evals.pose_priors import fit_inputs
from models import run_groups
from models.common import write_npz


@dataclass
class FakeResult:
    path: Path
    depth: np.ndarray
    valid: np.ndarray
    intrinsics: np.ndarray
    cam_to_world: np.ndarray | None = None
    arrays: dict | None = None


def fake_results(stems: list[str]) -> list[FakeResult]:
    out = []
    for stem in stems:
        depth = np.full((4, 5), 2.5, np.float32)
        valid = np.ones((4, 5), bool)
        out.append(
            FakeResult(
                Path(f"{stem}.npz"),
                depth,
                valid,
                np.array([100.0, 100.0, 2.0, 1.5]),
                np.eye(4),
            )
        )
    return out


def published_group(tmp_path: Path, stems=("S1", "S2"), key="k1") -> Path:
    out = tmp_path / "group"
    run_groups.write_group(
        out, fake_results(list(stems)), {"fingerprint": key, "members": list(stems)}
    )
    return out


# --- #106: a cache hit requires the expected outputs, at the recorded content --------------------


def test_a_published_group_is_reusable(tmp_path: Path) -> None:
    out = published_group(tmp_path)
    assert run_groups.reusable(out, ["S1", "S2"], "k1")


def test_a_missing_member_output_is_not_reusable(tmp_path: Path) -> None:
    out = published_group(tmp_path)
    (out / "S1.npz").unlink()
    assert not run_groups.reusable(out, ["S1", "S2"], "k1")


def test_a_truncated_member_output_is_not_reusable(tmp_path: Path) -> None:
    out = published_group(tmp_path)
    raw = (out / "S1.npz").read_bytes()
    (out / "S1.npz").write_bytes(raw[: len(raw) // 2])
    assert not run_groups.reusable(out, ["S1", "S2"], "k1")


def test_a_substituted_member_output_is_not_reusable(tmp_path: Path) -> None:
    out = published_group(tmp_path)
    write_npz(out, "S1", np.full((4, 5), 9.0, np.float32), np.ones((4, 5), bool), np.ones(4))
    assert not run_groups.reusable(out, ["S1", "S2"], "k1")


def test_another_inputs_run_json_is_not_reusable(tmp_path: Path) -> None:
    out = published_group(tmp_path, key="k1")
    assert not run_groups.reusable(out, ["S1", "S2"], "k2")


def test_a_corrupt_or_absent_run_json_is_not_reusable(tmp_path: Path) -> None:
    out = published_group(tmp_path)
    (out / "run.json").write_text("{truncated")
    assert not run_groups.reusable(out, ["S1", "S2"], "k1")
    (out / "run.json").unlink()
    assert not run_groups.reusable(out, ["S1", "S2"], "k1")


def test_a_run_json_without_output_records_is_not_reusable(tmp_path: Path) -> None:
    # Published by a pre-change version: no content identity to check, so regenerate once.
    out = tmp_path / "group"
    out.mkdir()
    (out / "run.json").write_text(json.dumps({"fingerprint": "k1"}))
    assert not run_groups.reusable(out, ["S1", "S2"], "k1")


# --- #170: a rerun leaves nothing of the previous run's derived output ----------------------------


def stale_outputs(tmp_path: Path) -> Path:
    out_dir = tmp_path / "results"
    out_dir.mkdir()
    (out_dir / "moge2.json").write_text("[]")
    (out_dir / "measure-lab.json").write_text("{}")
    (out_dir / "field_report.md").write_text("# an older run's report")
    (out_dir / "scored").mkdir()
    (out_dir / "scored" / "scored.md").write_text("# an older run's scored output")
    return out_dir


def test_a_failing_rerun_clears_the_previous_derived_outputs(tmp_path: Path) -> None:
    out_dir = stale_outputs(tmp_path)

    def boom(session_path):
        raise RuntimeError("the unpack failed")

    field.unpack = boom  # a failure after the cleanup, before any new output
    with pytest.raises(RuntimeError):
        field.score(tmp_path / "session.zip", None, None, None, out_dir)
    assert not (out_dir / "field_report.md").exists()
    assert not (out_dir / "scored").exists()
    assert not (out_dir / "moge2.json").exists()


def test_a_rerun_without_truth_leaves_no_older_scored_output(tmp_path: Path) -> None:
    out_dir = stale_outputs(tmp_path)
    folder = tmp_path / "folder"
    folder.mkdir()
    session = {
        "session": {"id": "s"},
        "keyframes": [{"id": "k1"}],
        "taps": [],
        "points": [],
        "walls": [],
        "measurements": [],
    }
    field.unpack = lambda p: (folder, "cap")
    field.load_session = lambda f: session
    field.work_dir = lambda f: folder / "work"
    field.capture_id = lambda f, c: "cap"
    field.triangulated_scales = lambda *a: {}
    field.learned_points = lambda *a: {}
    field.learned_values = lambda *a: {}
    (folder / "work").mkdir()
    (folder / "work" / "capture.json").write_text(json.dumps({"capture": "cap"}))
    (folder / "work" / "turns.json").write_text(json.dumps({"k1": 0}))
    report = field.score(tmp_path / "session.zip", None, None, None, out_dir)
    assert "Missing" in report  # the run reports its gaps instead of scoring
    assert not (out_dir / "field_report.md").exists()
    assert not (out_dir / "scored").exists()


# --- #173 (1): the fit-cache key carries the matching settings ------------------------------------


def fit_key(**changes) -> str:
    d = tmp_files()
    T = {"a": np.eye(4), "b": np.eye(4)}
    K = {"a": np.array([100.0, 100.0, 2.0, 1.5]), "b": np.array([100.0, 100.0, 2.0, 1.5])}
    kwargs = {"max_reproj_px": 2.0, "min_points": 20, "min_angle_deg": 2.0, **changes}
    return fit_inputs(T, K, images=d["images"], depths=d["depths"], **kwargs)


def tmp_files() -> dict:
    import tempfile

    d = Path(tempfile.mkdtemp())
    for name in ("a", "b"):
        (d / f"{name}.jpg").write_bytes(b"image bytes")
        np.savez_compressed(d / f"{name}.npz", depth=np.ones((2, 2), np.float32))
    return {
        "images": {n: d / f"{n}.jpg" for n in ("a", "b")},
        "depths": {n: d / f"{n}.npz" for n in ("a", "b")},
    }


def test_the_fit_key_changes_with_each_matching_setting_alone() -> None:
    baseline = fit_key()
    assert fit_key() == baseline  # stable
    assert fit_key(min_points=21) != baseline
    assert fit_key(min_angle_deg=3.0) != baseline
    old_ratio, old_features = triangulate.RATIO, triangulate.MAX_FEATURES
    try:
        triangulate.RATIO = 0.7
        assert fit_key() != baseline
        triangulate.RATIO = old_ratio
        triangulate.MAX_FEATURES = 3999
        assert fit_key() != baseline
    finally:
        triangulate.RATIO, triangulate.MAX_FEATURES = old_ratio, old_features


# --- #173 (2): a failed coverage run writes neither file ------------------------------------------


def test_a_coverage_refusal_writes_no_results(tmp_path: Path, monkeypatch) -> None:
    from evals import coverage

    monkeypatch.setattr(coverage, "check_kit", lambda: None)
    monkeypatch.setattr(coverage, "RUNS", ["s1"])
    monkeypatch.setattr(
        coverage,
        "evaluate_scene",
        lambda s: {"scene": s, "wall": {"x": 1}, "replica_mismatches": ["a mismatch"]},
    )
    monkeypatch.setattr(coverage, "RESULTS", tmp_path / "results")
    with pytest.raises(SystemExit, match="disagrees"):
        coverage.main()
    assert not (tmp_path / "results" / "coverage.json").exists()
    assert not (tmp_path / "results" / "coverage.md").exists()


def test_a_passing_coverage_run_writes_both_files(tmp_path: Path, monkeypatch) -> None:
    from evals import coverage

    monkeypatch.setattr(coverage, "check_kit", lambda: None)
    monkeypatch.setattr(coverage, "RUNS", ["s1"])
    monkeypatch.setattr(
        coverage,
        "evaluate_scene",
        lambda s: {"scene": s, "wall": {"x": 1}, "replica_mismatches": []},
    )
    monkeypatch.setattr(coverage, "results_json", lambda runs: "{}")
    monkeypatch.setattr(coverage, "markdown", lambda runs: "# coverage\n")
    monkeypatch.setattr(coverage, "RESULTS", tmp_path / "results")
    coverage.main()
    assert (tmp_path / "results" / "coverage.json").read_text() == "{}"
    assert (tmp_path / "results" / "coverage.md").read_text() == "# coverage\n"
