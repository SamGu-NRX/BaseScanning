"""Preview cloud: binary little-endian PLY, generated flag, fixed stride."""

import json

import numpy as np

from server.result.harness import sceneio
from server.result.preview import build_preview
from server.result.tests.support import DATA


def test_preview_is_binary_and_marks_generated(tmp_path):
    scene = json.loads((DATA / "synthetic" / "scene.json").read_text())
    assert build_preview(scene, DATA / "synthetic", tmp_path / "preview.ply", max_points=200_000) is None
    header = (tmp_path / "preview.ply").read_bytes().split(b"end_header", 1)[0]
    assert b"format binary_little_endian 1.0" in header
    cloud = sceneio.read_ply(tmp_path / "preview.ply")
    assert len(cloud["x"]) <= 200_000
    assert cloud["generated"].dtype == np.uint8
    inside = np.isclose(cloud["x"], 1.0) & np.isclose(cloud["z"], 0.2)
    assert int(cloud["generated"][inside][0]) == 1
    outside = np.isclose(cloud["x"], 5.0)
    assert int(cloud["generated"][outside][0]) == 0


def test_preview_stride_caps_the_cloud(tmp_path):
    scene = json.loads((DATA / "synthetic" / "scene.json").read_text())
    assert build_preview(scene, DATA / "synthetic", tmp_path / "preview.ply", max_points=2) is None
    cloud = sceneio.read_ply(tmp_path / "preview.ply")
    assert len(cloud["x"]) <= 2


def test_missing_cloud_file_is_preview_skipped(tmp_path):
    scene = json.loads((DATA / "synthetic" / "scene.json").read_text())
    scene["assets"][0]["path"] = "missing-laser.ply"
    empty = tmp_path / "scene"
    empty.mkdir()
    assert build_preview(scene, empty, tmp_path / "preview.ply", max_points=10) == "preview_skipped"
    assert not (tmp_path / "preview.ply").exists()


def test_preview_skipped_without_a_cloud(tmp_path):
    scene = json.loads((DATA / "synthetic" / "scene.json").read_text())
    scene["assets"] = []
    scene.pop("generated_regions", None)
    empty = tmp_path / "scene"
    empty.mkdir()
    assert build_preview(scene, empty, tmp_path / "preview.ply", max_points=10) == "preview_skipped"
    assert not (tmp_path / "preview.ply").exists()
