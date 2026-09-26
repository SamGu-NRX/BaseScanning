"""Sweep the battery and its clearance envelopes along every wall surface of an occupancy grid.

    python -m c2vol.sweep SCENE.json GRID.npz RULES.yaml OUT_DIR [--step 0.10] [--tag NAME]

No walls are extracted. A candidate is any observed free column next to a wall-like column (or next
to unobserved space, which could hide a wall); its yaw comes from the local wall cells. Every check
samples the grid in the candidate's own frame (u along the wall, v outward, w up), so corners,
recesses, plinths and bins need no special case: they either collide, break the backing, block the
front, or leave space unobserved. Any envelope that reaches unobserved space makes that check UNSURE.
"""

import argparse
import json
import math
import time
import warnings
from pathlib import Path

import numpy as np
import yaml
from numba import njit
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import Point, Polygon, box
from skimage.graph import MCP_Geometric

from c2vol.grid import FREE, OCCUPIED, UNKNOWN, Grid, coarsen

FT = 0.3048
PASS, UNSURE, FAIL = "pass", "unsure", "fail"
RANK = {PASS: 0, UNSURE: 1, FAIL: 2}
OPENINGS = ("window", "door", "garage_door", "window_well")


def c5(margin, err):
    """Margin rule (contract section 3, C5): PASS needs margin > err, FAIL needs margin < -err;
    anything in between is UNSURE."""
    return PASS if margin > err else (FAIL if margin < -err else UNSURE)


def worst(*outs):
    return max(outs, key=lambda o: RANK[o]) if outs else PASS


def check(cid, outcome, value_m=None, threshold_m=None, margin_m=None, err_m=None, unknown=0,
          subject=None, note=None):
    f = (lambda x: None if x is None else round(x / FT, 2))
    return {"id": cid, "outcome": outcome, "value_ft": f(value_m), "threshold_ft": f(threshold_m),
            "margin_ft": f(margin_m), "err_ft": f(err_m), "unknown_count": int(unknown),
            "subject": subject, "note": note}


# ---------------------------------------------------------------- grid-level maps

def effective_state(state: np.ndarray, hole: int = 2) -> tuple[np.ndarray, dict]:
    """Unknown pockets that cannot hold a cube of `hole` voxels (10 cm at 5 cm) are sampling holes
    between rays, not unobserved space: they become FREE. Everything thicker stays UNKNOWN."""
    unk = state == UNKNOWN
    big = ndimage.binary_opening(unk, np.ones((hole,) * 3, bool))
    out = state.copy()
    out[unk & ~big] = FREE
    return out, {"unknown_holes_filled": int((unk & ~big).sum()), "unknown_kept": int(big.sum())}


