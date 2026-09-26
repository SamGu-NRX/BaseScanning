"""Inputs, resampling geometry and output format shared by the model runners. numpy and OpenCV
only, so the geometry is testable without torch.

Conventions:
- intrinsics are [fx, fy, cx, cy] in pixels with OpenCV's convention: pixel (0, 0) has its centre
  at (0, 0), so the image spans [-0.5, W - 0.5];
- a network image is the input image scaled per axis by (sx, sy) and then cropped at (x0, y0), so a
  point at OpenCV pixel u in the input lies at u_net = sx * (u + 0.5) - x0 - 0.5 in the network
  image. `Resample` holds that map; every conversion between the two images goes through it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

DATA_ROOT = Path(os.environ.get("HOUSE_SCANNING_DATA", Path.home() / "house-scanning-data"))
CACHE_ROOT = DATA_ROOT / "evals"
MIN_FREE_BYTES_FOR_DOWNLOAD = 6 * 1024**3


def set_cache_dirs() -> dict[str, str]:
    """Keep Hugging Face and torch hub caches under the data root, where they are easy to delete,
    unless the caller already chose a place. Must run before torch or huggingface_hub is imported."""
    os.environ.setdefault("HF_HOME", str(CACHE_ROOT / "hf-cache"))
    os.environ.setdefault("TORCH_HOME", str(CACHE_ROOT / "torch-cache"))
    return {k: os.environ[k] for k in ("HF_HOME", "TORCH_HOME")}


def require_free_space_for_download(repo_id: str, filename: str, revision: str) -> None:
    """Refuse to start a checkpoint download with less than 6 GB free: the disk is shared and has
    run out before. A checkpoint already in the cache needs no space."""
    from huggingface_hub import try_to_load_from_cache

    cached = try_to_load_from_cache(repo_id, filename, revision=revision)
    if isinstance(cached, str) and Path(cached).exists():
        return
    free = shutil.disk_usage(Path.home()).free
    if free < MIN_FREE_BYTES_FOR_DOWNLOAD:
        raise RuntimeError(
            f"{repo_id}/{filename} is not cached and only {free / 1024**3:.1f} GB is free; "
            f"refusing to download with less than {MIN_FREE_BYTES_FOR_DOWNLOAD / 1024**3:.0f} GB free"
        )


def checkpoint_record(repo_id: str, filename: str, revision: str, sha256: str) -> dict:
    """Where the checkpoint came from, after checking its sha256 against the one Hugging Face lists
    for the pinned revision. (Cache blob names are not always the sha256, so it is recomputed.)"""
    from huggingface_hub import try_to_load_from_cache

    cached = try_to_load_from_cache(repo_id, filename, revision=revision)
    if not isinstance(cached, str):
        raise RuntimeError(f"{repo_id}/{filename}@{revision} should be cached after loading")
    h = hashlib.sha256()
    with open(cached, "rb") as f:
        while block := f.read(1 << 24):
            h.update(block)
    if h.hexdigest() != sha256:
        raise RuntimeError(f"{cached}: sha256 {h.hexdigest()} is not the pinned {sha256}")
    return {
        "repo": repo_id,
        "file": filename,
        "revision": revision,
        "sha256": sha256,
        "bytes": Path(cached).stat().st_size,
    }


# ---------------------------------------------------------------------------------------------
# Inputs


def read_image_list(path: Path) -> list[Path]:
    """One image path per line; blank lines and lines starting with # are skipped. Relative paths
    are relative to the list file. Stems name the outputs, so they must be unique."""
    images = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line).expanduser()
        if not p.is_absolute():
            p = path.parent / p
        if not p.is_file():
            raise FileNotFoundError(f"{path}: image {p} does not exist")
        images.append(p)
    if not images:
        raise ValueError(f"{path} lists no images")
    stems = [p.stem for p in images]
    duplicates = sorted({s for s in stems if stems.count(s) > 1})
    if duplicates:
        raise ValueError(
            f"{path}: image stems must be unique (outputs are <stem>.npz): {duplicates}"
        )
    return images


def _per_image(path: Path, images: list[Path], what: str) -> list:
    """A JSON file holding one entry per image: a list in --images order, or an object keyed by
    image path (as resolved from --images) or by image stem, covering every image."""
    data = json.loads(path.read_text())
    if isinstance(data, list):
        if len(data) != len(images):
            raise ValueError(f"{path}: {len(data)} {what} entries for {len(images)} images")
        return data
    if isinstance(data, dict):
        keyed = {str(Path(k).expanduser()): v for k, v in data.items()}
        entries, missing = [], []
        for p in images:
            if str(p) in keyed:
                entries.append(keyed[str(p)])
            elif p.stem in data:
                entries.append(data[p.stem])
            else:
                missing.append(p.stem)
        if missing:
            raise ValueError(f"{path}: no {what} keyed by path or stem for images {missing}")
        return entries
    raise ValueError(f"{path}: expected a JSON list or object of {what}, got {type(data).__name__}")


def read_intrinsics(path: Path, images: list[Path]) -> list[np.ndarray]:
    """[fx, fy, cx, cy] per image, in OpenCV pixels of that image."""
    out = []
    for p, entry in zip(images, _per_image(path, images, "intrinsics"), strict=True):
        k = np.asarray(entry, dtype=np.float64)
        if k.shape != (4,) or not np.all(np.isfinite(k)) or k[0] <= 0 or k[1] <= 0:
            raise ValueError(
                f"{path}: intrinsics for {p.stem} must be [fx, fy, cx, cy], got {entry}"
            )
        out.append(k)
    return out


def read_poses(path: Path, images: list[Path]) -> list[np.ndarray]:
    """4x4 camera-to-world per image, OpenCV camera (+x right, +y down, +z forward), metres."""
    out = []
    for p, entry in zip(images, _per_image(path, images, "poses"), strict=True):
        T = np.asarray(entry, dtype=np.float64)
        if T.shape != (4, 4) or not np.all(np.isfinite(T)):
            raise ValueError(f"{path}: pose for {p.stem} must be a 4x4 matrix")
        if not np.allclose(T[3], [0, 0, 0, 1]) or not np.allclose(
            T[:3, :3].T @ T[:3, :3], np.eye(3), atol=1e-4
        ):
            raise ValueError(f"{path}: pose for {p.stem} is not a rigid transform: {T.tolist()}")
        out.append(T)
    return out


def load_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"OpenCV could not read {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def check_intrinsics_fit(k: np.ndarray, width: int, height: int, name: str) -> None:
    """Catch intrinsics written for a different resolution: the principal point must lie inside."""
    if not (0 <= k[2] <= width - 1 and 0 <= k[3] <= height - 1):
        raise ValueError(
            f"{name}: principal point ({k[2]:.1f}, {k[3]:.1f}) is outside the {width}x{height} image; "
            "intrinsics must be in pixels of the image as listed"
        )


def matrix(k: np.ndarray) -> np.ndarray:
    fx, fy, cx, cy = k
    return np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])


def vector(K: np.ndarray) -> np.ndarray:
    return np.array([K[0, 0], K[1, 1], K[0, 2], K[1, 2]], dtype=np.float64)


# ---------------------------------------------------------------------------------------------
# Input image <-> network image


@dataclass(frozen=True)
class Resample:
    """Input image scaled to (resized_w, resized_h), then cropped to (net_w, net_h) at (x0, y0)."""

    in_w: int
    in_h: int
    resized_w: int
    resized_h: int
    x0: int
    y0: int
    net_w: int
    net_h: int

    @property
    def sx(self) -> float:
        return self.resized_w / self.in_w

    @property
    def sy(self) -> float:
        return self.resized_h / self.in_h

    def to_json(self) -> dict:
        return {
            "input_wh": [self.in_w, self.in_h],
            "resized_wh": [self.resized_w, self.resized_h],
            "crop_xy": [self.x0, self.y0],
            "network_wh": [self.net_w, self.net_h],
        }


def identity_resample(width: int, height: int) -> Resample:
    return Resample(width, height, width, height, 0, 0, width, height)


def stretch_resample(width: int, height: int, net_w: int, net_h: int) -> Resample:
    """Scale each axis to the network size, no crop (aspect ratio may change slightly)."""
    return Resample(width, height, net_w, net_h, 0, 0, net_w, net_h)


def cover_crop_resample(width: int, height: int, net_w: int, net_h: int) -> Resample:
    """Scale uniformly until the image covers the network size, then crop the centre."""
    s = max(net_w / width, net_h / height)
    rw, rh = max(net_w, round(width * s)), max(net_h, round(height * s))
    return Resample(width, height, rw, rh, (rw - net_w) // 2, (rh - net_h) // 2, net_w, net_h)


def longest_side_resample(width: int, height: int, max_side: int) -> Resample:
    """Uniform downscale so the longest side is at most max_side; never upscales."""
    s = min(1.0, max_side / max(width, height))
    rw, rh = max(1, round(width * s)), max(1, round(height * s))
    return Resample(width, height, rw, rh, 0, 0, rw, rh)


def patch_aligned_size(width: int, height: int, longest: int, patch: int) -> tuple[int, int]:
    """Network size with the input's aspect ratio, longest side `longest`, both sides multiples of
    `patch` (rounded to nearest)."""
    s = longest / max(width, height)
    return (
        max(patch, round(width * s / patch) * patch),
        max(patch, round(height * s / patch) * patch),
    )


def to_network_image(rgb: np.ndarray, r: Resample) -> np.ndarray:
    if rgb.shape[:2] != (r.in_h, r.in_w):
        raise ValueError(
            f"image is {rgb.shape[1]}x{rgb.shape[0]}, resample expects {r.in_w}x{r.in_h}"
        )
    if (r.resized_w, r.resized_h) != (r.in_w, r.in_h):
        down = r.resized_w * r.resized_h < r.in_w * r.in_h
        rgb = cv2.resize(
            rgb,
            (r.resized_w, r.resized_h),
            interpolation=cv2.INTER_AREA if down else cv2.INTER_CUBIC,
        )
    return np.ascontiguousarray(rgb[r.y0 : r.y0 + r.net_h, r.x0 : r.x0 + r.net_w])


def intrinsics_to_network(k: np.ndarray, r: Resample) -> np.ndarray:
    fx, fy, cx, cy = k
    return np.array(
        [fx * r.sx, fy * r.sy, (cx + 0.5) * r.sx - r.x0 - 0.5, (cy + 0.5) * r.sy - r.y0 - 0.5]
    )


def intrinsics_to_input(k_net: np.ndarray, r: Resample) -> np.ndarray:
    fx, fy, cx, cy = k_net
    return np.array(
        [fx / r.sx, fy / r.sy, (cx + 0.5 + r.x0) / r.sx - 0.5, (cy + 0.5 + r.y0) / r.sy - 0.5]
    )


def depth_to_input(
    depth_net: np.ndarray, valid_net: np.ndarray, r: Resample
) -> tuple[np.ndarray, np.ndarray]:
    """Resample a network-resolution depth map onto the input pixel grid.

    Depth is interpolated bilinearly. An input pixel is valid only if every network pixel the
    interpolation draws on is valid and it lies inside the network image's footprint (pixels the
    crop removed are invalid). Z-depth is unchanged by image scaling, so values are not rescaled.
    """
    if depth_net.shape != (r.net_h, r.net_w) or valid_net.shape != depth_net.shape:
        raise ValueError(f"network outputs are {depth_net.shape}, expected {(r.net_h, r.net_w)}")
    valid_net = valid_net & np.isfinite(depth_net) & (depth_net > 0)
    if r == identity_resample(r.in_w, r.in_h):
        return np.where(valid_net, depth_net, np.nan).astype(np.float32), valid_net
    u = r.sx * (np.arange(r.in_w, dtype=np.float64) + 0.5) - r.x0 - 0.5
    v = r.sy * (np.arange(r.in_h, dtype=np.float64) + 0.5) - r.y0 - 0.5
    map_x = np.broadcast_to(u[None, :], (r.in_h, r.in_w)).astype(np.float32)
    map_y = np.broadcast_to(v[:, None], (r.in_h, r.in_w)).astype(np.float32)
    filled = np.where(valid_net, depth_net, 0.0).astype(np.float32)
    depth = cv2.remap(filled, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    weight = cv2.remap(
        valid_net.astype(np.float32),
        map_x,
        map_y,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    inside = (map_x >= -0.5) & (map_x <= r.net_w - 0.5) & (map_y >= -0.5) & (map_y <= r.net_h - 0.5)
    valid = inside & (weight > 1.0 - 1e-4)
    return np.where(valid, depth, np.nan).astype(np.float32), valid


# ---------------------------------------------------------------------------------------------
# Runner interface


@dataclass(frozen=True)
class RunInputs:
    images: list[Path]
    intrinsics: list[np.ndarray] | None  # OpenCV pixels of each input image
    intrinsics_mode: str  # "known": fed to the model; "predicted": the model estimates them
    poses: list[np.ndarray] | None  # OpenCV camera-to-world, metres
    max_side: int | None
    device: str
    fp32: bool


@dataclass
class ImageResult:
    path: Path
    depth: np.ndarray  # float32 HxW metres at input resolution, NaN where invalid
    valid: np.ndarray  # bool HxW
    intrinsics: np.ndarray  # [fx, fy, cx, cy] OpenCV pixels of the input image
    seconds: float
    resample: Resample
    network_wh: tuple[int, int]  # image size the network itself ran at
    cam_to_world: np.ndarray | None = None
    arrays: dict[str, np.ndarray] = field(default_factory=dict)  # extra npz entries
    extra: dict = field(default_factory=dict)  # extra run.json fields


# ---------------------------------------------------------------------------------------------
# Outputs


def write_npz(
    out_dir: Path,
    stem: str,
    depth: np.ndarray,
    valid: np.ndarray,
    intrinsics: np.ndarray,
    cam_to_world: np.ndarray | None = None,
    extra_arrays: dict[str, np.ndarray] | None = None,
) -> Path:
    if depth.dtype != np.float32 or valid.dtype != bool or depth.shape != valid.shape:
        raise ValueError(f"{stem}: depth must be float32 and valid bool of one shape")
    arrays = {
        "depth": depth,
        "valid": valid,
        "intrinsics": np.asarray(intrinsics, dtype=np.float64).reshape(4),
    }
    if cam_to_world is not None:
        arrays["cam_to_world"] = np.asarray(cam_to_world, dtype=np.float64).reshape(4, 4)
    for name, value in (extra_arrays or {}).items():
        if name in arrays:
            raise ValueError(f"{stem}: extra array {name!r} would overwrite a standard one")
        arrays[name] = value
    path = out_dir / f"{stem}.npz"
    np.savez_compressed(path, **arrays)
    return path


def depth_summary(depth: np.ndarray, valid: np.ndarray) -> dict:
    d = depth[valid]
    if d.size == 0:
        return {"valid_fraction": 0.0}
    return {
        "valid_fraction": round(float(valid.mean()), 4),
        "median_m": round(float(np.median(d)), 3),
        "p5_m": round(float(np.percentile(d, 5)), 3),
        "p95_m": round(float(np.percentile(d, 95)), 3),
    }
