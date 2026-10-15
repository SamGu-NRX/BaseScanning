"""Occupancy voxel grid with three states, built by casting sensor rays.

A voxel is OCCUPIED if any ray ended in it, FREE if a ray passed through it and none ended there,
and UNKNOWN otherwise. Rays come from a sensor origin (a laser scanner, or a camera centre with a
depth per pixel) to a surface point, so free space is only what a sensor actually looked through.
"""

from dataclasses import dataclass

import numpy as np
from numba import njit, prange

UNKNOWN, FREE, OCCUPIED = 0, 1, 2


@dataclass
class Grid:
    origin: np.ndarray  # world position of voxel (0, 0, 0)'s min corner
    res: float
    state: np.ndarray  # (nx, ny, nz) uint8

    @property
    def shape(self):
        return self.state.shape

    def idx(self, X: np.ndarray) -> np.ndarray:
        return np.floor((np.asarray(X) - self.origin) / self.res).astype(np.int64)

    def center(self, ijk: np.ndarray) -> np.ndarray:
        return self.origin + (np.asarray(ijk) + 0.5) * self.res

    def inside(self, ijk: np.ndarray) -> np.ndarray:
        return np.all((ijk >= 0) & (ijk < np.array(self.shape)), axis=-1)

    def save(self, path):
        np.savez_compressed(path, origin=self.origin, res=self.res, state=self.state)

    @staticmethod
    def load(path) -> "Grid":
        z = np.load(path)
        return Grid(z["origin"], float(z["res"]), z["state"])


def unique_endpoints(points: np.ndarray, res: float) -> np.ndarray:
    """One point per voxel (the voxel centre), so each ray is cast once per voxel it ends in."""
    q = np.floor(points / res).astype(np.int64)
    q = np.unique(q, axis=0)
    return (q + 0.5) * res


@njit(cache=True)
def _clip(o, d, lo, hi):
    t0, t1 = 0.0, 1.0
    for a in range(3):
        if abs(d[a]) < 1e-12:
            if o[a] < lo[a] or o[a] > hi[a]:
                return 1.0, 0.0
        else:
            ta = (lo[a] - o[a]) / d[a]
            tb = (hi[a] - o[a]) / d[a]
            if ta > tb:
                ta, tb = tb, ta
            t0 = max(t0, ta)
            t1 = min(t1, tb)
    return t0, t1


@njit(parallel=True, cache=True)
def _carve(origin_w, res, shape, sensor, pts, passes, hits, stop):
    """stop: voxels that end a ray early (a surface already seen), or a (1,1,1) dummy."""
    use_stop = stop.shape[0] > 1
    nx, ny, nz = shape
    lo = origin_w
    hi = origin_w + np.array([nx, ny, nz]) * res
    for r in prange(pts.shape[0]):
        o = sensor
        p = pts[r]
        d = p - o
        t0, t1 = _clip(o, d, lo, hi)
        if t0 > t1:
            continue
        # endpoint voxel
        ei = int(np.floor((p[0] - lo[0]) / res))
        ej = int(np.floor((p[1] - lo[1]) / res))
        ek = int(np.floor((p[2] - lo[2]) / res))
        end_inside = 0 <= ei < nx and 0 <= ej < ny and 0 <= ek < nz
        # Amanatides-Woo traversal from the clipped entry point
        s = o + d * (t0 + 1e-9)
        i = min(max(int(np.floor((s[0] - lo[0]) / res)), 0), nx - 1)
        j = min(max(int(np.floor((s[1] - lo[1]) / res)), 0), ny - 1)
        k = min(max(int(np.floor((s[2] - lo[2]) / res)), 0), nz - 1)
        length = np.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
        if length < 1e-9:
            continue
        step = np.empty(3, np.int64)
        tmax = np.empty(3)
        tdelta = np.empty(3)
        cur = (i, j, k)
        for a in range(3):
            if d[a] > 0:
                step[a] = 1
                nb = lo[a] + (cur[a] + 1) * res
                tmax[a] = (nb - o[a]) / d[a]
                tdelta[a] = res / d[a]
            elif d[a] < 0:
                step[a] = -1
                nb = lo[a] + cur[a] * res
                tmax[a] = (nb - o[a]) / d[a]
                tdelta[a] = -res / d[a]
            else:
                step[a] = 0
                tmax[a] = 1e30
                tdelta[a] = 1e30
        reached = True
        while True:
            if i == ei and j == ej and k == ek:
                break
            if use_stop and stop[i, j, k]:
                reached = False
                break
            passes[i, j, k] = 1
            if tmax[0] < tmax[1] and tmax[0] < tmax[2]:
                t = tmax[0]
                i += step[0]
                tmax[0] += tdelta[0]
            elif tmax[1] < tmax[2]:
                t = tmax[1]
                j += step[1]
                tmax[1] += tdelta[1]
            else:
                t = tmax[2]
                k += step[2]
                tmax[2] += tdelta[2]
            if t > t1 or i < 0 or j < 0 or k < 0 or i >= nx or j >= ny or k >= nz:
                break
        if end_inside and reached:
            hits[ei, ej, ek] = 1


