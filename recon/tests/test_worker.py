"""Hand-computed cases for the worker's adapters, depth helpers and cache, fusion, geometry,
coverage, scene.json output, the server call, the acceptance oracle and GLB."""

import json
import re
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from recon import capture as cap
from recon import coverage, depth, geometry, scene, server
from recon.coverage import CELL_M, GROUND_MAX_M, CellCoverage, _runs, observed, seen_by
from recon.depth import Depth, lidar, rotated_intrinsics, upright_turns
from recon.eth3d import LaserView, View, laser_sees, laser_visible, nearest_seeing
from recon.fusion import Mesh, Volume, integrate, mesh
from recon.geometry import WallFrame, WallLine
from recon.glb import read_glb, write_glb
from recon.pipeline import PLUS_MINUS_FT, WALL_SOURCE


def test_outward_is_the_baseline_turned_clockwise_from_above():
    # Facing a wall along +x from +z: +x is to the right, so outward is +z.
    np.testing.assert_allclose(cap.outward_of(np.array([1.0, 0, 0])), [0, 0, 1])


def test_column_major_snaps_a_rounded_rotation_and_refuses_a_non_rotation():
    c, s = np.cos(0.3), np.sin(0.3)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    T = np.eye(4)
    T[:3, :3] = np.round(R, 4)
    T[:3, 3] = [1, 2, 3]
    out = cap.column_major(T.T.reshape(-1).tolist())
    np.testing.assert_allclose(out[:3, :3] @ out[:3, :3].T, np.eye(3), atol=1e-12)
    np.testing.assert_allclose(out[:3, 3], [1, 2, 3])
    with pytest.raises(ValueError):
        cap.column_major(np.diag([1.0, 1.0, -1.0, 1.0]).reshape(-1).tolist())  # a mirror


def test_scan_bundle_reads_feet_into_meters(tmp_path: Path):
    pose = np.eye(4)
    pose[:3, 3] = [10.0, 5.0, 0.0]  # feet
    scene = {
        "schema_version": "1.0",
        "meter": {"pos": [0.0, 5.0, 0.0], "wall_id": "w"},
        "walls": [{"id": "w", "baseline": [[-10.0, 0.0], [10.0, 0.0]]}],
        "keyframes": [
            {
                "id": "k1",
                "img": "k1.jpg",
                "w": 4,
                "h": 3,
                "intrinsics": [2, 2, 2, 1.5],
                "pose": pose.T.reshape(-1).tolist(),
            }
        ],
    }
    (tmp_path / "scene.json").write_text(json.dumps(scene))
    c = cap.load(tmp_path, tmp_path / "work")
    assert c.source == "scan-bundle" and c.ground_y == 0.0
    np.testing.assert_allclose(c.frames[0].center, [3.048, 1.524, 0.0])
    np.testing.assert_allclose(c.meter, [0.0, 1.524, 0.0])
    np.testing.assert_allclose(c.wall.along, [1, 0, 0])
    np.testing.assert_allclose(c.wall.outward, [0, 0, 1])


def test_rotated_intrinsics_follow_np_rot90():
    # A 4 x 2 image, one turn counter-clockwise: pixel (u, v) goes to (v, w - u).
    k = np.array([10.0, 20.0, 1.0, 0.5])
    np.testing.assert_allclose(rotated_intrinsics(k, 4, 2, 1), [20, 10, 0.5, 3.0])
    np.testing.assert_allclose(rotated_intrinsics(k, 4, 2, 2), [10, 20, 3.0, 1.5])
    np.testing.assert_allclose(rotated_intrinsics(k, 4, 2, 3), [20, 10, 1.5, 1.0])


def test_upright_turns_for_a_phone_held_upright():
    # Portrait phone: the sensor's +x points up in the world, so one turn counter-clockwise.
    T = np.eye(4)
    T[:3, :3] = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])  # camera +x -> world +y
    assert upright_turns(T) == 1
    assert upright_turns(np.eye(4)) == 0


def test_lidar_reads_float_meters_and_drops_low_confidence(tmp_path: Path):
    cv2.imwrite(str(tmp_path / "k.jpg"), np.zeros((6, 8, 3), np.uint8))
    np.array([[1.0, 2.0], [3.0, 0.0]], "<f4").tofile(tmp_path / "k.depth.f32")
    np.array([[2, 0], [1, 2]], np.uint8).tofile(tmp_path / "k.conf.u8")
    f = cap.Frame(
        "k",
        tmp_path / "k.jpg",
        8,
        6,
        np.array([8.0, 8, 4, 3]),
        np.eye(4),
        cap.LidarDepth(tmp_path / "k.depth.f32", tmp_path / "k.conf.u8", 2, 2),
    )
    d = lidar(f)
    assert d.depth[0, 0] == 1.0 and d.depth[1, 0] == 3.0
    assert np.isnan(d.depth[0, 1]) and np.isnan(d.depth[1, 1])  # low confidence; zero depth
    np.testing.assert_allclose(d.intrinsics, [2, 2, 1, 0.75])


