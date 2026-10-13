"""Geometry for the drift-anatomy questions. Pure numpy, no dataset access; every function here has
a strict test on a synthetic trajectory in tests/test_metrics.py.

Conventions: positions are (N, 3) in a y-up frame, meters. "Horizontal" is the (x, z) part. A
heading offset delta turns a y-up vector about +y the way `evals.modern_arkit.turn` and
`evals.geometry.rot_y` do, so ARKit displacements turned by the offset at a window's start are in the
reference's frame.
"""

from __future__ import annotations

import numpy as np

MAD_TO_SD = 1.4826  # median absolute deviation to standard deviation, for normal data


def turn(v: np.ndarray, delta: np.ndarray | float) -> np.ndarray:
    """Turn y-up vectors (N, 3) about +y by delta (radians); the same map as evals.modern_arkit.turn."""
    c, s = np.cos(delta), np.sin(delta)
    return np.c_[v[:, 0] * c + v[:, 2] * s, v[:, 1], v[:, 2] * c - v[:, 0] * s]


def yaw_offset(R_from: np.ndarray, R_to: np.ndarray) -> np.ndarray:
    """Heading angle (N,) of the rotation about +y closest to R_to @ R_from^T, radians. Turning a
    vector by it with `turn` equals applying evals.drift.yaw_between(R_from, R_to)."""
    M = R_to @ np.swapaxes(R_from, -1, -2)
    return np.arctan2(M[..., 0, 2] - M[..., 2, 0], M[..., 0, 0] + M[..., 2, 2])


def at(p: np.ndarray, j: np.ndarray) -> np.ndarray:
    """Positions at fractional sample indices, linearly interpolated (as evals.drift.at)."""
    lo = np.floor(j).astype(int)
    hi = np.minimum(lo + 1, len(p) - 1)
    f = (j - lo)[:, None]
    return p[lo] * (1 - f) + p[hi] * f


def path_length(p: np.ndarray) -> np.ndarray:
    """Cumulative horizontal distance travelled, starting at 0."""
    steps = np.linalg.norm(np.diff(p[:, [0, 2]], axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(steps)])


def horizontal_norm(v: np.ndarray) -> np.ndarray:
    return np.linalg.norm(v[..., [0, 2]], axis=-1)


# A1: return to the meter


def find_loops(
    times: np.ndarray,
    ref: np.ndarray,
    min_gap_s: float = 30.0,
    max_dist_m: float = 0.3,
    separation_s: float = 5.0,
) -> list[tuple[int, int]]:
    """Revisits (i, j): times[j] - times[i] > min_gap_s and |ref[j] - ref[i]| < max_dist_m.

    Each start i keeps its closest qualifying j. Starts less than separation_s apart form one
    cluster (so their separation_s-wide neighbourhoods overlap), and each cluster keeps only its
    closest pair. A walker who pauses at the meter, or retraces a stretch of path, therefore
    yields one loop, not one per sample. Returned in time order.
    """
    if len(times) != len(ref):
        raise ValueError(f"{len(times)} times for {len(ref)} positions")
    if np.any(np.diff(times) <= 0):
        raise ValueError("times must increase")
    candidates = []
    for i in range(len(times)):
        later = np.flatnonzero(times - times[i] > min_gap_s)
        if len(later) == 0:
            break
        d = np.linalg.norm(ref[later] - ref[i], axis=1)
        k = int(np.argmin(d))
        if d[k] < max_dist_m:
            candidates.append((float(d[k]), i, int(later[k])))
    loops: list[tuple[int, int]] = []
    cluster: list[tuple[float, int, int]] = []
    for c in candidates:  # already in time order of i
        if cluster and times[c[1]] - times[cluster[-1][1]] >= separation_s:
            loops.append(min(cluster)[1:])
            cluster = []
        cluster.append(c)
    if cluster:
        loops.append(min(cluster)[1:])
    return loops


def loop_errors(
    ark: np.ndarray, ref: np.ndarray, delta: np.ndarray, i: int, j: int
) -> dict[str, float]:
    """For a loop from sample i to sample j: the horizontal error of ARKit's displacement from i
    (turned by the heading offset at i) against the reference's, at j (the residual r) and at its
    largest over the samples strictly between (the peak). Also the reference's horizontal path
    length from i to j and its largest horizontal distance from the point at i (the excursion)."""
    if not 0 <= i < j - 1 < len(ref) - 1:
        raise ValueError(f"need a sample strictly between i={i} and j={j}")
    seg = slice(i, j + 1)
    da = turn(ark[seg] - ark[i], delta[i])
    dr = ref[seg] - ref[i]
    e = horizontal_norm(da - dr)
    return {
        "r": float(e[-1]),
        "peak": float(e[1:-1].max()),
        "path_m": float(path_length(ref[seg])[-1]),
        "excursion_m": float(horizontal_norm(dr).max()),
    }


