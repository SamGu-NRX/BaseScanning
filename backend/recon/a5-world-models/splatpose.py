"""Marble splat -> metric point cloud, and input-camera recovery in the splat frame.

Marble returns no camera poses. Each input photo is registered to the splat by render-and-match:
render the splat's Gaussian centres (colour + z-buffer depth) from a pose guess with the photo's own
intrinsics, SIFT-match photo <-> render, lift render keypoints to 3D with the render depth, PnP-RANSAC,
repeat. Only the photos, their intrinsics and the splat are used (no ground truth).

Frame: Marble raw is OpenCV (+x left?, +y down, +z forward per docs "marble_raw_opencv"). We keep the
splat's own frame (times metric_scale_factor) and express cameras in it; the scorer aligns afterwards.
"""
import json
import re
from pathlib import Path

import cv2
import kornia.feature as KF
import numpy as np
import torch

SH0 = 0.28209479177387814


def read_splat_ply(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        text = header.decode("ascii")
        n = int(re.search(r"element vertex (\d+)", text).group(1))
        props = re.findall(r"property (\w+) (\w+)", text.split("element vertex")[1])
        tmap = {"float": "<f4", "double": "<f8", "uchar": "u1", "int": "<i4", "uint": "<u4", "short": "<i2", "ushort": "<u2"}
        dt = np.dtype([(nm, tmap[t]) for t, nm in props])
        arr = np.frombuffer(f.read(n * dt.itemsize), dtype=dt, count=n)
    return arr


def splat_points(arr, msf, min_opacity=0.3):
    xyz = np.c_[arr["x"], arr["y"], arr["z"]].astype(np.float64) * msf
    op = 1 / (1 + np.exp(-arr["opacity"].astype(np.float64)))
    rgb = np.clip(0.5 + SH0 * np.c_[arr["f_dc_0"], arr["f_dc_1"], arr["f_dc_2"]], 0, 1)
    sc = np.exp(np.c_[arr["scale_0"], arr["scale_1"], arr["scale_2"]].astype(np.float64)) * msf
    keep = op >= min_opacity
    return xyz[keep], rgb[keep], op[keep], sc[keep]


def render(xyz, rgb, R_cw, t_cw, K, w, h, ds=4):
    """Point-splat render at 1/ds resolution. Returns rgb uint8 (h/ds, w/ds, 3), depth (0 = empty)."""
    Xc = xyz @ R_cw.T + t_cw
    z = Xc[:, 2]
    ok = z > 0.05
    Xc, z, col = Xc[ok], z[ok], rgb[ok]
    u = (K[0] * Xc[:, 0] / z + K[2]) / ds
    v = (K[1] * Xc[:, 1] / z + K[3]) / ds
    W, H = int(np.ceil(w / ds)), int(np.ceil(h / ds))
    ui, vi = np.floor(u).astype(int), np.floor(v).astype(int)
    ok = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    ui, vi, z, col = ui[ok], vi[ok], z[ok], col[ok]
    order = np.argsort(-z)  # far first, near overwrites
    depth = np.zeros((H, W))
    img = np.zeros((H, W, 3))
    depth[vi[order], ui[order]] = z[order]
    img[vi[order], ui[order]] = col[order]
    # fill 1-px holes from neighbours (nearest-depth wins)
    empty = depth == 0
    if empty.any():
        dd = np.where(empty, np.inf, depth)
        k = np.ones((3, 3), np.uint8)
        dmin = cv2.erode(dd.astype(np.float32), k)
        fill = empty & np.isfinite(dmin)
        for c in range(3):
            ch = img[..., c].astype(np.float32)
            dil = cv2.dilate(np.where(empty, 0, ch).astype(np.float32), k)
            img[..., c] = np.where(fill, dil, ch)
        depth = np.where(fill, dmin, depth)
    return (img * 255).astype(np.uint8), depth


_sift = cv2.SIFT_create(nfeatures=8000)


def match(img_a, img_b, ratio=0.8, mask_b=None):
    ga = cv2.cvtColor(img_a, cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(img_b, cv2.COLOR_RGB2GRAY)
    ka, da = _sift.detectAndCompute(ga, None)
    kb, db = _sift.detectAndCompute(gb, mask_b)
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return np.zeros((0, 2)), np.zeros((0, 2))
    m = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [p[0] for p in m if len(p) == 2 and p[0].distance < ratio * p[1].distance]
    pa = np.array([ka[g.queryIdx].pt for g in good]).reshape(-1, 2)
    pb = np.array([kb[g.trainIdx].pt for g in good]).reshape(-1, 2)
    return pa, pb


_lg = {}


def match_lg(img_a, img_b, mask_b=None, n=4096):
    """DISK + LightGlue (kornia, CPU). Returns matched pixel coords in a and b."""
    if not _lg:
        _lg["disk"] = KF.DISK.from_pretrained("depth").eval()
        _lg["lg"] = KF.LightGlueMatcher("disk").eval()
    feats = []
    for im in (img_a, img_b):
        t = torch.from_numpy(im).permute(2, 0, 1)[None].float() / 255
        with torch.inference_mode():
            f = _lg["disk"](t, n, pad_if_not_divisible=True)[0]
        feats.append(f)
    (ka, da), (kb, db) = [(f.keypoints, f.descriptors) for f in feats]
    if mask_b is not None and len(kb):
        kbi = kb.round().long()
        keep = torch.from_numpy(mask_b > 0)[kbi[:, 1].clamp(0, mask_b.shape[0] - 1), kbi[:, 0].clamp(0, mask_b.shape[1] - 1)]
        kb, db = kb[keep], db[keep]
    if len(ka) < 8 or len(kb) < 8:
        return np.zeros((0, 2)), np.zeros((0, 2))
    lafa = KF.laf_from_center_scale_ori(ka[None], torch.ones(1, len(ka), 1, 1))
    lafb = KF.laf_from_center_scale_ori(kb[None], torch.ones(1, len(kb), 1, 1))
    with torch.inference_mode():
        _, idx = _lg["lg"](da, db, lafa, lafb, hw1=torch.tensor(img_a.shape[:2]), hw2=torch.tensor(img_b.shape[:2]))
    idx = idx.numpy()
    return ka[idx[:, 0]].numpy().astype(float), kb[idx[:, 1]].numpy().astype(float)


def register(photo_rgb, K, xyz, rgb, R0, t0, iters=4, ds=4, reproj_px=8.0, matcher="lg"):
    """Refine a pose guess by render-and-match. Returns R_cw, t_cw, stats."""
    h, w = photo_rgb.shape[:2]
    small = cv2.resize(photo_rgb, (int(np.ceil(w / ds)), int(np.ceil(h / ds))), interpolation=cv2.INTER_AREA)
    R, t = R0.copy(), t0.copy()
    st = {}
    for it in range(iters):
        img, depth = render(xyz, rgb, R, t, K, w, h, ds)
        img = cv2.GaussianBlur(img, (5, 5), 1.2)
        mask = (depth > 0).astype(np.uint8) * 255
        pa, pb = match_lg(small, img, mask) if matcher == "lg" else match(small, img, mask_b=mask)
        if len(pa) < 12:
            st = {"iter": it, "matches": int(len(pa)), "inliers": 0, "ok": False}
            break
        ub, vb = pb[:, 0], pb[:, 1]
        d = depth[np.clip(vb.astype(int), 0, depth.shape[0] - 1), np.clip(ub.astype(int), 0, depth.shape[1] - 1)]
        ok = d > 0
        uf, vf = (ub[ok] + 0.5) * ds, (vb[ok] + 0.5) * ds
        Xc = np.c_[(uf - K[2]) / K[0] * d[ok], (vf - K[3]) / K[1] * d[ok], d[ok]]
        Xw = (Xc - t) @ R  # R^T (Xc - t)
        img_pts = (pa[ok] + 0.5) * ds
        if len(Xw) < 12:
            st = {"iter": it, "matches": int(len(pa)), "inliers": 0, "ok": False}
            break
        Km = np.array([[K[0], 0, K[2]], [0, K[1], K[3]], [0, 0, 1]])
        rvec0, _ = cv2.Rodrigues(R)
        okp, rvec, tvec, inl = cv2.solvePnPRansac(Xw, img_pts, Km, None, rvec0, t.reshape(3, 1), useExtrinsicGuess=True,
                                                  reprojectionError=reproj_px, iterationsCount=3000, confidence=0.999)
        if not okp or inl is None or len(inl) < 12:
            st = {"iter": it, "matches": int(len(pa)), "inliers": 0 if inl is None else int(len(inl)), "ok": False}
            break
        inl = inl.ravel()
        rvec, tvec = cv2.solvePnPRefineLM(Xw[inl], img_pts[inl], Km, None, rvec, tvec)
        Rn, _ = cv2.Rodrigues(rvec)
        tn = tvec.ravel()
        proj = (Xw[inl] @ Rn.T + tn)
        err = np.linalg.norm(np.c_[K[0] * proj[:, 0] / proj[:, 2] + K[2], K[1] * proj[:, 1] / proj[:, 2] + K[3]] - img_pts[inl], axis=1)
        moved = float(np.linalg.norm((-R.T @ t) - (-Rn.T @ tn)))
        R, t = Rn, tn
        st = {"iter": it, "matches": int(len(pa)), "with_depth": int(ok.sum()), "inliers": int(len(inl)),
              "reproj_median_px": round(float(np.median(err)), 2), "centre_moved_m": round(moved, 3), "ok": True}
        if moved < 0.01:
            break
    return R, t, st


def cams_json(entries):
    """entries: list of (image stem, R_cw, t_cw, K, w, h) -> harness cameras JSON."""
    return [{"image": n, "R_cw": R.tolist(), "t_cw": t.tolist(), "intrinsics": list(map(float, K)), "w": int(w), "h": int(h)}
            for n, R, t, K, w, h in entries]


def read_colmap_intrinsics(calib_dir):
    """image name -> (K [fx fy cx cy], w, h) from a COLMAP text model."""
    cams = {}
    for line in Path(calib_dir, "cameras.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        p = line.split()
        cams[p[0]] = (np.array(list(map(float, p[4:8]))), int(p[2]), int(p[3]))
    out = {}
    lines = [l for l in Path(calib_dir, "images.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    for l in lines:
        p = l.split()
        if len(p) >= 10 and p[-1].lower().endswith((".jpg", ".png", ".jpeg")):
            out[Path(p[9]).stem] = cams[p[8]]
    return out
