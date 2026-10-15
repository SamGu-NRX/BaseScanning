"""``run-hs11`` reproduces the committed facade golden."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.common.config import default_fixture_source
from server.eval.cli import main

ROOT = Path(__file__).resolve().parents[3]
SCENE = ROOT / "data" / "fixtures" / "scenes" / "eth3d-facade" / "gt-laser" / "scene.json"
GOLDEN = ROOT / "data" / "fixtures" / "verdicts" / "eth3d-facade" / "gt.C1.g80.json"


def _cloud_dir() -> Path | None:
    """Laser and occluder PLYs are not committed. They live in the fixture cache or the store."""
    candidates = [
        SCENE.parent,
        ROOT / ".fixtures" / "gt" / "eth3d-facade" / "gt-laser",
        Path(default_fixture_source()) / "eval-harness" / "results" / "eth3d-facade" / "gt-laser",
    ]
    for directory in candidates:
        if (directory / "laser.ply").is_file() and (directory / "occluders.ply").is_file():
            return directory
    return None


def test_run_hs11_reproduces_facade_gt(tmp_path: Path) -> None:
    clouds = _cloud_dir()
    if clouds is None:
        pytest.skip("facade laser and occluder PLYs are not on this machine")
    work = tmp_path / "gt-laser"
    work.mkdir()
    (work / "scene.json").write_bytes(SCENE.read_bytes())
    coverage = SCENE.parent / "coverage.npz"
    if coverage.is_file():
        (work / "coverage.npz").symlink_to(coverage)
    for name in ("laser.ply", "occluders.ply"):
        (work / name).symlink_to(clouds / name)
    out = tmp_path / "verdict.json"
    assert main(["run-hs11", "--scene", str(work / "scene.json"), "--out", str(out)]) == 0
    got = json.loads(out.read_text())
    ref = json.loads(GOLDEN.read_text())
    got["evaluator"].pop("runtime_s", None)
    ref["evaluator"].pop("runtime_s", None)
    got["scene"].pop("sha256", None)
    ref["scene"].pop("sha256", None)
    assert got == ref
