"""Shared helpers for A8 (metric scale without LiDAR).

Frames per scene:
  - "recon": a scale-free COLMAP model (A1 incremental for ETH3D, our own SfM for the barn).
  - "gt": the laser's metric frame (ETH3D DSLR calibration; T&T log x Barn_trans for the barn).
s_true = metres per recon unit, from a Sim(3) fit of recon camera centres to GT camera centres.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pycolmap

STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
HERE = Path(__file__).resolve().parent
HOUSES = STORE / "internal/data/houses"
WORK = Path.home() / "a8/work"
DATA = Path.home() / "a8/data"
sys.path.insert(0, str(STORE / "internal/eval-harness"))

IN = 0.0254
FT = 0.3048


@dataclass
class Cam:
    name: str
    R: np.ndarray  # world-to-camera (OpenCV)
    t: np.ndarray
    K: np.ndarray
    w: int
    h: int

    @property
    def C(self) -> np.ndarray:
        return -self.R.T @ self.t

    def ray(self, uv) -> np.ndarray:
        d = self.R.T @ np.linalg.solve(self.K, np.array([uv[0], uv[1], 1.0]))
        return d / np.linalg.norm(d)

    def project(self, X: np.ndarray):
        Xc = X @ self.R.T + self.t
        z = Xc[:, 2]
        uv = Xc[:, :2] / np.maximum(z[:, None], 1e-9) @ np.diag([self.K[0, 0], self.K[1, 1]]) + self.K[:2, 2]
        return uv, z


def _stem(name: str) -> str:
    return Path(name).stem


def cams_from_rec(rec: pycolmap.Reconstruction) -> dict[str, Cam]:
    out = {}
    for img in rec.images.values():
        if not img.has_pose:
            continue
        cfw = img.cam_from_world() if callable(img.cam_from_world) else img.cam_from_world
        cam = rec.cameras[img.camera_id]
        fx, fy, cx, cy = cam.params[:4] if cam.model.name == "PINHOLE" else (cam.params[0], cam.params[0], cam.params[1], cam.params[2])
        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])
        out[_stem(img.name)] = Cam(_stem(img.name), np.asarray(cfw.rotation.matrix()), np.asarray(cfw.translation), K, cam.width, cam.height)
    return out


def rec_points(rec: pycolmap.Reconstruction) -> np.ndarray:
    return np.array([p.xyz for p in rec.points3D.values()])


def sim3(src: np.ndarray, dst: np.ndarray):
    """Umeyama: dst ~ s R src + t."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt
    s = np.trace(np.diag(S) @ D) / (a ** 2).sum(1).mean()
    return s, R, md - s * R @ ms


# ---------------------------------------------------------------- ETH3D


def eth3d_gt_cams(scene: str) -> dict[str, Cam]:
    from eth3d.gt_common import read_cameras

    out = {}
    # The store copy of images.txt has its POINTS2D lines stripped, which breaks read_cameras.
    for n, R, t, K, w, h in read_cameras(DATA / "eth3d" / scene / "dslr_calibration_undistorted"):
        f = 2048.0 / w  # store frames are the undistorted DSLR images resized to 2048 px wide
        Km = np.array([[K[0] * f, 0, (K[2] + 0.5) * f - 0.5], [0, K[1] * f, (K[3] + 0.5) * f - 0.5], [0, 0, 1.0]])
        out[n] = Cam(n, R, t, Km, 2048, int(round(h * f)))
    return out


def eth3d_laser(scene: str, voxel: float = 0.01) -> np.ndarray:
    cache = WORK / scene / f"laser_{int(voxel * 1000)}mm.npy"
    if cache.exists():
        return np.load(cache)
    from eth3d.gt_common import load_scans, voxel_downsample

    P = load_scans(DATA / "eth3d" / scene / "dslr_scan_eval", np.full(3, -1e9), np.full(3, 1e9))
    P = voxel_downsample(P, voxel)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, P)
    return P


