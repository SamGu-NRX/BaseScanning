"""Weight loaders. Fetch is explicit; a missing token or volume is not a download.

Pins (do not change): π³ BSD-2 ``yyfz/Pi3@9fa3ddb3f8d53041f8b2738df404f62223bbaa7b``
(never Pi3X), MoGe-2 MIT ``microsoft/MoGe@925b8ed835a7a9cdb7578ba15c658a0afc969030``
weights ``Ruicheng/moge-2-vits-normal``, DA3-Large Apache-2.0
``ByteDance-Seed/Depth-Anything-3@3d835ec1a5802d64a8b8b15f817a1ab54809bfe4``.

Cached snapshots live under ``HSS_DENSE_WEIGHTS`` (default ``/vol/hf``; never
``HOUSE_WEIGHTS``) as ``<org>--<repo>`` directories. ``from_pretrained`` receives
that local directory (MoGe: the weight file) when it exists, and the hub repo
id only when the directory is missing. This module does not call
``snapshot_download`` except from ``--fetch``.
"""

from __future__ import annotations

import argparse
import os
from contextlib import nullcontext
from pathlib import Path

import numpy as np

from server.dense.params import DenseParams, dense_params_from_env

PI3_REPO = "yyfz233/Pi3"
PI3_REV = "9fa3ddb3f8d53041f8b2738df404f62223bbaa7b"
MOGE_CODE_REPO = "microsoft/MoGe"
MOGE_REV = "925b8ed835a7a9cdb7578ba15c658a0afc969030"
DA3_REPO = "depth-anything/DA3-LARGE-1.1"
DA3_REV = "3d835ec1a5802d64a8b8b15f817a1ab54809bfe4"
MOGE_WEIGHTS = "Ruicheng/moge-2-vits-normal"

# Printed by ``--fetch`` and tests. The short tags match the brief's pins.
PI3_PIN = f"yyfz/Pi3@{PI3_REV[:10]}"
MOGE_PIN = f"microsoft/MoGe@{MOGE_REV[:8]}"
DA3_PIN = f"ByteDance-Seed/Depth-Anything-3@{DA3_REV[:8]}"


def snapshot_dir(weights: Path, repo: str) -> Path:
    return Path(weights) / repo.replace("/", "--")


def local_snapshot(weights: Path, repo: str) -> Path | None:
    """A cached snapshot directory, or None when the volume does not have it."""
    folder = snapshot_dir(weights, repo)
    if not folder.is_dir():
        return None
    try:
        next(folder.iterdir())
    except StopIteration:
        return None
    return folder


def _weight_file(folder: Path) -> Path | None:
    """MoGe's loader torch-loads one file. Prefer safetensors, then pt/bin."""
    found: list[Path] = []
    for root, _, files in os.walk(folder):
        for name in files:
            if name.endswith((".safetensors", ".pt", ".pth", ".bin")):
                found.append(Path(root) / name)
    if not found:
        return None
    found.sort(key=lambda path: (0 if path.suffix == ".safetensors" else 1, len(str(path))))
    return found[0]


def _missing(kind: str, weights: Path, pin: str) -> FileNotFoundError:
    return FileNotFoundError(f"{kind} weights not loaded from {weights} ({pin})")


def pretrained_source(weights: Path, repo: str) -> str:
    """Local snapshot under ``weights``, or the hub repo id when that dir is missing.

    MoGe's loader torch-loads one file inside ``Ruicheng--moge-2-vits-normal``.
    π³ and DA3 take the snapshot directory (``yyfz233--Pi3``,
    ``depth-anything--DA3-LARGE-1.1``).
    """
    folder = local_snapshot(weights, repo)
    if folder is None:
        return repo
    if repo == MOGE_WEIGHTS:
        weight_file = _weight_file(folder)
        if weight_file is None:
            raise _missing("MoGe-2", weights, MOGE_WEIGHTS)
        return str(weight_file)
    return str(folder)


