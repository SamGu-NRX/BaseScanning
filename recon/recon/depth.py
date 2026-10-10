"""A metric depth map per frame: the phone's LiDAR when it has one, otherwise MoGe-2 rescaled by
features triangulated with the AR poses.

LiDAR depth is metric as measured and is used as is, keeping medium and high confidence only.
Keyframes a LiDAR capture saved without depth get none (`depth_maps` says why).

The photos-only path is method (b) of the evals (experiments/evals README section 3 on t3/evals):
MoGe-2 predicts each photo's depth with a scale that is a few percent wrong and different per
photo; SIFT features matched across the photo and its nearest neighbours facing the same way are
triangulated with the AR poses, and each photo's depth is multiplied by median(z_tri / z_pred).
Measured there on ETH3D electro, 8 photos, 1 to 3 m spans on walls: p90 2.8 in [2.0, 4.6] with
exact poses, 5.0 in [3.7, 6.9] with an assumed 2% pose scale error, 10.8 in [8.6, 13.1] with the
2018 iPhone's measured pose error. Those assume perfect feature matching; with real matching
walls reached 6.3 in even with exact poses.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from recon.capture import UP, Capture, Frame, UnsafePath
from recon.triangulate import view_scales

MAX_SIDE = 640  # MoGe-2 input; keeps the model process under 4 GB (2.97 GB peak at 640 x 480)
MIN_CONFIDENCE = 1  # ARKit confidence: 0 low, 1 medium, 2 high
NEIGHBOURS = 7
MODELS = Path(__file__).resolve().parents[1] / "models"
MOGE_SCRIPT = MODELS / "moge_depth.py"  # pins the checkpoint revision and resolution level
JPEG_QUALITY = 95
ACCURACY_NOTE = {
    "lidar": "LiDAR depth as measured; this worker has no measured error bar for iPhone LiDAR.",
    "moge2-triangulated": (
        "MoGe-2 rescaled per photo by features triangulated with the AR poses. On ETH3D, wall "
        "p90 over 1 to 3 m spans was 5.0 in [3.7, 6.9] at an assumed 2% pose scale error and "
        "10.8 in [8.6, 13.1] at the 2018 iPhone's measured pose error."
    ),
}


@dataclass
class Depth:
    depth: np.ndarray  # float32 meters, z along the camera axis, NaN where unknown
    intrinsics: (
        np.ndarray
    )  # fx, fy, cx, cy for this map's resolution, (0, 0) at the top-left corner
    color: np.ndarray  # BGR uint8 image at the same resolution
    source: str


def scaled(intrinsics: np.ndarray, s: float) -> np.ndarray:
    return intrinsics * s


def upright_turns(cam_to_world: np.ndarray) -> int:
    """np.rot90 turns (counter-clockwise) that put world up at the top of the frame's image.
    Sensor images are sideways for a phone held upright; MoGe-2 was trained on upright photos."""
    x, y = (cam_to_world[:3, :3].T @ UP)[:2]  # world up in ARKit camera axes (+y is image up)
    if abs(y) >= abs(x):
        return 0 if y > 0 else 2
    return 1 if x > 0 else 3


def rotated_intrinsics(k: np.ndarray, w: float, h: float, turns: int) -> np.ndarray:
    """Intrinsics of the image turned by np.rot90(image, turns), for a w x h original, with pixel
    (0, 0) at the top-left corner: a turn takes (u, v) to (v, w - u)."""
    fx, fy, cx, cy = k
    return {
        0: np.array([fx, fy, cx, cy]),
        1: np.array([fy, fx, cy, w - cx]),
        2: np.array([fx, fy, w - cx, h - cy]),
        3: np.array([fy, fx, h - cy, cx]),
    }[turns % 4]


def lidar(frame: Frame) -> Depth:
    d = frame.lidar
    depth = np.fromfile(d.file, dtype="<f4")
    if depth.size != d.width * d.height:
        raise ValueError(f"{d.file}: {depth.size} values, expected {d.width} x {d.height}")
    depth = depth.reshape(d.height, d.width).copy()
    if d.confidence is not None:
        conf = np.fromfile(d.confidence, dtype=np.uint8).reshape(d.height, d.width)
        depth[conf < MIN_CONFIDENCE] = np.nan
    depth[~(depth > 0)] = np.nan
    color = cv2.resize(_image(frame), (d.width, d.height), interpolation=cv2.INTER_AREA)
    return Depth(depth, scaled(frame.intrinsics, d.width / frame.width), color, "lidar")


def _moge_cached(path: Path) -> bool:
    """The cached prediction is usable: it opens and holds a 2-D depth array. A file the model
    process left truncated mid-write is treated as absent, so the next run regenerates it
    instead of failing here on np.load -- and if regeneration fails too, the load at the end
    still fails loudly rather than producing a false result."""
    try:
        with np.load(path, allow_pickle=False) as data:
            depth = data["depth"]
            return depth.ndim == 2 and depth.size > 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return False


def _image(frame: Frame) -> np.ndarray:
    img = cv2.imread(str(frame.image), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(frame.image)
    if img.shape[:2] != (frame.height, frame.width):
        got, want = f"{img.shape[1]}x{img.shape[0]}", f"{frame.width}x{frame.height}"
        raise ValueError(f"{frame.image}: {got}, keyframe says {want}")
    return img


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def moge_key(capture: Capture) -> dict:
    """Everything MoGe-2's depth maps for a capture depend on: each frame's image bytes, pose (it
    sets the upright turn) and intrinsics (the field of view); the model process, by the bytes of
    its script (checkpoint revision, resolution level) and lockfile (library versions); and the
    resizing and JPEG quality of its input. Frame IDs and folder names repeat across captures."""
    return {
        "model_script_sha256": _sha256(MOGE_SCRIPT),
        "model_lock_sha256": _sha256(MODELS / "uv.lock"),
        "max_side": MAX_SIDE,
        "jpeg_quality": JPEG_QUALITY,
        "frames": [
            {
                "id": f.id,
                "image_sha256": _sha256(f.image),
                "w": f.width,
                "h": f.height,
                "intrinsics": f.intrinsics.tolist(),
                "pose": f.cam_to_world.tolist(),
            }
            for f in capture.frames
        ],
    }


def moge_cache(capture: Capture, work: Path) -> Path:
    """The capture's MoGe-2 cache folder under `work`, named by a hash of `moge_key` and holding
    that key. Cached depth maps there are kept only when the stored key equals this capture's;
    otherwise (a hash collision, a folder written without a key) they are deleted, to recompute."""
    key = json.dumps(moge_key(capture), sort_keys=True)
    folder = work / hashlib.sha256(key.encode()).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    key_file = folder / "key.json"
    if not key_file.exists() or json.loads(key_file.read_text()) != json.loads(key):
        stale = sorted(folder.glob("*.moge2.npz"))
        if stale:
            print(
                f"depth: {folder} holds depth for other photos or another model; "
                f"recomputing its {len(stale)} maps",
                file=sys.stderr,
            )
        for path in stale:
            path.unlink()
        # Written before the model runs, so the maps a run finishes before failing stay reusable.
        key_file.write_text(key)
    return folder


def cache_file(work: Path, fid: str, suffix: str) -> Path:
    """`work/<fid><suffix>`, refused unless it resolves to a file directly in `work`. The
    readers already refuse unsafe ids (`capture.frame_id`); this second check covers any Capture
    built another way, since the path is written to and handed to the model process."""
    path = work / f"{fid}{suffix}"
    if path.resolve().parent != work.resolve():
        raise UnsafePath(f"cache file for keyframe {fid!r} resolves outside {work}")
    return path


def moge(capture: Capture, work: Path) -> dict[str, Depth]:
    """MoGe-2 depth per frame at up to MAX_SIDE px, in the frame's own (unrotated) orientation,
    cached under `work` by the capture's content (`moge_cache`)."""
    work = moge_cache(capture, work)
    manifest, meta = [], {}
    for f in capture.frames:
        img = _image(f)
        s = min(1.0, MAX_SIDE / max(f.width, f.height))
        small = cv2.resize(
            img, (round(f.width * s), round(f.height * s)), interpolation=cv2.INTER_AREA
        )
        k_small = scaled(f.intrinsics, small.shape[1] / f.width)
        turns = upright_turns(f.cam_to_world)
        up_path = cache_file(work, f.id, ".upright.jpg")
        cv2.imwrite(str(up_path), np.rot90(small, turns), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        k_up = rotated_intrinsics(k_small, small.shape[1], small.shape[0], turns)
        out = cache_file(work, f.id, ".moge2.npz")
        meta[f.id] = (out, turns, k_small, small)
        if not _moge_cached(out):
            manifest.append({"image": str(up_path), "fx": float(k_up[0]), "out": str(out)})
    if manifest:
        (work / "moge2.json").write_text(json.dumps(manifest, indent=1))
        subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(MODELS),
                "python",
                str(MODELS / "moge_depth.py"),
                str(work / "moge2.json"),
            ],
            check=True,
            env={**os.environ},
        )
    depths = {}
    for fid, (out, turns, k_small, small) in meta.items():
        d = np.rot90(np.load(out)["depth"], -turns).astype(np.float32)
        if d.shape != small.shape[:2]:
            raise ValueError(f"{out}: depth {d.shape} does not match the image {small.shape[:2]}")
        depths[fid] = Depth(np.ascontiguousarray(d), k_small, small, "moge2")
    return depths


