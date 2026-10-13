"""Edge geometry on ETH3D: where MoGe-2's error at object edges lands relative to the wall, and
whether a ray to the wall plane, a snapped tap or a second photo places an edge better.

    uv run python run.py        # writes results/edge_geometry.json and results/edge_geometry.md
    uv run python run.py --pair=widest   # M3 with the widest pair: results/edge_geometry_widest_pair.*

Reads the evals harness read-only (EDGE_EVALS_DIR, default below) and its caches under
~/house-scanning-data/evals: the ETH3D scenes after `evals.recon prepare`, MoGe-2 predictions
and the AR-like poses. Every draw is seeded, so a rerun gives the same numbers.
"""

from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial import cKDTree

import geom
import imagematch
import stats

# The evals checkout is read-only here: importing it must not write __pycache__ into it.
sys.dont_write_bytecode = True
EVALS_DIR = Path(
    os.environ.get(
        "EDGE_EVALS_DIR",
        Path.home() / "Programming Projects/house-scanning-evals/experiments/evals",
    )
)
sys.path.insert(0, str(EVALS_DIR))

from evals.eth3d import read_views, up_direction  # noqa: E402
from evals.paths import ETH3D_DIR  # noqa: E402
from evals.recon import (  # noqa: E402
    NEAR_M,
    PREDICTIONS,
    TOLERANCE,
    VERTICAL_MAX_UP,
    WIDTH,
    eval_candidates,
    load_prediction,
    visibility_dir,
)
from evals.triangulate import sample_depth  # noqa: E402

HERE = Path(__file__).parent
RESULTS = HERE / "results"

SCENES = ("electro", "facade")
MIN_POINTS_TO_REPORT = 200  # facade is reported only with this many edge points within 6 m
MAX_POINTS = 3000

# Wall planes from the laser scan.
PLANE_TOL_M = 0.03
MAX_TILT_DEG = 17.0  # the evals' vertical-surface limit, |normal . up| < 0.3
RANSAC_POINTS = 400_000
JOIN_M = 0.6  # an edge point joins the nearest plane this close
MEMBER_REACH_M = 1.0  # ... if one of the plane's own scan points lies this close to it
IN_PLANE_M = 0.05
EDGE_RADIUS_M = 0.15  # neighbourhood for an edge's direction
VERTICAL_EDGE_DEG = 30.0

# Methods.
MIN_WALL_PX = 50  # interior wall pixels a photo needs for an M1 plane
ANCHOR_ALONG_M = (1.0, 3.0)
ANCHOR_TARGET_M = 2.0
PATCH_RADIUS_M = 0.3
MIN_PATCH_PX = 20
ANCHOR_TRIES = 5
TAP_SIGMA_PX = 10.0
SNAP_RADIUS_PX = 25
SNAP_ROWS = 9
WORLD_PLANE_PX_PER_PHOTO = 3000
MIN_BASELINE_M = 0.3
MIN_RAY_ANGLE_DEG = 5.0
# M3's second photo, pre-registered: the narrowest ray angle at or above MIN_RAY_ANGLE_DEG, which
# keeps the two patches most alike for matching. A narrow pair triangulates worst, so after review
# `--pair=widest` reruns M3 with the widest qualifying angle instead, into separate results files.
PAIRING = "widest" if "--pair=widest" in sys.argv else "narrowest"
NCC_MIN = 0.8
NCC_PATCH = 15
DEPTH_RANGE_M = (0.5, 10.0)
SEED = 20260926

SETTINGS = [("exact", 0, "exact.json")] + [
    ("modern_assumed", d, "modern_assumed.json" if d == 0 else f"modern_assumed.draw{d}.json")
    for d in range(5)
]


@dataclass
class Wall:
    """One photo's interior pixels on one laser plane, within NEAR_M of the camera."""

    ids: np.ndarray  # candidate indices, sorted
    world: np.ndarray  # laser positions (N, 3)
    cam: np.ndarray  # MoGe-2 points in the camera frame at the oracle scale (N, 3)


@dataclass
class ViewData:
    name: str
    K: np.ndarray
    T: np.ndarray  # true camera-to-world
    index: np.ndarray  # visible candidates within NEAR_M of the camera, sorted
    uv: np.ndarray
    edge: np.ndarray
    depth: np.ndarray  # MoGe-2 depth at uv times the oracle scale, NaN where invalid
    scale: float
    walls: dict[int, Wall]

    @property
    def C(self) -> np.ndarray:
        return self.T[:3, 3]

    def find(self, ids: np.ndarray) -> np.ndarray:
        """Row of each candidate id in this view, or -1 when the view does not see it."""
        if not len(self.index):
            return np.full(np.shape(ids), -1)
        pos = np.searchsorted(self.index, ids)
        pos = np.minimum(pos, len(self.index) - 1)
        return np.where(self.index[pos] == ids, pos, -1)


def backproject(K: np.ndarray, uv: np.ndarray, z: np.ndarray) -> np.ndarray:
    return np.c_[(uv[:, 0] - K[0, 2]) / K[0, 0] * z, (uv[:, 1] - K[1, 2]) / K[1, 1] * z, z]


def cam_dirs(K: np.ndarray, uv: np.ndarray) -> np.ndarray:
    uv = np.atleast_2d(uv)
    return np.c_[(uv[:, 0] - K[0, 2]) / K[0, 0], (uv[:, 1] - K[1, 2]) / K[1, 1], np.ones(len(uv))]


