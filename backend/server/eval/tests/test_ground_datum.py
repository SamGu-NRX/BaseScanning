"""SD-101: a rejected, unreanchored ground plane cannot support an observed grade.

Facade's cameras sit about 4.3 m above a tilted plane, so the six ground claims
that the 80° rule called observed become non-decisive. Electro's plane is level
and the cameras are already 1.5 m up, so the same notes leave footprint_clear observed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np

from server.eval.chain import ground_y
from server.eval.coverage import (
    CoverageQuery,
    ground_incidence_untrusted,
    query_region,
    withhold_untrusted_ground,
)
from server.eval.frame import mat4_from_list
from server.eval.tests.flat_wall import _pose as flat_pose
from server.eval.tests.flat_wall import write as write_flat_wall
from server.eval.visibility import Camera

# Profile A on the M2 facade run. Claimed observed at 76.5–77.4°; laser truth is grazing.
_FACADE_CLAIMS = (
    ("ground_surface", "fail", [0.0, 0.6096], 77.4, 0.905),
    ("ac_clearance", "pass", [0.0, 0.6096], 77.4, 0.905),
    ("footprint_clear", "pass", [0.0, 0.6096], 77.4, 0.905),
    ("gas_clearance", "unsure", [0.0, 2.1336], 76.5, 0.912),
    ("drive_clearance", "pass", [0.0, 2.1336], 76.5, 0.912),
    ("pool_clearance", "pass", [0.0, 2.1336], 76.5, 0.912),
)
_FACADE_S = [-2.806, -2.1964]


def _pose(y: float) -> list[float]:
    return _pose_at(0.0, y, 0.0)


def _pose_at(x: float, y: float, z: float) -> list[float]:
    transform = np.eye(4)
    transform[:3, 3] = (x, y, z)
    return [float(v) for v in transform.flatten(order="F")]


def _scene(notes: list[str], normal: list[float], camera_y: float) -> dict:
    return {
        "provenance": {"notes": list(notes)},
        "ground": {"surface": {"kind": "plane", "normal": normal, "offset_m": 0.0}},
        "walls": [
            {
                "id": "w1",
                "baseline_m": [[-2.0, 0.0], [2.0, 0.0]],
                "height_m": 2.5,
                "s_range_m": [-2.0, 2.0],
                "plus_minus_m": 0.3,
                "evidence": "observed",
            }
        ],
        "chain": {"wall_ids": ["w1"]},
        "frame": {"anchor": {"element_id": "meter"}},
        "objects": [{"id": "meter", "geometry": {"kind": "point", "pos_m": [0.0, 0.0, 0.0]}}],
        "keyframes": [{"id": "k0", "pose": _pose(camera_y)}],
    }


def _facade_scene() -> dict:
    return _scene(
        ["ground_plane_rejected:4.75", "ground_base_unobserved"],
        [0.158189, 0.979744, -0.122788],
        4.82,
    )


def _electro_scene() -> dict:
    return _scene(
        ["ground_plane_rejected:3.24", "ground_base_unobserved"],
        [0.011096, 0.999506, -0.029414],
        1.56,
    )


def _evidence(best: float, share: float, out_m: list[float]) -> dict:
    return {
        "region": {"wall_id": "w1", "s_m": list(_FACADE_S), "out_m": list(out_m)},
        "status": "observed",
        "observed_share": share,
        "best_incidence_deg": best,
        "sensitivity": [
            {"rule_id": "angle75", "status": "grazing", "observed_share": 0.0},
            {"rule_id": "angle80", "status": "observed", "observed_share": share},
            {"rule_id": "footprint3in", "status": "observed", "observed_share": share},
        ],
    }


def _decisive(status: str, outcome: str) -> bool:
    return status == "observed" and outcome in ("pass", "fail")


def test_facade_ground_claims_are_not_decisive():
    scene = _facade_scene()
    assert ground_incidence_untrusted(scene)
    decisive = 0
    for _name, outcome, out_m, best, share in _FACADE_CLAIMS:
        ev = withhold_untrusted_ground(scene, _evidence(best, share, out_m), "ground", _evidence(best, share, out_m)["region"])
        assert ev["status"] != "observed"
        assert ev["sensitivity"][1]["status"] != "observed"
        assert ev["best_incidence_deg"] == best
        decisive += int(_decisive(ev["status"], outcome))
    assert decisive == 0


def test_electro_keeps_an_observed_footprint():
    scene = _electro_scene()
    assert not ground_incidence_untrusted(scene)
    region = {"wall_id": "w1", "s_m": [0.0, 0.6], "out_m": [0.0, 0.61]}
    ev = {
        "region": region,
        "status": "observed",
        "observed_share": 1.0,
        "best_incidence_deg": 68.6,
        "sensitivity": [
            {"rule_id": "angle75", "status": "grazing", "observed_share": 0.857},
            {"rule_id": "angle80", "status": "observed", "observed_share": 1.0},
        ],
    }
    out = withhold_untrusted_ground(scene, ev, "ground", region)
    assert out["status"] == "observed"
    assert out["sensitivity"][1]["status"] == "observed"
    assert _decisive(out["status"], "pass")


def test_reanchor_trusts_the_plane_again():
    scene = _facade_scene()
    scene["provenance"]["notes"].append("ground_reanchored")
    assert not ground_incidence_untrusted(scene)


def test_full_wall_face_stays_observed_when_the_datum_is_bad():
    scene = _facade_scene()
    region = {"wall_id": "w1", "s_m": [-1.0, 1.0], "height_m": [0.05, 2.13]}
    ev = {"status": "observed", "observed_share": 1.0, "region": region}
    assert withhold_untrusted_ground(scene, ev, "wall", region)["status"] == "observed"


def test_wall_foot_band_is_not_observed():
    scene = _facade_scene()
    region = {"wall_id": "w1", "s_m": [-1.0, 1.0], "height_m": [0.0, 0.3]}
    ev = {"status": "observed", "observed_share": 1.0, "region": region}
    assert withhold_untrusted_ground(scene, ev, "wall", region)["status"] == "partial"


def test_level_plane_with_high_cameras_is_untrusted():
    scene = _scene(["ground_base_unobserved"], [0.0, 1.0, 0.0], 4.82)
    assert ground_incidence_untrusted(scene)


def _wall(wall_id: str, x0: float, x1: float) -> dict:
    return {
        "id": wall_id,
        "baseline_m": [[x0, 0.0], [x1, 0.0]],
        "height_m": 2.5,
        "s_range_m": [x0, x1],
        "plus_minus_m": 0.3,
        "evidence": "observed",
    }


def _sloped_scene(camera_y: float, far_first: bool) -> dict:
    """Slight tilt (n_y > 0.99) and a decoy wall at x=-30. Camera height is per camera."""
    normal = np.array([0.1, 1.0, 0.0])
    normal /= np.linalg.norm(normal)
    far = _wall("w_far", -32.0, -28.0)
    near = _wall("w1", -2.0, 2.0)
    order = ["w_far", "w1"] if far_first else ["w1", "w_far"]
    walls = [far, near] if far_first else [near, far]
    scene = _scene(
        ["ground_plane_rejected:4.75", "ground_base_unobserved"],
        [float(v) for v in normal],
        camera_y,
    )
    scene["walls"] = walls
    scene["chain"] = {"wall_ids": order}
    scene["keyframes"] = [
        {"id": f"k{i}", "pose": _pose_at(x, camera_y, 3.0)} for i, x in enumerate((-2.0, 0.0, 2.0))
    ]
    return scene


def test_wall_midpoint_can_look_plausible_while_the_cameras_are_not():
    """A far wall on a permitted tilt sits ~1.55 m under cameras that are really ~4.55 m up."""
    high = _sloped_scene(4.55, far_first=True)
    assert float(high["ground"]["surface"]["normal"][1]) > 0.99
    gy_far = float(ground_y(high, np.array([[-30.0, 0.0]]))[0])
    assert 0.9 <= 4.55 - gy_far <= 2.5
    assert ground_incidence_untrusted(high)
    assert ground_incidence_untrusted(_sloped_scene(4.55, far_first=False))
    region = {"wall_id": "w1", "s_m": [-1.0, 1.0], "out_m": [0.0, 0.6]}
    ev = {"status": "observed", "observed_share": 1.0, "region": region, "sensitivity": [{"status": "observed"}]}
    assert withhold_untrusted_ground(high, ev, "ground", region)["status"] == "partial"
    standing = _sloped_scene(1.5, far_first=True)
    assert not ground_incidence_untrusted(standing)
    assert not ground_incidence_untrusted(_sloped_scene(1.5, far_first=False))
    assert withhold_untrusted_ground(standing, ev, "ground", region)["status"] == "observed"


def _cameras(scene: dict) -> CoverageQuery:
    cams = [
        Camera(k["id"], mat4_from_list(k["pose"]), np.asarray(k["intrinsics"], float), k["w"], k["h"])
        for k in scene["keyframes"]
    ]
    return CoverageQuery(cams, np.array([[0.0, -50.0, 0.0]]))


def test_query_region_caps_a_real_observed_grade(tmp_path: Path):
    write_flat_wall(tmp_path)
    scene_path = tmp_path / "scene.json"
    scene = json.loads(scene_path.read_text())
    region = {"wall_id": "w1", "s_m": [-0.3, 0.3], "out_m": [0.2, 0.8]}
    query = _cameras(scene)
    plain = query_region(scene, scene_path.parent, region, query)
    assert plain["status"] == "observed"

    flagged = copy.deepcopy(scene)
    flagged["provenance"]["notes"] = ["ground_plane_rejected:4.75", "ground_base_unobserved"]
    flagged["ground"]["surface"]["normal"] = [0.158189, 0.979744, -0.122788]
    for kf in flagged["keyframes"]:
        pose = mat4_from_list(kf["pose"])
        pose[1, 3] = 4.82
        kf["pose"] = [float(v) for v in pose.flatten(order="F")]
    capped = query_region(flagged, scene_path.parent, region, _cameras(flagged))
    assert capped["status"] != "observed"
    assert all(row["status"] != "observed" for row in capped["sensitivity"])

    electro = copy.deepcopy(scene)
    electro["provenance"]["notes"] = ["ground_plane_rejected:3.24", "ground_base_unobserved"]
    kept = query_region(electro, scene_path.parent, region, query)
    assert kept["status"] == "observed"


def _extra_camera(scene: dict, x: float, y: float, z: float, pitch: float) -> dict:
    out = copy.deepcopy(scene)
    sample = out["keyframes"][0]
    out["keyframes"].append(
        {
            "id": "extra",
            "pose": flat_pose(x, y, z, pitch),
            "intrinsics": list(sample["intrinsics"]),
            "w": sample["w"],
            "h": sample["h"],
        }
    )
    return out


def test_a_seeing_camera_outside_the_band_is_not_observed(tmp_path: Path):
    """Median height can stay plausible while one camera that sees the strip is not."""
    write_flat_wall(tmp_path)
    scene = json.loads((tmp_path / "scene.json").read_text())
    scene["provenance"]["notes"] = ["ground_base_unobserved"]
    region = {"wall_id": "w1", "s_m": [-0.3, 0.3], "out_m": [0.2, 0.8]}
    high = _extra_camera(scene, 0.0, 4.8, 3.2, 55.0)
    assert not ground_incidence_untrusted(high)
    capped = query_region(high, tmp_path, region, _cameras(high))
    assert capped["status"] != "observed"
    assert all(row["status"] != "observed" for row in capped["sensitivity"])

    missed = _extra_camera(scene, 0.0, 4.8, -12.0, 0.0)
    assert not ground_incidence_untrusted(missed)
    kept = query_region(missed, tmp_path, region, _cameras(missed))
    assert kept["status"] == "observed"