def _cv_pose(T: np.ndarray) -> np.ndarray:
    """ARKit camera axes to OpenCV's (+y down, +z forward)."""
    return T @ np.diag([1.0, -1.0, -1.0, 1.0])


def _cv_K(k: np.ndarray) -> np.ndarray:
    fx, fy, cx, cy = k
    return np.array([[fx, 0, cx - 0.5], [0, fy, cy - 0.5], [0, 0, 1.0]])


def neighbours(poses: dict[str, np.ndarray], fid: str, n: int = NEIGHBOURS) -> list[str]:
    """The frame and its n nearest frames looking the same way (within 60 degrees)."""
    T = poses[fid]
    fwd = -T[:3, 2]
    near = [k for k in poses if k != fid and -poses[k][:3, 2] @ fwd > 0.5]
    near.sort(key=lambda k: np.linalg.norm(poses[k][:3, 3] - T[:3, 3]))
    return [fid, *near[:n]]


def rescale(capture: Capture, depths: dict[str, Depth]) -> tuple[dict[str, Depth], dict]:
    """Multiplies each frame's MoGe-2 depth by its triangulated scale. Frames without enough
    triangulated points take the median of the others; with none at all this refuses."""
    poses = {f.id: f.cam_to_world for f in capture.frames}
    gray = {k: cv2.cvtColor(d.color, cv2.COLOR_BGR2GRAY) for k, d in depths.items()}
    fits = {}
    for fid in poses:
        group = neighbours(poses, fid)
        if len(group) < 2:
            continue
        fits[fid] = view_scales(
            {k: gray[k] for k in group},
            {k: _cv_K(depths[k].intrinsics) for k in group},
            {k: _cv_pose(poses[k]) for k in group},
            {k: depths[k].depth for k in group},
        )[fid]
    good = [f.scale for f in fits.values() if f.scale is not None]
    if not good:
        raise RuntimeError(
            "no frame had enough features triangulated with the AR poses to fix MoGe-2's scale; "
            "the photos need texture and overlap"
        )
    fallback = float(np.median(good))
    out, used = {}, {}
    for fid, d in depths.items():
        fit = fits.get(fid)
        fitted = fit is not None and fit.scale is not None
        s = fit.scale if fitted else fallback
        used[fid] = {"scale": s, "points": fit.points if fit else 0, "fitted": fitted}
        out[fid] = Depth(d.depth * np.float32(s), d.intrinsics, d.color, "moge2-triangulated")
    report = {
        "fitted": len(good),
        "frames": len(depths),
        "median_scale": fallback,
        "scale_range": [float(min(good)), float(max(good))],
        "per_frame": used,
    }
    return out, report