def evals_commit() -> str:
    out = subprocess.run(
        ["git", "-C", str(EVALS_DIR), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return out.stdout.strip() or "unknown"


def plane_members(cands, normals, planes) -> np.ndarray:
    """Plane index per candidate (-1 for none): within PLANE_TOL_M and facing the same way,
    nearest plane first."""
    best = np.full(len(cands), -1, np.int16)
    best_d = np.full(len(cands), np.inf, np.float32)
    cos_agree = np.cos(np.radians(25.0))
    for k, (n, d) in enumerate(planes):
        dist = np.abs(cands @ n.astype(np.float32) - np.float32(d))
        ok = (dist < PLANE_TOL_M) & (np.abs(normals @ n.astype(np.float32)) > cos_agree)
        ok &= dist < best_d
        best[ok] = k
        best_d[ok] = dist[ok]
    return best


def prep_view(scene, view, cands, plane_of) -> ViewData:
    h = round(view.height * WIDTH / view.width)
    K = view.scaled_K(WIDTH, h)
    z = np.load(visibility_dir(scene, TOLERANCE) / f"{view.name}.npz")
    index, uv, edge = z["index"], z["uv"].astype(np.float64), z["edge"]
    P = cands[index].astype(np.float64)
    z_laser = (P @ view.R_wc.T + view.t_wc)[:, 2]
    depth, _, _ = load_prediction(PREDICTIONS / scene / "moge2" / f"{view.name}.npz")
    if depth.shape != (h, WIDTH):
        raise ValueError(f"{scene}/{view.name}: MoGe-2 depth {depth.shape}, expected {(h, WIDTH)}")
    pred = sample_depth(depth, uv)
    interior = ~edge & np.isfinite(pred)
    # The oracle scale: the photo's own laser depth over its prediction, on interior pixels.
    scale = float(np.median(z_laser[interior] / pred[interior]))
    near = np.linalg.norm(P - view.center, axis=1) <= NEAR_M
    index, uv, edge, pred, P = index[near], uv[near], edge[near], pred[near] * scale, P[near]
    walls = {}
    on = ~edge & np.isfinite(pred) & (plane_of[index] >= 0)
    for k in np.unique(plane_of[index[on]]):
        sel = on & (plane_of[index] == k)
        if sel.sum() >= MIN_WALL_PX:
            walls[int(k)] = Wall(
                ids=index[sel], world=P[sel], cam=backproject(K, uv[sel], pred[sel])
            )
    return ViewData(view.name, K, view.cam_to_world, index, uv, edge, pred, scale, walls)


def level_up(normals, near_cam, camera_up) -> np.ndarray:
    """World up as the mean normal of near-horizontal scan points (the ground and other level
    surfaces) near the cameras. The mean camera up axis the evals use is about 5 degrees off on
    electro: every wall plane tilts 3 to 6 degrees against it and under 1 degree against this."""
    n = normals[near_cam].astype(np.float64)
    level = n[np.abs(n @ camera_up) > np.cos(np.radians(15))]
    if len(level) < 1000:
        raise ValueError(f"only {len(level)} level scan points near the cameras to fix up")
    level *= np.sign(level @ camera_up)[:, None]
    up = level.mean(axis=0)
    return up / np.linalg.norm(up)


def assign_edges(E, cands, plane_of, planes) -> tuple[np.ndarray, np.ndarray]:
    """Nearest plane within JOIN_M for each edge point, provided one of the plane's own points is
    within MEMBER_REACH_M (planes are infinite; this keeps a point on its own stretch of wall)."""
    best = np.full(len(E), -1)
    best_sd = np.full(len(E), np.inf)
    for k, (n, d) in enumerate(planes):
        sd = E @ n - d
        cand = np.flatnonzero((np.abs(sd) <= JOIN_M) & (np.abs(sd) < np.abs(best_sd)))
        if not len(cand):
            continue
        members = cands[plane_of == k].astype(np.float64)
        dist, _ = cKDTree(members).query(E[cand], distance_upper_bound=MEMBER_REACH_M)
        ok = cand[np.isfinite(dist)]
        best[ok] = k
        best_sd[ok] = sd[ok]
    return best, best_sd


def edge_directions(E_all, E_query, up) -> np.ndarray:
    """1 for a vertical edge, 0 for any other line or an unclear shape: the principal axis of the
    edge points within EDGE_RADIUS_M, whose spread along it must be at least twice the next."""
    tree = cKDTree(E_all)
    out = np.zeros(len(E_query), np.int8)
    for i, nb in enumerate(tree.query_ball_point(E_query, EDGE_RADIUS_M)):
        if len(nb) < 5:
            continue
        X = E_all[nb] - E_all[nb].mean(axis=0)
        w, V = np.linalg.eigh(X.T @ X)
        if w[2] >= 4.0 * max(w[1], 1e-12) and abs(V[:, 2] @ up) >= np.cos(
            np.radians(VERTICAL_EDGE_DEG)
        ):
            out[i] = 1
    return out


def choose_anchors(wall: Wall, s_edge: float, h: np.ndarray) -> np.ndarray:
    """Rows of `wall` 1 to 3 m along the wall from the edge, nearest to 2 m first."""
    gap = np.abs(wall.world @ h - s_edge)
    rows = np.flatnonzero((gap >= ANCHOR_ALONG_M[0]) & (gap <= ANCHOR_ALONG_M[1]))
    return rows[np.lexsort((rows, np.abs(gap[rows] - ANCHOR_TARGET_M)))]


class Images:
    """Grey images and their vertical-edge strength, loaded on first use."""

    def __init__(self, scene: str):
        self.dir = ETH3D_DIR / scene / f"images_{WIDTH}"
        self.gray: dict[str, np.ndarray] = {}
        self.strength: dict[str, np.ndarray] = {}

    def get(self, name: str) -> np.ndarray:
        if name not in self.gray:
            img = cv2.imread(str(self.dir / f"{name}.jpg"), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise FileNotFoundError(self.dir / f"{name}.jpg")
            self.gray[name] = img.astype(np.float32) / 255.0
        return self.gray[name]

    def edges(self, name: str) -> np.ndarray:
        if name not in self.strength:
            self.strength[name] = imagematch.vertical_edge_strength(self.get(name), SNAP_ROWS)
        return self.strength[name]


def run_scene(scene: str) -> dict:
    t0 = time.time()
    views = {v.name: v for v in read_views(ETH3D_DIR / scene)}
    up = up_direction(list(views.values()))
    pred_dir = PREDICTIONS / scene / "moge2"
    vis_dir = visibility_dir(scene, TOLERANCE)
    img_dir = ETH3D_DIR / scene / f"images_{WIDTH}"
    names = sorted(
        n
        for n in views
        if (vis_dir / f"{n}.npz").exists()
        and (pred_dir / f"{n}.npz").exists()
        and (img_dir / f"{n}.jpg").exists()
    )
    cands = eval_candidates(scene)
    normals = np.load(ETH3D_DIR / scene / "normals.npy")

    # Wall planes: RANSAC over vertical scan points within NEAR_M of some camera.
    centres = np.array([views[n].center for n in names])
    near_cam = cKDTree(centres).query(cands, distance_upper_bound=NEAR_M)[0] < np.inf
    camera_up = up
    up = level_up(normals, near_cam, camera_up)
    vert = np.flatnonzero(near_cam & (np.abs(normals @ up.astype(np.float32)) < VERTICAL_MAX_UP))
    sub = vert[np.linspace(0, len(vert) - 1, min(RANSAC_POINTS, len(vert))).astype(int)]
    planes = geom.ransac_planes(
        cands[sub].astype(np.float64),
        normals[sub].astype(np.float64),
        up,
        tol=PLANE_TOL_M,
        max_tilt_deg=MAX_TILT_DEG,
        seed=SEED,
    )
    plane_of = plane_members(cands, normals, planes)
    frames = [geom.wall_frame(n, up) for n, _ in planes]
    print(f"{scene}: {len(names)} photos, {len(planes)} wall planes ({time.time() - t0:.0f} s)")

    vd = {n: prep_view(scene, views[n], cands, plane_of) for n in names}
    print(f"{scene}: views prepared ({time.time() - t0:.0f} s)")

    # Edge points: near side of a depth edge in at least one photo within NEAR_M.
    edge_ids = np.unique(np.concatenate([d.index[d.edge] for d in vd.values()]))
    E_all = cands[edge_ids].astype(np.float64)
    plane_e, sd_e = assign_edges(E_all, cands, plane_of, planes)
    assigned = np.flatnonzero(plane_e >= 0)
    pick = assigned[np.unique(np.linspace(0, len(assigned) - 1, min(MAX_POINTS, len(assigned))).round().astype(int))] if len(assigned) else assigned
    ids = edge_ids[pick]
    P = E_all[pick]
    plane = plane_e[pick]
    in_plane = np.abs(sd_e[pick]) <= IN_PLANE_M
    vertical_edge = edge_directions(E_all, P, up).astype(bool)
    meta = {
        "photos": len(names),
        "wall_planes": len(planes),
        "edge_points_within_6m": len(edge_ids),
        "edge_points_on_a_wall": len(assigned),
        "points_sampled": len(ids),
        "in_plane": int(in_plane.sum()),
        "proud": int((~in_plane).sum()),
        "vertical_edges": int(vertical_edge.sum()),
        "up_vs_camera_up_deg": round(float(np.degrees(np.arccos(up @ camera_up))), 2),
        "wall_tilt_deg_max": round(float(max(np.degrees(np.arcsin(abs(n @ up))) for n, _ in planes)), 2),
        "oracle_scale_median": float(np.median([d.scale for d in vd.values()])),
    }
    print(f"{scene}: {meta}")
    if len(ids) < MIN_POINTS_TO_REPORT:
        return {"meta": meta, "skipped": f"{len(ids)} edge points on a wall within 6 m"}

    # Every (point, photo) where the photo sees the point within NEAR_M.
    obs = {"j": [], "view": [], "row": []}
    for vi, n in enumerate(names):
        rows = vd[n].find(ids)
        j = np.flatnonzero(rows >= 0)
        obs["j"].append(j)
        obs["view"].append(np.full(len(j), vi))
        obs["row"].append(rows[j])
    obs = {k: np.concatenate(v) for k, v in obs.items()}

    single = single_photo_methods(ids, P, plane, planes, frames, names, vd, obs, up)
    single["in_plane"] = in_plane[single["j"]]
    multi = multi_photo_methods(scene, ids, P, plane, planes, frames, names, vd, obs, up)
    for rec in (multi["exact"], multi["modern_assumed"], multi["tap"]):
        rec["in_plane"] = in_plane[rec["j"]]
        rec["vertical"] = vertical_edge[rec["j"]]
    single["laser_offset"] = np.abs(sd_e[pick])[single["j"]]
    meta["seconds"] = round(time.time() - t0)
    return {"meta": meta, "single": single, "multi": multi}


def single_photo_methods(ids, P, plane, planes, frames, names, vd, obs, up) -> dict:
    """M0, M1 and M1-anchor on every photo where the point is an edge."""
    out: dict[str, list] = {k: [] for k in (
        "j", "angle", "m0", "m1", "m1_offset", "m1a", "psi", "s", "pred_signed", "pred_mag",
    )}
    fits: dict[tuple[str, int], tuple[np.ndarray, float]] = {}
    for j, vi, row in zip(obs["j"], obs["view"], obs["row"], strict=True):
        d = vd[names[vi]]
        if not d.edge[row]:
            continue
        k = int(plane[j])
        n_k, F = planes[k][0], frames[k]
        p = P[j]
        ray = p - d.C
        angle = float(geom.view_angle_deg(ray[None], n_k)[0])
        R, C = d.T[:3, :3], d.C
        uv = d.uv[row]
        nan3 = np.full(3, np.nan)

        m0 = nan3
        if np.isfinite(d.depth[row]):
            m0 = F @ (R @ backproject(d.K, uv[None], d.depth[row : row + 1])[0] + C - p)

        m1, m1_offset = nan3, np.nan
        wall = d.walls.get(k)
        if wall is not None:
            if (d.name, k) not in fits:
                fits[(d.name, k)] = geom.fit_plane(wall.cam)
            n_c, d_c = fits[(d.name, k)]
            X = geom.ray_plane(np.zeros(3), cam_dirs(d.K, uv), n_c, d_c)[0]
            m1 = F @ (R @ X + C - p)
            m1_offset = abs(n_c @ (R.T @ (p - C)) - d_c)

        m1a, psi, s = nan3, np.nan, np.nan
        if wall is not None:
            s_edge = p @ F[0]
            for a in choose_anchors(wall, s_edge, F[0])[:ANCHOR_TRIES]:
                patch = np.linalg.norm(wall.world - wall.world[a], axis=1) <= PATCH_RADIUS_M
                if patch.sum() < MIN_PATCH_PX:
                    continue
                n_c, d_c = geom.fit_plane(wall.cam[patch])
                X = geom.ray_plane(np.zeros(3), cam_dirs(d.K, uv), n_c, d_c)[0]
                m1a = F @ (R @ X + C - p)
                psi = geom.signed_yaw(R @ n_c, n_k, up)
                s = s_edge - wall.world[a] @ F[0]
                break
        out["j"].append(j)
        out["angle"].append(angle)
        out["m0"].append(m0)
        out["m1"].append(m1)
        out["m1_offset"].append(m1_offset)
        out["m1a"].append(m1a)
        out["psi"].append(psi)
        out["s"].append(s)
        out["pred_signed"].append(float(geom.yaw_shift(s, psi, ray[None], n_k, up)[0]))
        out["pred_mag"].append(abs(s) * abs(psi) * np.tan(np.radians(angle)))
    return {k: np.array(v) for k, v in out.items()}


def load_poses(scene: str) -> dict[tuple[str, int], dict]:
    root = ETH3D_DIR / scene / "ar_poses"
    out = {}
    for setting, draw, fname in SETTINGS:
        groups = json.loads((root / fname).read_text())
        out[(setting, draw)] = {
            g: {m: np.array(T) for m, T in body["poses"].items()}
            for g, body in groups.items()
            if g.startswith("n8-")
        }
    return out


def multi_photo_methods(scene, ids, P, plane, planes, frames, names, vd, obs, up) -> dict:
    """M2 (tap, snap, median) and M3 (two photos) with each pose setting and draw."""
    poses = load_poses(scene)
    groups = poses[("exact", 0)]
    for (setting, draw), gp in poses.items():
        if gp.keys() != groups.keys():
            raise ValueError(f"{scene} {setting} draw {draw}: groups differ from exact")
    images = Images(scene)
    name_i = {n: i for i, n in enumerate(names)}

    # Photos seeing each point, and the one group of 8 per point that has the most of them.
    seen: dict[int, list[tuple[str, int]]] = {}
    for j, vi, row in zip(obs["j"], obs["view"], obs["row"], strict=True):
        seen.setdefault(int(j), []).append((names[vi], int(row)))
    plan = {}
    for j, photos in seen.items():
        best = None
        for g in sorted(groups):
            members = [(n, r) for n, r in photos if n in groups[g]]
            if len(members) >= 2 and (best is None or len(members) > len(best[1])):
                best = (g, members)
        if best is None:
            continue
        g, members = best
        p = P[j]
        dist = [np.linalg.norm(vd[n].C - p) for n, _ in members]
        flagged = [i for i, (n, r) in enumerate(members) if vd[n].edge[r]]
        ref = min(flagged or range(len(members)), key=lambda i: dist[i])
        rn = members[ref][0]
        second = None
        best_angle = np.inf if PAIRING == "narrowest" else -np.inf
        for n, r in members:
            if n == rn or np.linalg.norm(vd[n].C - vd[rn].C) < MIN_BASELINE_M:
                continue
            a, b = p - vd[rn].C, p - vd[n].C
            ang = np.degrees(np.arccos(np.clip(a @ b / np.linalg.norm(a) / np.linalg.norm(b), -1, 1)))
            better = ang < best_angle if PAIRING == "narrowest" else ang > best_angle
            if ang >= MIN_RAY_ANGLE_DEG and better:
                second, best_angle = (n, r), ang
        plan[j] = (g, members, ref, second)

    # Taps: one draw per (point, photo), shared by every pose setting. "snap" is M2; "tap" (no
    # snap) and "true" (the true pixel) were added after the run to locate M2's error.
    pixels: dict[str, dict] = {"snap": {}, "tap": {}, "true": {}}
    tap_err, snap_err, snap_true_err, tap_j = [], [], [], []
    for j, (_g, members, _ref, _second) in plan.items():
        for n, r in members:
            d = vd[n]
            rng = np.random.default_rng([SEED, int(ids[j]), name_i[n]])
            tap = d.uv[r] + rng.normal(0.0, TAP_SIGMA_PX, 2)
            snapped = np.array(imagematch.snap(images.edges(n), tap[0], tap[1], SNAP_RADIUS_PX))
            pixels["snap"][(j, n)] = snapped
            pixels["tap"][(j, n)] = tap
            pixels["true"][(j, n)] = d.uv[r]
            tap_err.append(abs(tap[0] - d.uv[r][0]))
            snap_err.append(abs(snapped[0] - d.uv[r][0]))
            from_true = imagematch.snap(images.edges(n), d.uv[r][0], d.uv[r][1], SNAP_RADIUS_PX)
            snap_true_err.append(abs(from_true[0] - d.uv[r][0]))
            tap_j.append(j)
    m2_angle = {
        j: float(np.median([geom.view_angle_deg((P[j] - vd[n].C)[None], planes[int(plane[j])][0])[0] for n, _ in members]))
        for j, (_g, members, _ref, _second) in plan.items()
    }

    world_planes: dict = {}

    def world_plane(key, g, k, gp):
        if key not in world_planes:
            pts = []
            for m in gp[g]:
                wall = vd[m].walls.get(k) if m in vd else None
                if wall is None:
                    continue
                sel = np.linspace(0, len(wall.cam) - 1, min(WORLD_PLANE_PX_PER_PHOTO, len(wall.cam))).astype(int)
                T = gp[g][m]
                pts.append(wall.cam[sel] @ T[:3, :3].T + T[:3, 3])
            n_pts = sum(len(x) for x in pts)
            world_planes[key] = geom.fit_plane(np.concatenate(pts)) if n_pts >= MIN_WALL_PX else None
        return world_planes[key]

    out = {}
    for (setting, draw), gp in poses.items():
        rec: dict[str, list] = {k: [] for k in (
            "j", "draw", "m2_frame", "m2_span", "m2_span_tap", "m2_span_true", "m2_frame_true",
            "m2_angle", "m2_offset", "m3_pair", "m3_ncc", "m3_px", "m3_frame",
            "m3_span", "m3_anchor",
        )}
        for j, (g, members, ref, second) in plan.items():
            k = int(plane[j])
            F, p = frames[k], P[j]
            rn, rrow = members[ref]
            wp = world_plane((setting, draw, g, k), g, k, gp)
            nan3 = np.full(3, np.nan)

            m2 = {src: (nan3, nan3) for src in pixels}
            m2_offset = np.nan
            wall = vd[rn].walls.get(k)
            if wp is not None:
                a_est, a_true = nan3, nan3
                if wall is not None:
                    for a in choose_anchors(wall, p @ F[0], F[0])[:1]:
                        a_hits = []
                        for n, _ in members:
                            row = vd[n].find(wall.ids[a : a + 1])[0]
                            if row < 0:
                                continue
                            o, dvec = imagematch.pixel_ray(vd[n].K, gp[g][n], vd[n].uv[row])
                            a_hits.append(F @ geom.ray_plane(o, dvec[None], *wp)[0])
                        a_ok = [x for x in a_hits if np.isfinite(x).all()]
                        a_est = np.median(a_ok, axis=0) if a_ok else nan3
                        a_true = F @ wall.world[a]
                for src, px in pixels.items():
                    hits = []
                    for n, _ in members:
                        o, dvec = imagematch.pixel_ray(vd[n].K, gp[g][n], px[(j, n)])
                        hits.append(F @ geom.ray_plane(o, dvec[None], *wp)[0])
                    ok_hits = [x for x in hits if np.isfinite(x).all()]
                    est = np.median(ok_hits, axis=0) if ok_hits else nan3
                    m2[src] = (est - F @ p, (est - a_est) - (F @ p - a_true))
                m2_offset = abs(wp[0] @ p - wp[1])

            m3_ncc, m3_px, m3_frame, m3_span, m3_anchor = np.nan, np.nan, nan3, nan3, False
            if second is not None:
                n2, row2 = second
                K1, K2 = vd[rn].K, vd[n2].K
                T1, T2 = gp[g][rn], gp[g][n2]
                img1, img2 = images.get(rn), images.get(n2)

                def triangulate(uv1, K1=K1, K2=K2, T1=T1, T2=T2, img1=img1, img2=img2):
                    uv2, score = imagematch.epipolar_match(
                        img1, img2, uv1, K1, T1, K2, T2, DEPTH_RANGE_M, NCC_PATCH
                    )
                    if score < NCC_MIN:
                        return None, uv2, score
                    o1, d1 = imagematch.pixel_ray(K1, T1, uv1)
                    o2, d2 = imagematch.pixel_ray(K2, T2, uv2)
                    X, _ = geom.midpoint_triangulate(o1, d1, o2, d2)
                    return (X if np.isfinite(X).all() else None), uv2, score

                X, uv2, m3_ncc = triangulate(vd[rn].uv[rrow])
                if X is not None:
                    m3_px = float(np.linalg.norm(uv2 - vd[n2].uv[row2]))
                    m3_frame = F @ (X - p)
                    wall = vd[rn].walls.get(k)
                    if wall is not None:
                        rows2 = vd[n2].find(wall.ids)
                        tried = 0
                        for a in choose_anchors(wall, p @ F[0], F[0]):
                            if rows2[a] < 0 or vd[n2].edge[rows2[a]]:
                                continue
                            tried += 1
                            if tried > ANCHOR_TRIES:
                                break
                            row_a = vd[rn].find(wall.ids[a : a + 1])[0]
                            A, _, _ = triangulate(vd[rn].uv[row_a])
                            if A is not None:
                                m3_span = F @ ((X - A) - (p - wall.world[a]))
                                m3_anchor = True
                                break
            rec["j"].append(j)
            rec["draw"].append(draw)
            rec["m2_frame"].append(m2["snap"][0])
            rec["m2_span"].append(m2["snap"][1])
            rec["m2_span_tap"].append(m2["tap"][1])
            rec["m2_span_true"].append(m2["true"][1])
            rec["m2_frame_true"].append(m2["true"][0])
            rec["m2_angle"].append(m2_angle[j])
            rec["m2_offset"].append(m2_offset)
            rec["m3_pair"].append(second is not None)
            rec["m3_ncc"].append(m3_ncc)
            rec["m3_px"].append(m3_px)
            rec["m3_frame"].append(m3_frame)
            rec["m3_span"].append(m3_span)
            rec["m3_anchor"].append(m3_anchor)
        out[(setting, draw)] = {k: np.array(v) for k, v in rec.items()}
        print(f"{scene}: {setting} draw {draw} done")
    merged = {"exact": out[("exact", 0)]}
    draws = [out[("modern_assumed", d)] for d in range(5)]
    merged["modern_assumed"] = {k: np.concatenate([x[k] for x in draws]) for k in draws[0]}
    merged["tap"] = {
        "tap_px": np.array(tap_err),
        "snap_px": np.array(snap_err),
        "snap_from_true_px": np.array(snap_true_err),
        "j": np.array(tap_j, int),
    }
    merged["plans"] = {
        "points_with_2_photos_in_a_group": len(plan),
        "photos_per_point_median": float(np.median([len(m) for _, m, _, _ in plan.values()])),
        "points_with_a_second_photo": sum(1 for *_, s in plan.values() if s is not None),
    }
    return merged


# ---------------------------------------------------------------- summaries


def components(vecs: np.ndarray) -> dict[str, np.ndarray]:
    """Wall-frame error vectors (h, n, w) to along-wall, out-of-plane and 3-D magnitudes."""
    v = np.atleast_2d(vecs)
    return {"along": v[:, 0], "out": v[:, 1], "3d": np.linalg.norm(v, axis=1)}


def summarise_single(s: dict) -> dict:
    bins = geom.angle_bin(np.clip(s["angle"], 0, 90))
    cats = {"all": np.ones(len(s["j"]), bool), "in_plane": s["in_plane"], "proud": ~s["in_plane"]}
    out: dict = {}
    for method in ("m0", "m1", "m1a"):
        comp = components(s[method])
        out[method] = {}
        for cat, cmask in cats.items():
            out[method][cat] = {}
            for b, label in [(-1, "all angles"), *enumerate(geom.ANGLE_LABELS)]:
                mask = cmask if b < 0 else cmask & (bins == b)
                out[method][cat][label] = {
                    c: stats.summary(x[mask], s["j"][mask]) for c, x in comp.items()
                } | {"missing": int((~np.isfinite(comp["3d"][mask])).sum())}
    # M1-anchor: yaw error and how well s psi tan(angle) explains the along-wall error.
    ok = np.isfinite(s["psi"]) & np.isfinite(s["m1a"]).all(axis=1)
    psi = np.degrees(np.abs(s["psi"][ok]))
    err = s["m1a"][ok, 0]
    pred = s["pred_signed"][ok]
    mag = s["pred_mag"][ok]
    ip = s["in_plane"][ok]
    ang = s["angle"][ok]
    if ok.sum() < 3:
        out["m1a_yaw"] = {"observations": int(ok.sum())}
        return out
    yaw: dict = {
        "observations": int(ok.sum()),
        "abs_psi_deg": {q: round(float(np.percentile(psi, p)), 2) for q, p in (("median", 50), ("p90", 90))},
        "abs_s_m_median": round(float(np.median(np.abs(s["s"][ok]))), 2),
    }
    for cat, m in (("all", np.ones(len(err), bool)), ("in_plane", ip)):
        e, pr, mg = err[m], pred[m], mag[m]
        if len(e) < 3:
            yaw[cat] = {"n": len(e)}
            continue
        ss = float(np.sum((e - e.mean()) ** 2))
        slope = float(pr @ e / (pr @ pr))
        r2 = 1 - float(np.sum((e - slope * pr) ** 2)) / ss
        big = mg > 0.01
        big1 = np.abs(pr) > 0.01
        low = ang[m] < 60
        pl, el = pr[low], e[low]
        slope60 = float(pl @ el / (pl @ pl))
        r2_60 = 1 - float(np.sum((el - slope60 * pl) ** 2) / np.sum((el - el.mean()) ** 2))
        yaw[cat + "_added_after_the_run"] = {
            "sign_agreement_where_prediction_over_1cm": round(float(np.mean(np.sign(e[big1]) == np.sign(pr[big1]))), 2),
            "median_error_over_prediction_where_over_1cm": round(float(np.median(e[big1] / pr[big1])), 2),
            "view_angle_under_60": {"n": int(low.sum()), "slope": round(slope60, 2), "r2": round(r2_60, 2)},
            "spearman_abs_error_vs_tan_angle_alone": round(spearman(np.abs(e), np.tan(np.radians(ang[m]))), 2),
        }
        yaw[cat] = {
            "n": int(m.sum()),
            "signed_first_order_slope": round(slope, 2),
            "signed_first_order_r2": round(r2, 2),
            "signed_r2_with_slope_1": round(1 - float(np.sum((e - pr) ** 2)) / ss, 2),
            "abs_error_over_s_psi_tan_median": round(float(np.median(np.abs(e[big]) / mg[big])), 2),
            "abs_error_over_s_psi_tan_n": int(big.sum()),
            "spearman_abs_error_vs_s_psi_tan": round(spearman(np.abs(e), mg), 2),
            "abs_error_p90_in": round(float(np.percentile(np.abs(e), 90)) / stats.M_PER_IN, 2),
            "residual_after_first_order_p90_in": round(
                float(np.percentile(np.abs(e - pr), 90)) / stats.M_PER_IN, 2
            ),
        }
    out["m1a_yaw"] = yaw
    ipm = s["in_plane"]
    out["added_after_the_run_edge_point_to_plane"] = {
        "laser_plane_in_plane_edges": stats.summary(s["laser_offset"][ipm], s["j"][ipm]),
        "m1_plane_in_plane_edges": stats.summary(s["m1_offset"][ipm], s["j"][ipm]),
    }
    return out


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def summarise_multi(m: dict) -> dict:
    out: dict = {}
    for setting in ("exact", "modern_assumed"):
        r = m[setting]
        cats = {
            "all": np.ones(len(r["j"]), bool),
            "in_plane": r["in_plane"],
            "proud": ~r["in_plane"],
            "in_plane_vertical": r["in_plane"] & r["vertical"],
        }
        bins = geom.angle_bin(np.clip(r["m2_angle"], 0, 90))
        res: dict = {}
        for cat, c in cats.items():
            span = components(r["m2_span"][c])
            frame = components(r["m2_frame"][c])
            pair = r["m3_pair"][c]
            matched = pair & (r["m3_ncc"][c] >= NCC_MIN) & np.isfinite(r["m3_frame"][c]).all(axis=1)
            m3_span = components(r["m3_span"][c])
            m3_frame = components(r["m3_frame"][c])
            j = r["j"][c]
            res[cat] = {
                "M2": {
                    "span_along": stats.summary(span["along"], j)
                    | {"p90_ci95_in": stats.bootstrap_p90(span["along"], j)},
                    "span_3d": stats.summary(span["3d"], j),
                    "frame_along": stats.summary(frame["along"], j),
                    "frame_3d": stats.summary(frame["3d"], j),
                },
                "M2_added_after_the_run": {
                    "span_along_tap_no_snap": stats.summary(r["m2_span_tap"][c][:, 0], j),
                    "span_along_true_pixel": stats.summary(r["m2_span_true"][c][:, 0], j),
                    "frame_along_true_pixel": stats.summary(r["m2_frame_true"][c][:, 0], j),
                    "edge_point_to_fused_plane": stats.summary(r["m2_offset"][c], j),
                    "span_along_by_median_view_angle": {
                        label: stats.summary(span["along"][bins[c] == b], j[bins[c] == b])
                        for b, label in enumerate(geom.ANGLE_LABELS)
                    },
                    "span_along_true_pixel_by_median_view_angle": {
                        label: stats.summary(r["m2_span_true"][c][bins[c] == b, 0], j[bins[c] == b])
                        for b, label in enumerate(geom.ANGLE_LABELS)
                    },
                },
                "M3": {
                    "pairs": int(pair.sum()),
                    "match_rate": round(float(matched.sum() / max(pair.sum(), 1)), 3),
                    "match_px": pct(r["m3_px"][c][matched]),
                    "frame_3d": stats.summary(m3_frame["3d"], j),
                    "frame_along": stats.summary(m3_frame["along"], j),
                    "span_along": stats.summary(m3_span["along"], j)
                    | {"p90_ci95_in": stats.bootstrap_p90(m3_span["along"], j)},
                    "span_3d": stats.summary(m3_span["3d"], j)
                    | {"p90_ci95_in": stats.bootstrap_p90(m3_span["3d"], j)},
                    "anchor_matched": int(r["m3_anchor"][c].sum()),
                    "added_after_the_run_frame_3d_where_match_within_2px": stats.summary(
                        m3_frame["3d"][matched & (r["m3_px"][c] <= 2.0)], j[matched & (r["m3_px"][c] <= 2.0)]
                    ),
                    "added_after_the_run_share_of_matches_within_2px": round(
                        float(np.mean(r["m3_px"][c][matched] <= 2.0)) if matched.any() else float("nan"), 3
                    ),
                },
            }
        out[setting] = res
    tap = m["tap"]
    out["tap_px"] = {
        "tap_abs_du": pct(tap["tap_px"]),
        "snap_abs_du": pct(tap["snap_px"]),
        "snap_within_1px": round(float(np.mean(tap["snap_px"] <= 1.0)), 3),
        "added_after_the_run_snap_from_true_pixel_abs_du": pct(tap["snap_from_true_px"]),
        "added_after_the_run_vertical_in_plane_edges": {
            k: pct(tap[k][tap["vertical"] & tap["in_plane"]])
            for k in ("tap_px", "snap_px", "snap_from_true_px")
        },
    }
    out["plans"] = m["plans"]
    return out


def pct(x: np.ndarray) -> dict:
    if not len(x):
        return {"n": 0}
    return {"n": len(x), "median": round(float(np.median(x)), 2), "p90": round(float(np.percentile(x, 90)), 2)}


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    suffix = "" if PAIRING == "narrowest" else "_widest_pair"
    report: dict = {
        "m3_pairing": PAIRING,
        "evals_commit": evals_commit(),
        "evals_dir": str(EVALS_DIR).replace(str(Path.home()), "~"),
        "parameters": {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float, tuple))},
        "scenes": {},
    }
    # `--from-raw` re-summarises the per-point records of the last full run (data/, git-ignored).
    for scene in SCENES:
        raw = HERE / "data" / f"raw_{scene}{suffix}.pkl"
        if "--from-raw" in sys.argv:
            res = pickle.loads(raw.read_bytes())
        else:
            res = run_scene(scene)
            raw.parent.mkdir(exist_ok=True)
            raw.write_bytes(pickle.dumps(res))
        if "skipped" in res:
            report["scenes"][scene] = res
            continue
        single, multi = res["single"], res["multi"]
        summary = {"meta": res["meta"], "single": summarise_single(single), "multi": summarise_multi(multi)}
        summary["criteria"] = grade(single, multi)
        report["scenes"][scene] = summary
    (RESULTS / f"edge_geometry{suffix}.json").write_text(json.dumps(report, indent=1, default=float) + "\n")
    (RESULTS / f"edge_geometry{suffix}.md").write_text(markdown(report))
    print((RESULTS / f"edge_geometry{suffix}.md").read_text())


def p90_in(x: np.ndarray) -> float:
    x = np.abs(x[np.isfinite(x)])
    return round(float(np.percentile(x, 90)) / stats.M_PER_IN, 2) if len(x) else float("nan")


def grade(single: dict, multi: dict) -> dict:
    bins = geom.angle_bin(np.clip(single["angle"], 0, 90))
    along = single["m0"][:, 0]
    near = p90_in(along[bins == 0])
    wide = p90_in(along[bins >= 2])
    per_bin = [p90_in(along[bins == b]) for b in range(len(geom.ANGLE_LABELS))]
    ip = single["in_plane"]
    m1 = p90_in(single["m1"][ip, 0])
    ma = multi["modern_assumed"]
    m2 = p90_in(ma["m2_span"][ma["in_plane"], 0])
    pair = ma["m3_pair"]
    matched = pair & (ma["m3_ncc"] >= NCC_MIN) & np.isfinite(ma["m3_frame"]).all(axis=1)
    rate = float(matched.sum() / max(pair.sum(), 1))
    m3 = p90_in(np.linalg.norm(ma["m3_span"], axis=1))
    return {
        "P1": {"m0_along_p90_in_0_15": near, "m0_along_p90_in_over_30": wide, "pass": bool(near <= 0.5 * wide)},
        "P2": {"m1_along_p90_in_in_plane": m1, "pass": bool(m1 <= 4.0)},
        "P3": {"m2_span_along_p90_in_in_plane_modern": m2, "pass": bool(m2 <= 2.0)},
        "P4": {"m3_span_3d_p90_in_modern": m3, "match_rate": round(rate, 3), "pass": bool(m3 <= 2.0 and rate >= 0.7)},
        "kill": {"m0_along_p90_in_per_bin": per_bin, "triggered": bool(all(x <= 4.0 for x in per_bin))},
    }


def fmt(x: dict | None, key: str = "p90_in") -> str:
    if not x or key not in x:
        return "n/a"
    return f"{x['median_in']:.1f} / {x[key]:.1f} ({x.get('points', x['n'])})"


def markdown(report: dict) -> str:
    L = [
        "# Edge geometry (generated by `uv run python run.py`)",
        "",
        f"Evals harness commit `{report['evals_commit']}`. Errors in inches, median / p90 (points). "
        f"M3 pairs each point's reference photo with the {report.get('m3_pairing', 'narrowest')} "
        f"qualifying ray angle.",
    ]
    for scene, r in report["scenes"].items():
        L += ["", f"## {scene}", "", f"`{json.dumps(r['meta'])}`"]
        if "skipped" in r:
            L += ["", f"Skipped: {r['skipped']}."]
            continue
        s = r["single"]
        for method, title in (("m0", "M0, depth at the pixel"), ("m1", "M1, ray to this photo's wall plane"), ("m1a", "M1-anchor, ray to a 0.3 m patch plane 1-3 m away")):
            L += ["", f"### {title}", "", "| Edges | View angle | Along the wall | Out of plane | 3-D |", "| --- | --- | --- | --- | --- |"]
            for cat in ("all", "in_plane", "proud"):
                for label, row in s[method][cat].items():
                    L.append(f"| {cat} | {label} | {fmt(row['along'])} | {fmt(row['out'])} | {fmt(row['3d'])} |")
        L += ["", "### M1-anchor yaw", "", f"`{json.dumps(s['m1a_yaw'])}`"]
        m = r["multi"]
        L += ["", "### M2 and M3", "", "| Poses | Edges | M2 span along | M2 p90 95% CI | M3 pairs | M3 match | M3 match px | M3 span along | M3 span 3-D | M3 3-D p90 95% CI | M3 frame 3-D |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for setting in ("exact", "modern_assumed"):
            for cat, row in m[setting].items():
                m2, m3 = row["M2"], row["M3"]
                L.append(
                    f"| {setting} | {cat} | {fmt(m2['span_along'])} | {m2['span_along'].get('p90_ci95_in')} | {m3['pairs']} | {m3['match_rate']:.0%} "
                    f"| {m3['match_px'].get('median', 'n/a')} / {m3['match_px'].get('p90', 'n/a')} | {fmt(m3['span_along'])} | {fmt(m3['span_3d'])} | {m3['span_3d'].get('p90_ci95_in')} | {fmt(m3['frame_3d'])} |"
                )
        L += ["", "M3 match px is in pixels, not inches.", "", "### Added after the run", ""]
        L += ["| Poses | Edges | M2 with the true pixel | M2 tap, no snap | Edge point to fused MoGe-2 plane | M3 3-D where the match is within 2 px | Matches within 2 px |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for setting in ("exact", "modern_assumed"):
            for cat, row in m[setting].items():
                d2, m3 = row["M2_added_after_the_run"], row["M3"]
                L.append(
                    f"| {setting} | {cat} | {fmt(d2['span_along_true_pixel'])} | {fmt(d2['span_along_tap_no_snap'])} | {fmt(d2['edge_point_to_fused_plane'])} "
                    f"| {fmt(m3['added_after_the_run_frame_3d_where_match_within_2px'])} | {m3['added_after_the_run_share_of_matches_within_2px']} |"
                )
        L += ["", "M2 along-wall span error by the point's median view angle (snap, then true pixel):", ""]
        for setting in ("exact", "modern_assumed"):
            d2 = m[setting]["in_plane"]["M2_added_after_the_run"]
            snapped = ", ".join(f"{b} {fmt(x)}" for b, x in d2["span_along_by_median_view_angle"].items())
            true = ", ".join(f"{b} {fmt(x)}" for b, x in d2["span_along_true_pixel_by_median_view_angle"].items())
            L += [f"- {setting}, in-plane, snapped: {snapped}", f"- {setting}, in-plane, true pixel: {true}"]
        L += ["", f"Edge point to plane (single photo): `{json.dumps(s.get('added_after_the_run_edge_point_to_plane'))}`"]
        L += ["", "", f"Tap and snap, horizontal pixel error: `{json.dumps(m['tap_px'])}`", "", f"`{json.dumps(m['plans'])}`"]
        L += ["", "### Criteria", "", f"`{json.dumps(r['criteria'])}`"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