def test_fused_wall_is_where_the_depth_says_and_faces_the_camera():
    # A camera at the origin looking along -z at a flat wall 2 m away.
    w, h = 64, 48
    depth = Depth(
        np.full((h, w), 2.0, np.float32),
        np.array([40.0, 40, w / 2, h / 2]),
        np.full((h, w, 3), 128, np.uint8),
        "lidar",
    )
    vol = integrate({"a": depth}, {"a": np.eye(4)}, voxel=0.05)
    m = mesh(vol)
    assert abs(np.median(m.vertices[:, 2]) + 2.0) < 0.05
    assert np.median(m.normals[:, 2]) > 0.9  # toward the camera (+z)
    v = m.vertices[m.faces.astype(int)]
    winding = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])[:, 2]
    assert np.median(winding) > 0  # counter-clockwise seen from the camera
    assert vol.occupied(np.array([[0.0, 0, -2.1]]))[0]
    assert (
        not vol.occupied(np.array([[0.0, 0, -1.0]]))[0]
        and vol.observed(np.array([[0.0, 0, -1.0]]))[0]
    )
    assert not vol.observed(np.array([[0.0, 0, -2.5]]))[0]  # behind the wall: never seen


def test_observed_entries_in_feet():
    cells = np.arange(4) * CELL_M
    cov = CellCoverage(
        cells=cells,
        wall=np.array([True, True, False, True]),
        ground_out=np.array([1.3, 1.3, 0.0, 0.0]),  # 4.27 ft out for the first two cells
        facing_gap=np.array([np.nan, 1.0, 1.0, np.nan]),
        facing_clear=np.array([2.0, 0.0, 0.0, 0.5]),
        overhead_clearance=np.full(4, np.nan),
        overhead_clear=np.array([2.5, 2.5, 0.0, 0.0]),
    )
    e = observed(cov)
    assert {"band": "wall", "span_ft": [0.0, 1.0]} in e
    assert {"band": "wall", "span_ft": [1.5, 2.0]} in e
    assert {"band": "ground", "span_ft": [0.0, 1.0], "out_ft": 4.0} in e
    assert not any(x["band"] == "ground" and x["out_ft"] == 6.0 for x in e)
    assert {"band": "facing", "span_ft": [0.5, 1.5]} in e  # something measured in front
    assert {"band": "facing", "span_ft": [0.0, 0.5], "out_ft": 6.5} in e  # 2.0 m seen clear
    assert {"band": "overhead", "span_ft": [0.0, 1.0], "out_ft": 8.0} in e


def test_runs():
    assert _runs(np.array([True, True, False, True])) == [(0, 2), (3, 4)]
    assert _runs(np.array([False])) == []


def test_glb_round_trip(tmp_path: Path):
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    write_glb(
        tmp_path / "m.glb",
        v,
        np.array([[0, 1, 2]]),
        np.array([[255, 0, 0]] * 3),
        np.tile([0, 0, 1.0], (3, 1)),
    )
    doc, blob = read_glb(tmp_path / "m.glb")
    prim = doc["meshes"][0]["primitives"][0]
    assert set(prim["attributes"]) == {"POSITION", "COLOR_0", "NORMAL"}
    pos = doc["accessors"][prim["attributes"]["POSITION"]]
    assert pos["count"] == 3 and pos["max"] == [1.0, 1.0, 0.0]
    view = doc["bufferViews"][pos["bufferView"]]
    got = np.frombuffer(blob, np.float32, 9, view["byteOffset"]).reshape(3, 3)
    np.testing.assert_allclose(got, v)


# --- Coverage: what counts as seen ------------------------------------------------------------

W, H, K = 64, 48, np.array([40.0, 40, 32, 24])


def _frames_facing(ids_x: dict[str, float], height: float, rot: np.ndarray) -> list[cap.Frame]:
    frames = []
    for fid, x in ids_x.items():
        T = np.eye(4)
        T[:3, :3] = rot
        T[:3, 3] = [x, height, 0.0]
        frames.append(cap.Frame(fid, Path(f"{fid}.jpg"), W, H, K, T))
    return frames