MIN_LIDAR_FRAMES = 2  # coverage needs a sample seen from two positions; one frame sees nothing


def depth_maps(capture: Capture, work: Path, mode: str) -> tuple[dict[str, Depth], dict]:
    """Depth per frame, keyed by frame id, and a report. mode: "auto" (LiDAR when at least
    MIN_LIDAR_FRAMES keyframes carry it, else MoGe-2), "lidar" or "moge".

    When only some keyframes carry LiDAR (a LiDAR phone that saved a frame without its depth
    map), the reconstruction uses those frames' LiDAR and gives the others no depth at all. They
    add nothing to the model and see nothing in coverage, so wall or ground that only they showed
    stays unobserved and the server answers UNSURE there, never clear. Filling them with MoGe-2
    instead would fuse a second, coarser source into the same volume: its wall p90 on ETH3D was
    about 5 in at a simulated 2% pose error, and the fused wall and every error bar sent with it
    would then have to carry that worse bound. It would also start a 3 GB model for a few frames.
    """
    with_lidar = [f for f in capture.frames if f.lidar is not None]
    if mode == "lidar" or (mode == "auto" and len(with_lidar) >= MIN_LIDAR_FRAMES):
        if len(with_lidar) < MIN_LIDAR_FRAMES:
            raise ValueError(
                f"--depth lidar, but only {len(with_lidar)} of {len(capture.frames)} keyframes "
                f"carry LiDAR depth; coverage needs at least {MIN_LIDAR_FRAMES}"
            )
        without = [f.id for f in capture.frames if f.lidar is None]
        if without:
            capture.notes.append(
                f"{len(without)} of {len(capture.frames)} keyframes carry no LiDAR depth "
                f"({', '.join(without[:5])}{', ...' if len(without) > 5 else ''}); they add "
                "nothing to the model, and what only they saw stays unobserved"
            )
        report = {"source": "lidar", "frames": len(with_lidar), "without_depth": without}
        return {f.id: lidar(f) for f in with_lidar}, report
    print(f"depth: MoGe-2 on {len(capture.frames)} frames", file=sys.stderr)
    raw = moge(capture, work / "moge2")
    depths, report = rescale(capture, raw)
    return depths, {"source": "moge2-triangulated", **report}
