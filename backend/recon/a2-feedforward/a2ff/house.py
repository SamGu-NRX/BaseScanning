"""Run feed-forward models on an online walkaround house (robustness and coverage only, no accuracy).

    python -m a2ff.house --model pi3 --house yt-0gotY_syWWU --frames 24

Real video frames, no ground truth, no metadata (images only). Frames are picked uniformly over the
whole clip in time order. The network size keeps the clip's aspect ratio (longest side 518, sides
multiples of 14). Reports, per run:

- registration: camera step lengths along the clip, jumps (a step > 4x the median step), and the
  loop gap (first-to-last camera distance / path length; small only for clips that close a loop);
- gravity and ground: up = the mean of the cameras' image-up axes (handheld video is upright), the
  ground plane by RANSAC on the lowest points, camera height above it. Metric models should give a
  handheld height of ~1.2-1.8 m; scale-free models get the scale that puts the median at 1.5 m;
- coverage: how far the ground plane reaches from the camera path (14 ft = 4.27 m needed) and how
  high the reconstructed surfaces reach above the ground, after the camera-height scale;
- a top-down and a side render (PNG) with the camera path, and a coloured point cloud (PLY).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from a2ff.common import ROOT
from a2ff.run import ADAPTERS
from models.common import cover_crop_resample, load_rgb, patch_aligned_size, to_network_image

HOUSES = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/data/houses")
OUT = ROOT / "houses"
ASSUMED_CAMERA_HEIGHT_M = 1.5
FT14 = 14 * 0.3048


def pick_frames(house: str, n: int) -> list[Path]:
    frames = sorted((HOUSES / house / "frames").glob("*.jpg"))
    idx = np.linspace(0, len(frames) - 1, min(n, len(frames))).round().astype(int)
    return [frames[i] for i in idx]


def world_points(p: dict) -> tuple[np.ndarray, np.ndarray]:
    d = p["depth"]
    fx, fy, cx, cy = p["K"]
    h, w = d.shape
    u, v = np.meshgrid(np.arange(w) + 0.0, np.arange(h) + 0.0)
    x = (u - cx) / fx * d
    y = (v - cy) / fy * d
    pc = np.stack([x, y, d], -1).reshape(-1, 3)
    T = p["c2w"]
    return pc @ T[:3, :3].T + T[:3, 3], np.isfinite(pc).all(1) & (d.reshape(-1) > 0)


def conf_keep(conf: np.ndarray, frac: float = 0.5) -> np.ndarray:
    """Top `frac` of pixels by the model's confidence (per view)."""
    if conf is None:
        return np.ones(0, bool)
    c = conf.reshape(-1)
    return c >= np.quantile(c[np.isfinite(c)], 1 - frac)


def ransac_ground(P: np.ndarray, up: np.ndarray, rng, iters: int = 400, tol: float = 0.03):
    """Plane n.x = d with n near `up`, among the lowest 30% of points; tol relative to scene size."""
    h = P @ up
    low = P[h <= np.quantile(h, 0.3)]
    if len(low) < 100:
        return None
    scale = np.median(np.linalg.norm(P - np.median(P, 0), axis=1))
    t = tol * scale
    best, best_n = None, 0
    for _ in range(iters):
        s = low[rng.choice(len(low), 3, replace=False)]
        n = np.cross(s[1] - s[0], s[2] - s[0])
        if np.linalg.norm(n) < 1e-12:
            continue
        n /= np.linalg.norm(n)
        if n @ up < 0:
            n = -n
        if n @ up < np.cos(np.radians(25)):
            continue
        inl = np.abs(low @ n - n @ s[0]) < t
        if inl.sum() > best_n:
            best, best_n = (n, float(n @ s[0])), int(inl.sum())
    if best is None:
        return None
    n, d = best
    inl = np.abs(P @ n - d) < t
    return n, d, inl


def render(P, C, cols, a, b, path, size=900):
    """Orthographic render of points P onto axes (a, b), camera centres C as a red path."""
    X = np.c_[P @ a, P @ b]
    Cc = np.c_[C @ a, C @ b]
    lo, hi = np.quantile(np.r_[X, Cc], 0.01, axis=0), np.quantile(np.r_[X, Cc], 0.99, axis=0)
    span = max(hi - lo) * 1.1
    mid = (lo + hi) / 2
    to_px = lambda q: ((q - mid) / span * size + size / 2).astype(int)
    img = np.full((size, size, 3), 255, np.uint8)
    q = to_px(X)
    ok = (q >= 0).all(1) & (q < size).all(1)
    img[size - 1 - q[ok, 1], q[ok, 0]] = cols[ok][:, ::-1]
    qc = to_px(Cc)
    for i in range(len(qc) - 1):
        cv2.line(img, (qc[i, 0], size - 1 - qc[i, 1]), (qc[i + 1, 0], size - 1 - qc[i + 1, 1]), (0, 0, 255), 2)
    for i, c in enumerate(qc):
        cv2.circle(img, (c[0], size - 1 - c[1]), 4, (0, 0, 200) if i else (0, 160, 0), -1)
    cv2.imwrite(str(path), img)


def write_ply(path: Path, P: np.ndarray, cols: np.ndarray) -> None:
    with open(path, "wb") as f:
        f.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {len(P)}\nproperty float x\n"
                f"property float y\nproperty float z\nproperty uchar red\nproperty uchar green\n"
                f"property uchar blue\nend_header\n".encode())
        rec = np.zeros(len(P), dtype=[("p", "<f4", 3), ("c", "u1", 3)])
        rec["p"], rec["c"] = P, cols
        f.write(rec.tobytes())


