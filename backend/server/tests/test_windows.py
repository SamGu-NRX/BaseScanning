"""Dense-stage pure functions."""

from pathlib import Path

import numpy as np

from server.dense.params import DenseParams
from server.dense.run import _from_predictions, run_dense
from server.dense.tracks import read_tracks, resolve_tracks, write_tracks
from server.dense.windows import build_windows, net_wh
from server.scene.harness import load as load_harness


def test_windows_drop_opposite_facing():
    frames = []
    for i in range(6):
        pose = np.eye(4)
        pose[2, 3] = float(i)
        frames.append({"epoch": "e", "timestamp": i, "pose": pose})
    turned = np.eye(4)
    turned[:3, :3] = np.diag([1.0, 1.0, -1.0])
    frames.append({"epoch": "e", "timestamp": 6, "pose": turned})
    windows = build_windows(frames, DenseParams(window=8, stride=8))
    assert windows and 6 not in windows[0]
    assert net_wh(1440, 1920)[1] == 518


def test_window_rows_keep_flagged_when_second_opinion_is_set():
    centers = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    poses = []
    for center in centers:
        pose = np.eye(4)
        pose[:3, 3] = center
        poses.append(pose)
    points, src, rows, opinion, tracks = _from_predictions(
        {"window_predictions": [{"frames": [0, 1, 2], "pred_centers": centers, "points": centers + 0.1}]},
        poses,
        DenseParams(),
        "photo",
    )
    assert opinion == "da3"
    assert rows and all("flagged" in row and "index" in row for row in rows)
    assert len(points) == len(src) == len(tracks)
    assert all(row.tolist() == [0, 1, 2] for row in tracks)


def test_sparse_ply_is_unioned_as_measurement(tmp_path: Path):
    poses = tmp_path / "poses"
    poses.mkdir()
    sparse = np.array([[0.0, 0.0, 0.0], [1.0, 0.2, 0.0], [0.4, 1.0, 0.1]])
    load_harness()["sceneio"].write_ply(poses / "sparse.ply", sparse.astype(np.float32))
    pose = np.eye(4)
    report = run_dense(
        {},
        poses,
        {"source": "arkit", "scale_factor": 1.0, "origin_m": [0, 0, 0], "rel_bound_95": 0.09},
        tmp_path / "dense",
        params=DenseParams(),
        models={
            "cameras": [{"keyframe_id": "k0", "pose": pose.tolist(), "intrinsics": [1, 1, 0, 0], "w": 4, "h": 4}],
            "points_w": np.ones((4, 3)),
            "point_src": np.ones(4, int),
            "flow": "photo",
            "source_frame": "colmap_model",
        },
    )
    cloud = np.load(tmp_path / "dense" / "cloud.npz")
    assert int((cloud["src"] == 0).sum()) == 3
    assert report.counts["points"] == 7
    assert "tracks_missing" in report.warnings
    loaded, _ids = read_tracks(tmp_path / "dense" / "tracks.npz")
    assert loaded is not None
    assert all(row.size == 0 for row in loaded)


def test_sparse_union_keeps_sfm_tracks(tmp_path: Path):
    """Sparse points keep the cameras in ``tracks.npz``, not ``index % n_cameras``."""
    poses = tmp_path / "poses"
    poses.mkdir()
    sparse = np.array([[0.0, 0.0, 0.0], [1.0, 0.2, 0.0], [0.4, 1.0, 0.1]])
    load_harness()["sceneio"].write_ply(poses / "sparse.ply", sparse.astype(np.float32))
    write_tracks(
        poses / "tracks.npz",
        [np.array([1, 2], np.int32), np.array([0], np.int32), np.zeros(0, np.int32)],
        np.array([10, 11, 12], np.int64),
    )
    pose = np.eye(4)
    cameras = [
        {"keyframe_id": f"k{i}", "pose": pose.tolist(), "intrinsics": [1, 1, 0, 0], "w": 4, "h": 4} for i in range(3)
    ]
    report = run_dense(
        {},
        poses,
        {"source": "arkit", "scale_factor": 1.0, "origin_m": [0, 0, 0], "rel_bound_95": 0.09},
        tmp_path / "dense",
        params=DenseParams(),
        models={
            "cameras": cameras,
            "points_w": np.ones((4, 3)),
            "point_src": np.ones(4, int),
            "flow": "photo",
            "source_frame": "colmap_model",
        },
    )
    loaded, ids = read_tracks(tmp_path / "dense" / "tracks.npz")
    assert loaded is not None
    assert [row.tolist() for row in loaded[:4]] == [[], [], [], []]
    assert loaded[4].tolist() == [1, 2]
    assert loaded[5].tolist() == [0]
    assert loaded[6].tolist() == []
    assert ids[4:].tolist() == [10, 11, 12]
    resolved = resolve_tracks({}, tmp_path / "dense", 7)
    assert resolved[4].tolist() == [1, 2]
    assert resolved[6].tolist() == []
    assert "tracks_missing" not in report.warnings


def test_mismatched_tracks_claim_no_view(tmp_path: Path):
    poses = tmp_path / "poses"
    poses.mkdir()
    sparse = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    load_harness()["sceneio"].write_ply(poses / "sparse.ply", sparse.astype(np.float32))
    write_tracks(poses / "tracks.npz", [np.array([0], np.int32)], np.array([7], np.int64))
    pose = np.eye(4)
    report = run_dense(
        {},
        poses,
        {"source": "arkit", "scale_factor": 1.0, "origin_m": [0, 0, 0], "rel_bound_95": 0.09},
        tmp_path / "dense",
        params=DenseParams(),
        models={
            "cameras": [{"keyframe_id": "k0", "pose": pose.tolist(), "intrinsics": [1, 1, 0, 0], "w": 4, "h": 4}],
            "points_w": np.zeros((0, 3)),
            "point_src": np.zeros(0, int),
        },
    )
    loaded, _ids = read_tracks(tmp_path / "dense" / "tracks.npz")
    assert loaded is not None
    assert all(row.size == 0 for row in loaded)
    assert "tracks_count_mismatch" in report.warnings
