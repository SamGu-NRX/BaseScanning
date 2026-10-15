"""Dense-stage pure functions."""

from pathlib import Path

import numpy as np
import pytest

from server.common.scaleio import as_pose
from server.dense.params import DenseParams
from server.dense.run import _from_predictions, run_dense
from server.dense.tracks import read_tracks, resolve_tracks, write_tracks
from server.dense.windows import build_windows, camera_forward, net_wh
from server.scene.harness import load as load_harness

# Column-major ``pose`` rows from a real facade ``cameras.json`` (DSC_0324, DSC_0344, DSC_0393).
_FACADE_CAMERAS = [
    {
        "image": "DSC_0324.jpg",
        "pose": [
            -0.999165,
            -0.019218,
            0.036066,
            0.0,
            -0.029416,
            0.950839,
            -0.308286,
            0.0,
            -0.028369,
            -0.309089,
            -0.95061,
            0.0,
            1.022616,
            1.732442,
            41.0284,
            1.0,
        ],
    },
    {
        "image": "DSC_0344.jpg",
        "pose": [
            -0.004366,
            -0.007289,
            -0.999964,
            0.0,
            0.336004,
            0.941824,
            -0.008332,
            0.0,
            0.94185,
            -0.336028,
            -0.001663,
            0.0,
            5.856385,
            1.44893,
            27.531595,
            1.0,
        ],
    },
    {
        "image": "DSC_0393.jpg",
        "pose": [
            -0.063255,
            0.009783,
            -0.997949,
            0.0,
            -0.033174,
            0.999379,
            0.011899,
            0.0,
            0.997446,
            0.033859,
            -0.062892,
            0.0,
            0.30703,
            1.767714,
            43.577231,
            1.0,
        ],
    },
]


def test_camera_forward_reads_column_major_cameras_json():
    frames = []
    for index, cam in enumerate(_FACADE_CAMERAS):
        pose = cam["pose"]
        assert np.asarray(pose, float).shape == (16,)
        forward = camera_forward(pose)
        assert forward.shape == (3,)
        assert forward == pytest.approx(-as_pose(pose)[:3, 2])
        frames.append({"epoch": "e", "timestamp": index, "pose": pose})
    # Different headings: the facing cut may drop the window, and it must not index the flat pose.
    build_windows(frames, DenseParams(window=3, stride=3))


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
