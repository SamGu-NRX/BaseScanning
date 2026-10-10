"""The reference: analytic visibility on known geometry, the study's independent judge.

It shares nothing with the recon worker's methods: rays are exact, samples are 2x finer than
the worker's grids, and the physics gates are only what light does - a sample is visible when
some camera's ray reaches it unblocked inside the frame. No range cap (a camera sees past 5 m;
the LiDAR in the simulator does not - that mismatch shows up as honest misses, not as evidence
of lying). Two-position aggregation mirrors the worker's so an EVIDENCE_DEFICIT finding means
"the worker's own bar was not met", not "the oracle counts differently".
"""

from __future__ import annotations

import numpy as np

from .scenes import Scene
from .sim import FOCAL, FRAME_H, FRAME_W, Walk

WALL_LABELS = {"wall", "door", "pilaster", "arc"}
CELL = 0.1524  # 0.5 ft
BASELINE_MIN = 0.25  # m between two camera positions for a two-position claim
FRUSTUM_MARGIN = 0.0  # physics: no policy margins


def coverage_fraction(n_seen: int, n_samples: int) -> float:
    """Seen fraction of a sample set. An empty denominator is UNKNOWN (nan), never 1.0: an
    unobserved region must not read as completely observed."""
    if n_samples <= 0:
        return float("nan")
    return n_seen / n_samples