# A2: local scale


def path_windows(ref: np.ndarray, length_m: float, every_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Windows of exactly `length_m` of horizontal reference path. Starts are the first sample at
    or past each multiple of `every_m` along the path (duplicates removed); ends are fractional
    sample indices, interpolated to the exact length. Windows that run off the end are dropped."""
    walked = path_length(ref)
    marks = np.arange(0.0, walked[-1] - length_m, every_m)
    i = np.unique(np.searchsorted(walked, marks))
    target = walked[i] + length_m
    j0 = np.searchsorted(walked, target)
    keep = j0 < len(ref)
    i, j0, target = i[keep], j0[keep], target[keep]
    frac = (target - walked[j0 - 1]) / (walked[j0] - walked[j0 - 1])
    return i, (j0 - 1) + frac


def local_scale(ark: np.ndarray, ref: np.ndarray, i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """ARKit's straight-line displacement over the reference's, per window."""
    return np.linalg.norm(at(ark, j) - ark[i], axis=1) / np.linalg.norm(at(ref, j) - ref[i], axis=1)


def robust_sd(x: np.ndarray) -> float:
    """1.4826 x the median absolute deviation from the median."""
    return float(MAD_TO_SD * np.median(np.abs(x - np.median(x))))


def split_by_length(
    sd_short: float, sd_long: float, short_m: float, long_m: float
) -> tuple[float, float]:
    """Split a scale spread measured on windows of two lengths into a part that falls as 1/length
    and a part that does not: sd(L)^2 = persistent^2 + (c / L)^2. Endpoint noise, from either
    track, falls as 1/L. Returns (the 1/L part at short_m, the persistent part), each clipped at 0
    when the two spreads are inconsistent with the model."""
    k = (long_m / short_m) ** 2
    falling_short = (sd_short**2 - sd_long**2) * k / (k - 1)
    persistent = (k * sd_long**2 - sd_short**2) / (k - 1)
    return float(np.sqrt(max(falling_short, 0.0))), float(np.sqrt(max(persistent, 0.0)))


# A3: along and across travel


def along_across(err: np.ndarray, ref_disp: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Horizontal error (N, 3) split into its signed component along the reference's horizontal
    displacement (N, 3) and the signed component across it. With u the unit travel direction and e
    the error, both as (x, z): along = u . e and across = u_z e_x - u_x e_z."""
    u = ref_disp[:, [0, 2]]
    n = np.linalg.norm(u, axis=1)
    if np.any(n == 0):
        raise ValueError("a window with no horizontal reference displacement has no direction")
    u = u / n[:, None]
    e = err[:, [0, 2]]
    along = (e * u).sum(axis=1)
    across = u[:, 1] * e[:, 0] - u[:, 0] * e[:, 1]
    return along, across


def chord_deviation(ref: np.ndarray, i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """Per window, the largest horizontal distance of the reference path from the straight line
    through its start ref[i] and its (fractional) end at(ref, j). A wall plane removes the error
    along the wall's normal, which is the across-travel axis only while the walk runs straight
    along the wall; this measures how far a window strays from that."""
    out = np.empty(len(i))
    for k, (a, b) in enumerate(zip(i, j, strict=True)):
        start = ref[a, [0, 2]]
        end = at(ref, np.array([b]))[0, [0, 2]]
        pts = np.vstack([ref[a : int(np.floor(b)) + 1, [0, 2]], end]) - start
        chord = end - start
        n = np.linalg.norm(chord)
        if n == 0:
            raise ValueError(f"window {a}..{b} ends where it starts and has no travel line")
        u = chord / n
        out[k] = np.abs(u[1] * pts[:, 0] - u[0] * pts[:, 1]).max()
    return out


# A4: a range to the meter


def range_correct(est: np.ndarray, rng_m: np.ndarray) -> np.ndarray:
    """Move each estimated displacement from the anchor (N, 3) radially in the horizontal plane,
    keeping its height, until its 3-D distance from the anchor equals the measured range. A range
    shorter than the height difference puts the point directly above or below the anchor."""
    h = horizontal_norm(est)
    if np.any(h == 0):
        raise ValueError(
            "an estimate on the anchor's vertical has no horizontal direction to move along"
        )
    target = np.sqrt(np.maximum(np.asarray(rng_m) ** 2 - est[:, 1] ** 2, 0.0))
    out = est.copy()
    out[:, [0, 2]] *= (target / h)[:, None]
    return out