def image_path(scene: str, stem: str) -> Path:
    if scene == "barn":  # annotate the undistorted images the barn model uses
        return WORK / "barn/undistorted/images" / f"{stem}.jpg"
    h = {"facade": "eth3d-facade", "electro": "eth3d-electro"}[scene]
    d = HOUSES / h / "frames"
    for ext in (".jpg", ".JPG"):
        if (d / f"{stem}{ext}").exists():
            return d / f"{stem}{ext}"
    raise FileNotFoundError(stem)


# ---------------------------------------------------------------- barn


def barn_gt_centres() -> dict[str, np.ndarray]:
    calib = HOUSES / "tt-barn/calibration"
    trans = np.loadtxt(calib / "Barn_trans.txt")
    rows = [ln.split() for ln in (calib / "Barn_COLMAP_SfM.log").read_text().splitlines() if ln.strip()]
    out = {}
    for k in range(0, len(rows), 5):
        out[f"{int(rows[k][0]) + 1:06d}"] = (trans @ np.array(rows[k + 1:k + 5], float))[:3, 3]
    return out


def barn_laser(voxel: float = 0.01) -> np.ndarray:
    cache = WORK / "barn" / f"laser_{int(voxel * 1000)}mm.npy"
    if cache.exists():
        return np.load(cache)
    from eth3d.gt_common import voxel_downsample
    from evalharness.sceneio import read_ply

    d = read_ply(DATA / "tt" / "Barn.ply")
    P = voxel_downsample(np.c_[d["x"], d["y"], d["z"]].astype(float), voxel)
    np.save(cache, P)
    return P


# ---------------------------------------------------------------- scene bundle


@dataclass
class Scene:
    name: str
    rec_cams: dict[str, Cam]
    rec_pts: np.ndarray
    gt_cams: dict[str, Cam]  # metric, laser frame
    laser: np.ndarray | None
    s_true: float  # metres per recon unit
    R: np.ndarray  # recon -> gt rotation
    t: np.ndarray
    sim3_rms_m: float


def load_scene(name: str, laser: bool = True) -> Scene:
    if name in ("facade", "electro"):
        rec = pycolmap.Reconstruction(str(WORK / name / "inc"))
        rc = cams_from_rec(rec)
        gc = eth3d_gt_cams(name)
        common = sorted(set(rc) & set(gc))
        s, R, t = sim3(np.array([rc[n].C for n in common]), np.array([gc[n].C for n in common]))
        L = eth3d_laser(name) if laser else None
    elif name == "barn":
        rec = pycolmap.Reconstruction(str(WORK / "barn/undistorted/sparse"))
        rc = cams_from_rec(rec)
        centres = barn_gt_centres()
        common = sorted(set(rc) & set(centres))
        s, R, t = sim3(np.array([rc[n].C for n in common]), np.array([centres[n] for n in common]))
        # GT-frame cameras: our intrinsics and rotations, T&T metric centres (laser frame).
        gc = {}
        for n in common:
            Rg = rc[n].R @ R.T
            gc[n] = Cam(n, Rg, -Rg @ centres[n], rc[n].K, rc[n].w, rc[n].h)
        L = None  # Barn.ply download is blocked (Google Drive quota); truth = our model x s_true
    else:
        raise KeyError(name)
    src = np.array([rc[n].C for n in common])
    dst = np.array([gc[n].C for n in common])
    rms = float(np.sqrt((((s * src @ R.T + t) - dst) ** 2).sum(1).mean()))
    return Scene(name, rc, rec_points(rec), gc, L, float(s), R, t, rms)


def to_gt(sc: Scene, X: np.ndarray) -> np.ndarray:
    return sc.s_true * X @ sc.R.T + sc.t


# ---------------------------------------------------------------- measurement on a plane


