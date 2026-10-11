"""Regressions for the reconstruction cache and staleness findings (#111, #139): each failed
before its fix. No model weights and no real captures are used."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from recon import depth, pipeline

# --- #111: a failed run must not leave the previous run's report ----------------------------------


class ExplodingCap:
    @staticmethod
    def load(bundle, work):
        raise RuntimeError("the capture unpack failed")


def test_a_failure_before_the_report_leaves_no_report(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.md").write_text("# the previous run's reconstruction")
    monkeypatch.setattr(pipeline, "cap", ExplodingCap)
    with pytest.raises(RuntimeError, match="unpack failed"):
        pipeline.run(tmp_path / "b.zip", out, tmp_path / "work", "moge2", None, False)
    assert not (out / "report.md").exists(), "the old report described the previous run"


def test_a_fresh_run_creates_the_directory_and_reports(tmp_path: Path, monkeypatch) -> None:
    # The cleanup must not break a first run: the directory is made, the failure still raises.
    monkeypatch.setattr(pipeline, "cap", ExplodingCap)
    with pytest.raises(RuntimeError):
        pipeline.run(
            tmp_path / "b.zip", tmp_path / "fresh", tmp_path / "work", "moge2", None, False
        )
    assert not (tmp_path / "fresh" / "report.md").exists()


# --- #139: a damaged cached depth archive is regenerated, not loaded ------------------------------


def frame(tmp_path: Path, fid: str) -> SimpleNamespace:
    import cv2

    image = tmp_path / f"{fid}.jpg"
    cv2.imwrite(str(image), np.full((48, 64, 3), 128, np.uint8))
    return SimpleNamespace(
        id=fid,
        image=image,
        width=64,
        height=48,
        intrinsics=np.array([50.0, 50.0, 32.0, 24.0]),
        cam_to_world=np.eye(4),
        lidar=None,
    )


def trunc_npz(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, depth=np.full((48, 64), 2.0, np.float32))
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) // 2])


def cache_path(tmp_path: Path, fid: str) -> Path:
    # depth_maps gives the moge step work / "moge2"; the cached archives live there.
    return tmp_path / "moge2" / f"{fid}.moge2.npz"


def run_depth_maps(tmp_path: Path, monkeypatch, subprocess_side_effect) -> dict:
    capture = SimpleNamespace(source="test", frames=[frame(tmp_path, "f1")])
    cache_dir = tmp_path / "moge2"
    cache_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(depth, "moge_cache", lambda c, work: work)
    monkeypatch.setattr(depth.subprocess, "run", subprocess_side_effect)
    # `moge`, not `depth_maps`: below the triangulated rescale, which needs textured,
    # overlapping photos. The cache behaviour under test lives entirely in `moge`.
    return depth.moge(capture, cache_dir)


def test_a_truncated_cache_is_repaired_by_the_next_run(tmp_path: Path, monkeypatch) -> None:
    trunc_npz(cache_path(tmp_path, "f1"))  # the model process died mid-write last time

    def regenerate(cmd, **_):
        manifest = json.loads(Path(cmd[-1]).read_text())  # cmd[-1]: the manifest path
        for entry in manifest:
            np.savez_compressed(entry["out"], depth=np.full((48, 64), 3.0, np.float32))

    run_depth_maps(tmp_path, monkeypatch, regenerate)  # repairs the cache
    # A second run with no model runs available at all: the now-valid cache carries it.
    monkeypatch.setattr(depth.subprocess, "run", lambda *a, **k: None)
    result = run_depth_maps(tmp_path, monkeypatch, lambda *a, **k: None)
    assert result["f1"].depth.mean() == pytest.approx(3.0)


def test_a_failed_regeneration_fails_loudly(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "moge2").mkdir(exist_ok=True)
    trunc_npz(cache_path(tmp_path, "f1"))
    monkeypatch.setattr(depth, "moge_cache", lambda c, work: work)
    monkeypatch.setattr(depth.subprocess, "run", lambda *a, **k: None)  # the model process no-ops
    with pytest.raises((zipfile.BadZipFile, ValueError, OSError)):
        depth.moge(SimpleNamespace(source="t", frames=[frame(tmp_path, "f1")]), tmp_path / "moge2")
