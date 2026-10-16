"""Run feed-forward multi-view models on ETH3D seed groups, with and without ARKit-like metadata.

    python -m a2ff.run --model mapanything --scene delivery_area --sizes 8 \
        --modes images K:exact KT:exact KT:nonlidar_modern.draw0

Modes: `images` (no metadata); `K:<setting>` (intrinsics of a pose setting fed to the model);
`KT:<setting>` (intrinsics and camera-to-world poses fed to the model). Settings are the files
written by `a2ff.prepare` (exact, lidar_modern.drawN, nonlidar_modern.drawN, nonlidar_2018.drawN).
Models that cannot take metadata run `images` only; their metric scale can still come from the poses
after the fact (a Sim(3) fit of their cameras to the ARKit cameras, applied at scoring time).

CPU only. The transformer trunks run under bf16 autocast (the CPU has AMX-BF16) with their weights
stored in bf16; prediction heads run in fp32, as the models' own GPU code does. Weights are streamed
from safetensors onto a meta-initialised model so peak memory stays near the bf16 footprint.

Outputs: ~/a2/preds/<scene>/<model key>_<mode>/<group id>/<view>.npz and run.json.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from a2ff.common import (
    PRED_ROOT, group_id, groups, intrinsics_to_network, load_poses, matrix, network_inputs,
    fit_intrinsics_from_local_points, vector, write_view,
)

torch.set_num_threads(4)


def stream_load(model: torch.nn.Module, path: str, bf16_prefixes: tuple[str, ...],
                rename=lambda k: k) -> torch.nn.Module:
    """Load a meta-initialised model from safetensors, storing the 2-D (Linear) weights under
    `bf16_prefixes` in bf16; tokens, norms, biases and convolutions stay fp32 (CPU autocast will not
    concatenate a bf16 parameter with fp32 activations). Non-persistent buffers left on meta are
    rebuilt by the caller."""
    from safetensors import safe_open

    weights = {}
    with safe_open(path, "pt", device="cpu") as f:
        for key in f.keys():
            t = f.get_tensor(key)
            k = rename(key)
            if k.startswith(bf16_prefixes) and t.ndim == 2 and k.endswith("weight"):
                t = t.to(torch.bfloat16)
            weights[k] = t
    missing, unexpected = model.load_state_dict(weights, strict=False, assign=True)
    return model, missing, unexpected


def build_on_meta(cls, *args, **kwargs):
    """Construct a model on the meta device (no memory). Constructors that call `.item()` on a
    `torch.linspace` (stochastic-depth rates) get that one tensor on the CPU."""
    orig = torch.linspace
    torch.linspace = lambda *a, **k: orig(*a, **{**k, "device": "cpu"})
    try:
        with torch.device("meta"):
            return cls(*args, **kwargs)
    finally:
        torch.linspace = orig


def meta_left(model: torch.nn.Module) -> list[str]:
    return [n for n, t in [*model.named_parameters(), *model.named_buffers()] if t.is_meta]


# ---------------------------------------------------------------------------------------------
# Adapters: infer(rgbs uint8 list, K_net list|None, T list|None) -> list of per-view dicts with
# depth (H, W) z-depth, conf (H, W), mask (H, W) or None, K (4,) network pixels, c2w (4, 4).


class MapAnything:
    key = "ma518"
    net = (518, 336)
    accepts = {"images", "K", "KT"}
    license = "Apache-2.0 (facebook/map-anything-apache)"

    def load(self):
        from huggingface_hub import hf_hub_download
        from mapanything.models import MapAnything as MA

        repo, rev = "facebook/map-anything-apache", "00f9c245bbcb60522d1ed7f9e9d88462c6e3f38a"
        config = json.loads(Path(hf_hub_download(repo, "config.json", revision=rev)).read_text())
        config["encoder_config"] = {**config["encoder_config"], "torch_hub_pretrained": False}
        with torch.device("meta"):
            model = MA(**config)
        model, _, _ = stream_load(model, hf_hub_download(repo, "model.safetensors", revision=rev),
                                  ("encoder.", "info_sharing."))
        left = meta_left(model)
        if left:
            raise RuntimeError(f"MapAnything tensors missing: {left[:5]}")
        self.model = model.eval()
        self.checkpoint = f"{repo}@{rev}"

    def infer(self, rgbs, K_net, T):
        from mapanything.utils.image import preprocess_inputs

        views = []
        for i, rgb in enumerate(rgbs):
            v = {"img": torch.from_numpy(rgb)}
            if K_net is not None:
                v["intrinsics"] = torch.from_numpy(matrix(K_net[i]).astype(np.float32))
            if T is not None:
                v["camera_poses"] = torch.from_numpy(T[i].astype(np.float32))
                v["is_metric_scale"] = torch.tensor([True])
            views.append(v)
        views = preprocess_inputs(views, resize_mode="fixed_size", size=self.net)
        with torch.no_grad():
            preds = self.model.infer(views, memory_efficient_inference=True, minibatch_size=1,
                                     use_amp=True, amp_dtype="bf16", apply_mask=False,
                                     mask_edges=False)
        out = []
        for p in preds:
            pose = p["camera_poses"][0].float().numpy().astype(np.float64)
            if T is not None:
                pose = T[0] @ pose
            out.append({
                "depth": p["depth_z"][0, ..., 0].float().numpy(),
                "conf": p["conf"][0].float().numpy(),
                "mask": p["non_ambiguous_mask"][0].numpy().astype(bool),
                "K": vector(p["intrinsics"][0].float().numpy().astype(np.float64)),
                "c2w": pose,
                "extra": {"metric_scaling_factor": float(p["metric_scaling_factor"].reshape(-1)[0])},
            })
        return out


class VGGT:
    key = "vggt518"
    net = (518, 336)
    accepts = {"images"}
    license = "CC BY-NC 4.0 (facebook/VGGT-1B); commercial checkpoint facebook/VGGT-1B-Commercial is gated"

    def load(self):
        from huggingface_hub import hf_hub_download
        from vggt.models.vggt import VGGT as V

        repo, rev = "facebook/VGGT-1B", "860abec7937da0a4c03c41d3c269c366e82abdf9"
        model = build_on_meta(V)
        model, missing, _ = stream_load(model, hf_hub_download(repo, "model.safetensors", revision=rev),
                                        ("aggregator.",))
        # Non-persistent normalisation buffers.
        agg = model.aggregator
        for name in ("_resnet_mean", "_resnet_std"):
            if hasattr(agg, name) and getattr(agg, name).is_meta:
                val = [0.485, 0.456, 0.406] if "mean" in name else [0.229, 0.224, 0.225]
                agg.register_buffer(name, torch.tensor(val).view(1, 1, 3, 1, 1), persistent=False)
        left = meta_left(model)
        if left:
            raise RuntimeError(f"VGGT tensors missing: {left[:5]}")
        self.model = model.eval()
        self.checkpoint = f"{repo}@{rev}"

    def infer(self, rgbs, K_net, T):
        from vggt.utils.pose_enc import pose_encoding_to_extri_intri

        m = self.model
        imgs = torch.from_numpy(np.stack(rgbs)).permute(0, 3, 1, 2).float()[None] / 255.0
        with torch.no_grad():
            with torch.autocast("cpu", dtype=torch.bfloat16):
                tokens, ps_idx = m.aggregator(imgs)
            keep = set(m.depth_head.intermediate_layer_idx) | {len(tokens) - 1}
            tokens = [t.float() if i in keep else None for i, t in enumerate(tokens)]
            pose_enc = m.camera_head(tokens)[-1]
            depth, conf = m.depth_head(tokens, images=imgs, patch_start_idx=ps_idx)
            extri, intri = pose_encoding_to_extri_intri(pose_enc, imgs.shape[-2:])
        out = []
        for i in range(len(rgbs)):
            w2c = np.eye(4)
            w2c[:3] = extri[0, i].numpy()
            out.append({
                "depth": depth[0, i, ..., 0].numpy(),
                "conf": conf[0, i].numpy(),
                "mask": None,
                "K": vector(intri[0, i].numpy().astype(np.float64)),
                "c2w": np.linalg.inv(w2c),
            })
        return out


class Pi3:
    key = "pi3_518"
    net = (518, 336)
    accepts = {"images"}
    license = "BSD-2-Clause (yyfz233/Pi3 code and weights)"

    def load(self):
        from huggingface_hub import hf_hub_download
        from pi3.models.pi3 import Pi3 as P

        repo, rev = "yyfz233/Pi3", "ae722e7039287d0c8fde9f11f197f804f44b510c"
        model = build_on_meta(P)
        model, missing, _ = stream_load(model, hf_hub_download(repo, "model.safetensors", revision=rev),
                                        ("encoder.", "decoder.", "point_decoder.", "conf_decoder.",
                                         "camera_decoder."))
        for name, val in (("image_mean", [0.485, 0.456, 0.406]), ("image_std", [0.229, 0.224, 0.225])):
            if getattr(model, name).is_meta:
                model.register_buffer(name, torch.tensor(val).view(1, 1, 3, 1, 1), persistent=False)
        left = meta_left(model)
        if left:
            raise RuntimeError(f"Pi3 tensors missing: {left[:5]}")
        self.model = model.eval()
        self.checkpoint = f"{repo}@{rev}"

    def infer(self, rgbs, K_net, T):
        from pi3.utils.geometry import homogenize_points

        m = self.model
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
                camh = m.camera_decoder(hidden, xpos=pos)
            s = m.patch_start_idx
            ret = m.point_head([ph.float()[:, s:]], (H, W)).reshape(B, N, H, W, -1)
            xy, z = ret.split([2, 1], dim=-1)
            z = torch.exp(z)
            local = torch.cat([xy * z, z], dim=-1)
            conf = m.conf_head([ch.float()[:, s:]], (H, W)).reshape(B, N, H, W, -1)
            poses = m.camera_head(camh.float()[:, s:], H // 14, W // 14).reshape(B, N, 4, 4)
        out = []
        for i in range(N):
            lp = local[0, i].numpy().astype(np.float64)
            c = torch.sigmoid(conf[0, i, ..., 0]).numpy()
            out.append({
                "depth": lp[..., 2].astype(np.float32),
                "conf": c,
                "mask": c > 0.1,
                "K": fit_intrinsics_from_local_points(lp, c > 0.1),
                "c2w": poses[0, i].numpy().astype(np.float64),
            })
        return out


class DA3:
    net = (504, 336)
    accepts = {"images", "KT"}

    def __init__(self, repo: str, key: str, license: str):
        self.repo, self.key, self.license = repo, key, license

    def load(self):
        from depth_anything_3.api import DepthAnything3

        torch.cuda.is_bf16_supported = lambda *a, **k: True  # CPU autocast in bf16, not fp16
        self.model = DepthAnything3.from_pretrained(self.repo).to("cpu").eval()
        self.checkpoint = self.repo

    def infer(self, rgbs, K_net, T):
        ext = np.stack([np.linalg.inv(t) for t in T]) if T is not None else None
        ixt = np.stack([matrix(k) for k in K_net]) if T is not None else None
        pred = self.model.inference(list(rgbs), extrinsics=ext, intrinsics=ixt,
                                    process_res=max(self.net), process_res_method="upper_bound_resize")
        if pred.depth.shape[1:] != (self.net[1], self.net[0]):
            raise RuntimeError(f"DA3 changed the network size to {pred.depth.shape}")
        out = []
        for i in range(len(rgbs)):
            w2c = np.eye(4)
            w2c[:3] = pred.extrinsics[i][:3]
            out.append({
                "depth": pred.depth[i].astype(np.float32),
                "conf": pred.conf[i].astype(np.float32) if pred.conf is not None else None,
                "mask": None,
                "K": vector(np.asarray(pred.intrinsics[i], np.float64)),
                "c2w": np.linalg.inv(w2c),
            })
        return out


class MapAnything392(MapAnything):
    key = "ma392"
    net = (392, 266)


class Pi3Tall(Pi3):
    """518x350 keeps every row of ETH3D's 3:2 images (crops ~1% of the columns instead)."""
    key = "pi3_518x350"
    net = (518, 350)