def _flat_depths(frames: list[cap.Frame], z: float) -> dict[str, Depth]:
    """Every pixel at camera depth z: a plane square to each camera's axis."""
    return {
        f.id: Depth(np.full((H, W), z, np.float32), K, np.zeros((H, W, 3), np.uint8), "lidar")
        for f in frames
    }


def _seen(frames, depths, points, normal) -> np.ndarray:
    poses = {f.id: f.cam_to_world for f in frames}
    vol = integrate(depths, poses, voxel=0.05)
    return seen_by(points, normal, frames, depths, vol)


# Two cameras 0.4 m apart at 1 m height, looking along -z at the plane z = -2 (outward +z).
WALL_FRAMES = _frames_facing({"a": -0.2, "b": 0.2}, 1.0, np.eye(3))
WALL_POINTS = np.array([[x, y, -2.0] for x in (-0.2, 0.0, 0.2) for y in (0.6, 1.0, 1.4)])


def test_a_wall_where_the_depth_puts_a_surface_is_seen():
    saw = _seen(WALL_FRAMES, _flat_depths(WALL_FRAMES, 2.0), WALL_POINTS, np.array([0, 0, 1.0]))
    assert saw.all()


def test_an_opening_with_a_surface_behind_it_is_not_wall_seen():
    # No surface at the wall line; the depth reaches a surface 1 m behind it. Nothing occludes the
    # samples, but a clear view through an opening is no evidence of wall there.
    saw = _seen(WALL_FRAMES, _flat_depths(WALL_FRAMES, 3.0), WALL_POINTS, np.array([0, 0, 1.0]))
    assert not saw.any()


def test_a_wall_hidden_by_something_nearer_is_not_seen():
    saw = _seen(WALL_FRAMES, _flat_depths(WALL_FRAMES, 1.5), WALL_POINTS, np.array([0, 0, 1.0]))
    assert not saw.any()


# Two cameras 0.4 m apart, 1.5 m up, looking straight down (camera -z is world -y).
DOWN = np.array([[1.0, 0, 0], [0, 0, 1], [0, -1, 0]])
GROUND_FRAMES = _frames_facing({"a": -0.2, "b": 0.2}, 1.5, DOWN)
GROUND_POINTS = np.array([[x, 0.0, z] for x in (-0.2, 0.0, 0.2) for z in (-0.3, 0.0, 0.3)])


def test_ground_needs_a_surface_at_the_ground_too():
    up = coverage.UP
    assert _seen(GROUND_FRAMES, _flat_depths(GROUND_FRAMES, 1.5), GROUND_POINTS, up).all()
    # A pit (a window well) 1 m deep: seen into, but there is no ground at the sample.
    assert not _seen(GROUND_FRAMES, _flat_depths(GROUND_FRAMES, 2.5), GROUND_POINTS, up).any()


