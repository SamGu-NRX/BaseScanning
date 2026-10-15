"""Metric geometry backend for the tool-augmented VLM judge.

A Geometry is a metric point cloud (metres, world frame), an up vector, a ground plane and
per-frame pinhole cameras (COLMAP world-to-camera convention). Any Track A output that gives
points plus poses can be loaded into it; `load_eth3d_facade()` builds the laser-scan version.
All tool coordinates exchanged with the model are packet-photo pixels normalised to 0-1000.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import vlm

M_PER_FT = 0.3048
BATTERY_W_M = 31 * 0.0254
BATTERY_D_M = 22 * 0.0254
BATTERY_H_M = 36 * 0.0254


@dataclass
class Camera:
    frame: str
    K: np.ndarray  # 3x3, original frame pixels
    R: np.ndarray  # world -> camera
    t: np.ndarray
    w: int
    h: int

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t


@dataclass
class Geometry:
    name: str
    points: np.ndarray  # (N, 3) float32, metres
    up: np.ndarray
    ground_d: float  # ground plane: up . X = ground_d
    cameras: dict[str, Camera]
    source: str  # "laser_scan_ground_truth" or a Track A path id
    anchors: dict[str, np.ndarray] = field(default_factory=dict)  # e.g. homeowner meter tap

    def height(self, X: np.ndarray) -> np.ndarray:
        return X @ self.up - self.ground_d

    def plan_axes(self) -> tuple[np.ndarray, np.ndarray]:
        a = np.array([1.0, 0, 0]) - self.up[0] * self.up
        a /= np.linalg.norm(a)
        return a, np.cross(self.up, a)


def qvec_to_rot(q) -> np.ndarray:
    w, x, y, z = q
    return np.array([
        [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
        [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
        [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y],
    ])


def read_colmap_text(calib: Path) -> dict[str, Camera]:
    cams = {}
    for line in (calib / "cameras.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        p = line.split()
        fx, fy, cx, cy = map(float, p[4:8])
        cams[p[0]] = (np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]]), int(p[2]), int(p[3]))
    out = {}
    lines = [l for l in (calib / "images.txt").read_text().splitlines() if not l.startswith("#")]
    for line in lines:
        p = line.split()
        if len(p) < 10 or not p[-1].lower().endswith((".jpg", ".png", ".jpeg")):
            continue
        K, w, h = cams[p[8]]
        out[p[9]] = Camera(p[9], K, qvec_to_rot(list(map(float, p[1:5]))), np.array(list(map(float, p[5:8]))), w, h)
    return out


def _read_ply_xyz(path: Path) -> np.ndarray:
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
    n = int(re.search(rb"element vertex (\d+)", header).group(1))
    props = re.findall(rb"property (\w+) (\w+)", header.split(b"element vertex")[1].split(b"element")[0])
    stride = sum({b"float": 4, b"double": 8, b"uchar": 1, b"int": 4}[t] for t, _ in props)
    raw = np.memmap(path, dtype=np.uint8, mode="r", offset=len(header), shape=(n, stride))
    return np.frombuffer(np.ascontiguousarray(raw[:, :12]).tobytes(), dtype="<f4").reshape(n, 3)


def _voxel_down(X: np.ndarray, voxel: float) -> np.ndarray:
    key = np.floor(X / voxel).astype(np.int64)
    _, idx = np.unique(key, axis=0, return_index=True)
    return X[idx]


def _ransac_plane(X, thr, iters, rng):
    best, best_n, best_d = 0, None, None
    for _ in range(iters):
        s = X[rng.choice(len(X), 3, replace=False)]
        n = np.cross(s[1] - s[0], s[2] - s[0])
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        if abs(n[2]) < 0.95:
            continue
        d = n @ s[0]
        c = int((np.abs(X @ n - d) < thr).sum())
        if c > best:
            best, best_n, best_d = c, n, d
    m = np.abs(X @ best_n - best_d) < thr
    P = X[m]
    c = P.mean(0)
    n = np.linalg.svd(P - c, full_matrices=False)[2][2]
    return n, float(n @ c)


def load_eth3d_facade(scan_dir: Path = Path.home() / "data/eth3d/facade/dslr_scan_eval") -> Geometry:
    house = "eth3d-facade"
    cache = vlm.CACHE / house / "laser_geometry.npz"
    cams = read_colmap_text(vlm.DATA / "houses" / house / "calibration")
    ann = json.loads((vlm.C3 / "support/pr13-eth3d-facade/annotations.json").read_text())
    if cache.exists():
        z = np.load(cache)
        P, up, gd, meter = z["P"], z["up"], float(z["gd"]), z["meter"]
    else:
        mats = {}
        txt = (scan_dir / "scan_alignment.mlp").read_text()
        for name, body in re.findall(r'filename="([^"]+)".*?<MLMatrix44>(.*?)</MLMatrix44>', txt, re.S):
            mats[name] = np.array(body.split(), dtype=float).reshape(4, 4)
        C = np.array([c.center for c in cams.values()])
        lo, hi = C.min(0) - 25, C.max(0) + 25
        parts = []
        for name, T in mats.items():
            X = _read_ply_xyz(scan_dir / name).astype(np.float64)
            X = X @ T[:3, :3].T + T[:3, 3]
            m = np.all((X > lo) & (X < hi), axis=1)
            parts.append(_voxel_down(X[m], 0.03).astype(np.float32))
        P = _voxel_down(np.concatenate(parts).astype(np.float64), 0.03).astype(np.float32)
        rng = np.random.default_rng(0)
        (x0, y0), (x1, y1) = ann["ground_fit_scan_xy_m"]
        G = P[(P[:, 0] > x0) & (P[:, 0] < x1) & (P[:, 1] > y0) & (P[:, 1] < y1)].astype(np.float64)
        zlow = np.percentile(G[:, 2], 20)
        G = G[np.abs(G[:, 2] - zlow) < 0.6]
        n, d = _ransac_plane(G[rng.choice(len(G), min(len(G), 200000), replace=False)], 0.03, 600, rng)
        if n[2] < 0:
            n, d = -n, -d
        up, gd = n, d
        # invented meter (PR #13): on w1, 42.1 ft from its left end, 5 ft up
        a, b = (np.array(p) for p in ann["walls"][0]["rough_scan_xy_m"])
        seg = b - a
        L = np.linalg.norm(seg)
        near = P[:, :2].astype(np.float64)
        tt = np.clip((near - a) @ seg / L**2, 0, 1)
        dist = np.linalg.norm(near - (a + np.outer(tt, seg)), axis=1)
        H = P.astype(np.float64) @ up - gd
        W = P[(dist < 0.5) & (H > 0.3) & (H < 2.5)][:, :2].astype(np.float64)
        c = W.mean(0)
        dirv = np.linalg.svd(W - c, full_matrices=False)[2][0]
        if dirv @ seg < 0:
            dirv = -dirv
        proj = (W - c) @ dirv
        left = c + dirv * proj.min()
        foot2 = left + dirv * 42.1 * M_PER_FT
        foot = np.array([foot2[0], foot2[1], 0.0])
        foot[2] = (gd - up[0] * foot[0] - up[1] * foot[1]) / up[2]
        meter = foot + up * 5 * M_PER_FT
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, P=P, up=up, gd=gd, meter=meter)
    return Geometry(house, P, up, gd, cams, "laser_scan_ground_truth", {"electric_meter": meter})


class SceneTools:
    """The measurement and render tools a VLM can call, bound to one house's packet photos."""

    def __init__(self, geo: Geometry, photos: list[vlm.Photo]):
        self.geo = geo
        self.photos = {p.index: p for p in photos}
        self.marks: dict[str, np.ndarray] = {}
        self._depth: dict[int, tuple[np.ndarray, float]] = {}

    # ---- internals ----
    def _cam(self, idx: int) -> Camera:
        return self.geo.cameras[self.photos[idx].frame]

    def _depth_buffer(self, idx: int) -> tuple[np.ndarray, float]:
        if idx in self._depth:
            return self._depth[idx]
        cam = self._cam(idx)
        s = 0.5 * self.photos[idx].scale  # depth buffer at half the packet resolution
        w, h = int(cam.w * s), int(cam.h * s)
        Xc = self.geo.points.astype(np.float64) @ cam.R.T + cam.t
        z = Xc[:, 2]
        m = z > 0.3
        Xc, z = Xc[m], z[m]
        u = (cam.K[0, 0] * Xc[:, 0] / z + cam.K[0, 2]) * s
        v = (cam.K[1, 1] * Xc[:, 1] / z + cam.K[1, 2]) * s
        ui, vi = u.astype(np.int64), v.astype(np.int64)
        ok = (ui >= 0) & (ui < w) & (vi >= 0) & (vi < h)
        ui, vi, z = ui[ok], vi[ok], z[ok]
        buf = np.full((h, w), np.inf, dtype=np.float32)
        order = np.argsort(-z)
        buf[vi[order], ui[order]] = z[order]
        self._depth[idx] = (buf, s)
        return buf, s

    def _pixel_to_world(self, idx: int, x: float, y: float) -> tuple[np.ndarray | None, str]:
        if idx not in self.photos:
            return None, f"no photo {idx}"
        if not self.geo.cameras.get(self.photos[idx].frame):
            return None, f"photo {idx} has no camera pose"
        cam = self._cam(idx)
        buf, s = self._depth_buffer(idx)
        u = x / 1000 * cam.w
        v = y / 1000 * cam.h
        bu, bv = int(u * s), int(v * s)
        r = 4
        win = buf[max(0, bv - r) : bv + r + 1, max(0, bu - r) : bu + r + 1]
        vals = win[np.isfinite(win)]
        if len(vals) < 3:
            return None, "no reconstructed geometry at that pixel (unobserved or sky)"
        z = float(np.percentile(vals, 25))
        ray = np.linalg.inv(cam.K) @ np.array([u, v, 1.0])
        Xc = ray * z
        return cam.R.T @ (Xc - cam.t), "ok"

    def _describe(self, X: np.ndarray) -> dict:
        a, b = self.geo.plan_axes()
        out = {
            "height_above_ground_ft": round(float(self.geo.height(X[None])[0]) / M_PER_FT, 2),
            "plan_xy_ft": [round(float(X @ a) / M_PER_FT, 2), round(float(X @ b) / M_PER_FT, 2)],
        }
        m = self.geo.anchors.get("electric_meter")
        if m is not None:
            d = X - m
            dh = d - (d @ self.geo.up) * self.geo.up
            out["horizontal_dist_to_meter_ft"] = round(float(np.linalg.norm(dh)) / M_PER_FT, 2)
        return out

    def _resolve(self, ref: dict) -> tuple[np.ndarray | None, str]:
        if "mark" in ref:
            if ref["mark"] in self.marks:
                return self.marks[ref["mark"]], "ok"
            if ref["mark"] in self.geo.anchors:
                return self.geo.anchors[ref["mark"]], "ok"
            return None, f"unknown mark {ref['mark']}"
        return self._pixel_to_world(int(ref["photo"]), float(ref["x"]), float(ref["y"]))

    # ---- tools ----
    def scene_info(self) -> dict:
        a, b = self.geo.plan_axes()
        cams = {}
        for i, p in self.photos.items():
            c = self.geo.cameras.get(p.frame)
            if c is None:
                continue
            C = c.center
            fwd = c.R[2]
            fwd_p = fwd - (fwd @ self.geo.up) * self.geo.up
            ang = math.degrees(math.atan2(fwd_p @ b, fwd_p @ a))
            cams[f"photo {i}"] = {"plan_xy_ft": [round(float(C @ a) / M_PER_FT, 1), round(float(C @ b) / M_PER_FT, 1)],
                                  "height_ft": round(float(self.geo.height(C[None])[0]) / M_PER_FT, 1),
                                  "heading_deg": round(ang)}
        info = {
            "geometry_source": self.geo.source,
            "units": "feet; plan x/y are horizontal axes, heights are above the fitted ground plane",
            "cameras": cams,
            "anchors": {k: self._describe(v) for k, v in self.geo.anchors.items()},
            "notes": "Pixel coordinates are normalised 0-1000 in x (left to right) and y (top to bottom) of the packet photo. "
                     "A pixel with no geometry means that surface was not reconstructed (unobserved).",
        }
        return info

    def locate(self, photo: int, x: float, y: float, name: str | None = None) -> dict:
        X, msg = self._pixel_to_world(photo, x, y)
        if X is None:
            return {"ok": False, "reason": msg}
        cam = self._cam(photo)
        out = {"ok": True, **self._describe(X), "range_from_camera_ft": round(float(np.linalg.norm(X - cam.center)) / M_PER_FT, 2)}
        if name:
            self.marks[name] = X
            out["saved_as"] = name
        return out

    def measure(self, a: dict, b: dict) -> dict:
        A, ma = self._resolve(a)
        B, mb = self._resolve(b)
        if A is None or B is None:
            return {"ok": False, "reason": f"a: {ma}; b: {mb}"}
        d = B - A
        dv = float(d @ self.geo.up)
        dh = float(np.linalg.norm(d - dv * self.geo.up))
        return {"ok": True, "distance_ft": round(float(np.linalg.norm(d)) / M_PER_FT, 2),
                "horizontal_ft": round(dh / M_PER_FT, 2), "vertical_ft": round(dv / M_PER_FT, 2)}

    def _wall_frame(self, X: np.ndarray, toward: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Foot point on the ground at the wall, along-wall direction and outward normal (plan)."""
        P = self.geo.points
        up = self.geo.up
        d = P - X
        dh = d - np.outer(d @ up, up)
        H = self.geo.height(P)
        m = (np.linalg.norm(dh, axis=1) < 0.8) & (H > 0.3) & (H < 2.5)
        if m.sum() < 30:
            return None
        W = P[m].astype(np.float64)
        Wp = W - np.outer(W @ up, up)
        c = Wp.mean(0)
        _, sv, vt = np.linalg.svd(Wp - c, full_matrices=False)
        along = vt[0] - (vt[0] @ up) * up
        along /= np.linalg.norm(along)
        normal = np.cross(up, along)
        if normal @ (toward - c) < 0:
            normal = -normal
        Xp = X - (X @ up) * up
        foot_p = c + along * ((Xp - c) @ along)
        foot = foot_p + up * self.geo.ground_d
        return foot, along, normal

    def facing_clearance(self, photo: int, x: float, y: float) -> dict:
        """From a wall point, the open horizontal distance straight out to the first obstruction 1-6 ft high."""
        X, msg = self._pixel_to_world(photo, x, y)
        if X is None:
            return {"ok": False, "reason": msg}
        wf = self._wall_frame(X, self._cam(photo).center)
        if wf is None:
            return {"ok": False, "reason": "no wall surface found near that point"}
        foot, along, normal = wf
        P = self.geo.points.astype(np.float64)
        d = P - foot
        out_d = d @ normal
        side = d @ along
        H = self.geo.height(P)
        m = (out_d > 0.3) & (np.abs(side) < BATTERY_W_M / 2 + 0.15) & (H > 0.3) & (H < 1.83)
        if not m.any():
            seen = (out_d > 0.3) & (np.abs(side) < 1.0) & (H < 0.2)
            far = float(out_d[seen].max()) / M_PER_FT if seen.any() else 0.0
            return {"ok": True, "facing_clearance_ft": None, "open_ground_observed_to_ft": round(far, 1),
                    "note": "no obstruction found in the reconstructed geometry; clearance is at least the observed ground extent"}
        return {"ok": True, "facing_clearance_ft": round(float(out_d[m].min()) / M_PER_FT, 2)}

    def top_down(self, center: dict | None = None, radius_ft: float = 40.0) -> Image.Image:
        a, b = self.geo.plan_axes()
        P = self.geo.points
        H = self.geo.height(P)
        if center:
            C, _ = self._resolve(center)
        else:
            C = None
        if C is None:
            C = np.mean([self._cam(i).center for i in self.photos if self.photos[i].frame in self.geo.cameras], axis=0)
        cx, cy = float(C @ a), float(C @ b)
        R = radius_ft * M_PER_FT
        S = 900
        px = lambda X: ((X @ a - cx + R) / (2 * R) * S, (cy + R - X @ b) / (2 * R) * S)
        img = Image.new("RGB", (S, S + 40), "white")
        u, v = px(P)
        ok = (u >= 0) & (u < S) & (v >= 0) & (v < S)
        arr = np.array(img)
        for lo, hi, col in ((-1, 0.25, (205, 205, 205)), (2.5, 50, (140, 150, 200)), (0.25, 2.5, (30, 30, 30))):
            m = ok & (H >= lo) & (H < hi)
            arr[v[m].astype(int), u[m].astype(int)] = col
        img = Image.fromarray(arr)
        dr = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        for i, p in self.photos.items():
            c = self.geo.cameras.get(p.frame)
            if c is None:
                continue
            x0, y0 = px(c.center)
            f = c.R[2]
            x1, y1 = px(c.center + 1.5 * (f - (f @ self.geo.up) * self.geo.up))
            dr.line([x0, y0, x1, y1], fill=(0, 120, 0), width=2)
            dr.ellipse([x0 - 5, y0 - 5, x0 + 5, y0 + 5], outline=(0, 120, 0), width=2)
            dr.text((x0 + 6, y0 - 6), str(i), fill=(0, 100, 0), font=font)
        for name, X in list(self.geo.anchors.items()) + list(self.marks.items()):
            x0, y0 = px(X)
            dr.rectangle([x0 - 4, y0 - 4, x0 + 4, y0 + 4], fill=(220, 0, 0))
            dr.text((x0 + 6, y0 + 2), name, fill=(200, 0, 0), font=font)
        bar = 10 * M_PER_FT / (2 * R) * S
        dr.line([20, S + 20, 20 + bar, S + 20], fill="black", width=3)
        dr.text((25 + bar, S + 14), "10 ft", fill="black", font=font)
        dr.text((140, S + 14), "top-down: black = geometry 0.25-2.5 m high (walls, objects), light grey = ground, blue = above 2.5 m; green = cameras (photo numbers), red = marks. Unscanned = white.", fill="black", font=font)
        return img

    def render_battery(self, photo: int, x: float, y: float) -> tuple[Image.Image | None, dict]:
        """Draw a 31x22x36 in battery flush to the wall at the pixel, plus 3 ft and 5 ft ground offsets."""
        X, msg = self._pixel_to_world(photo, x, y)
        if X is None:
            return None, {"ok": False, "reason": msg}
        cam = self._cam(photo)
        wf = self._wall_frame(X, cam.center)
        if wf is None:
            return None, {"ok": False, "reason": "no wall surface found near that point"}
        foot, along, normal = wf
        up = self.geo.up
        hw = BATTERY_W_M / 2

        def box(o0, o1, s0, s1, h):
            return [foot + along * s + normal * o + up * z for z in (0, h) for (s, o) in ((s0, o0), (s1, o0), (s1, o1), (s0, o1))]

        ph = self.photos[photo]
        img = Image.open(ph.path).convert("RGB")
        dr = ImageDraw.Draw(img)
        sc = ph.scale

        def proj(Xw):
            Xc = cam.R @ Xw + cam.t
            if Xc[2] <= 0.1:
                return None
            return ((cam.K[0, 0] * Xc[0] / Xc[2] + cam.K[0, 2]) * sc, (cam.K[1, 1] * Xc[1] / Xc[2] + cam.K[1, 2]) * sc)

        def draw_poly(pts, col, width, closed=True):
            q = [proj(p) for p in pts]
            if any(v is None for v in q):
                return
            dr.line(q + ([q[0]] if closed else []), fill=col, width=width)

        corners = box(0, BATTERY_D_M, -hw, hw, BATTERY_H_M)
        draw_poly(corners[:4], (255, 40, 40), 3)
        draw_poly(corners[4:], (255, 40, 40), 3)
        for i in range(4):
            draw_poly([corners[i], corners[i + 4]], (255, 40, 40), 3, closed=False)
        for off, col in ((3 * M_PER_FT, (255, 200, 0)), (5 * M_PER_FT, (0, 200, 255))):
            ring = box(0, BATTERY_D_M + off, -hw - off, hw + off, 0)[:4]
            draw_poly(ring, col, 2)
        fc = self.facing_clearance(photo, x, y)
        return img, {"ok": True, "battery_center": self._describe(foot + normal * BATTERY_D_M / 2),
                     "legend": "red = battery 31 in W x 22 in D x 36 in H flush to the wall; yellow = 3 ft ground offset; cyan = 5 ft ground offset",
                     "facing_clearance": fc}