class Maps:
    def __init__(self, g: Grid, band=(0.3, 1.0)):
        self.g = g
        s = g.state
        occ, free = s == OCCUPIED, s == FREE
        # a surface hit with free space above and no occupied voxel 10 cm below it (a sill or wall
        # top has solid wall underneath)
        cond = occ[:, :, :-1] & free[:, :, 1:]
        cond[:, :, 2:] &= ~occ[:, :, :-3]
        has = cond.any(2)
        k = np.argmax(cond, 2)
        z = g.origin[2] + (k + 1) * g.res
        obs = np.where(has, z, np.nan)
        # gate: ledges, sills and lids are small next to the ground, so a low percentile over 0.5 m
        # blocks, smoothed over 1.5 m, follows the ground; hits more than 0.3 m off it are not ground
        B = max(1, int(round(0.5 / g.res)))
        nbx, nby = -(-obs.shape[0] // B), -(-obs.shape[1] // B)
        pad = np.full((nbx * B, nby * B), np.nan)
        pad[: obs.shape[0], : obs.shape[1]] = obs
        blk = pad.reshape(nbx, B, nby, B).transpose(0, 2, 1, 3).reshape(nbx, nby, -1)
        cnt = np.isfinite(blk).sum(2)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            low = np.nanpercentile(blk, 20, axis=2)
        low[cnt < max(3, B * B // 20)] = np.nan
        if np.isfinite(low).any():
            _, (bi, bj) = ndimage.distance_transform_edt(~np.isfinite(low), return_indices=True)
            low = ndimage.median_filter(low[bi, bj], size=3)
            gate = np.repeat(np.repeat(low, B, 0), B, 1)[: obs.shape[0], : obs.shape[1]]
            obs[np.abs(obs - gate) > 0.3] = np.nan
        has = np.isfinite(obs)
        self.ground_obs = obs
        # reference terrain: nearest fill, then a 0.5 m median
        miss = ~has
        if miss.all():
            raise SystemExit("no observed ground")
        _, (ii, jj) = ndimage.distance_transform_edt(miss, return_indices=True)
        filled = self.ground_obs[ii, jj]
        n = max(3, int(round(0.5 / g.res)) | 1)
        self.terrain = ndimage.median_filter(filled, size=n)
        # band states per column, relative to the reference terrain
        kz = np.arange(s.shape[2])
        zc = g.origin[2] + (kz + 0.5) * g.res
        h = zc[None, None, :] - self.terrain[:, :, None]
        # ground seen: a return within 5 cm, or a ray passed through the 10 cm just above the terrain
        near_hit = ndimage.maximum_filter(has.astype(np.uint8), size=3) > 0
        skim = (free & (h >= 0.0) & (h <= 0.1)).any(2)
        self.ground_seen = near_hit | skim
        # unseen ground patches at least 0.5 m across (room for a driveway edge or a pool)
        k5 = max(3, int(round(0.5 / g.res)))
        self.ground_unseen_big = ndimage.binary_opening(~self.ground_seen, np.ones((k5, k5), bool))
        inband = (h >= band[0]) & (h <= band[1])
        nb = inband.sum(2).clip(min=1)
        self.f_occ = (occ & inband).sum(2) / nb
        self.f_free = (free & inband).sum(2) / nb
        self.f_unk = ((s == UNKNOWN) & inband).sum(2) / nb
        self.wallish = self.f_occ >= 0.5
        self.free_col = (self.f_occ == 0) & (self.f_free >= 0.5)
        self.unk_col = (self.f_occ < 0.5) & (self.f_unk >= 0.5)
        near_wall = ndimage.binary_dilation(self.wallish, np.ones((3, 3), bool))
        # unobserved patches of at least 0.5 m^2 next to free space could hide a wall
        lab, nl = ndimage.label(self.unk_col)
        area = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, nl + 1)) * g.res ** 2
        big_unk = np.isin(lab, np.nonzero(area >= 0.5)[0] + 1)
        near_unk = ndimage.binary_dilation(big_unk, np.ones((3, 3), bool))
        self.skin = self.free_col & near_wall
        self.frontier = self.free_col & near_unk & ~self.skin
        self.big_unk = big_unk

    def xy(self, ij):
        return self.g.origin[:2] + (np.asarray(ij) + 0.5) * self.g.res


def sample_states(g: Grid, X: np.ndarray) -> np.ndarray:
    ijk = np.floor((X - g.origin) / g.res).astype(np.int64)
    ok = np.all((ijk >= 0) & (ijk < np.array(g.shape)), axis=-1)
    out = np.full(X.shape[:-1], UNKNOWN, np.uint8)
    out[ok] = g.state[ijk[ok, 0], ijk[ok, 1], ijk[ok, 2]]
    return out


def column_values(M: Maps, arr: np.ndarray, XY: np.ndarray) -> np.ndarray:
    ij = np.floor((XY - M.g.origin[:2]) / M.g.res).astype(np.int64)
    ok = np.all((ij >= 0) & (ij < np.array(arr.shape)), axis=-1)
    out = np.full(len(XY), np.nan)
    out[ok] = arr[ij[ok, 0], ij[ok, 1]]
    return out


def wall_frames(M: Maps, cells_ij: np.ndarray, radius=0.5):
    """Along-wall unit vector and outward normal per cell, from PCA of nearby wall (or big
    unobserved) cells; the normal points away from their centroid."""
    src = np.argwhere(M.wallish | M.big_unk)
    S = M.xy(src)
    tree = cKDTree(S)
    C = M.xy(cells_ij)
    along = np.full((len(C), 2), np.nan)
    normal = np.full((len(C), 2), np.nan)
    backed_by_wall = np.zeros(len(C), bool)
    wall_xy = set(map(tuple, np.argwhere(M.wallish)))
    for n, (c, nb) in enumerate(zip(C, tree.query_ball_point(C, radius))):
        if len(nb) < 3:
            continue
        Q = S[nb]
        m = Q.mean(0)
        _, V = np.linalg.eigh(np.cov((Q - m).T))
        a = V[:, 1]
        nv = np.array([-a[1], a[0]])
        if np.dot(c - m, nv) < 0:
            nv = -nv
        along[n], normal[n] = a, nv
        backed_by_wall[n] = any(tuple(src[i]) in wall_xy for i in nb)
    return along, normal, backed_by_wall


# ---------------------------------------------------------------- unobserved envelopes

@njit(cache=True)
def _bfs(C, seeds, lo, hi, max_depth, hmin, hmax, hgrid):
    """6-connected BFS (a rasterised surface only blocks face-connected paths) through observed FREE
    coarse voxels inside [lo, hi), restricted to heights [hmin, hmax] above the column terrain
    (hgrid = voxel-centre height above terrain). Returns the UNKNOWN voxels on the frontier of the
    reached free space, with their BFS depth. The flood never enters unknown space, so an unobserved
    patch of wall cannot lead it into a building's unobserved interior."""
    nx, ny, nz = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    depth = np.full((nx, ny, nz), -1, np.int32)
    q = np.empty((nx * ny * nz, 3), np.int64)
    head, tail = 0, 0
    for s in range(seeds.shape[0]):
        i, j, k = seeds[s, 0] - lo[0], seeds[s, 1] - lo[1], seeds[s, 2] - lo[2]
        if 0 <= i < nx and 0 <= j < ny and 0 <= k < nz and depth[i, j, k] < 0:
            if C[seeds[s, 0], seeds[s, 1], seeds[s, 2]] == 1:
                depth[i, j, k] = 0
                q[tail, 0], q[tail, 1], q[tail, 2] = i, j, k
                tail += 1
    out = np.empty((nx * ny * nz, 4), np.int64)
    n_out = 0
    while head < tail:
        i, j, k = q[head, 0], q[head, 1], q[head, 2]
        head += 1
        d = depth[i, j, k]
        if d >= max_depth:
            continue
        for nb in range(6):
            a, b, c = i, j, k
            if nb == 0:
                a += 1
            elif nb == 1:
                a -= 1
            elif nb == 2:
                b += 1
            elif nb == 3:
                b -= 1
            elif nb == 4:
                c += 1
            else:
                c -= 1
            if a < 0 or b < 0 or c < 0 or a >= nx or b >= ny or c >= nz:
                continue
            if depth[a, b, c] >= 0:
                continue
            st = C[a + lo[0], b + lo[1], c + lo[2]]
            if st == 2:
                continue
            h = hgrid[a + lo[0], b + lo[1], c + lo[2]]
            if h < hmin or h > hmax:
                continue
            depth[a, b, c] = d + 1
            if st == 0:
                out[n_out, 0], out[n_out, 1], out[n_out, 2], out[n_out, 3] = a + lo[0], b + lo[1], c + lo[2], d + 1
                n_out += 1
                continue
            q[tail, 0], q[tail, 1], q[tail, 2] = a, b, c
            tail += 1
    return out[:n_out]


# ---------------------------------------------------------------- the sweep

class Sweep:
    def __init__(self, scene, grid: Grid, rules, step=0.10, ground_types=None, ground_err=0.0):
        self.sc, self.R = scene, rules
        self.ground_err = ground_err
        self.res = grid.res
        b = rules["battery"]
        self.W, self.D, self.H = b["width_ft"] * FT, b["depth_ft"] * FT, b["height_ft"] * FT
        t = time.time()
        eff, self.hole_stats = effective_state(grid.state)
        self.g = Grid(grid.origin, grid.res, eff)
        self.M = Maps(self.g)
        self.timing = {"maps": time.time() - t}
        self.objects = scene.get("objects", [])
        for o in self.objects:
            o["_poly"] = Polygon(o["plan_polygon"])
        self.ground_types = ground_types if ground_types is not None else \
            [(Polygon(gt["plan_polygon"]), gt["type"]) for gt in scene.get("ground_types", [])]
        self._coarse()
        self._candidates(step)
        self._route_setup()

    # coarse 10 cm grid for envelopes, openings sealed
    def _coarse(self):
        f = 2
        g, M = self.g, self.M
        C = coarsen(g.state, f)
        self.cres = g.res * f
        nx, ny, nz = C.shape
        ii, jj, kk = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
        X = g.origin + (np.stack([ii, jj, kk], -1) + 0.5) * self.cres
        for o in self.objects:
            if o["type"] not in OPENINGS:
                continue
            mn, mx = np.array(o["plan_polygon"]).min(0), np.array(o["plan_polygon"]).max(0)
            sel = ((X[..., 0] >= mn[0]) & (X[..., 0] <= mx[0]) & (X[..., 1] >= mn[1]) & (X[..., 1] <= mx[1])
                   & (X[..., 2] >= o["z_range"][0]) & (X[..., 2] <= o["z_range"][1]))
            idx = np.argwhere(sel)
            if len(idx):
                P = X[sel][:, :2]
                inside = np.array([o["_poly"].buffer(self.cres).contains(Point(p)) for p in P])
                idx = idx[inside]
                C[idx[:, 0], idx[:, 1], idx[:, 2]] = OCCUPIED
        terr = M.terrain[: nx * f, : ny * f].reshape(nx, f, ny, f).mean((1, 3))
        self.C = C
        self.cterr = terr
        self.cground_unseen = M.ground_unseen_big[: nx * f, : ny * f].reshape(nx, f, ny, f).any((1, 3))
        self.hgrid = (X[..., 2] - terr[:, :, None]).astype(np.float64)

    def _candidates(self, step):
        M = self.M
        cand_mask = M.skin | M.frontier
        ij = np.argwhere(cand_mask)
        XY = M.xy(ij)
        key = np.floor(XY / step).astype(np.int64)
        _, first = np.unique(key, axis=0, return_index=True)
        ij = ij[np.sort(first)]
        along, normal, backed = wall_frames(M, ij)
        ok = ~np.isnan(along[:, 0])
        self.c_ij, self.c_along, self.c_normal = ij[ok], along[ok], normal[ok]
        self.c_kind = np.where(M.skin[ij[ok, 0], ij[ok, 1]], "wall", "frontier")

    # --- route graph on the 2D skin
    def _route_setup(self):
        M, R = self.M, self.R["route"]
        res = self.res
        # a cable runs along the wall: free columns within 0.25 m of it (cuts across window recesses),
        # plus unobserved columns within 1 m (flagged unsure)
        band = ndimage.binary_dilation(M.wallish, iterations=int(round(0.25 / res)))
        near = ndimage.binary_dilation(M.skin | M.wallish, iterations=int(round(1.0 / res)))
        passable = M.skin | (M.free_col & band) | (M.unk_col & near) | M.frontier
        blocked = np.zeros_like(passable)
        ii, jj = np.nonzero(passable)
        XY = M.xy(np.stack([ii, jj], 1))
        for o in self.objects:
            if o["type"] in R["blocked_by"]:
                poly = o["_poly"].buffer(0.25)
                mn, mx = np.array(poly.bounds[:2]), np.array(poly.bounds[2:])
                sel = np.nonzero(np.all((XY >= mn) & (XY <= mx), 1))[0]
                for s in sel:
                    if poly.contains(Point(XY[s])):
                        blocked[ii[s], jj[s]] = True
        self.route_unknown = (M.unk_col & passable) | M.frontier
        cost = np.where(passable & ~blocked, 1.0, np.inf)
        self.route_blocked = blocked
        self.passable = passable & ~blocked
        # every passable cell gets a wall normal for corner detection
        pij = np.argwhere(self.passable)
        _, nrm, _ = wall_frames(M, pij, radius=1.0)
        self.route_normal = np.full(M.skin.shape + (2,), np.nan)
        self.route_normal[pij[:, 0], pij[:, 1]] = nrm
        mp = np.array(self.sc["meter"]["pos"][:2])
        d = np.linalg.norm(M.xy(pij) - mp, axis=1)
        self.meter_start = tuple(pij[np.argmin(d)])
        self.meter_start_offset = float(d.min())
        self.mcp = MCP_Geometric(cost, fully_connected=True)
        self.route_cost, _ = self.mcp.find_costs([self.meter_start])
        self.route_cost = self.route_cost * res
        # meter working space box (NEC 110.26), on the meter's wall
        mij = np.floor((mp - self.g.origin[:2]) / res).astype(int)
        a, n, _ = wall_frames(M, mij[None])
        self.meter_along, self.meter_normal = a[0], n[0]
        mws = self.R["meter_working_space"]
        w2, dep = mws["width_ft"] * FT / 2, mws["depth_ft"] * FT
        corners = [mp + s * w2 * a[0] + t * dep * n[0] for s, t in ((-1, 0), (1, 0), (1, 1), (-1, 1))]
        self.meter_ws = Polygon(corners)

    # ------------------------------------------------------------ one candidate

    def local(self, c, a, n, U, V, Wz):
        """world points for local samples (U, V arrays broadcast) at absolute heights Wz."""
        U, V, Wz = np.broadcast_arrays(U, V, Wz)
        X = c[0] + U * a[0] + V * n[0]
        Y = c[1] + U * a[1] + V * n[1]
        return np.stack([X, Y, Wz], -1)

    def evaluate(self, idx):
        g, M, R, res = self.g, self.M, self.R, self.res
        c = M.xy(self.c_ij[idx])
        a, n = self.c_along[idx], self.c_normal[idx]
        W, D, H = self.W, self.D, self.H
        us = np.arange(-W / 2 + res / 4, W / 2, res / 2)
        checks = []

        # ground height under the nominal footprint
        vs0 = np.arange(res / 4, D, res / 2)
        UU, VV = np.meshgrid(us, vs0, indexing="ij")
        XYf = self.local(c, a, n, UU, VV, 0.0)[..., :2].reshape(-1, 2)
        gobs = column_values(M, M.ground_obs, XYf)
        gref = column_values(M, M.terrain, XYf)
        zg = float(np.nanmedian(gobs)) if np.isfinite(gobs).any() else float(np.nanmedian(gref))

        # push back: rear face = most outward occupied surface behind the candidate
        vb = np.arange(-0.6, 0.1 + 1e-9, res / 2)
        ws = np.arange(zg + 0.1, zg + H, res / 2)
        U3, V3, W3 = np.meshgrid(us, vb, ws, indexing="ij")
        st = sample_states(g, self.local(c, a, n, U3, V3, W3))
        occ_v = np.where(st == OCCUPIED, V3, -np.inf).max(axis=(0, 2))
        occ_any = np.isfinite(occ_v)
        rear = (float(vb[occ_any].max()) + res / 2) if occ_any.any() else 0.0
        v0, v1 = rear, rear + D

        # body collision
        vbody = np.arange(v0 + res / 4, v1, res / 2)
        U3, V3, W3 = np.meshgrid(us, vbody, ws, indexing="ij")
        st = sample_states(g, self.local(c, a, n, U3, V3, W3))
        n_occ = int(((st == OCCUPIED).reshape(len(us), len(vbody), -1).any(2)).sum())
        n_unk = int((st == UNKNOWN).sum())
        o = FAIL if n_occ >= 3 else (UNSURE if n_occ or n_unk else PASS)
        checks.append(check("body_clear", o, unknown=n_unk, note=f"{n_occ} occupied columns"))

        # backing: rear face against wall over its area, and wall tall enough
        gap = R["wall_backing"]["max_gap_ft"] * FT
        vg = np.arange(v0 - gap, v0 + 1e-9, res / 2)
        U3, V3, W3 = np.meshgrid(us, vg, ws, indexing="ij")
        st = sample_states(g, self.local(c, a, n, U3, V3, W3))
        backed = (st == OCCUPIED).any(1)
        unk = ((st == UNKNOWN).any(1)) & ~backed
        frac = backed.mean()
        need = R["wall_backing"]["min_backed_fraction"]
        if frac >= need:
            ob = PASS
        elif frac + unk.mean() >= need:
            ob = UNSURE
        else:
            ob = FAIL
        # wall height behind the centre: occupied near the minimum wall height
        hmin = R["wall_backing"]["min_wall_height_ft"] * FT
        wt = np.arange(zg + hmin - 0.3, zg + hmin + 1e-9, res / 2)
        vt = np.arange(v0 - 0.5, v0 + 0.15, res / 2)
        U3, V3, W3 = np.meshgrid(us[len(us) // 4: -len(us) // 4 or None], vt, wt, indexing="ij")
        st = sample_states(g, self.local(c, a, n, U3, V3, W3))
        if (st == OCCUPIED).any():
            oh = PASS
        elif (st == UNKNOWN).any():
            oh = UNSURE
        else:
            oh = FAIL
        checks.append(check("wall_backing", worst(ob, oh), value_m=None, unknown=int(unk.sum()),
                            note=f"backed {frac:.0%} (need {need:.0%}); wall at {hmin / FT:.1f} ft: {oh}"))

        # ground under the footprint
        vs = np.arange(v0 + res / 4, v1, res / 2)
        UU, VV = np.meshgrid(us, vs, indexing="ij")
        XYf = self.local(c, a, n, UU, VV, 0.0)[..., :2].reshape(-1, 2)
        gobs = column_values(M, M.ground_obs, XYf)
        fin = np.isfinite(gobs)
        seen = column_values(M, M.ground_seen.astype(float), XYf) == 1
        obs_frac = seen.mean()
        G = R["ground"]
        og, note = PASS, []
        if fin.sum() >= 6:
            A = np.column_stack([XYf[fin], np.ones(fin.sum())])
            coef, *_ = np.linalg.lstsq(A, gobs[fin], rcond=None)
            slope = math.degrees(math.atan(math.hypot(coef[0], coef[1])))
            resid = gobs[fin] - A @ coef
            stepv = float(np.percentile(resid, 97.5) - np.percentile(resid, 2.5))
            note.append(f"slope {slope:.1f} deg, step {stepv / FT:.2f} ft")
            # slope +-1 deg, step +-half a voxel (ground hits are quantised to the grid): placeholders
            og = worst(c5(G["max_slope_deg"] - slope, 1.0), c5(G["max_step_ft"] * FT - stepv, res / 2))
        else:
            slope, stepv = None, None
        if og == PASS and obs_frac < G["min_observed_fraction"]:
            og = UNSURE
        cxy = self.local(c, a, n, 0.0, (v0 + v1) / 2, 0.0)[:2]
        gt = next((t for p, t in self.ground_types if p.contains(Point(cxy))), None)
        if gt is None:
            og = worst(og, UNSURE)
        elif gt not in G["allowed"]:
            og = FAIL
        note.append(f"observed {obs_frac:.0%}, type {gt}")
        checks.append(check("ground", og, unknown=int((~seen).sum()), note="; ".join(note)))

        # facing (working space in front)
        Fc = R["facing"]
        fw = max(W, Fc["min_width_ft"] * FT)
        uf = np.arange(-fw / 2 + res / 4, fw / 2, res / 2)
        need_v = (v0 + Fc["from_wall_ft"] * FT) if Fc.get("measured_from", "wall") == "wall" \
            else (v1 + Fc["from_front_ft"] * FT)
        vmax = need_v + 1.0
        vf = np.arange(v1 + res / 4, vmax, res / 2)
        wf = np.arange(zg + Fc["ignore_below_ft"] * FT, zg + Fc["top_ft"] * FT, res / 2)
        U3, V3, W3 = np.meshgrid(uf, vf, wf, indexing="ij")
        st = sample_states(g, self.local(c, a, n, U3, V3, W3))
        occ_slice = (st == OCCUPIED).sum(axis=(0, 2))
        unk_slice = (st == UNKNOWN).sum(axis=(0, 2))
        hit = np.nonzero(occ_slice >= 2)[0]
        v_obst = vf[hit[0]] - res / 4 if len(hit) else vmax
        clear = v_obst - v0
        margin = v_obst - need_v
        before = vf < min(v_obst, need_v)
        n_unk_f = int(unk_slice[before].sum())
        of = c5(margin, res)
        if of == PASS and n_unk_f:
            of = UNSURE
        checks.append(check("facing", of, value_m=clear, threshold_m=need_v - v0, margin_m=margin,
                            err_m=res, unknown=n_unk_f))

        # headroom over the footprint (the first 0.1 m off the wall is wall relief)
        need_h = R["headroom"]["min_ft"] * FT
        vh = np.arange(v0 + 0.1, v1, res / 2)
        wh = np.arange(zg + H, zg + need_h + 0.5, res / 2)
        U3, V3, W3 = np.meshgrid(us, vh, wh, indexing="ij")
        st = sample_states(g, self.local(c, a, n, U3, V3, W3))
        occ_w = (st == OCCUPIED).any(axis=(0, 1))
        k1 = np.nonzero(occ_w)[0]
        z_obst = wh[k1[0]] if len(k1) else wh[-1] + res / 2
        clear_h = z_obst - zg
        below = wh < min(z_obst, zg + need_h)
        n_unk_h = int((st[:, :, below] == UNKNOWN).sum())
        oh = c5(clear_h - need_h, res)
        if oh == PASS and n_unk_h:
            oh = UNSURE
        checks.append(check("headroom", oh, value_m=clear_h, threshold_m=need_h,
                            margin_m=clear_h - need_h, err_m=res, unknown=n_unk_h))

        # labelled objects: plan distance footprint <-> polygon
        foot = Polygon([self.local(c, a, n, u, v, 0.0)[:2] for u, v in
                        ((-W / 2, v0), (W / 2, v0), (W / 2, v1), (-W / 2, v1))])
        CL = R["clearances"]
        rule_of = {"gas_meter": "gas_ft", "gas": "gas_ft", "ac": "ac_ft", "ac_unit": "ac_ft",
                   "driveway": "drive_ft", "drive": "drive_ft", "pool": "pool_ft", "fence": "fence_ft"}
        for t in CL.get("opening_types", OPENINGS):
            rule_of[t] = "opening_ft"
        per_rule = {}
        for o in self.objects:
            key = rule_of.get(o["type"])
            if key is None:
                continue
            Rm = CL[key] * FT
            d = foot.distance(o["_poly"])
            err = o.get("plus_minus_m", 0.0) + res
            m = d - Rm
            out = c5(m, err)
            prev = per_rule.get(key)
            if prev is None or (RANK[out], -m) > (RANK[prev["outcome"]], -(prev["margin_ft"] * FT)):
                per_rule[key] = check(key.replace("_ft", "_clearance"), out, value_m=d, threshold_m=Rm,
                                      margin_m=m, err_m=err, subject=o["id"])
        # drivable ground regions count for the drive clearance like a labelled driveway
        for poly, t in self.ground_types:
            if t in R["ground"].get("drivable", ["drive"]) and "drive_ft" in CL:
                Rm = CL["drive_ft"] * FT
                d = foot.distance(poly)
                err = self.ground_err + res
                out = c5(d - Rm, err)
                prev = per_rule.get("drive_ft")
                if prev is None or RANK[out] > RANK[prev["outcome"]]:
                    per_rule["drive_ft"] = check("drive_clearance", out, value_m=d, threshold_m=Rm, margin_m=d - Rm,
                                                 err_m=err, subject=f"ground {t}")

        def keep_out(name, poly, err, subject):
            """footprint must stay out of `poly`: margin = gap (negative = penetration depth)."""
            if foot.intersects(poly):
                pen = foot.intersects(poly.buffer(-err)) if poly.area > 0 else True
                gap = -err * 2 if pen else 0.0
            else:
                gap = foot.distance(poly)
            return check(name, c5(gap, err), value_m=gap, threshold_m=0.0, margin_m=gap, err_m=err, subject=subject)

        for o in self.objects:
            if o["type"] in R["clearances"].get("wall_equipment_types", []):
                ch = keep_out("wall_equipment", o["_poly"], o.get("plus_minus_m", 0.0) + res, o["id"])
                prev = per_rule.get("wall_equipment")
                if prev is None or RANK[ch["outcome"]] > RANK[prev["outcome"]]:
                    per_rule["wall_equipment"] = ch
        per_rule["meter_working_space"] = keep_out("meter_working_space", self.meter_ws,
                                                   self.sc["meter"].get("plus_minus_m", 0.0) + res, "meter")

        # unobserved envelopes: unknown space reachable from the spot within each clearance
        env = self.envelopes(c, a, n, v0, v1, zg)
        for key, (cnt, band) in env.items():
            name = key.replace("_ft", "_clearance")
            ex = per_rule.get(key)
            tol = R["unknown"]["tolerance_voxels"]
            if ex is None:
                ex = check(name, PASS, threshold_m=CL[key] * FT, note="no labelled object in range")
            ex["unknown_count"] = int(cnt)
            if cnt > tol and ex["outcome"] == PASS:
                ex["outcome"] = UNSURE
                ex["note"] = (ex.get("note") or "") + f" {cnt} unobserved 10 cm voxels within reach ({band} band)"
            per_rule[key] = ex
        checks.extend(per_rule.values())

        # route along the wall from the meter
        route = self.route(c, a, n, v0)
        checks.append(route.pop("check"))

        outcome = worst(*[ch["outcome"] for ch in checks])
        return {
            "center_xy": [round(float(x), 3) for x in self.local(c, a, n, 0.0, (v0 + v1) / 2, 0.0)[:2]],
            "rear_mid_xy": [round(float(x), 3) for x in self.local(c, a, n, 0.0, v0, 0.0)[:2]],
            "along": [round(float(x), 4) for x in a], "outward": [round(float(x), 4) for x in n],
            "ground_z": round(zg, 3), "kind": str(self.c_kind[idx]),
            "footprint_xy": [[round(float(x), 3) for x in p] for p in foot.exterior.coords[:-1]],
            "outcome": outcome, "checks": checks, "route": route,
        }

    def envelopes(self, c, a, n, v0, v1, zg):
        CL, U = self.R["clearances"], self.R["unknown"]
        rules = {"gas_ft": "wall", "ac_ft": "wall", "opening_ft": "wall", "fence_ft": "wall",
                 "drive_ft": "ground", "pool_ft": "ground"}
        rules = {k: b for k, b in rules.items() if k in CL}
        Rmax = max(CL[k] for k in rules) * FT
        cr, C = self.cres, self.C
        W = self.W
        # seeds: body and the first 0.3 m in front, 0.1 m..H above ground
        us = np.arange(-W / 2, W / 2 + 1e-9, cr / 2)
        vs = np.arange(v0 + cr / 2, v1 + 0.3, cr / 2)
        ws = np.arange(zg + 0.1, zg + self.H, cr)
        U3, V3, W3 = np.meshgrid(us, vs, ws, indexing="ij")
        X = self.local(c, a, n, U3, V3, W3).reshape(-1, 3)
        seeds = np.unique(np.floor((X - self.g.origin) / cr).astype(np.int64), axis=0)
        ci = np.floor((c - self.g.origin[:2]) / cr).astype(int)
        half = int(math.ceil((Rmax + W + 0.5) / cr))
        lo = np.array([max(ci[0] - half, 0), max(ci[1] - half, 0), 0], np.int64)
        hi = np.array([min(ci[0] + half, C.shape[0]), min(ci[1] + half, C.shape[1]), C.shape[2]], np.int64)
        top = U["wall_band_top_ft"] * FT
        max_depth = int(math.ceil((2.0 * Rmax + 0.5) / cr))
        got = _bfs(C, seeds, lo, hi, max_depth, 0.15, top, self.hgrid)
        out = {}
        if len(got) == 0:
            return {k: (0, b) for k, b in rules.items()}
        P = self.g.origin[:2] + (got[:, :2] + 0.5) * cr
        rel = P - c
        u = rel @ a
        v = rel @ n
        du = np.maximum(np.abs(u) - W / 2, 0)
        dv = np.maximum(np.maximum(v0 - v, v - v1), 0)
        dist = np.hypot(du, dv)
        h = self.hgrid[got[:, 0], got[:, 1], got[:, 2]]
        unk = self.C[got[:, 0], got[:, 1], got[:, 2]] == UNKNOWN
        # reached voxels just above unseen ground patches: the ground there is unobserved
        gu = self.cground_unseen[got[:, 0], got[:, 1]] & (h <= 0.3)
        for k, band in rules.items():
            Rm = CL[k] * FT
            bt = top if band == "wall" else U["ground_band_top_ft"] * FT
            reach = (dist <= Rm) & (got[:, 3] <= (2.0 * Rm + 0.5) / cr)
            # the lowest 15 cm is the ground layer itself (unknown below any surface)
            sel = reach & unk & (h >= 0.15) & (h <= bt)
            cnt = int(sel.sum())
            if band == "ground":
                cols = np.unique(got[reach & gu][:, :2], axis=0)
                cnt += len(cols)
            out[k] = (cnt, band)
        return out

    def route(self, c, a, n, v0):
        R, res, M = self.R["route"], self.res, self.M
        W = self.W
        # footprint rear-edge cells
        us = np.arange(-W / 2, W / 2 + 1e-9, res / 2)
        best = None
        for dv in (0.0, res, -res, 2 * res):
            P = np.stack([c + u * a + (v0 + res / 2 + dv) * n for u in us])
            ij = np.floor((P - self.g.origin[:2]) / res).astype(int)
            ok = np.all((ij >= 0) & (ij < np.array(M.skin.shape)), 1)
            for i, j in ij[ok]:
                cst = self.route_cost[i, j]
                if np.isfinite(cst) and (best is None or cst < best[0]):
                    best = (cst, (i, j))
        mx = R["max_ft"] * FT
        if best is None:
            return {"length_ft": None, "check": check("route", FAIL, threshold_m=mx,
                                                       note="no path along the wall to the meter")}
        length = best[0] + self.meter_start_offset
        path = np.array(self.mcp.traceback(best[1]))
        n_unk = int(self.route_unknown[path[:, 0], path[:, 1]].sum())
        # corners: the wall normal turns more than corner_turn_deg within 1 m of path
        nr = self.route_normal[path[:, 0], path[:, 1]]
        ang = np.unwrap(np.arctan2(nr[:, 1], nr[:, 0]))
        ok = np.isfinite(ang)
        corners = 0
        if ok.sum() > 2:
            s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))] * res
            s, ang_ok = s[ok], ang[ok]
            half = 0.75
            turn = np.array([abs(np.interp(si + half, s, ang_ok) - np.interp(si - half, s, ang_ok))
                             for si in s])
            mark = turn > math.radians(R["corner_turn_deg"])
            corners = int(np.sum(mark[1:] & ~mark[:-1]) + (1 if mark[0] else 0))
        eff = length + corners * R.get("corner_penalty_ft", 0.0) * FT
        err = res * 2 + self.sc["meter"].get("plus_minus_m", 0.0)
        margin = mx - eff
        o = FAIL if (R.get("same_wall") and corners) else c5(margin, err)
        if o == PASS and (n_unk or eff > R.get("confident_reach_ft", R["max_ft"]) * FT):
            o = UNSURE
        poly = self.g.origin[:2] + (path[:: max(1, len(path) // 40)] + 0.5) * res
        return {"length_ft": round(eff / FT, 2), "corners": corners, "unknown_cells": n_unk,
                "polyline_xy": poly.round(3).tolist(),
                "check": check("route", o, value_m=eff, threshold_m=mx, margin_m=margin, err_m=err,
                               unknown=n_unk, note=f"{corners} corner(s)")}

    def run(self):
        t = time.time()
        spots = [self.evaluate(i) for i in range(len(self.c_ij))]
        self.timing["evaluate"] = time.time() - t
        return spots


# ---------------------------------------------------------------- site decision, s mapping

def site_decision(spots, unknown_in_reach):
    """pass if any spot passes; manual_review if any is unsure, or if unobserved wall-side space lies
    within cable reach of the meter (a spot could exist there); reject only otherwise."""
    passes = [s for s in spots if s["outcome"] == PASS]
    unsure = [s for s in spots if s["outcome"] == UNSURE]
    if passes:
        d = "pass"
    elif unsure or unknown_in_reach:
        d = "manual_review"
    else:
        d = "reject"
    best = None
    pool = passes or unsure or spots
    if pool:
        def key(s):
            r = s["route"].get("length_ft")
            return (RANK[s["outcome"]], sum(RANK[c["outcome"]] for c in s["checks"]),
                    r if r is not None else 1e9)
        best = min(pool, key=key)
    return d, best


def s_of(chain, xy):
    """along-wall s (ft) of a plan point on the reference wall chain, and its distance (m)."""
    best = None
    for w in chain["walls"]:
        A, B = np.array(w["a"][:2]), np.array(w["b"][:2])
        d = B - A
        L = np.linalg.norm(d)
        t = np.clip(np.dot(np.array(xy) - A, d) / L ** 2, 0, 1)
        dist = np.linalg.norm(A + t * d - xy)
        if best is None or dist < best[0]:
            best = (dist, w["id"], w["s0_ft"] + t * L / FT)
    return best


def ground_from_scene_v0(sv):
    """ground.regions of a scene v0 (meter frame [x, z]) as scan-frame plan polygons, via the inverse
    of frame.from_source (column-major, scene = T @ scan)."""
    T = np.array(sv["frame"]["from_source"], float).reshape(4, 4).T
    Ti = np.linalg.inv(T)
    out, err = [], 0.0
    for r in sv["ground"]["regions"]:
        P = np.array(r["polygon_m"], float)
        X = np.column_stack([P[:, 0], np.zeros(len(P)), P[:, 1], np.ones(len(P))]) @ Ti.T
        out.append((Polygon(X[:, :2]), r["material"]))
        err = max(err, r.get("plus_minus_m", 0.0))
    return out, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene", type=Path)
    ap.add_argument("grid", type=Path)
    ap.add_argument("rules", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--step", type=float, default=0.10)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--ground-from", type=Path, help="scene v0 whose ground.regions replace the C2 scene's ground_types")
    a = ap.parse_args()
    sc = json.loads(a.scene.read_text())
    rules = yaml.safe_load(a.rules.read_text())
    g = Grid.load(a.grid)
    t0 = time.time()
    gts, gerr = None, 0.0
    if a.ground_from:
        gts, gerr = ground_from_scene_v0(json.loads(a.ground_from.read_text()))
    sw = Sweep(sc, g, rules, a.step, gts, gerr)
    spots = sw.run()
    chain = sc.get("reference_chain")
    W_ft = rules["battery"]["width_ft"]
    for s in spots:
        if chain:
            dist, wid, sc_ = s_of(chain, s["rear_mid_xy"])
            s["ref"] = {"wall_id": wid, "s_center_ft": round(sc_, 2), "start_ft": round(sc_ - W_ft / 2, 2),
                        "dist_m": round(float(dist), 3)}
    reach = sw.route_cost <= rules["route"]["max_ft"] * FT
    unknown_in_reach = int((reach & sw.route_unknown).sum())
    decision, best = site_decision(spots, unknown_in_reach)
    strict_outcome = "manual_review" if decision == "reject" else decision
    counts = {k: sum(s["outcome"] == k for s in spots) for k in (PASS, UNSURE, FAIL)}
    fail_why = {}
    unsure_why = {}
    for s in spots:
        for c in s["checks"]:
            if c["outcome"] == FAIL:
                fail_why[c["id"]] = fail_why.get(c["id"], 0) + 1
            elif c["outcome"] == UNSURE:
                unsure_why[c["id"]] = unsure_why.get(c["id"], 0) + 1
    tag = a.tag or a.grid.stem + "_" + a.rules.stem
    out = {
        "scene": sc["id"], "grid": str(a.grid.name), "rules": str(a.rules.name), "real": sc.get("real"),
        "decision": decision, "decision_no_reject_policy": strict_outcome,
        "best": best, "counts": counts, "fail_checks": fail_why, "unsure_checks": unsure_why,
        "n_candidates": len(spots), "unknown_route_cells_in_reach": unknown_in_reach, "hole_stats": sw.hole_stats, "meter_start_offset_m": round(sw.meter_start_offset, 3),
        "timing_s": {k: round(v, 1) for k, v in {**sw.timing, "total": time.time() - t0}.items()},
        "spots": spots,
    }
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / f"{tag}.json").write_text(json.dumps(out))
    print(json.dumps({k: out[k] for k in ("decision", "decision_no_reject_policy", "counts",
                                          "unknown_route_cells_in_reach", "n_candidates", "timing_s")}))
    if best:
        print("best", best["outcome"], best.get("ref"), best["route"].get("length_ft"),
              [(c["id"], c["outcome"]) for c in best["checks"] if c["outcome"] != PASS])


if __name__ == "__main__":
    main()
