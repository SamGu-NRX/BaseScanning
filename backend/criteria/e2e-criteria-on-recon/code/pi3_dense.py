"""pi3 dense depth on refined A1 poses (A1 COLMAP + ARKit priors -> pi3 windows -> fused cloud).

Each window of consecutive views goes through pi3 (504x336, bf16 trunk on CPU, A2's adapter).
pi3 depth is scale-free, so each window gets one scale: the median ratio of SfM point depth
(A1 refined poses, observed track points) to pi3 depth at the same pixels. Pixels are then
unprojected with the A1 pose and the known intrinsics, so the cloud lives in the A1 model's
frame and inherits its (ARKit) scale. Filters: pi3 confidence, depth-edge, range, voxel fusion.

    python pi3_dense.py --model A1_MODEL_DIR --images IMG_DIR --out OUT_DIR [--win 16 --stride 12]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pycolmap
import torch
from PIL import Image

torch.set_num_threads(4)
NET_W, NET_H = 504, 336


def stream_load(model, path, bf16_prefixes):
    from safetensors import safe_open
    weights = {}
    with safe_open(path, "pt", device="cpu") as f:
        for key in f.keys():
            t = f.get_tensor(key)
            if key.startswith(bf16_prefixes) and t.ndim == 2 and key.endswith("weight"):
                t = t.to(torch.bfloat16)
            weights[key] = t
    model.load_state_dict(weights, strict=False, assign=True)
    return model


def load_pi3():
    from huggingface_hub import hf_hub_download
    from pi3.models.pi3 import Pi3
    orig = torch.linspace
    torch.linspace = lambda *a, **k: orig(*a, **{**k, "device": "cpu"})
    try:
        with torch.device("meta"):
            m = Pi3()
    finally:
        torch.linspace = orig
    m = stream_load(m, hf_hub_download("yyfz233/Pi3", "model.safetensors", revision="ae722e7039287d0c8fde9f11f197f804f44b510c"),
                    ("encoder.", "decoder.", "point_decoder.", "conf_decoder.", "camera_decoder."))
    for name, val in (("image_mean", [0.485, 0.456, 0.406]), ("image_std", [0.229, 0.224, 0.225])):
        if getattr(m, name).is_meta:
            m.register_buffer(name, torch.tensor(val).view(1, 1, 3, 1, 1), persistent=False)
    left = [n for n, t in [*m.named_parameters(), *m.named_buffers()] if t.is_meta]
    if left:
        raise RuntimeError(f"pi3 tensors missing: {left[:5]}")
    return m.eval()


def infer(m, rgbs):
    imgs = torch.from_numpy(np.stack(rgbs)).permute(0, 3, 1, 2).float()[None] / 255.0
    with torch.no_grad():
        x = (imgs - m.image_mean) / m.image_std
        B, N, C, H, W = x.shape
        with torch.autocast("cpu", dtype=torch.bfloat16):
            hidden = m.encoder(x.reshape(B * N, C, H, W), is_training=True)
            if isinstance(hidden, dict):
                hidden = hidden["x_norm_patchtokens"]
            hidden, pos = m.decode(hidden, N, H, W)
            ph = m.point_decoder(hidden, xpos=pos)
            ch = m.conf_decoder(hidden, xpos=pos)
        s = m.patch_start_idx
        ret = m.point_head([ph.float()[:, s:]], (H, W)).reshape(B, N, H, W, -1)
        z = torch.exp(ret[..., 2])
        conf = torch.sigmoid(m.conf_head([ch.float()[:, s:]], (H, W)).reshape(B, N, H, W, -1)[..., 0])
    return z[0].numpy().astype(np.float32), conf[0].numpy().astype(np.float32)


def edge_mask(z: np.ndarray, rel: float = 0.04) -> np.ndarray:
    d = np.zeros_like(z)
    for ax in (0, 1):
        g = np.abs(np.diff(z, axis=ax))
        if ax == 0:
            d[1:] = np.maximum(d[1:], g); d[:-1] = np.maximum(d[:-1], g)
        else:
            d[:, 1:] = np.maximum(d[:, 1:], g); d[:, :-1] = np.maximum(d[:, :-1], g)
    return d / np.maximum(z, 1e-6) < rel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--win", type=int, default=16)
    ap.add_argument("--stride", type=int, default=12)
    ap.add_argument("--conf", type=float, default=0.5)
    ap.add_argument("--pix", type=int, default=1, help="pixel stride when unprojecting")
    ap.add_argument("--per-view", action="store_true", help="scale each view by its own SfM depth ratio")
    ap.add_argument("--cache", type=Path, help="shared pi3 inference cache (keyed by window members)")
    ap.add_argument("--min-agree", type=int, default=1)
    ap.add_argument("--rel-tol", type=float, default=0.02, help="multi-view depth agreement")
    ap.add_argument("--max-depth", type=float, default=40.0)
    ap.add_argument("--voxel", type=float, default=0.03)
    a = ap.parse_args()
    t0 = time.time()
    a.out.mkdir(parents=True, exist_ok=True)
    rec = pycolmap.Reconstruction(str(a.model))
    ims = sorted(rec.images.values(), key=lambda im: im.name)
    names = [im.name for im in ims]
    files = {p.stem.lower(): p for p in a.images.iterdir()}
    ALL = np.array([p.xyz for p in rec.points3D.values()])
    views = []
    for im in ims:
        cfw = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
        R, t = cfw.rotation.matrix(), np.asarray(cfw.translation)
        cam = rec.cameras[im.camera_id]
        fx, fy, cx, cy = cam.params[:4]
        sx, sy = NET_W / cam.width, NET_H / cam.height
        views.append({"name": im.name, "R": R, "t": t, "K": np.array([fx * sx, fy * sy, cx * sx - 0.5, cy * sy - 0.5]),
                      "K_full": [fx, fy, cx, cy], "w": int(cam.width), "h": int(cam.height), "X": ALL})
    n = len(views)
    starts = list(range(0, max(n - a.win, 0) + 1, a.stride))
    if starts[-1] + a.win < n:
        starts.append(n - a.win)
    m = load_pi3()
    pts, wins = [], []
    for w0 in starts:
        idx = list(range(w0, min(w0 + a.win, n)))
        rgbs = [np.asarray(Image.open(files[Path(views[i]["name"]).stem.lower()]).convert("RGB").resize((NET_W, NET_H), Image.BILINEAR)) for i in idx]
        tw = time.time()
        import hashlib
        hv = hashlib.sha1("|".join(views[i]["name"] for i in idx).encode()).hexdigest()[:10]
        cache = (a.cache or a.out / "cache") / f"win_{hv}.npz"
        if cache.exists():
            z_ = np.load(cache)
            Z, C = z_["Z"], z_["C"]
        else:
            Z, C = infer(m, rgbs)
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(cache, Z=Z, C=C)
        ratios = []
        vscale = {}
        for k, i in enumerate(idx):
            v = views[i]
            if len(v["X"]) == 0:
                continue
            Xc = v["X"] @ v["R"].T + v["t"]
            ok = Xc[:, 2] > 0.3
            u = v["K"][0] * Xc[ok, 0] / Xc[ok, 2] + v["K"][2]
            vv = v["K"][1] * Xc[ok, 1] / Xc[ok, 2] + v["K"][3]
            ui, vi = np.round(u).astype(int), np.round(vv).astype(int)
            inb = (ui >= 0) & (ui < NET_W) & (vi >= 0) & (vi < NET_H)
            ui, vi, zc = ui[inb], vi[inb], Xc[ok, 2][inb]
            # sparse z-buffer: only the front-most SfM point per 4x4 cell (no tracks in the light model)
            cell = (vi // 4) * (NET_W // 4 + 1) + ui // 4
            order = np.lexsort((zc, cell))
            first = np.r_[True, cell[order][1:] != cell[order][:-1]]
            sel = order[first]
            zmin = zc[sel]
            ui, vi = ui[sel], vi[sel]
            zs, zp, cf = zmin, Z[k][vi, ui], C[k][vi, ui]
            good = cf > a.conf
            ratios.append(zs[good] / zp[good])
            vscale[i] = float(np.median(zs[good] / zp[good])) if good.sum() >= 50 else float("nan")
        r = np.concatenate(ratios) if ratios else np.zeros(0)
        scale = float(np.median(r)) if len(r) >= 50 else float("nan")
        spread = float(np.median(np.abs(np.log(r / scale)))) if len(r) >= 50 else float("nan")
        wins.append({"start": w0, "views": [views[i]["name"] for i in idx], "scale": scale, "mad_log": spread,
                     "n_ratio": int(len(r)), "infer_s": round(time.time() - tw, 1)})
        print(json.dumps(wins[-1]), flush=True)
        if not np.isfinite(scale) or spread > 0.15:
            continue
        uu, vv = np.meshgrid(np.arange(0, NET_W, a.pix), np.arange(0, NET_H, a.pix))
        for k, i in enumerate(idx):
            v = views[i]
            sk = vscale.get(i, scale) if a.per_view else scale
            if not np.isfinite(sk):
                continue
            z = Z[k] * sk
            keep = (C[k] > a.conf) & edge_mask(Z[k]) & (z < a.max_depth)
            keep = keep[vv, uu]
            zz = z[vv, uu][keep]
            x = (uu[keep] - v["K"][2]) / v["K"][0] * zz
            y = (vv[keep] - v["K"][3]) / v["K"][1] * zz
            Xc = np.c_[x, y, zz]
            Xw = (Xc - v["t"]) @ v["R"]
            # multi-view consistency on the refined poses: another view of the window must see the
            # same depth (within rel_tol) at the point's projection
            agree = np.zeros(len(Xw), int)
            for kj in sorted(range(len(idx)), key=lambda q: abs(q - k))[1:5]:
                vj = views[idx[kj]]
                Xj = Xw @ vj["R"].T + vj["t"]
                okz = Xj[:, 2] > 0.3
                uj = np.round(vj["K"][0] * Xj[:, 0] / np.maximum(Xj[:, 2], 1e-6) + vj["K"][2]).astype(int)
                vjj = np.round(vj["K"][1] * Xj[:, 1] / np.maximum(Xj[:, 2], 1e-6) + vj["K"][3]).astype(int)
                okz &= (uj >= 0) & (uj < NET_W) & (vjj >= 0) & (vjj < NET_H)
                zj = np.full(len(Xw), np.inf)
                sj = vscale.get(idx[kj], scale) if a.per_view else scale
                zj[okz] = Z[kj][vjj[okz], uj[okz]] * (sj if np.isfinite(sj) else scale)
                cj = np.zeros(len(Xw))
                cj[okz] = C[kj][vjj[okz], uj[okz]]
                agree += okz & (cj > a.conf) & (np.abs(Xj[:, 2] - zj) < a.rel_tol * zj)
            pts.append(Xw[agree >= a.min_agree])
    X = np.concatenate(pts)
    key = np.floor(X / a.voxel).astype(np.int64)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    acc = np.zeros((cnt.size, 3))
    np.add.at(acc, inv, X)
    Xf = (acc / cnt[:, None]).astype(np.float32)
    from plyfile import PlyData, PlyElement
    el = np.zeros(len(Xf), dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    el["x"], el["y"], el["z"] = Xf.T
    PlyData([PlyElement.describe(el, "vertex")], text=False, byte_order="<").write(str(a.out / "points_model.ply"))
    cams = []
    for v in views:
        cams.append({"image": Path(v["name"]).stem, "R_cw": v["R"].tolist(), "t_cw": v["t"].tolist(),
                     "intrinsics": v["K_full"], "w": v["w"], "h": v["h"]})
    (a.out / "cameras_model.json").write_text(json.dumps(cams))
    rep = {"views": n, "windows": wins, "raw_points": int(len(X)), "fused_points": int(len(Xf)),
           "params": vars(a) | {"model": str(a.model), "images": str(a.images), "out": str(a.out)}, "runtime_s": round(time.time() - t0, 1)}
    (a.out / "pi3_report.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: rep[k] for k in ("views", "raw_points", "fused_points", "runtime_s")}))


if __name__ == "__main__":
    main()
