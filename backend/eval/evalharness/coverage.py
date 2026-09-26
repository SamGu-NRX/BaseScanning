"""Observed-coverage grids (unknown / observed free / observed occupied) and their band summary.

A voxel is observed free when a camera ray passed through it on the way to a surface the camera
saw, and observed occupied when the ray ended in it. Everything else is unknown. The grid is
built per camera so `views` counts distinct cameras. Producers call `build_grid` with their own
rays (to triangulated points, depth-map pixels, LiDAR returns); the ground-truth builder calls it
with rays to the laser surface.
"""

from __future__ import annotations

import numpy as np

from evalharness.chain import Chain, ground_y
from evalharness.sceneio import CoverageGrid

WALL_PROBE_HEIGHTS = (0.15, 0.5, 1.0)  # m, up to the battery's height (39.5 in)
HEADROOM_M = 2.13  # 7 ft, the strictest headroom in the three profiles
OVERHEAD_OUT_M = 0.91  # 3 ft out from the wall
GROUND_MAX_OUT_M = 8.0
FACING_PROBE_Y = 0.91  # 3 ft
FACING_MAX_OUT_M = 18.3


def build_grid(ray_sets, lo: np.ndarray, hi: np.ndarray, voxel: float, occupied_ok=None,
               max_samples: int = 12_000_000) -> CoverageGrid:
    """ray_sets: iterable of (origin (3,), ends (M, 3), end_is_surface (M,) bool or None), one per
    camera. Free space is marked up to 0.75 voxel before each end; the end voxel is marked occupied
    when end_is_surface is true for that ray."""
    lo = np.asarray(lo, float)
    shape = tuple(int(v) for v in np.ceil((np.asarray(hi, float) - lo) / voxel))
    n = int(np.prod(shape))
    free = np.zeros(n, bool)
    occ = np.zeros(n, bool)
    views = np.zeros(n, np.uint8)
    step = voxel * 0.5
    dims = np.array(shape)

    def lin(P):
        ijk = np.floor((P - lo) / voxel).astype(np.int64)
        ok = np.all((ijk >= 0) & (ijk < dims), axis=1)
        ijk = ijk[ok]
        return (ijk[:, 0] * shape[1] + ijk[:, 1]) * shape[2] + ijk[:, 2]

    for origin, ends, surf in ray_sets:
        o = np.asarray(origin, float)
        ends = np.asarray(ends, float)
        if len(ends) == 0:
            continue
        seen_here = []
        L = np.linalg.norm(ends - o, axis=1)
        order = np.argsort(L)
        ends, L = ends[order], L[order]
        surf = None if surf is None else np.asarray(surf, bool)[order]
        dirs = (ends - o) / np.maximum(L, 1e-9)[:, None]
        a = 0
        while a < len(ends):
            nmax = int(np.ceil(L[min(len(L) - 1, a)] / step)) + 1
            b = min(len(ends), a + max(1, max_samples // max(nmax, 1)))
            nmax = int(np.ceil(L[b - 1] / step)) + 1
            t = (np.arange(nmax) * step)[None, :]
            keep = t < (L[a:b, None] - 0.75 * voxel)
            P = o + dirs[a:b, None, :] * t[..., None]
            idx = lin(P[keep])
            free[idx] = True
            seen_here.append(idx)
            a = b
        end_idx = lin(ends if surf is None else ends[surf])
        occ[end_idx] = True
        seen_here.append(end_idx)
        u = np.unique(np.concatenate(seen_here))
        views[u] = np.minimum(views[u].astype(np.int32) + 1, 255).astype(np.uint8)
    state = np.zeros(n, np.uint8)
    state[free] = CoverageGrid.FREE
    state[occ] = CoverageGrid.OCCUPIED
    return CoverageGrid(state.reshape(shape), views.reshape(shape), lo, voxel)


def _runs(ss, ok, step):
    out = []
    for s, k in zip(ss, ok):
        if not k:
            continue
        if out and abs(out[-1][1] - (s - step / 2)) < 1e-6:
            out[-1][1] = s + step / 2
        else:
            out.append([s - step / 2, s + step / 2])
    return out


def bands_from_grid(grid: CoverageGrid, scene: dict, step: float = 0.25) -> list[dict]:
    """PR #11-style band summary (in meters) of what the grid observed along each chain wall."""
    chain = Chain(scene)
    bands = []
    for w in chain.walls:
        ss = np.arange(w.s0 + step / 2, w.s1, step)
        if len(ss) == 0:
            continue
        q = w.plan_at(ss)
        gy = ground_y(scene, q)

        def obs(out, y):
            p = q + out * w.outward
            return grid.observed_at(np.c_[p[:, 0], gy + y, p[:, 1]])

        wall_ok = np.all([obs(0.1, y) for y in WALL_PROBE_HEIGHTS], axis=0)
        for a, b in _runs(ss, wall_ok, step):
            bands.append({"wall_id": w.id, "band": "wall", "s_m": [round(a, 3), round(b, 3)]})

        outs = np.arange(0.2, GROUND_MAX_OUT_M + 1e-9, 0.1)
        g = np.stack([obs(d, 0.05) for d in outs], 1)
        ground_out = np.where(g.all(1), GROUND_MAX_OUT_M, outs[np.argmin(g, 1)] - 0.1)
        ground_out = np.where(g[:, 0], ground_out, 0.0)
        _emit_quantized(bands, w.id, "ground", ss, ground_out, step, min_out=0.3)

        hs = np.arange(0.2, 3.0 + 1e-9, 0.1)
        cols = np.stack([np.all([obs(d, h) for d in (0.2, 0.5, OVERHEAD_OUT_M)], axis=0) for h in hs], 1)
        over_h = np.where(cols.all(1), 3.0, hs[np.argmin(cols, 1)] - 0.1)
        over_h = np.where(cols[:, 0], over_h, 0.0)
        _emit_quantized(bands, w.id, "overhead", ss, over_h, step, min_out=HEADROOM_M, cap=3.0)

        fo = np.arange(0.2, FACING_MAX_OUT_M, 0.1)
        f = np.stack([obs(d, FACING_PROBE_Y) for d in fo], 1)
        face_out = np.where(f.all(1), FACING_MAX_OUT_M, fo[np.argmin(f, 1)] - 0.1)
        face_out = np.where(f[:, 0], face_out, 0.0)
        _emit_quantized(bands, w.id, "facing", ss, face_out, step, min_out=0.3)
    return bands


def _emit_quantized(bands, wall_id, band, ss, vals, step, min_out, cap=None, q=0.5):
    """Merge stations into runs whose value quantizes the same; each run keeps its minimum."""
    cur = None
    for s, v in zip(ss, vals):
        if v < min_out:
            cur = None
            continue
        key = np.floor(v / q)
        if cur and cur["key"] == key and abs(cur["b"] - (s - step / 2)) < 1e-6:
            cur["b"] = s + step / 2
            cur["v"] = min(cur["v"], v)
        else:
            cur = {"key": key, "a": s - step / 2, "b": s + step / 2, "v": v}
            bands.append(cur)
        cur["band"], cur["wall_id"] = band, wall_id
    for i, c in enumerate(bands):
        if isinstance(c, dict) and "key" in c:
            e = {"wall_id": c["wall_id"], "band": c["band"], "s_m": [round(c["a"], 3), round(c["b"], 3)]}
            if not (cap is not None and c["v"] >= cap):
                e["out_m"] = round(float(c["v"]), 2)
            bands[i] = e