def fit_plane(P: np.ndarray, thr: float, iters: int = 400, seed: int = 0):
    """RANSAC plane n.x = d; returns (n, d, inlier_count)."""
    rng = np.random.default_rng(seed)
    best = (None, None, -1, None)
    if len(P) < 3:
        return None, None, -1
    for _ in range(iters):
        a, b, c = P[rng.choice(len(P), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-12:
            continue
        n /= np.linalg.norm(n)
        inl = np.abs(P @ n - n @ a) < thr
        if inl.sum() > best[2]:
            best = (n, n @ a, int(inl.sum()), inl)
    n, d, k, inl = best
    if n is None:
        return None, None, -1
    Q = P[inl]
    c = Q.mean(0)
    n = np.linalg.svd(Q - c, full_matrices=False)[2][-1]
    return n, float(n @ c), k


def region_points(cam: Cam, P: np.ndarray, uv_poly: np.ndarray, zmax: float | None = None):
    """Points of P that project inside the convex quad/polygon uv_poly (Nx2) in front of cam."""
    from matplotlib.path import Path as MPath

    uv, z = cam.project(P)
    m = z > 0
    if zmax is not None:
        m &= z < zmax
    inside = np.zeros(len(P), bool)
    inside[m] = MPath(uv_poly).contains_points(uv[m])
    return P[inside], z[inside]


def segment_band(a, b, lateral: float = 0.25, ext: float = 0.1, min_px: float = 40.0) -> np.ndarray:
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    L = np.linalg.norm(d)
    u = d / L
    v = np.array([-u[1], u[0]])
    w = max(lateral * L, min_px)
    e = ext * L
    return np.array([a - e * u - w * v, b + e * u - w * v, b + e * u + w * v, a - e * u + w * v])


def ray_plane(C, r, n, d):
    lam = (d - n @ C) / (n @ r)
    return C + lam * r


def measure_segment(cam: Cam, P: np.ndarray, a_uv, b_uv, rel_thr: float = 0.004, band=None):
    """Length between two clicked pixels, by intersecting their rays with a RANSAC plane fitted to
    the points of P (any frame/scale) that project into a band around the segment."""
    poly = segment_band(a_uv, b_uv) if band is None else band
    Q, z = region_points(cam, P, poly)
    if len(Q) < 8:
        return None
    # Occlusion: keep the front-most point per 8 px cell (SfM/laser clouds include surfaces hidden behind
    # the wall, e.g. the far side of a building on a 360 loop), then the near layer only.
    uv, _ = cam.project(Q)
    key = (uv[:, 1] // 8).astype(np.int64) * 100000 + (uv[:, 0] // 8).astype(np.int64)
    order = np.lexsort((z, key))
    key, z, Q = key[order], z[order], Q[order]
    first = np.r_[True, key[1:] != key[:-1]]
    Q, z = Q[first], z[first]
    if len(Q) < 8:
        return None
    zm = np.percentile(z, 25)
    keep = np.abs(z - zm) < 0.25 * zm
    Q, z = Q[keep], z[keep]
    zm = np.median(z)
    if len(Q) > 20000:
        Q = Q[np.random.default_rng(0).choice(len(Q), 20000, replace=False)]
    n, d, k = fit_plane(Q, rel_thr * zm)
    if n is None or k < 6:
        return None
    A = ray_plane(cam.C, cam.ray(a_uv), n, d)
    B = ray_plane(cam.C, cam.ray(b_uv), n, d)
    return {"length": float(np.linalg.norm(B - A)), "A": A, "B": B, "n_pts": int(len(Q)), "inliers": int(k),
            "depth": float(np.linalg.norm((A + B) / 2 - cam.C)), "plane": (n, d)}


def line_separation(cam: Cam, plane, la, lb, A_uv, B_uv) -> float:
    """3D distance between two image lines back-projected onto a plane: from the point of line a nearest
    the measured segment to line b, perpendicular to b (in 3D, so perspective does not tilt it)."""
    n, d = plane

    def on_plane(uv):
        return ray_plane(cam.C, cam.ray(uv), n, d)

    b0, b1 = on_plane(lb["point"] - 60 * lb["dir"]), on_plane(lb["point"] + 60 * lb["dir"])
    u = (b1 - b0) / np.linalg.norm(b1 - b0)
    pa = on_plane(A_uv)
    v = pa - b0
    return float(np.linalg.norm(v - (v @ u) * u))


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
