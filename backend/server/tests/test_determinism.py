import numpy as np

from server.dense.run import _canonical_point_order
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


def test_dense_point_order_is_canonical():
    points = np.array([[2.0, 0.0, 1.0], [1.0, 0.0, 3.0], [1.0, 0.0, 2.0]])
    src = np.array([1, 0, 1])
    tracks = [np.array([2]), np.array([1]), np.array([0])]
    permutation = np.array([2, 0, 1])

    first_order = _canonical_point_order(points, src, tracks)
    second_order = _canonical_point_order(points[permutation], src[permutation], [tracks[i] for i in permutation])

    assert np.array_equal(points[first_order], points[permutation][second_order])
    assert np.array_equal(src[first_order], src[permutation][second_order])


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
