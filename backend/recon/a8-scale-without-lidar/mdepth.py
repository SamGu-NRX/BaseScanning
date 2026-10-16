"""Learned metric depth as a scale prior: per-image scale = median(pred depth / SfM depth) at the image's
own SfM track points, compared with s_true (metres per recon unit).

    python mdepth.py SCENE MODEL [--n 16]    MODEL in moge2-vits | metric3d-vits
    -> results/<scene>/mdepth_<model>.json
"""

from __future__ import annotations

import argparse
import math
import sys
import time

import cv2
import numpy as np
import pycolmap
import torch

from common import HERE, WORK, dump, image_path, load_scene

torch.set_num_threads(4)
WIDTH = 1024


class MoGe2:
    def __init__(self):
        from moge.model.v2 import MoGeModel

        self.m = MoGeModel.from_pretrained("Ruicheng/moge-2-vits-normal").eval()

    def __call__(self, rgb, K):
        fov_x = math.degrees(2 * math.atan(rgb.shape[1] / (2 * K[0, 0])))
        t = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255
        out = self.m.infer(t, resolution_level=9, fov_x=fov_x, use_fp16=False)
        return out["depth"].numpy(), out["mask"].numpy().astype(bool)


class Metric3D:
    INPUT = (616, 1064)
    MEAN = np.array([123.675, 116.28, 103.53])
    STD = np.array([58.395, 57.12, 57.375])

    def __init__(self):
        sys.path.insert(0, str(WORK.parent / "Metric3D"))
        import hubconf

        self.m = hubconf.metric3d_vit_small(pretrain=True).eval()

    def __call__(self, rgb, K):
        h, w = rgb.shape[:2]
        s = min(self.INPUT[0] / h, self.INPUT[1] / w)
        img = cv2.resize(rgb, (int(w * s), int(h * s)), interpolation=cv2.INTER_LINEAR)
        fx = K[0, 0] * s
        ph, pw = self.INPUT[0] - img.shape[0], self.INPUT[1] - img.shape[1]
        top, left = ph // 2, pw // 2
        img = cv2.copyMakeBorder(img, top, ph - top, left, pw - left, cv2.BORDER_CONSTANT, value=self.MEAN.tolist())
        x = torch.from_numpy(((img - self.MEAN) / self.STD).transpose(2, 0, 1)).float()[None]
        with torch.no_grad():
            d, _conf, _ = self.m.inference({"input": x})
        d = d[0, 0, top: top + int(h * s), left: left + int(w * s)]
        d = torch.nn.functional.interpolate(d[None, None], (h, w), mode="bilinear")[0, 0].numpy()
        d = d * fx / 1000.0  # canonical camera with a 1000 px focal
        return d, (d > 0) & (d < 300)


def track_points(rec: pycolmap.Reconstruction, stem: str):
    for img in rec.images.values():
        if img.name.rsplit(".", 1)[0] == stem:
            ids = [p.point3D_id for p in img.points2D if p.has_point3D()]
            return np.array([rec.points3D[i].xyz for i in ids]) if ids else np.zeros((0, 3))
    return np.zeros((0, 3))


def visible_points(P, cam, K, shape, cell=8, tol=0.05):
    """SfM points in view, keeping only the front-most ones per cell (coarse z-buffer on the points)."""
    Xc = P @ cam.R.T + cam.t
    z = Xc[:, 2]
    m = z > 0
    uv = (Xc[m, :2] / z[m, None]) * [K[0, 0], K[1, 1]] + K[:2, 2]
    inside = (uv[:, 0] >= 0) & (uv[:, 1] >= 0) & (uv[:, 0] < shape[1]) & (uv[:, 1] < shape[0])
    Q, uv, zz = P[m][inside], uv[inside], z[m][inside]
    key = (uv[:, 1] // cell).astype(int) * 100000 + (uv[:, 0] // cell).astype(int)
    order = np.lexsort((zz, key))
    key, zz, Q = key[order], zz[order], Q[order]
    first = np.r_[True, key[1:] != key[:-1]]
    zmin = np.maximum.accumulate(np.where(first, np.arange(len(key)), 0))
    keep = zz <= zz[zmin] * (1 + tol)
    return Q[keep]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("model")
    ap.add_argument("--n", type=int, default=16)
    a = ap.parse_args()
    sc = load_scene(a.scene, laser=False)
    rec = pycolmap.Reconstruction(str(WORK / a.scene / "inc") if a.scene != "barn" else str(WORK / "barn/undistorted/sparse"))
    names = sorted(sc.rec_cams)
    views = names[:: max(1, len(names) // a.n)][: a.n]
    t0 = time.time()
    model = MoGe2() if a.model == "moge2-vits" else Metric3D()
    load_s = time.time() - t0
    rows = []
    for v in views:
        cam = sc.rec_cams[v]
        bgr = cv2.imread(str(image_path(a.scene, v)))
        f = WIDTH / bgr.shape[1]
        rgb = cv2.cvtColor(cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        K = cam.K.copy()
        K[:2] *= f
        t = time.time()
        with torch.no_grad():
            d, valid = model(rgb, K)
        sec = time.time() - t
        X = track_points(rec, v)
        if len(X) < 50:
            X = visible_points(sc.rec_pts, cam, K, d.shape)
        Xc = X @ cam.R.T + cam.t
        z = Xc[:, 2]
        uv = (Xc[:, :2] / z[:, None]) * [K[0, 0], K[1, 1]] + K[:2, 2]
        ui, vi = np.round(uv[:, 0]).astype(int), np.round(uv[:, 1]).astype(int)
        m = (z > 0) & (ui >= 0) & (vi >= 0) & (ui < d.shape[1]) & (vi < d.shape[0])
        ui, vi, z = ui[m], vi[m], z[m]
        ok = valid[vi, ui]
        r = d[vi, ui][ok] / z[ok]
        # depth-banded ratios (metres, using the model's own depth)
        dz = d[vi, ui][ok]
        row = {"view": v, "seconds": sec, "n_pts": int(len(r)), "scale_est": float(np.median(r)) if len(r) else None,
               "scale_err_pct": float(100 * (np.median(r) / sc.s_true - 1)) if len(r) else None,
               "ratio_iqr_pct": float(100 * (np.percentile(r, 75) - np.percentile(r, 25)) / np.median(r)) if len(r) else None,
               "median_depth_m": float(np.median(dz)) if len(r) else None}
        for lo, hi in ((0, 6), (6, 12), (12, 100)):
            mm = (dz >= lo) & (dz < hi)
            row[f"err_pct_{lo}_{hi}m"] = float(100 * (np.median(r[mm]) / sc.s_true - 1)) if mm.sum() > 20 else None
        rows.append(row)
        print(a.scene, a.model, row, flush=True)
    est = [r["scale_est"] for r in rows if r["scale_est"]]
    summ = {"scene": a.scene, "model": a.model, "s_true": sc.s_true, "load_s": load_s, "rows": rows,
            "median_of_images_err_pct": float(100 * (np.median(est) / sc.s_true - 1)),
            "per_image_err_pct_p10_p90": [float(np.percentile([r["scale_err_pct"] for r in rows if r["scale_err_pct"] is not None], q)) for q in (10, 90)]}
    dump(HERE / "results" / a.scene / f"mdepth_{a.model}.json", summ)
    print("SUMMARY", {k: v for k, v in summ.items() if k != "rows"})


if __name__ == "__main__":
    main()