def analyse(preds: list[dict], rgbs: list[np.ndarray], metric: bool, out: Path) -> dict:
    rng = np.random.default_rng(0)
    C = np.array([p["c2w"][:3, 3] for p in preds])
    steps = np.linalg.norm(np.diff(C, axis=0), axis=1)
    med = float(np.median(steps))
    path_len = float(steps.sum())
    up = -np.mean([p["c2w"][:3, 1] for p in preds], axis=0)
    up /= np.linalg.norm(up)
    Ps, cols = [], []
    for p, rgb in zip(preds, rgbs):
        P, ok = world_points(p)
        keep = ok & (conf_keep(p.get("conf"), 0.6) if p.get("conf") is not None else ok)
        sel = np.flatnonzero(keep)
        sel = sel[rng.choice(len(sel), min(len(sel), 20000), replace=False)] if len(sel) else sel
        Ps.append(P[sel])
        cols.append(rgb.reshape(-1, 3)[sel])
    P, cols = np.concatenate(Ps), np.concatenate(cols)
    g = ransac_ground(P, up, rng)
    res = {"frames": len(preds), "path_length_model_units": round(path_len, 3),
           "median_step": round(med, 4), "jumps_gt_4x_median": int((steps > 4 * med).sum()),
           "loop_gap_over_path": round(float(np.linalg.norm(C[-1] - C[0]) / max(path_len, 1e-9)), 3),
           "points": int(len(P))}
    if g is None:
        res["ground"] = "not found"
    else:
        n, d, inl = g
        cam_h = C @ n - d
        med_h = float(np.median(cam_h))
        s = 1.0 if metric else ASSUMED_CAMERA_HEIGHT_M / med_h
        res.update({
            "ground_tilt_vs_camera_up_deg": round(float(np.degrees(np.arccos(np.clip(n @ up, -1, 1)))), 1),
            "ground_inlier_fraction": round(float(inl.mean()), 3),
            "camera_height_model_units": {"median": round(med_h, 3), "p10": round(float(np.percentile(cam_h, 10)), 3),
                                          "p90": round(float(np.percentile(cam_h, 90)), 3)},
            "scale_applied": round(s, 4),
            "scale_source": "model metric output" if metric else f"camera height set to {ASSUMED_CAMERA_HEIGHT_M} m",
        })
        G = P[inl]
        Gh = G - np.outer(G @ n - d, n)
        Ch = C - np.outer(C @ n - d, n)
        from scipy.spatial import cKDTree

        dist = cKDTree(Ch).query(Gh)[0] * s
        heights = (P @ n - d) * s
        res.update({
            "ground_reach_from_path_m": {"p50": round(float(np.median(dist)), 2), "p90": round(float(np.percentile(dist, 90)), 2),
                                         "frac_beyond_14ft": round(float((dist > FT14).mean()), 3)},
            "surface_height_above_ground_m": {"p90": round(float(np.percentile(heights, 90)), 2),
                                              "p99": round(float(np.percentile(heights, 99)), 2)},
            "path_length_m": round(path_len * s, 2),
        })
        a = np.cross(n, [1.0, 0, 0]) if abs(n[0]) < 0.9 else np.cross(n, [0, 1.0, 0])
        a /= np.linalg.norm(a)
        b = np.cross(n, a)
        render(P, C, cols, a, b, out / "topdown.png")
        side = C[-1] - C[0] if np.linalg.norm(C[-1] - C[0]) > 1e-6 else a
        side -= (side @ n) * n
        side /= np.linalg.norm(side)
        render(P, C, cols, side, n, out / "side.png")
    write_ply(out / "points.ply", P.astype(np.float32), cols.astype(np.uint8))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, choices=["mapanything", "pi3", "vggt", "da3large"])
    ap.add_argument("--house", nargs="+", required=True)
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--longest", type=int, default=518)
    args = ap.parse_args()
    adapter = ADAPTERS[args.model]()
    adapter.load()
    for house in args.house:
        paths = pick_frames(house, args.frames)
        rgb0 = load_rgb(paths[0])
        net = patch_aligned_size(rgb0.shape[1], rgb0.shape[0], args.longest, 14)
        adapter.net = net
        rs = [cover_crop_resample(rgb0.shape[1], rgb0.shape[0], *net)] * len(paths)
        rgbs = [to_network_image(load_rgb(p), r) for p, r in zip(paths, rs)]
        out = OUT / house / f"{adapter.key}_n{len(paths)}"
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        preds = adapter.infer(rgbs, None, None)
        secs = time.perf_counter() - t0
        metric = args.model == "mapanything"
        info = {"house": house, "model": adapter.key, "checkpoint": adapter.checkpoint, "license": adapter.license,
                "network_wh": list(net), "frames": [p.name for p in paths], "seconds": round(secs, 1),
                "inputs": "real video frames, images only (no metadata), no ground truth",
                **analyse(preds, rgbs, metric, out)}
        for p, path in zip(preds, paths):
            np.savez_compressed(out / f"{path.stem}.npz", depth=p["depth"], K=p["K"], c2w=p["c2w"],
                                conf=p["conf"] if p.get("conf") is not None else np.zeros(0))
        (out / "run.json").write_text(json.dumps(info, indent=1))
        print(json.dumps(info | {"frames": len(paths)}, indent=1), flush=True)


if __name__ == "__main__":
    main()