ADAPTERS = {
    "mapanything": MapAnything,
    "mapanything392": MapAnything392,
    "pi3tall": Pi3Tall,
    "vggt": VGGT,
    "pi3": Pi3,
    "da3large": lambda: DA3("depth-anything/DA3-LARGE-1.1", "da3L", "Apache-2.0 (DA3-LARGE-1.1)"),
    "da3nested": lambda: DA3("depth-anything/DA3NESTED-GIANT-LARGE-1.1", "da3NGL",
                             "CC BY-NC 4.0 (DA3NESTED-GIANT-LARGE-1.1)"),
}


def run_group(adapter, scene: str, n: int, members: list[str], mode: str, force: bool = False) -> dict | None:
    kind, _, setting = mode.partition(":")
    if kind not in adapter.accepts:
        return None
    method = f"{adapter.key}_{kind}" + (f"-{setting}" if setting else "")
    out_dir = PRED_ROOT / scene / method / group_id(n, members)
    if (out_dir / "run.json").exists() and not force:
        return json.loads((out_dir / "run.json").read_text())
    net_w, net_h = adapter.net
    rgbs, resamples = network_inputs(scene, members, net_w, net_h)
    K_net = T = None
    if setting:
        poses = load_poses(scene, setting)
        K_net = [intrinsics_to_network(poses[m]["K"], r) for m, r in zip(members, resamples)]
        if kind == "KT":
            T = [poses[m]["T"] for m in members]
    t0 = time.perf_counter()
    preds = adapter.infer(rgbs, K_net, T)
    seconds = time.perf_counter() - t0
    summaries = {}
    for m, r, p in zip(members, resamples, preds):
        summaries[m] = write_view(out_dir, m, p["depth"], p["K"], p["c2w"], r, p.get("conf"), p.get("mask"))
        summaries[m].update(p.get("extra", {}))
    info = {
        "scene": scene, "model": adapter.key, "checkpoint": adapter.checkpoint, "license": adapter.license,
        "mode": mode, "members": members, "network_wh": list(adapter.net), "seconds_total": round(seconds, 1),
        "device": "cpu (4 threads, bf16 autocast trunk)", "views": summaries,
        "frame": "input poses" if T is not None else "model frame",
    }
    (out_dir / "run.json").write_text(json.dumps(info, indent=1))
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, choices=list(ADAPTERS))
    ap.add_argument("--scene", nargs="+", required=True)
    ap.add_argument("--sizes", nargs="+", type=int, default=[8])
    ap.add_argument("--seeds", type=int, default=8, help="first k seed groups (spread through the capture)")
    ap.add_argument("--seed-index", nargs="+", type=int, default=None, help="explicit seed indices")
    ap.add_argument("--modes", nargs="+", default=["images"])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    adapter = ADAPTERS[args.model]()
    t0 = time.perf_counter()
    adapter.load()
    print(f"loaded {adapter.key} in {time.perf_counter() - t0:.0f}s", flush=True)
    for scene in args.scene:
        g = groups(scene)
        for n in args.sizes:
            idx = args.seed_index if args.seed_index is not None else list(range(args.seeds))
            for k in idx:
                if k >= len(g[str(n)]):
                    continue
                members = g[str(n)][k]
                for mode in args.modes:
                    info = run_group(adapter, scene, n, members, mode, args.force)
                    if info:
                        print(f"{time.strftime('%T')} {scene} n{n} seed{k} {adapter.key} {mode}: "
                              f"{info['seconds_total']}s", flush=True)


if __name__ == "__main__":
    main()