class Reference:
    def __init__(self, scene: Scene, walk: Walk):
        self.scene = scene
        self.walk = walk
        tris_parts, labels = [], []
        solids = scene.build_solids() + scene._opening_solids()  # noqa: SLF001 - own module
        for solid in solids:
            t = solid.tris()
            tris_parts.append(t)
            labels += [solid.label] * len(t)
        for g in scene.grounds:
            t = g.tris()
            tris_parts.append(t)
            labels += ["ground"] * len(t)
        self.tris = np.concatenate(tris_parts, axis=0)
        self.tri_labels = np.array(labels)
        self.wall_mask = np.isin(self.tri_labels, sorted(WALL_LABELS))
        centres = walk.positions
        d = np.linalg.norm(centres[:, None] - centres[None, :], axis=-1)
        self.far = d >= BASELINE_MIN
        self.occupiers = solids  # occupancy queries: things that stand in space

    # ---- ray casting -----------------------------------------------------------------

    def _moller(self, origins: np.ndarray, dirs: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        """Nearest t per ray against the (masked) triangles; inf when nothing hits."""
        tris = self.tris if mask is None else self.tris[mask]
        v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
        out = np.full(len(origins), np.inf)
        for i0 in range(0, len(origins), 4096):
            o, d = origins[i0 : i0 + 4096], dirs[i0 : i0 + 4096]
            pv = np.cross(d[:, None, :], e2[None, :, :])  # (n, m, 3)
            det = np.einsum("nmk,mk->nm", pv, e1)
            ok = np.abs(det) > 1e-12
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tv = o[:, None, :] - v0[None, :, :]
            u = np.einsum("nmk,nmk->nm", tv, pv) * inv
            qv = np.cross(tv, e1[None, :, :])
            w = np.einsum("nk,nmk->nm", d, qv) * inv
            t = np.einsum("mk,nmk->nm", e2, qv) * inv
            hit = ok & (u >= -1e-9) & (w >= -1e-9) & (u + w <= 1 + 1e-9) & (t > 1e-6)
            out[i0 : i0 + 4096] = np.where(hit, t, np.inf).min(axis=1)
        return out

    def blocked(self, targets: np.ndarray) -> np.ndarray:
        """Per target (N,3): per frame, does anything solid stand between camera and target?"""
        targets = np.atleast_2d(targets)
        out = np.zeros((len(self.walk.positions), len(targets)), dtype=bool)
        for f, (c, R) in enumerate(zip(self.walk.positions, self.walk.rotations, strict=True)):
            d = targets - c
            dist = np.linalg.norm(d, axis=-1)
            dirs = d / dist[:, None]
            t = self._moller(np.repeat(c[None], len(targets), axis=0), dirs, None)
            out[f] = t < dist - 1e-4
        return out

    # ---- visibility ------------------------------------------------------------------

    def _in_frustum(self, pos: np.ndarray, R: np.ndarray, pts: np.ndarray) -> np.ndarray:
        p = (pts - pos) @ R.T
        z = -p[:, 2]
        ok = z > 0.02
        u = FRAME_W / 2 + FOCAL * np.where(ok, p[:, 0], 0) / np.where(ok, z, 1)
        v = FRAME_H / 2 - FOCAL * np.where(ok, p[:, 1], 0) / np.where(ok, z, 1)
        return (
            ok
            & (u >= FRUSTUM_MARGIN)
            & (u <= FRAME_W - FRUSTUM_MARGIN)
            & (v >= FRUSTUM_MARGIN)
            & (v <= FRAME_H - FRUSTUM_MARGIN)
        )

    def visible(
        self, targets: np.ndarray, clearance: float = 1e-4
    ) -> tuple[np.ndarray, np.ndarray]:
        """Per target: (n_frames, two_position_flag). Two-position mirrors the worker's rule.
        `clearance` relaxes the blocking test for grazing rays (2 cm for free-space probes that
        may brush a surface, 0.1 mm for samples sitting on a face)."""
        targets = np.atleast_2d(targets)
        saw = np.zeros((len(self.walk.positions), len(targets)), dtype=bool)
        for f, (c, R) in enumerate(zip(self.walk.positions, self.walk.rotations, strict=True)):
            d = targets - c
            dist = np.linalg.norm(d, axis=-1)
            dirs = d / dist[:, None]
            hit_any = self._moller(np.repeat(c[None], len(targets), axis=0), dirs, None)
            free = ~(hit_any < dist - clearance)
            saw[f] = free & self._in_frustum(c, R, targets)
        counts = saw.sum(axis=0)
        sf = saw.T.astype(np.float32)  # (P, F)
        two = ((sf @ self.far.astype(np.float32)) * sf).sum(axis=1) > 0
        return counts, two

    # ---- sampling the truth ------------------------------------------------------------

    def wall_face(self, s: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The true wall-face point at plan (s, y): the first wall-labelled surface along -z from
        z = +0.4. NaN where nothing wall-like is there (an opening)."""
        s, y = np.broadcast_arrays(np.asarray(s, float), np.asarray(y, float))
        origin = np.stack([s.ravel(), y.ravel(), np.full(s.size, 0.4)], axis=-1)
        dirs = np.tile([0.0, 0.0, -1.0], (s.size, 1))
        t = self._moller(origin, dirs, self.wall_mask)
        pts = origin + dirs * t[:, None]
        pts = np.where(np.isfinite(t)[:, None], pts, np.nan)
        pts[(t - 0.4) < -0.3 - 1e-9] = np.nan  # a face 0.3 m deep is not this cell's wall
        return pts.reshape(s.shape + (3,)), t.reshape(s.shape)

    def ground_point(self, s: np.ndarray, out: np.ndarray) -> np.ndarray:
        """True ground point at plan (s, out) with a 2 mm lift; NaN off every patch."""
        s, out = np.broadcast_arrays(np.asarray(s, float), np.asarray(out, float))
        h = self.scene.true_ground_height(s, out)
        pts = np.stack([s, h + 0.002, out], axis=-1)
        return np.where(np.isnan(h)[..., None], np.nan, pts)

    def occupied(self, pts: np.ndarray) -> np.ndarray:
        """Is each point inside a solid? (A thing occupies the space, so it is not clear.)"""
        pts = np.atleast_2d(pts)
        occ = np.zeros(len(pts), dtype=bool)
        for solid in self.occupiers:
            occ |= solid.contains(pts)
        return occ

    # ---- per-band truth tables ----------------------------------------------------------

    @staticmethod
    def _grid3(plane2d: np.ndarray, third: np.ndarray) -> np.ndarray:
        """(C, K) plane coords broadcast against a 1-D third axis -> (C, K, T).
        (np.meshgrid flattens multi-d inputs, which is wrong here.)"""
        plane2d = np.asarray(plane2d, float)
        third = np.asarray(third, float)
        out = np.broadcast_to(plane2d[..., None], plane2d.shape + (len(third),)).copy()
        return out

    def wall_band(self, cells: np.ndarray) -> dict:
        """Truth for wall cells (true s per cell): exists/seen/two-pos over cols x rows."""
        cols = np.array([-0.0572, -0.0191, 0.0191, 0.0572])
        rows = np.linspace(0.30, 1.98, 18)
        S = self._grid3(cells[:, None] + cols[None, :], rows)
        Y = np.broadcast_to(rows, S.shape)
        pts, _ = self.wall_face(S, Y)
        exists = np.isfinite(pts[..., 0])
        exists &= (pts[..., 0] > cells[:, None, None] - CELL / 2 - 0.005) & (
            pts[..., 0] < cells[:, None, None] + CELL / 2 + 0.005
        )
        probe = pts.copy()
        probe[..., 2] += 0.001  # lift off the face
        counts, two = self.visible(probe[exists])
        seen, twos = np.zeros_like(exists), np.zeros_like(exists)
        seen[exists], twos[exists] = counts > 0, two
        return {"cells": cells, "exists": exists, "seen": seen, "two_pos": twos, "probe": pts}

    def ground_band(self, cells: np.ndarray, out_max: float = 3.048) -> dict:
        """Truth for ground cells: exists/seen at outs from the wall to out_max."""
        outs = np.concatenate([np.arange(0.05, out_max, 0.1524), [out_max]])
        cols = np.array([-0.05, 0.0, 0.05])
        S = self._grid3(cells[:, None] + cols[None, :], outs)
        O = np.broadcast_to(outs, S.shape)
        pts = self.ground_point(S, O)
        exists = np.isfinite(pts[..., 0])
        probe = pts.copy()
        probe[..., 1] += 0.002
        counts, two = self.visible(probe[exists])
        seen, twos = np.zeros_like(exists), np.zeros_like(exists)
        seen[exists], twos[exists] = counts > 0, two
        return {"outs": outs, "exists": exists, "seen": seen, "two_pos": twos}

    def facing_band(self, cells: np.ndarray, out_max: float = 6.1) -> dict:
        """Truth for the facing band: occupied(out) and observed-clear(out) per cell. Oracle
        resolution: 0.25 ft in out, 0.15 m in height - half the worker's slab grid."""
        outs = np.arange(0.0381, out_max, 0.0762)
        hs = np.arange(0.30, 1.801, 0.15)
        across = np.array([-0.05, 0.0, 0.05])
        S = self._grid3(cells[:, None] + across[None, :], outs)  # (C, 3, len(outs))
        S = np.repeat(S[..., None], len(hs), axis=-1)  # (C, 3, outs, hs)
        O = np.broadcast_to(outs[None, None, :, None], S.shape)
        H = np.broadcast_to(hs[None, None, None, :], S.shape)
        pts = np.stack([S, H, O], axis=-1)  # plan (x, z) = (s, out): points at (s, h, out)
        occ = self.occupied(pts.reshape(-1, 3)).reshape(S.shape)
        counts, two = self.visible(pts.reshape(-1, 3), clearance=0.02)
        n, tw = counts.reshape(S.shape), two.reshape(S.shape)
        return {
            "outs": outs,
            "occupied": occ,
            "clear_observed": ~occ & (n > 0),
            "n_frames": n,
            "two_pos": tw,
        }

    def overhead_band(self, cells: np.ndarray, out_lo: float = 0.05, out_hi: float = 0.56) -> dict:
        """Truth for the overhead strip: occupied(h) and observed-clear(h) from out_lo to out_hi."""
        outs = np.arange(out_lo, out_hi + 1e-9, 0.05)
        hs = np.arange(0.05, 3.001, 0.075)  # from 0.05 m: low clearance claims must have samples
        across = np.array([-0.05, 0.0, 0.05])
        S = np.repeat(self._grid3(cells[:, None] + across[None, :], outs)[..., None], len(hs), axis=-1)
        O = np.broadcast_to(outs[None, None, :, None], S.shape)
        H = np.broadcast_to(hs[None, None, None, :], S.shape)
        pts = np.stack([S, H, O], axis=-1)
        occ = self.occupied(pts.reshape(-1, 3)).reshape(S.shape)
        counts, two = self.visible(pts.reshape(-1, 3), clearance=0.02)
        n, tw = counts.reshape(S.shape), two.reshape(S.shape)
        return {
            "heights": hs,
            "occupied": occ,
            "clear_observed": ~occ & (n > 0),
            "n_frames": n,
            "two_pos": tw,
        }
