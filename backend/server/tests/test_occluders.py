"""SD-61: a missing laser or recon cloud is an error, never a silent empty occluder set."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from server.common.occluders import (
    MissingOccluderGeometry,
    check_occluder_geometry,
    gate_rejects_provisional,
)
from server.coverage.occlusion import gate_rejects_provisional as coverage_gate


def _keyframe() -> dict:
    return {
        "id": "k0",
        "pose": np.eye(4).flatten(order="F").tolist(),
        "intrinsics": [100.0, 100.0, 50.0, 50.0],
        "w": 100,
        "h": 100,
    }


def _scene(*assets: dict) -> dict:
    return {"assets": list(assets), "keyframes": [_keyframe()]}


def _laser() -> dict:
    return {"id": "laser", "kind": "point_cloud", "path": "laser.ply", "format": "ply"}


def _occluders() -> dict:
    return {"id": "occluders", "kind": "other", "path": "occluders.ply", "format": "ply"}


def test_missing_declared_ply_raises(tmp_path: Path) -> None:
    scene = _scene(_laser(), _occluders())
    with pytest.raises(MissingOccluderGeometry) as exc:
        check_occluder_geometry((scene, tmp_path))
    assert "laser.ply" in str(exc.value)
    assert "occluders.ply" in str(exc.value)


def test_allow_missing_marks_provisional_and_gates_reject(tmp_path: Path) -> None:
    scene = _scene(_laser())
    status = check_occluder_geometry((scene, tmp_path), allow_missing=True)
    assert status["provisional"] is True
    assert status["missing"]
    assert gate_rejects_provisional(status)
    assert coverage_gate(status)
    assert gate_rejects_provisional({"provisional": False}) is False


def test_present_clouds_are_not_provisional(tmp_path: Path) -> None:
    (tmp_path / "laser.ply").write_bytes(b"present")
    (tmp_path / "occluders.ply").write_bytes(b"present")
    status = check_occluder_geometry((_scene(_laser(), _occluders()), tmp_path))
    assert status == {"provisional": False, "missing": []}


def _scoreable(tmp_path: Path) -> Path:
    scene = _scene(_laser())
    pose = np.eye(4)
    pose[:3, 3] = [1.0, 1.5, 3.0]
    scene["keyframes"][0]["pose"] = pose.flatten(order="F").tolist()
    scene.update(
        {
            "walls": [
                {
                    "id": "w1",
                    "baseline_m": [[0.0, 0.0], [2.0, 0.0]],
                    "height_m": 3.0,
                    "plus_minus_m": 0.01,
                    "evidence": "observed",
                    "s_range_m": [0.0, 2.0],
                    "base_y_m": 0.0,
                }
            ],
            "chain": {"wall_ids": ["w1"]},
            "frame": {"anchor": {"element_id": "meter"}},
            "objects": [{"id": "meter", "geometry": {"kind": "point", "pos_m": [1.0, 1.5, 0.2]}}],
            "ground": {"surface": {"kind": "plane", "normal": [0.0, 1.0, 0.0], "offset_m": 0.0}},
        }
    )
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(scene))
    return path


def test_score_scene_rejects_a_missing_cloud(tmp_path: Path) -> None:
    from server.common.occluders import check_occluder_geometry as check

    scene_path = _scoreable(tmp_path)
    scene = json.loads(scene_path.read_text())
    with pytest.raises(MissingOccluderGeometry):
        check((scene, tmp_path))
