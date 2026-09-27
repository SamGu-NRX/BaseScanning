"""2D boxes become wall rectangles, and ground votes need two agreeing frames."""

from __future__ import annotations

import numpy as np

from server.scene.contract import check_scene
from server.scene.objects2d import apply_detections, ground_material


def _flat(pose: np.ndarray) -> list[float]:
    return [float(value) for value in np.asarray(pose, float).reshape(-1, order="F")]


def _pose(x: float, yaw_deg: float = 0.0) -> np.ndarray:
    pose = np.eye(4)
    if yaw_deg:
        theta = np.radians(yaw_deg)
        c, s = float(np.cos(theta)), float(np.sin(theta))
        pose[:3, :3] = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    pose[:3, 3] = [x, 1.5, 3.0]
    return pose


def _scene() -> dict:
    return {
        "format": "base-scene/v0",
        "scene_id": "box",
        "provenance": {
            "data": "synthetic",
            "site": "box",
            "producer": {"path": "hss-geometry"},
            "scale_source": "none",
            "gravity_source": "arkit",
            "notes": [],
        },
        "frame": {
            "units": "m",
            "convention": "meter_anchored/v0",
            "anchor": {"element_id": "meter", "plus_minus_m": 0.05},
        },
        "walls": [
            {
                "id": "w1",
                "role": "house",
                "baseline_m": [[0.0, 0.0], [4.0, 0.0]],
                "height_m": 3.0,
                "s_range_m": [0.0, 4.0],
                "plus_minus_m": 0.05,
                "evidence": "observed",
            }
        ],
        "chain": {
            "wall_ids": ["w1"],
            "ends": {
                "left": {"kind": "unexplored", "point_m": [0.0, 0.0], "s_m": 0.0},
                "right": {"kind": "unexplored", "point_m": [4.0, 0.0], "s_m": 4.0},
            },
        },
        "ground": {
            "surface": {"kind": "plane", "normal": [0.0, 1.0, 0.0], "offset_m": 0.0},
            "plus_minus_m": 0.05,
            "regions": [],
        },
        "objects": [
            {
                "id": "meter",
                "class": "meter",
                "geometry": {"kind": "point", "pos_m": [2.0, 1.5, 0.0]},
                "wall_id": "w1",
                "confidence": 1.0,
                "plus_minus_m": 0.05,
                "source": {"kind": "tap"},
                "evidence": "observed",
            }
        ],
        "coverage": {
            "method": "rays",
            "bands": [{"band": "ground", "wall_id": "w1", "s_m": [0.0, 4.0], "out_m": 2.0}],
        },
        "keyframes": [
            {
                "id": "cam-a",
                "pose": _flat(_pose(2.0)),
                "intrinsics": [100.0, 100.0, 50.0, 50.0],
                "w": 100,
                "h": 100,
                "pose_source": "arkit",
            },
            {
                "id": "cam-b",
                "pose": _flat(_pose(2.0, 25.0)),
                "intrinsics": [100.0, 100.0, 50.0, 50.0],
                "w": 100,
                "h": 100,
                "pose_source": "arkit",
            },
        ],
    }


def _box(image: str) -> dict:
    return {"image": image, "box_px": [40.0, 40.0, 60.0, 60.0], "score": 0.9}


def test_two_views_become_a_wall_rect():
    scene = _scene()
    apply_detections(
        scene,
        {
            "format": "hss-objects/v0",
            "objects": [
                {
                    "id": "window-1",
                    "class": "window",
                    "label": "front window",
                    "confidence": 0.8,
                    "views": [_box("cam-a"), _box("cam-b")],
                    "source": {"model": "ws07"},
                    "attrs": {"operable": True, "ignored": 1},
                }
            ],
            "ground_type": [
                {"image": "cam-a", "material": "concrete", "confidence": 0.7},
                {"image": "cam-b", "material": "concrete", "confidence": 0.6},
            ],
        },
    )
    check_scene(scene)
    window = next(obj for obj in scene["objects"] if obj["id"] == "window-1")
    geom = window["geometry"]
    assert geom["kind"] == "wall_rect"
    assert geom["wall_id"] == "w1"
    assert 1.4 < float(np.mean(geom["s_m"])) < 2.6
    assert geom["bottom_m"] < geom["top_m"]
    assert window["plus_minus_m"] == 0.25
    assert window["source"]["kind"] == "detector"
    assert window["source"]["model"] == "ws07"
    assert window["attrs"] == {"operable": True}
    assert scene["ground"]["regions"][0]["material"] == "concrete"
    assert scene["ground"]["regions"][0]["source"]["kind"] == "vlm"
    assert scene["ground"]["regions"][0]["confidence"] == 1.0
    assert scene["ground"]["regions"][0]["slope_deg"] == 0.0
    assert "objects_2d:1" in scene["provenance"]["notes"]


def test_one_registered_view_is_not_cast():
    scene = _scene()
    apply_detections(
        scene,
        {
            "format": "hss-objects/v0",
            "objects": [{"id": "door-1", "class": "door", "confidence": 0.5, "views": [_box("cam-a")]}],
        },
    )
    assert [obj["id"] for obj in scene["objects"]] == ["meter"]


def test_wrong_format_is_ignored():
    scene = _scene()
    apply_detections(scene, {"format": "other", "objects": [{"id": "x", "class": "window"}]})
    assert [obj["id"] for obj in scene["objects"]] == ["meter"]
    assert scene["ground"]["regions"] == []


def test_ground_votes_need_two_agreeing_frames():
    images = {"cam-a", "cam-b"}
    assert ground_material(None, images) == ("unknown", "reconstruction", None)
    assert ground_material([{"image": "cam-a", "material": "lawn"}], images) == ("unknown", "reconstruction", None)
    assert ground_material(
        [{"image": "cam-a", "material": "gravel"}, {"image": "cam-b", "material": "lawn"}],
        images,
    ) == ("unknown", "reconstruction", None)
    assert ground_material(
        [
            {"image": "cam-a", "material": "lawn"},
            {"image": "cam-b", "material": "lawn"},
            {"image": "cam-a", "material": "dirt"},
        ],
        images,
    ) == ("lawn", "vlm", 0.6667)
    assert ground_material(
        [{"image": "other", "material": "lawn"}, {"image": "other", "material": "lawn"}],
        images,
    ) == ("unknown", "reconstruction", None)


def test_no_ground_band_writes_no_region():
    scene = _scene()
    scene["coverage"]["bands"] = []
    apply_detections(
        scene,
        {
            "format": "hss-objects/v0",
            "objects": [],
            "ground_type": [
                {"image": "cam-a", "material": "pavers"},
                {"image": "cam-b", "material": "pavers"},
            ],
        },
    )
    assert scene["ground"]["regions"] == []
