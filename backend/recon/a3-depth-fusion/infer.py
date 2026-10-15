"""Per-image metric depth for ETH3D views at WIDTH px, CPU.

    python infer.py --scene electro --model moge2-vits [--limit N]

Every model is given the camera's true focal length (ARKit reports intrinsics per frame, so the
server always has them). Output: PRED/<scene>/<model>/<view>.npz with depth (H, W) float32 meters,
valid (H, W) bool, K (3x3, OpenCV, the dataset's), seconds.
"""

from __future__ import annotations

import argparse
import json
import math
import time

import cv2
import numpy as np
import torch

from common import PRED, ROOT, eval_image, scene_views

torch.set_num_threads(4)

MODELS = {
    "moge2-vits": "Ruicheng/moge-2-vits-normal",
    "moge2-vitb": "Ruicheng/moge-2-vitb-normal",
    "moge2-vitl": "Ruicheng/moge-2-vitl-normal",
    "depthpro": "apple/DepthPro",
    "unidepth2-vits": "lpiccinelli/unidepth-v2-vits14",
    "unidepth2-vitl": "lpiccinelli/unidepth-v2-vitl14",
    "metric3d2-vits": "metric3d_vit_small",
    "metric3d2-vitl": "metric3d_vit_large",
}


class MoGe2:
    def __init__(self, repo: str):
        from moge.model.v2 import MoGeModel

        self.m = MoGeModel.from_pretrained(repo).eval()

    def __call__(self, rgb: np.ndarray, K: np.ndarray):
        h, w = rgb.shape[:2]
        fov_x = math.degrees(2 * math.atan(w / (2 * K[0, 0])))
        t = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255
        out = self.m.infer(t, resolution_level=9, fov_x=fov_x, use_fp16=False)
        return out["depth"].numpy(), out["mask"].numpy().astype(bool)


class DepthPro:
    def __init__(self, repo: str):
        import depth_pro
        from depth_pro.depth_pro import DEFAULT_MONODEPTH_CONFIG_DICT
        from huggingface_hub import hf_hub_download

        ckpt = hf_hub_download(repo, "depth_pro.pt")
        cfg = DEFAULT_MONODEPTH_CONFIG_DICT
        cfg.checkpoint_uri = ckpt
        self.m, self.tf = depth_pro.create_model_and_transforms(
            config=cfg, device=torch.device("cpu"), precision=torch.float32
        )
        self.m.eval()

    def __call__(self, rgb, K):
        img = self.tf(rgb)
        pred = self.m.infer(img, f_px=torch.tensor(float(K[0, 0])))
        d = pred["depth"].squeeze().numpy()
        return d, np.isfinite(d) & (d > 0)


class UniDepth2:
    def __init__(self, repo: str):
        from unidepth.models import UniDepthV2

        self.m = UniDepthV2.from_pretrained(repo).eval()

    def __call__(self, rgb, K):
        t = torch.from_numpy(rgb).permute(2, 0, 1)
        pred = self.m.infer(t, torch.from_numpy(K.astype(np.float32)))
        d = pred["depth"].squeeze().numpy()
        return d, np.isfinite(d) & (d > 0)


class Metric3D2:
    INPUT = (616, 1064)
    MEAN = np.array([123.675, 116.28, 103.53])
    STD = np.array([58.395, 57.12, 57.375])

    def __init__(self, entry: str):
        import sys

        sys.path.insert(0, str(ROOT / "Metric3D"))
        import hubconf

        self.m = getattr(hubconf, entry)(pretrain=True).eval()

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
        d = d[0, 0, top : top + int(h * s), left : left + int(w * s)]
        d = torch.nn.functional.interpolate(d[None, None], (h, w), mode="bilinear")[0, 0].numpy()
        d = d * fx / 1000.0  # predicted in a canonical camera with a 1000 px focal
        d = np.clip(d, 0, 300)
        return d, d > 0


def build(name: str):
    repo = MODELS[name]
    if name.startswith("moge2"):
        return MoGe2(repo)
    if name == "depthpro":
        return DepthPro(repo)
    if name.startswith("unidepth2"):
        return UniDepth2(repo)
    if name.startswith("metric3d2"):
        return Metric3D2(repo)
    raise KeyError(name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--model", required=True, choices=list(MODELS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--views", default="", help="comma-separated view names")
    args = ap.parse_args()
    views = scene_views(args.scene)
    if args.views:
        keep = set(args.views.split(","))
        views = [v for v in views if v.name in keep]
    if args.limit:
        views = views[:: max(1, len(views) // args.limit)][: args.limit]
    out = PRED / args.scene / args.model
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    model = build(args.model)
    load_s = time.perf_counter() - t0
    times = []
    for v in views:
        path = out / f"{v.name}.npz"
        if path.exists():
            continue
        rgb, K = eval_image(v)
        t = time.perf_counter()
        with torch.no_grad():
            d, valid = model(rgb, K)
        sec = time.perf_counter() - t
        times.append(sec)
        np.savez_compressed(path, depth=d.astype(np.float32), valid=valid, K=K, seconds=sec)
        print(f"{args.scene} {args.model} {v.name} {sec:.1f}s median depth {np.nanmedian(d[valid]):.2f} m", flush=True)
    log = out / "run.json"
    prev = json.loads(log.read_text()) if log.exists() else {"seconds": []}
    prev["seconds"] += times
    prev["load_seconds"] = load_s
    prev["repo"] = MODELS[args.model]
    log.write_text(json.dumps(prev))


if __name__ == "__main__":
    main()