def load_pi3(weights: Path, device: str = "cpu"):
    """π³ from the local snapshot, or the hub repo id when that dir is missing."""
    source = pretrained_source(weights, PI3_REPO)
    try:
        import torch
        from pi3.models.pi3 import Pi3
    except ImportError as exc:
        raise _missing("π³", weights, PI3_REV) from exc
    model = Pi3.from_pretrained(source)
    return model.to(torch.device(device)).eval()


def load_da3(weights: Path, device: str = "cpu"):
    """DA3-Large from the local snapshot, or the hub repo id when that dir is missing."""
    source = pretrained_source(weights, DA3_REPO)
    try:
        import torch
        from depth_anything_3.api import DepthAnything3
    except ImportError as exc:
        raise _missing("DA3-Large", weights, DA3_REV) from exc
    model = DepthAnything3.from_pretrained(source)
    return model.to(torch.device(device)).eval()


def load_moge(weights: Path, device: str = "cpu"):
    """MoGe-2 ViT-S from the local snapshot, or the hub repo id when that dir is missing."""
    source = pretrained_source(weights, MOGE_WEIGHTS)
    try:
        import torch
        from moge.model.v2 import MoGeModel
    except ImportError as exc:
        raise _missing("MoGe-2", weights, f"{MOGE_CODE_REPO}@{MOGE_REV}") from exc
    model = MoGeModel.from_pretrained(source)
    return model.to(torch.device(device)).eval()


def _as_numpy(value) -> np.ndarray:
    import torch

    if isinstance(value, torch.Tensor):
        return value.detach().float().cpu().numpy()
    return np.asarray(value)


def infer_pi3_predictions(
    images: list[np.ndarray],
    cameras: list[dict],
    params: DenseParams,
    device: str = "cuda",
) -> list[dict]:
    """One π³ window prediction per same-facing group. Points stay in the model frame.

    ``_from_predictions`` aligns them to the SfM centres. This does not download weights.
    """
    from server.dense.windows import build_windows, net_wh

    if len(images) < 3:
        raise FileNotFoundError("π³ needs at least 3 frames")
    model = load_pi3(params.resolved_weights(), device=device)
    import torch
    from PIL import Image

    frames = []
    for index, cam in enumerate(cameras):
        pose = np.asarray(cam["pose"], float)
        frames.append(
            {
                "epoch": cam.get("epoch", "e"),
                "timestamp": float(cam.get("timestamp", index)),
                "pose": pose,
            }
        )
    groups = build_windows(frames, params) if len(frames) >= 3 else []
    if not groups:
        groups = [list(range(min(len(images), max(3, params.window))))]
    predictions = []
    for group in groups:
        rgbs = []
        for index in group:
            rgb = np.asarray(images[index])
            if rgb.ndim != 3:
                continue
            height, width = rgb.shape[:2]
            net_w, net_h = net_wh(width, height)
            if (width, height) != (net_w, net_h):
                rgb = np.asarray(
                    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).resize(
                        (net_w, net_h), Image.Resampling.LANCZOS
                    )
                )
            rgbs.append(rgb)
        if len(rgbs) < 3:
            continue
        batch = torch.from_numpy(np.stack(rgbs)).permute(0, 3, 1, 2).float().to(device) / 255.0
        autocast = torch.autocast(device, dtype=torch.bfloat16) if device != "cpu" else nullcontext()
        with torch.inference_mode(), autocast:
            out = model(batch[None])
        if not isinstance(out, dict):
            out = {key: getattr(out, key) for key in ("points", "conf", "camera_poses") if hasattr(out, key)}
        points = _as_numpy(out["points"])
        poses = _as_numpy(out["camera_poses"])
        if points.ndim == 5:
            points = points[0]
        if poses.ndim == 4:
            poses = poses[0]
        if points.shape[-1] > 3:
            points = points[..., :3]
        centers = np.asarray(poses[:, :3, 3], float)
        flat = points.reshape(points.shape[0], -1, 3)
        stride = max(1, int(params.stride))
        kept = flat[:, :: stride * stride, :].reshape(-1, 3)
        finite = np.isfinite(kept).all(axis=1)
        predictions.append({"frames": list(group), "pred_centers": centers, "points": kept[finite]})
    del model
    if device != "cpu":
        torch.cuda.empty_cache()
    if not predictions:
        raise FileNotFoundError("π³ produced no windows")
    return predictions


