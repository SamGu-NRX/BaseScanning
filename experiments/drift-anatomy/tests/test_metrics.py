"""Strict checks of metrics.py on synthetic trajectories with known answers."""

import numpy as np
import pytest

from metrics import (
    along_across,
    at,
    chord_deviation,
    find_loops,
    local_scale,
    loop_errors,
    path_length,
    path_windows,
    range_correct,
    split_by_length,
    turn,
    yaw_offset,
)


def rot_y(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def square_lap(side_m: float = 15.0, speed: float = 1.0, hz: float = 1.0) -> np.ndarray:
    """A square walked counter-clockwise from the origin back to it, y up, samples at `hz`."""
    corners = np.array([[0, 0], [side_m, 0], [side_m, side_m], [0, side_m], [0, 0]], float)
    s = np.arange(0, 4 * side_m + 1e-9, speed / hz)
    seg = np.minimum((s // side_m).astype(int), 3)
    f = (s - seg * side_m) / side_m
    xz = corners[seg] * (1 - f[:, None]) + corners[seg + 1] * f[:, None]
    return np.c_[xz[:, 0], np.full(len(s), 1.4), xz[:, 1]]


def stand(p: np.ndarray, seconds: int) -> np.ndarray:
    return np.repeat(p[None], seconds, axis=0)


# Loop detection


def test_pause_at_the_start_and_end_counts_one_loop():
    lap = square_lap()  # 61 samples, origin at both ends
    ref = np.vstack([stand(lap[0], 10), lap[1:], stand(lap[-1], 10)])  # back at the origin at 69
    times = np.arange(len(ref), dtype=float)
    assert find_loops(times, ref) == [(0, 69)]


def test_two_laps_collapse_to_one_loop_because_every_start_revisits():
    lap = square_lap()
    ref = np.vstack([lap, lap[1:]])
    times = np.arange(len(ref), dtype=float)
    # Every sample of lap 1 has a twin 60 s later, so the starts form one cluster.
    assert find_loops(times, ref) == [(0, 60)]


def test_two_separate_returns_count_two_loops():
    lap = square_lap()
    away = lap[:31]  # out to the far corner
    ref = np.vstack([lap, away[1:], away[::-1][1:], stand(lap[0], 1)])
    times = np.arange(len(ref), dtype=float)
    # Lap 1 returns at 60 and the out-and-back from 60 returns at 120. Starts 0 to 30 revisit (the
    # out-and-back retraces lap 1's first half) and starts 60 to 74 revisit on the way back, so
    # there are two clusters with a gap of 30 s between them.
    assert find_loops(times, ref) == [(0, 60), (60, 120)]


def test_returns_under_30_s_or_over_the_distance_are_not_loops():
    lap = square_lap(side_m=5.0)  # 20 s lap
    times = np.arange(len(lap), dtype=float)
    assert find_loops(times, lap) == []
    big = square_lap()
    for miss, expected in ((0.25, 1), (0.35, 0)):
        ref = big.copy()
        ref[-1, 0] += miss
        found = find_loops(np.arange(len(ref), dtype=float), ref)
        assert len(found) == expected


def test_find_loops_rejects_unordered_times():
    with pytest.raises(ValueError, match="increase"):
        find_loops(np.array([0.0, 2.0, 1.0]), np.zeros((3, 3)))


# Heading alignment


def test_turn_matches_the_rotation_matrix_about_y():
    v = np.random.default_rng(0).normal(size=(20, 3))
    a = 0.7
    np.testing.assert_allclose(turn(v, a), v @ rot_y(a).T, atol=1e-12)


def test_yaw_offset_recovers_a_planted_heading_and_aligns_displacements():
    rng = np.random.default_rng(1)
    theta = np.radians(-137.0)
    ref = np.cumsum(rng.normal(size=(50, 3)), axis=0)
    # Arbitrary reference orientations: yaw, then a pitch and roll.
    R_ref = np.array(
        [
            rot_y(y) @ np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
            for y, p in zip(rng.uniform(-3, 3, 50), rng.uniform(-0.3, 0.3, 50), strict=True)
        ]
    )
    # ARKit's world is the reference's turned by -theta about y and shifted.
    ark = ref @ rot_y(-theta).T + np.array([4.0, -2.0, 9.0])
    R_ark = rot_y(-theta) @ R_ref
    delta = yaw_offset(R_ark, R_ref)
    np.testing.assert_allclose(np.angle(np.exp(1j * (delta - theta))), 0, atol=1e-12)
    np.testing.assert_allclose(turn(ark[7:] - ark[3], delta[3]), ref[7:] - ref[3], atol=1e-9)


def test_loop_errors_use_the_offset_at_the_loop_start_and_find_the_peak():
    lap = square_lap()
    ref = np.vstack([stand(lap[0], 5), lap])
    n = len(ref)
    delta = np.linspace(0.2, 0.9, n)  # varies, so using any sample but the start would be wrong
    ark = turn(ref, -delta[5])
    bump = np.zeros((n, 3))
    bump[30] = [0.3, 0.5, 0.4]  # horizontal size 0.5
    ark = ark + turn(bump, -delta[5])
    ark[-1] += turn(np.array([[0.03, 0.0, -0.04]]), -delta[5])[0]  # r = 0.05
    out = loop_errors(ark, ref, delta, 5, n - 1)
    assert out["r"] == pytest.approx(0.05, abs=1e-12)
    assert out["peak"] == pytest.approx(0.5, abs=1e-12)
    assert out["path_m"] == pytest.approx(60.0, abs=1e-9)
    assert out["excursion_m"] == pytest.approx(15 * np.sqrt(2), abs=1e-9)


# Local scale


def test_windows_cover_exactly_the_path_length():
    ref = square_lap(hz=3.0)
    i, j = path_windows(ref, 5.0, 1.0)
    walked = path_length(ref)
    ends = np.interp(j, np.arange(len(ref)), walked)
    np.testing.assert_allclose(ends - walked[i], 5.0, atol=1e-9)
    np.testing.assert_allclose(walked[i], np.arange(len(i)), atol=0.34)


def test_local_scale_reads_a_planted_three_percent():
    rng = np.random.default_rng(2)
    ref = np.cumsum(rng.normal(scale=0.3, size=(400, 3)) * [1, 0.1, 1], axis=0)
    ark = 1.03 * ref @ rot_y(1.1).T + 5.0
    i, j = path_windows(ref, 5.0, 1.0)
    np.testing.assert_allclose(local_scale(ark, ref, i, j), 1.03, atol=1e-12)


def test_local_scale_follows_a_scale_change_within_the_walk():
    ref = np.c_[np.arange(0, 40.01, 0.25), np.zeros(161), np.zeros(161)]
    ark = ref.copy()
    ark[81:, 0] = ark[80, 0] + 1.03 * (ref[81:, 0] - ref[80, 0])  # 3% long after 20 m
    i, j = path_windows(ref, 5.0, 1.0)
    s = local_scale(ark, ref, i, j)
    start, end = ref[i, 0], at(ref, j)[:, 0]
    np.testing.assert_allclose(s[end <= 20.0], 1.0, atol=1e-12)
    np.testing.assert_allclose(s[start >= 20.0], 1.03, atol=1e-12)


def test_split_by_length_recovers_both_parts():
    persistent, c = 0.004, 0.05
    sd5 = np.hypot(persistent, c / 5)
    sd10 = np.hypot(persistent, c / 10)
    falling5, kept = split_by_length(sd5, sd10, 5.0, 10.0)
    assert falling5 == pytest.approx(c / 5, rel=1e-9)
    assert kept == pytest.approx(persistent, rel=1e-9)


# Along and across travel


def test_along_across_split_a_known_error():
    travel = np.array([[3.0, 0.2, 4.0]])  # horizontal direction (0.6, 0.8)
    err = np.array([[0.6 * 2 - 0.8 * 5, 7.0, 0.8 * 2 + 0.6 * 5]])  # 2 along, 5 across
    along, across = along_across(err, travel)
    assert along[0] == pytest.approx(2.0, abs=1e-12)
    assert abs(across[0]) == pytest.approx(5.0, abs=1e-12)


def test_along_across_is_pythagorean_and_turn_invariant():
    rng = np.random.default_rng(3)
    err, travel = rng.normal(size=(100, 3)), rng.normal(size=(100, 3))
    along, across = along_across(err, travel)
    np.testing.assert_allclose(along**2 + across**2, (err[:, [0, 2]] ** 2).sum(1), rtol=1e-12)
    a2, c2 = along_across(turn(err, 0.4), turn(travel, 0.4))
    np.testing.assert_allclose(a2, along, atol=1e-12)
    np.testing.assert_allclose(c2, across, atol=1e-12)


def test_chord_deviation_is_zero_on_a_straight_walk_and_the_corner_offset_on_a_turn():
    lap = square_lap(side_m=15.0)  # 1 m samples: corners at 15, 30, 45
    i, j = np.array([0, 10]), np.array([12.5, 20.0])
    dev = chord_deviation(lap, i, j)
    assert dev[0] == pytest.approx(0.0, abs=1e-12)  # 12.5 m along the first side
    # From (10, 0) round the corner to (15, 5): the corner is 5 / sqrt(2) off the chord.
    assert dev[1] == pytest.approx(5 / np.sqrt(2), abs=1e-12)


def test_chord_deviation_refuses_a_window_with_no_travel():
    p = np.c_[np.zeros(5), np.ones(5), np.zeros(5)]
    with pytest.raises(ValueError, match="no travel line"):
        chord_deviation(p, np.array([0]), np.array([3.0]))


# Range to the meter


def test_range_correction_is_exact_without_noise_when_the_direction_is_right():
    rng = np.random.default_rng(4)
    truth = rng.normal(scale=5.0, size=(200, 3))
    est = truth.copy()
    est[:, [0, 2]] *= rng.uniform(0.8, 1.2, size=(200, 1))  # scale error only
    fixed = range_correct(est, np.linalg.norm(truth, axis=1))
    np.testing.assert_allclose(fixed, truth, atol=1e-9)


def test_range_correction_keeps_height_and_direction_and_meets_the_range():
    rng = np.random.default_rng(5)
    est = rng.normal(scale=5.0, size=(200, 3))
    ranges = rng.uniform(np.abs(est[:, 1]) + 0.1, 20.0)
    fixed = range_correct(est, ranges)
    np.testing.assert_allclose(np.linalg.norm(fixed, axis=1), ranges, rtol=1e-12)
    np.testing.assert_allclose(fixed[:, 1], est[:, 1])
    cross = fixed[:, 0] * est[:, 2] - fixed[:, 2] * est[:, 0]
    np.testing.assert_allclose(cross, 0, atol=1e-9)
    assert np.all(fixed[:, 0] * est[:, 0] + fixed[:, 2] * est[:, 2] > 0)


def test_range_shorter_than_the_height_lands_on_the_vertical():
    fixed = range_correct(np.array([[1.0, 2.0, 1.0]]), np.array([1.5]))
    np.testing.assert_allclose(fixed, [[0.0, 2.0, 0.0]])