def carve(grid_lo: np.ndarray, grid_hi: np.ndarray, res: float,
          rays: list[tuple[np.ndarray, np.ndarray]],
          extra_rays: list[tuple[np.ndarray, np.ndarray]] = ()) -> Grid:
    """rays: (sensor_origin (3,), endpoints (N, 3)) per sensor. extra_rays are cast afterwards and
    stop at the first surface the real rays found (labelled surfaces the sensor did not return,
    such as window glass), so they never carve through a real obstacle."""
    shape = tuple(np.ceil((grid_hi - grid_lo) / res).astype(int))
    passes = np.zeros(shape, np.uint8)
    hits = np.zeros(shape, np.uint8)
    dummy = np.zeros((1, 1, 1), np.uint8)
    lo = grid_lo.astype(np.float64)
    for o, P in rays:
        _carve(lo, float(res), shape, o.astype(np.float64), P.astype(np.float64), passes, hits, dummy)
    if len(extra_rays):
        stop = hits.copy()
        for o, P in extra_rays:
            _carve(lo, float(res), shape, o.astype(np.float64), P.astype(np.float64), passes, hits,
                   stop)
    state = np.full(shape, UNKNOWN, np.uint8)
    state[passes > 0] = FREE
    state[hits > 0] = OCCUPIED
    return Grid(grid_lo.astype(np.float64), float(res), state)


def depth_rays(cam, points: np.ndarray, stride: int, max_angle_deg: float | None, normals=None):
    """Rays a camera could observe given the true surface: z-buffer the scan into the image at
    1/stride resolution; each pixel with a depth gives one ray to its nearest surface point.
    With max_angle_deg, surface points seen more obliquely than that are dropped as endpoints
    (the ray still carves free space up to them only if kept, so they are dropped entirely)."""
    Xc = points @ cam.R.T + cam.t
    z = Xc[:, 2]
    m = z > 0.3
    Xc, P = Xc[m], points[m]
    u = (cam.fx * Xc[:, 0] / Xc[:, 2] + cam.cx) / stride
    v = (cam.fy * Xc[:, 1] / Xc[:, 2] + cam.cy) / stride
    W, H = cam.w // stride, cam.h // stride
    ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
    u, v, zz, P = u[ok].astype(int), v[ok].astype(int), Xc[ok, 2], P[ok]
    if normals is not None:
        normals = normals[m][ok]
    if len(P) == 0:
        return cam.center, np.zeros((0, 3))
    pix = v * W + u
    order = np.lexsort((zz, pix))
    pix_s = pix[order]
    first = np.r_[True, pix_s[1:] != pix_s[:-1]]
    sel = order[first]
    E = P[sel]
    if max_angle_deg is not None and normals is not None:
        to_cam = cam.center - E
        to_cam /= np.linalg.norm(to_cam, axis=1, keepdims=True)
        cosang = np.abs(np.sum(to_cam * normals[sel], axis=1))
        E = E[cosang >= np.cos(np.radians(max_angle_deg))]
    return cam.center, E


def coarsen(state: np.ndarray, f: int) -> np.ndarray:
    """f x f x f blocks: OCCUPIED if any voxel is, else UNKNOWN if any is, else FREE."""
    nx, ny, nz = (s // f for s in state.shape)
    b = state[: nx * f, : ny * f, : nz * f].reshape(nx, f, ny, f, nz, f)
    occ = (b == OCCUPIED).any(axis=(1, 3, 5))
    unk = (b == UNKNOWN).any(axis=(1, 3, 5))
    out = np.full((nx, ny, nz), FREE, np.uint8)
    out[unk] = UNKNOWN
    out[occ] = OCCUPIED
    return out
