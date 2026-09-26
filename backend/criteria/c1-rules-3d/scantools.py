"""ETH3D laser-scan helpers: load and align scans, voxel-thin, fit planes, render plan views.

Reading follows PR #13's build_scene.py (verification/scenes/eth3d-facade): each scan is a binary
PLY whose first element is float x, y, z vertices, placed by the 4x4 in scan_alignment.mlp.
Units in the scan frame are metres, z roughly up.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

ETH3D = Path("/home/ubuntu/eth3d")
M_PER_FT = 0.3048


def read_alignment(path: Path) -> dict[str, np.ndarray]:
    txt = path.read_text()
    return {
        m.group(1): np.array(m.group(2).split(), float).reshape(4, 4)
        for m in re.finditer(r'filename="([^"]+)">\s*<MLMatrix44>([^<]+)<', txt)
    }


def load_scan(scene: str, roi_xy=None, voxel_m: float | None = 0.02) -> np.ndarray:
    """All laser points of an ETH3D scene in the common scan frame (m), optionally cropped to a
    plan box [[x0, y0], [x1, y1]] and thinned to one point per voxel."""
    scan_dir = ETH3D / scene / "dslr_scan_eval"
    out = []
    for name, T in read_alignment(scan_dir / "scan_alignment.mlp").items():
        with open(scan_dir / name, "rb") as f:
            header = b""
            while not header.endswith(b"end_header\n"):
                header += f.readline()
        n = int(re.search(rb"element vertex (\d+)", header).group(1))
        X = np.memmap(scan_dir / name, dtype="<f4", mode="r", offset=len(header), shape=(n, 3))
        for a in range(0, n, 3_000_000):
            x = np.asarray(X[a : a + 3_000_000], float) @ T[:3, :3].T + T[:3, 3]
            if roi_xy is not None:
                (x0, y0), (x1, y1) = roi_xy
                keep = (x[:, 0] > x0) & (x[:, 0] < x1) & (x[:, 1] > y0) & (x[:, 1] < y1)
                x = x[keep]
            if voxel_m:
                x = voxel_thin(x, voxel_m)
            out.append(x)
    P = np.concatenate(out)
    return voxel_thin(P, voxel_m) if voxel_m else P


def voxel_thin(P: np.ndarray, v: float) -> np.ndarray:
    if len(P) == 0:
        return P
    key = np.floor(P / v).astype(np.int64)
    _, idx = np.unique(key, axis=0, return_index=True)
    return P[np.sort(idx)]


def ransac_plane(X, thr, rng, iters=600, accept=lambda n: True):
    Xs = X[rng.integers(0, len(X), min(len(X), 40000))]
    best_k, best = -1, None
    for _ in range(iters):
        s = Xs[rng.integers(0, len(Xs), 3)]
        n = np.cross(s[1] - s[0], s[2] - s[0])
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        if not accept(n):
            continue
        k = int((np.abs((Xs - s[0]) @ n) < thr).sum())
        if k > best_k:
            best_k, best = k, (n, s[0])
    inl = np.abs((X - best[1]) @ best[0]) < thr
    c = X[inl].mean(0)
    n = np.linalg.svd(X[inl] - c, full_matrices=False)[2][-1]
    return n, c, inl


def ransac_line2d(Q, thr, rng, iters=400):
    best_k, best = -1, None
    for _ in range(iters):
        a, b = Q[rng.integers(0, len(Q), 2)]
        d = b - a
        if np.linalg.norm(d) < 0.2:
            continue
        d /= np.linalg.norm(d)
        nrm = np.array([-d[1], d[0]])
        k = int((np.abs((Q - a) @ nrm) < thr).sum())
        if k > best_k:
            best_k, best = k, (a, nrm)
    inl = np.abs((Q - best[0]) @ best[1]) < thr
    c = Q[inl].mean(0)
    d = np.linalg.svd(Q[inl] - c, full_matrices=False)[2][0]
    return c, d, inl


def plan_image(P: np.ndarray, path: Path, res_m=0.05, zlo=None, zhi=None, grid_m=2.0, marks=()):
    """Top view: colour = max height per cell within [zlo, zhi]; grid lines every grid_m with
    labels in scan coordinates so rough wall ends can be read off."""
    from PIL import Image, ImageDraw

    z = P[:, 2]
    zlo = np.percentile(z, 1) if zlo is None else zlo
    zhi = np.percentile(z, 99) if zhi is None else zhi
    m = (z >= zlo) & (z <= zhi)
    Q = P[m]
    x0, y0 = Q[:, 0].min(), Q[:, 1].min()
    x1, y1 = Q[:, 0].max(), Q[:, 1].max()
    W, H = int((x1 - x0) / res_m) + 1, int((y1 - y0) / res_m) + 1
    img = np.zeros((H, W), float) - np.inf
    cnt = np.zeros((H, W), int)
    ix = ((Q[:, 0] - x0) / res_m).astype(int)
    iy = (H - 1 - (Q[:, 1] - y0) / res_m).astype(int)
    np.maximum.at(img, (iy, ix), Q[:, 2])
    np.add.at(cnt, (iy, ix), 1)
    t = np.clip((img - zlo) / (zhi - zlo), 0, 1)
    rgb = np.zeros((H, W, 3), np.uint8)
    have = cnt > 0
    # height ramp: low = blue, mid = green, high = red
    rgb[..., 0] = np.where(have, (255 * np.clip(2 * t - 1, 0, 1)), 0)
    rgb[..., 1] = np.where(have, (255 * (1 - np.abs(2 * t - 1))), 0)
    rgb[..., 2] = np.where(have, (255 * np.clip(1 - 2 * t, 0, 1)), 0)
    im = Image.fromarray(rgb)
    d = ImageDraw.Draw(im)
    gx = np.arange(np.ceil(x0 / grid_m) * grid_m, x1, grid_m)
    gy = np.arange(np.ceil(y0 / grid_m) * grid_m, y1, grid_m)
    for g in gx:
        u = int((g - x0) / res_m)
        d.line([(u, 0), (u, H)], fill=(80, 80, 80))
        d.text((u + 2, 2), f"{g:.0f}", fill=(255, 255, 255))
    for g in gy:
        v = int(H - 1 - (g - y0) / res_m)
        d.line([(0, v), (W, v)], fill=(80, 80, 80))
        d.text((2, v + 2), f"{g:.0f}", fill=(255, 255, 255))
    for (mx, my, label) in marks:
        u, v = (mx - x0) / res_m, H - 1 - (my - y0) / res_m
        d.ellipse([u - 5, v - 5, u + 5, v + 5], outline=(255, 255, 255), width=2)
        d.text((u + 7, v - 7), label, fill=(255, 255, 0))
    im.save(path)
    return (x0, y0, res_m, H)
