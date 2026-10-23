import numpy as np

from server.dense.run import _canonicalize_point_rows
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


def test_dense_point_order_keeps_observations_with_their_points():
    points = np.array([[2.0, 0.0, 1.0], [1.0, 0.0, 3.0], [1.0, 0.0, 2.0]])
    src = np.array([1, 0, 1])
    tracks = [np.array([20]), np.array([10]), np.array([30])]
    permutation = np.array([2, 0, 1])

    canonical_points, canonical_src, canonical_tracks = _canonicalize_point_rows(points, src, tracks)
    shuffled_tracks = [tracks[i] for i in permutation]
    shuffled_points, shuffled_src, shuffled_tracks = _canonicalize_point_rows(
        points[permutation], src[permutation], shuffled_tracks
    )

    assert np.array_equal(canonical_points, shuffled_points)
    assert np.array_equal(canonical_src, shuffled_src)
    assert [row.tolist() for row in canonical_tracks] == [row.tolist() for row in shuffled_tracks]
    assert {tuple(point): int(track[0]) for point, track in zip(canonical_points, canonical_tracks, strict=True)} == {
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