def _resize_rgb(rgb: np.ndarray, net_w: int, net_h: int) -> np.ndarray:
    from PIL import Image

    height, width = rgb.shape[:2]
    if (width, height) == (net_w, net_h):
        return rgb
    return np.asarray(
        Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).resize((net_w, net_h), Image.Resampling.LANCZOS)
    )


def infer_moge_depths(
    images: list[np.ndarray],
    cameras: list[dict],
    params: DenseParams,
    device: str = "cuda",
) -> dict:
    """MoGe-2 depth maps for coverage. Not used as measurement points (SD-65)."""
    from server.dense.windows import net_wh

    if not images:
        return {}
    model = load_moge(params.resolved_weights(), device=device)
    import torch

    limit = max(1, int(params.max_depth_frames))
    maps: dict = {}
    try:
        for index, (rgb, cam) in enumerate(zip(images, cameras, strict=False)):
            if index >= limit:
                break
            rgb = np.asarray(rgb)
            if rgb.ndim != 3:
                continue
            height, width = rgb.shape[:2]
            net_w, net_h = net_wh(width, height)
            rgb = _resize_rgb(rgb, net_w, net_h)
            tensor = torch.from_numpy(rgb).permute(2, 0, 1).float().to(device) / 255.0
            with torch.inference_mode():
                depth_out = model.infer(tensor, resolution_level=9, fov_x=60.0, use_fp16=device != "cpu")
            depth = _as_numpy(depth_out["depth"])
            # Quarter resolution, matching the maps coverage reads.
            quarter = np.asarray(depth[::4, ::4], np.float32)
            key = str(cam.get("keyframe_id") or cam.get("image") or index)
            maps[key] = {"depth": quarter, "scale": 1.0, "source": "moge2"}
    finally:
        del model
        if device != "cpu":
            torch.cuda.empty_cache()
    if not maps:
        raise FileNotFoundError("MoGe-2 produced no depth maps")
    return maps


def infer_da3_second_opinion(
    images: list[np.ndarray],
    params: DenseParams,
    device: str = "cuda",
) -> dict:
    """DA3-Large second opinion. Depth stays out of the measurement cloud (SD-65)."""
    if len(images) < 1:
        raise FileNotFoundError("DA3 needs at least one frame")
    model = load_da3(params.resolved_weights(), device=device)
    finite = 0
    frames = 0
    try:
        chunk = 8
        for start in range(0, len(images), chunk):
            batch = [np.asarray(rgb) for rgb in images[start : start + chunk] if np.asarray(rgb).ndim == 3]
            if not batch:
                continue
            prediction = model.inference(batch, export_dir=None)
            depth = _as_numpy(prediction.depth)
            finite += int(np.isfinite(depth).sum())
            frames += len(batch)
    finally:
        del model
        if device != "cpu":
            import torch

            torch.cuda.empty_cache()
    if frames == 0:
        raise FileNotFoundError("DA3 produced no frames")
    return {"frames": frames, "finite": finite}


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve dense-model weights (no download unless --fetch).")
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    weights = dense_params_from_env().resolved_weights()
    print(f"HSS_DENSE_WEIGHTS={weights}")
    print(f"pins: {PI3_PIN} {DA3_PIN} {MOGE_PIN} {MOGE_WEIGHTS}")
    if not args.fetch:
        for repo in (PI3_REPO, DA3_REPO, MOGE_WEIGHTS):
            folder = local_snapshot(weights, repo)
            print(f"{'cached' if folder else 'missing'} {repo}")
        print("fetch skipped (pass --fetch to download into the weights dir)")
        return
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub is not installed; weights were not downloaded")
        return
    weights.mkdir(parents=True, exist_ok=True)
    for repo in (PI3_REPO, DA3_REPO, MOGE_WEIGHTS):
        if local_snapshot(weights, repo) is not None:
            print(f"cached {repo}")
            continue
        snapshot_download(repo, local_dir=str(snapshot_dir(weights, repo)))
        print(f"fetched {repo}")


if __name__ == "__main__":
    main()
