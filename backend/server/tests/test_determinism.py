import numpy as np

from server.common.tracks import read_tracks
from server.dense.params import DenseParams
from server.dense.run import run_dense
from server.eval.extract import sequential_lines


def _line_geometry(segments):
    return sorted(
        (
            round(float(segment.c[0]), 6),
            round(float(segment.c[1]), 6),
            round(float(segment.d[0]), 6),
            round(float(segment.d[1]), 6),
            round(float(segment.t0), 6),
            round(float(segment.t1), 6),
        )
        for segment in segments
    )


def test_dense_stage_keeps_observations_with_their_points(tmp_path):
    points = np.array([[2.0, 0.0, 1.0], [1.0, 0.0, 3.0], [1.0, 0.0, 2.0]])
    tracks = [np.array([20]), np.array([10]), np.array([30])]
    permutation = np.array([2, 0, 1])
    cameras = [{"pose": np.eye(4).tolist(), "w": 4, "h": 4, "intrinsics": [1, 1, 0, 0]}]
    poses_dir = tmp_path / "poses"
    poses_dir.mkdir()
    (poses_dir / "cameras.json").write_text("[]")

    run_dense(
        {},
        poses_dir,
        {"scale_factor": 1.0, "origin_m": [0, 0, 0]},
        tmp_path / "dense",
        params=DenseParams(),
        models={
            "cameras": cameras,
            "points_w": points[permutation],
            "point_src": np.ones(len(points), int),
            "point_tracks": [tracks[i] for i in permutation],
        },
    )
    written_points = np.load(tmp_path / "dense" / "cloud.npz")["points"]
    written_tracks, _ = read_tracks(tmp_path / "dense" / "tracks.npz")
    assert written_tracks is not None
    assert {tuple(point): int(track[0]) for point, track in zip(written_points, written_tracks, strict=True)} == {
        (2.0, 0.0, 1.0): 20,
        (1.0, 0.0, 3.0): 10,
        (1.0, 0.0, 2.0): 30,
    }


def test_sequential_lines_is_independent_of_point_order():
    rng = np.random.default_rng(1)
    x = np.linspace(-4.0, 4.0, 100)
    points = np.vstack(
        (
            np.column_stack((x + rng.normal(0.0, 0.01, len(x)), rng.normal(0.0, 0.04, len(x)))),
            np.column_stack((x + rng.normal(0.0, 0.01, len(x)), 1.0 + rng.normal(0.0, 0.04, len(x)))),
        )
    )
    permuted = points[rng.permutation(len(points))]

    first = sequential_lines(points, np.random.default_rng(7), min_pts=60, max_lines=2)
    second = sequential_lines(permuted, np.random.default_rng(7), min_pts=60, max_lines=2)

    assert _line_geometry(first) == _line_geometry(second)