def _all_visible_setup():
    """A wall along +x through the origin facing +z, two cells, no reconstructed face."""
    frames = _frames_facing({"a": -0.2, "b": 0.2}, 1.0, np.eye(3))
    wall = WallFrame(
        np.array([0.0, 1.0, 0.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), 0.0, (0, 0.3)
    )
    empty = Mesh(
        np.zeros((0, 3), np.float32),
        np.zeros((0, 3), np.uint32),
        np.zeros((0, 3), np.float32),
        np.zeros((0, 3), np.uint8),
    )
    return wall, frames, {}, None, empty, np.array([0.0, CELL_M])


def test_ground_seen_everywhere_reaches_ten_feet(monkeypatch):
    monkeypatch.setattr(coverage, "seen_by", lambda points, *_: np.ones((len(points), 2), bool))
    wall_ok, ground_out, _ = coverage.wall_and_ground(*_all_visible_setup())
    assert wall_ok.all()
    np.testing.assert_array_equal(ground_out, [GROUND_MAX_M, GROUND_MAX_M])
    nan = np.full(2, np.nan)
    cells = np.array([0.0, CELL_M])
    cov = CellCoverage(cells, wall_ok, ground_out, nan, np.zeros(2), nan, np.zeros(2))
    assert {"band": "ground", "span_ft": [0.0, 1.0], "out_ft": 10.0} in observed(cov)


def test_ground_unseen_at_ten_feet_reports_the_last_sampled_distance(monkeypatch):
    # Seen everywhere short of 3 m out (out is world z here): 10 ft itself was not seen.
    monkeypatch.setattr(
        coverage, "seen_by", lambda points, *_: np.repeat((points[:, 2] < 3.0)[:, None], 2, 1)
    )
    _, ground_out, _ = coverage.wall_and_ground(*_all_visible_setup())
    assert np.all(ground_out < GROUND_MAX_M - 0.1)
    nan = np.full(2, np.nan)
    cells = np.array([0.0, CELL_M])
    cov = CellCoverage(cells, np.ones(2, bool), ground_out, nan, np.zeros(2), nan, np.zeros(2))
    levels = {e["out_ft"] for e in observed(cov) if e["band"] == "ground"}
    assert levels == {2.0, 4.0, 6.0, 8.0}


# --- The acceptance oracle ------------------------------------------------------------------


def test_no_laser_return_is_unseen_not_seen():
    z = np.array([2.0, 2.0, 2.0, 2.0])
    nearest = np.array([np.nan, 2.0, 1.5, 3.0])  # no return; the surface; an occluder; beyond
    np.testing.assert_array_equal(laser_sees(nearest, z), [False, True, False, True])


# --- scene.json: the wall line's provenance ----------------------------------------------------

WALL = WallFrame(
    np.array([0.0, 1.5, 0.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), 0.0, (-1.0, 1.0)
)
NO_COVERAGE = CellCoverage(
    np.array([0.0]),
    np.array([False]),
    np.zeros(1),
    np.full(1, np.nan),
    np.zeros(1),
    np.full(1, np.nan),
    np.zeros(1),
)


def _new_scene(source: str) -> dict:
    frames = _frames_facing({"a": 0.0}, 1.0, np.eye(3))
    c = cap.Capture("measure-lab", Path("s"), frames, 0.0, None, None)
    return scene.build(c, WALL, NO_COVERAGE, WALL_SOURCE[source], PLUS_MINUS_FT[source])


def test_a_new_scene_labels_its_wall_by_depth_source_and_leaves_the_error_to_the_server():
    # No plus_minus_ft: the server adds its drift per foot walked only to walls without one.
    (lidar_wall,) = _new_scene("lidar")["walls"]
    (photo_wall,) = _new_scene("moge2-triangulated")["walls"]
    assert lidar_wall["source"] == "mesh" and "plus_minus_ft" not in lidar_wall
    assert photo_wall["source"] == "plane" and "plus_minus_ft" not in photo_wall


def test_a_bundles_tap_bound_does_not_survive_the_reconstructed_line():
    prior = {
        "schema_version": "1.0",
        "meter": {"pos": [0.0, 5.0, 0.0], "wall_id": "w"},
        "walls": [
            {
                "id": "w",
                "baseline": [[-3.0, 0.0], [3.0, 0.0]],
                "source": "tap",
                "plus_minus_ft": 0.1,
                "height_ft": 9.0,
            },
            {"id": "v", "baseline": [[3.0, 0.0], [3.0, -5.0]], "plus_minus_ft": 0.2},
        ],
    }
    c = cap.Capture("scan-bundle", Path("b"), [], 0.0, np.array([0.0, 1.524, 0]), None, prior)
    doc = scene.build(c, WALL, NO_COVERAGE, WALL_SOURCE["lidar"], PLUS_MINUS_FT["lidar"])
    w, v = doc["walls"]
    assert w["source"] == "mesh" and "plus_minus_ft" not in w and w["height_ft"] == 9.0
    assert v == prior["walls"][1]  # another wall's tap stays as the phone marked it
    assert prior["walls"][0]["plus_minus_ft"] == 0.1  # the input scene is not modified


# --- The MoGe-2 depth cache -----------------------------------------------------------------


def _capture_named_scan(parent: Path, pixel: int) -> cap.Capture:
    root = parent / "scan"
    root.mkdir(parents=True)
    cv2.imwrite(str(root / "k.png"), np.full((6, 8, 3), pixel, np.uint8))
    frame = cap.Frame("k", root / "k.png", 8, 6, np.array([8.0, 8, 4, 3]), np.eye(4))
    return cap.Capture("scan-bundle", root, [frame], 0.0, None, None)


def _fake_moge(calls: list):
    """Stands in for the MoGe-2 process: depth = the image's mean pixel value."""

    def run(cmd, **_):
        items = json.loads(Path(cmd[-1]).read_text())
        calls.append(len(items))
        for item in items:
            img = cv2.imread(item["image"])
            np.savez(item["out"], depth=np.full(img.shape[:2], img.mean(), np.float32))

    return run


def test_same_named_captures_with_different_pixels_do_not_share_depth(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(depth.subprocess, "run", _fake_moge(calls))
    a = _capture_named_scan(tmp_path / "a", 10)
    b = _capture_named_scan(tmp_path / "b", 200)
    assert a.root.name == b.root.name == "scan"
    work = tmp_path / "work"
    da, db = depth.moge(a, work)["k"].depth, depth.moge(b, work)["k"].depth
    assert calls == [1, 1] and np.allclose(da, 10) and np.allclose(db, 200)
    depth.moge(a, work)
    assert calls == [1, 1]  # the same photos again: reused


def test_a_cache_whose_stored_key_differs_is_recomputed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(depth.subprocess, "run", _fake_moge(calls))
    a = _capture_named_scan(tmp_path / "a", 10)
    folder = depth.moge_cache(a, tmp_path / "work")
    np.savez(folder / "k.moge2.npz", depth=np.full((6, 8), 99.0, np.float32))  # someone else's
    (folder / "key.json").write_text(json.dumps({"frames": []}))
    d = depth.moge(a, tmp_path / "work")["k"].depth
    assert calls == [1] and np.allclose(d, 10)


def test_the_cache_key_changes_with_pose_intrinsics_and_the_model(tmp_path, monkeypatch):
    c = _capture_named_scan(tmp_path, 10)
    (f,) = c.frames
    base = depth.moge_key(c)
    moved = f.cam_to_world.copy()
    moved[0, 3] = 0.5
    for changed in (replace(f, cam_to_world=moved), replace(f, intrinsics=f.intrinsics * 2)):
        assert depth.moge_key(replace(c, frames=[changed])) != base
    other = tmp_path / "moge_depth.py"
    other.write_text(
        depth.MOGE_SCRIPT.read_text().replace("RESOLUTION_LEVEL = 9", "RESOLUTION_LEVEL = 8")
    )
    monkeypatch.setattr(depth, "MOGE_SCRIPT", other)
    assert depth.moge_key(c) != base


# --- Depth: a LiDAR capture with frames saved without depth -----------------------------------


def _lidar_frames(root: Path, with_depth: dict[str, bool]) -> list[cap.Frame]:
    frames = []
    for fid, has in with_depth.items():
        cv2.imwrite(str(root / f"{fid}.jpg"), np.zeros((6, 8, 3), np.uint8))
        lid = None
        if has:
            np.full((2, 2), 2.0, "<f4").tofile(root / f"{fid}.f32")
            lid = cap.LidarDepth(root / f"{fid}.f32", None, 2, 2)
        frames.append(
            cap.Frame(fid, root / f"{fid}.jpg", 8, 6, np.array([8.0, 8, 4, 3]), np.eye(4), lid)
        )
    return frames


def test_a_frame_saved_without_lidar_keeps_the_other_frames_lidar(tmp_path, monkeypatch):
    monkeypatch.setattr(depth, "moge", lambda *_: pytest.fail("MoGe-2 must not run"))
    frames = _lidar_frames(tmp_path, {"a": True, "b": False, "c": True})
    c = cap.Capture("measure-lab", tmp_path, frames, 0.0, None, None)
    depths, report = depth.depth_maps(c, tmp_path / "work", "auto")
    assert sorted(depths) == ["a", "c"]  # b gets no depth, so it can see nothing in coverage
    assert report["source"] == "lidar" and report["without_depth"] == ["b"]
    assert any(n.startswith("1 of 3 keyframes carry no LiDAR depth (b)") for n in c.notes)


def test_one_lidar_frame_falls_back_to_photos_in_auto_and_refuses_in_lidar_mode(
    tmp_path, monkeypatch
):
    frames = _lidar_frames(tmp_path, {"a": True, "b": False, "c": False})
    c = cap.Capture("measure-lab", tmp_path, frames, 0.0, None, None)
    monkeypatch.setattr(depth, "moge", lambda *_: {})
    monkeypatch.setattr(depth, "rescale", lambda *_: ({}, {"fitted": 0}))
    assert depth.depth_maps(c, tmp_path / "work", "auto")[1]["source"] == "moge2-triangulated"
    with pytest.raises(ValueError, match="only 1 of 3 keyframes carry LiDAR depth"):
        depth.depth_maps(c, tmp_path / "work", "lidar")


# --- Geometry: the ground plane and the wall stretch -----------------------------------------


def _upward_mesh(points: np.ndarray) -> Mesh:
    n = len(points)
    return Mesh(
        points.astype(np.float32),
        np.zeros((0, 3), np.uint32),
        np.tile([0.0, 1.0, 0.0], (n, 1)).astype(np.float32),
        np.zeros((n, 3), np.uint8),
    )


def test_ground_height_is_the_plane_at_the_meter_not_the_median_sample():
    # Ground rising 1 in 10 away from a wall at x = 1, where it is 0.5 m; sampled only on the
    # wall's outward side, so the samples' median height (0.65 m) is 1.5 m out, not at the wall.
    x, z = np.meshgrid(np.linspace(1, 4, 31), np.linspace(-2, 2, 21))
    pts = np.stack([x.ravel(), 0.5 + 0.1 * (x.ravel() - 1), z.ravel()], axis=1)
    ground = geometry.fit_ground(_upward_mesh(pts), 0.5)
    assert np.median(pts[:, 1]) == pytest.approx(0.65)
    assert ground.height_at(1.0, 0.7) == pytest.approx(0.5, abs=1e-5)
    line = WallLine(
        np.array([1.0, 0.5, 0.0]), np.array([0, 0, -1.0]), np.array([1.0, 0, 0]), (-2, 2), 0, 0
    )
    c = cap.Capture("measure-lab", Path("s"), [], 0.5, np.array([1.1, 1.7, 0.4]), None)
    wall = geometry.wall_frame(line, c, ground, False)
    assert wall.ground_y == pytest.approx(0.5, abs=1e-5)


def _stretch(extent: tuple[float, float], z: float = 0.0) -> WallLine:
    return WallLine(
        np.array([0.0, 0.0, z]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), extent, 0.01, 100
    )


def test_the_stretch_beside_the_meter_wins_over_a_collinear_one_past_a_doorway():
    # One wall split at a doorway into two stretches on the same infinite line.
    far, near = _stretch((-6.0, -3.0)), _stretch((1.0, 4.0))
    hint = cap.WallHint(np.array([-5.0, 0, 0.1]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]))
    meter = np.array([2.0, 1.2, 0.05])
    with_meter = cap.Capture("scan-bundle", Path("b"), [], 0.0, meter, hint)
    assert geometry.choose_wall([far, near], with_meter, False) is near
    no_meter = cap.Capture("measure-lab", Path("s"), [], 0.0, None, hint)  # the phone's point
    assert geometry.choose_wall([near, far], no_meter, False) is far
    with pytest.raises(geometry.WallMismatch):  # a parallel line 1 m off is not the wall
        geometry.choose_wall([_stretch((1.0, 4.0), z=1.0)], with_meter, False)


# --- Coverage: facing space is measured from the wall's front ---------------------------------


def _slab_volume(occupied: list[tuple[float, float]], observed_to: float) -> Volume:
    """x in [-0.5, 0.5], y in [0, 3.2], z in [-0.5, 7] at 5 cm: occupied where z falls in one of
    the ranges, seen empty elsewhere up to z = observed_to, unobserved beyond."""
    voxel, origin, shape = 0.05, np.array([-0.5, 0.0, -0.5]), (21, 65, 151)
    z = origin[2] + np.arange(shape[2]) * voxel
    occ = np.zeros(shape[2], bool)
    for a, b in occupied:
        occ |= (z >= a - 1e-6) & (z <= b + 1e-6)
    tsdf = np.broadcast_to(np.where(occ, -1.0, 1.0).astype(np.float32), shape).copy()
    weight = np.broadcast_to((z <= observed_to + 1e-6).astype(np.float32), shape).copy()
    color = np.zeros((*shape, 3), np.float32)
    return Volume(origin, voxel, shape, tsdf, weight, color, np.zeros(shape, np.float32))


def test_facing_gap_and_clear_space_start_at_a_proud_wall_face():
    # The fitted plane is z = 0 (outward +z). A pilaster stands 0.3 m proud of it; something
    # faces the wall 1.0 m from the pilaster's face, 1.3 m from the plane.
    wall = WallFrame(
        np.array([0.0, 1.0, 0.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), 0.0, (0, 0.3)
    )
    cells = np.array([0.0])
    flat = coverage.free_space(wall, _slab_volume([(-0.3, 0.0), (1.0, 1.4)], 7.0), cells)
    proud = coverage.free_space(wall, _slab_volume([(-0.3, 0.3), (1.3, 1.7)], 7.0), cells)
    assert proud[0][0] == pytest.approx(flat[0][0]) == pytest.approx(0.95, abs=0.01)
    # Nothing faces it, and the volume was seen empty to 2.0 m from the plane: 1.7 m from the face.
    clear = coverage.free_space(wall, _slab_volume([(-0.3, 0.3)], 2.0), cells)
    assert np.isnan(clear[0][0]) and 1.6 <= clear[1][0] <= 1.7


def _facing_clear_seen_from(xs: dict[str, float]) -> float:
    """Facing clear distance in front of a wall at z = 0 (outward +z), its space seen empty in
    the volume to z = 7, when only cameras at x = xs, 4 m out and 1 m up, looking at it see it."""
    wall = WallFrame(
        np.array([0.0, 1.0, 0.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), 0.0, (0, 0.3)
    )
    frames = [
        cap.Frame(f.id, f.image, f.width, f.height, f.intrinsics, f.cam_to_world + _AT_Z4)
        for f in _frames_facing(xs, 1.0, np.eye(3))
    ]
    view = coverage.SeenFromTwo(_slab_volume([(-0.3, 0.0)], 7.0), frames, _flat_depths(frames, 4.0))
    _, clear, _, _ = coverage.free_space(wall, view, np.array([0.0]))
    return float(clear[0])


_AT_Z4 = np.zeros((4, 4))
_AT_Z4[2, 3] = 4.0


def test_free_space_seen_from_one_position_is_not_clearance_evidence():
    # The volume alone calls all of it observed; one camera, or two closer than 0.25 m, is not
    # the two positions wall and ground need, so no clear distance may be claimed.
    assert _facing_clear_seen_from({"a": 0.075}) == 0.0
    assert _facing_clear_seen_from({"a": 0.025, "b": 0.125}) == 0.0
    # Two positions 0.4 m apart see it clear until the facing band's foot leaves their view,
    # 2.5 m out with this lens.
    assert _facing_clear_seen_from({"a": -0.125, "b": 0.275}) == pytest.approx(2.5, abs=0.06)


# --- The server call ------------------------------------------------------------------------


def _replies(monkeypatch, *replies):
    it = iter(replies)
    monkeypatch.setattr(server, "_post", lambda *_: next(it))


def test_a_refused_site_plan_fails_the_run_and_leaves_no_plan_behind(tmp_path, monkeypatch):
    (tmp_path / "site-plan.svg").write_text("<svg>a previous run's plan</svg>")
    _replies(monkeypatch, (200, b'{"decision": "manual_review"}'), (500, b"plan failed"))
    with pytest.raises(RuntimeError, match=r"refused its site plan \(500\): plan failed"):
        server.place({}, "https://example.test", tmp_path)
    assert not (tmp_path / "site-plan.svg").exists()


def test_a_placed_scene_saves_its_result_and_plan(tmp_path, monkeypatch):
    _replies(monkeypatch, (200, b'{"decision": "manual_review"}'), (200, b"<svg/>"))
    assert server.place({}, "https://example.test", tmp_path) == {"decision": "manual_review"}
    assert (tmp_path / "site-plan.svg").read_bytes() == b"<svg/>"


# --- The acceptance oracle: which view scores a laser point -----------------------------------


def test_a_laser_point_is_scored_from_the_nearest_view_that_sees_it():
    dist = np.array([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])
    sees = np.array([[False, True, True], [False, False, False]])
    np.testing.assert_array_equal(nearest_seeing(dist, sees), [1, -1])


def test_laser_visibility_needs_the_point_in_frame_and_unoccluded():
    # A COLMAP camera at the origin looking along +z; points 2 m ahead, and one out of frame.
    view = View("v", 512, 512, np.array([256.0, 256, 256, 256]), np.eye(3), np.zeros(3))
    pts = np.array([[0.0, 0, 2.0], [10.0, 0, 2.0]])
    toward_camera = np.array([0, 0, -1.0])
    miss = np.zeros((1024, 1024), bool)

    def visible(nearest: float) -> list[bool]:
        lv = LaserView(view, np.full((512, 512), nearest, np.float32), miss)
        return laser_visible(lv, pts, pts, np.eye(3), toward_camera).tolist()

    assert visible(2.0) == [True, False]
    assert visible(1.0) == [False, False]  # the laser saw something nearer: occluded
    assert visible(np.nan) == [False, False]  # no laser return is no evidence


# --- Adapters: files a manifest names stay inside its folder ---------------------------------


def _manifest(root: Path, fmt: str, img: str, depth_file: str, conf_file: str) -> None:
    """A one-keyframe scan bundle or Measure Lab session naming the three files."""
    kf = {
        "id": "k",
        "img": img,
        "w": 8,
        "h": 6,
        "intrinsics": [8, 8, 4, 3],
        "pose": np.eye(4).reshape(-1).tolist(),
        "depth": {"file": depth_file, "confidenceFile": conf_file, "w": 2, "h": 2},
    }
    if fmt == "scan-bundle":
        doc = {
            "schema_version": "1.0",
            "meter": {"pos": [0.0, 5.0, 0.0], "wall_id": "w"},
            "walls": [{"id": "w", "baseline": [[-3.0, 0.0], [3.0, 0.0]]}],
            "keyframes": [kf],
        }
        (root / "scene.json").write_text(json.dumps(doc))
    else:
        doc = {"format": "measure-lab-session", "formatVersion": 2, "keyframes": [kf]}
        (root / "session.json").write_text(json.dumps(doc))


@pytest.fixture
def packet(tmp_path: Path) -> tuple[Path, Path]:
    """A packet folder holding valid files, and a secret beside it, outside the folder."""
    root = tmp_path / "packet"
    root.mkdir()
    (root / "k.jpg").write_bytes(b"jpg")
    (root / "k.f32").write_bytes(b"depth")
    (root / "k.u8").write_bytes(b"conf")
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"not the homeowner's")
    (root / "escape.jpg").symlink_to(secret)
    return root, secret


FORMATS = ["scan-bundle", "measure-lab"]
FIELDS = {"img": 0, "depth.file": 1, "depth.confidenceFile": 2}


@pytest.mark.parametrize("fmt", FORMATS)
def test_files_inside_the_packet_load(packet, fmt):
    root, _ = packet
    _manifest(root, fmt, "k.jpg", "k.f32", "k.u8")
    (f,) = cap.load(root, root / "work").frames
    assert f.image == root / "k.jpg"
    assert f.lidar.file == root / "k.f32" and f.lidar.confidence == root / "k.u8"


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("kind", ["absolute", "traversal", "symlink"])
def test_a_file_outside_the_packet_is_refused(packet, fmt, field, kind):
    root, secret = packet
    bad = {"absolute": str(secret), "traversal": "../secret.jpg", "symlink": "escape.jpg"}[kind]
    refs = ["k.jpg", "k.f32", "k.u8"]
    refs[FIELDS[field]] = bad
    _manifest(root, fmt, *refs)
    with pytest.raises(cap.UnsafePath, match=f"^{re.escape(field)} "):
        cap.load(root, root / "work")


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("bad", ["/tmp/x", "../../x", "a/b", "..", ".", "", 7])
def test_a_frame_id_that_is_not_one_safe_name_is_refused(packet, fmt, bad):
    root, _ = packet
    _manifest(root, fmt, "k.jpg", "k.f32", "k.u8")
    name = "scene.json" if fmt == "scan-bundle" else "session.json"
    doc = json.loads((root / name).read_text())
    doc["keyframes"][0]["id"] = bad
    (root / name).write_text(json.dumps(doc))
    with pytest.raises(cap.BadFrameId, match="keyframe id"):
        cap.load(root, root / "work")


@pytest.mark.parametrize("good", ["k00001", "DSC_9257", "frame-1.v2"])
def test_frame_ids_real_captures_use_are_accepted(good):
    assert cap.frame_id(good) == good


@pytest.mark.parametrize("bad", ["../escaped", "/tmp/escaped", "sub/escaped"])
def test_the_moge_cache_refuses_a_file_outside_its_folder(tmp_path, monkeypatch, bad):
    # A Capture built without the readers' id check: the cache still refuses to write out.
    monkeypatch.setattr(depth.subprocess, "run", lambda *_, **__: pytest.fail("model ran"))
    c = _capture_named_scan(tmp_path, 10)
    c = replace(c, frames=[replace(c.frames[0], id=bad)])
    work = tmp_path / "work"
    with pytest.raises(cap.UnsafePath, match="resolves outside"):
        depth.moge(c, work)
    assert not list(tmp_path.glob("escaped*")) and not list(work.rglob("*.upright.jpg"))


def test_the_moge_cache_file_for_a_valid_id_is_directly_in_its_folder(tmp_path):
    assert depth.cache_file(tmp_path, "k00001", ".moge2.npz") == tmp_path / "k00001.moge2.npz"


@pytest.mark.parametrize("fmt", FORMATS)
def test_two_keyframes_sharing_an_id_are_refused(packet, fmt):
    # The caretaker's case: two views 0.3 m apart. With one id their depth maps collapse to one,
    # and that one view would count from both positions as two-view coverage.
    root, _ = packet
    _manifest(root, fmt, "k.jpg", "k.f32", "k.u8")
    name = "scene.json" if fmt == "scan-bundle" else "session.json"
    doc = json.loads((root / name).read_text())
    second = json.loads(json.dumps(doc["keyframes"][0]))
    pose = np.eye(4)
    pose[0, 3] = 0.3
    second["pose"] = pose.T.reshape(-1).tolist()  # column-major, 0.3 m along x
    doc["keyframes"].append(second)
    (root / name).write_text(json.dumps(doc))
    with pytest.raises(cap.DuplicateFrameId, match="keyframe id 'k' appears more than once"):
        cap.load(root, root / "work")
    second["id"] = "k2"
    (root / name).write_text(json.dumps(doc))
    assert [f.id for f in cap.load(root, root / "work").frames] == ["k", "k2"]
