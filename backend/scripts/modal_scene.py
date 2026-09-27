"""Dense and depth on Modal (L4). Stays inside the WS-06 $40 cap; this entry runs at most 8 frames.

``uv run modal run scripts/modal_scene.py --images-dir DIR --max-frames 4``

The script never prints token values. Weights cache on the ``hss-ws06-weights`` volume.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import modal
import numpy as np

from server.dense.models import DA3_REPO, MOGE_WEIGHTS, PI3_REPO, pretrained_source
from server.dense.params import dense_params_from_env

APP_NAME = "hss-ws06-dense"
VOL_NAME = "hss-ws06-weights"
GPU = "L4"
# Published Modal rate used by the housepipe bench. A hard timeout keeps one run under a dollar.
GPU_USD_PER_S = 0.000222
CPU_USD_PER_S = 0.0000131
MAX_FRAMES = 8
GPU_TIMEOUT_S = 8 * 60
PROVE_TIMEOUT_S = 25 * 60
FETCH_TIMEOUT_S = 20 * 60
SPEND_STOP_USD = 30.0

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12")
    .env(
        {
            "HF_HOME": "/vol/hf",
            "HUGGINGFACE_HUB_CACHE": "/vol/hf/hub",
            "TORCH_HOME": "/vol/hf/torch",
            "PYTHONUNBUFFERED": "1",
        }
    )
    .pip_install("torch==2.5.1", "torchvision==0.20.1", index_url="https://download.pytorch.org/whl/cu124")
    .pip_install(
        "numpy==1.26.4",
        "opencv-python-headless==4.10.0.84",
        "huggingface_hub==0.26.2",
        "safetensors==0.4.5",
        "einops==0.8.0",
        "pillow==10.4.0",
    )
    .apt_install("git", "libgl1", "libglib2.0-0")
    .run_commands(
        "pip install --no-cache-dir https://codeload.github.com/yyfz/Pi3/tar.gz/9fa3ddb3f8d53041f8b2738df404f62223bbaa7b",
        "pip install --no-cache-dir https://codeload.github.com/microsoft/MoGe/tar.gz/925b8ed835a7a9cdb7578ba15c658a0afc969030",
        "pip install --no-cache-dir https://codeload.github.com/ByteDance-Seed/Depth-Anything-3/tar.gz/3d835ec1a5802d64a8b8b15f817a1ab54809bfe4",
        "pip install --no-cache-dir torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124",
        "pip install --no-cache-dir --force-reinstall opencv-python-headless==4.10.0.84 addict",
    )
    .pip_install("open3d==0.20.0", "pydantic>=2.13.5", "matplotlib==3.11.2", "jsonschema==4.26.0")
    .run_commands("pip install --no-cache-dir numpy==1.26.4")
    .apt_install("libegl1", "libopengl0", "libgl1-mesa-dri", "libusb-1.0-0", "libx11-6", "libxext6", "libsm6")
    .add_local_python_source("server")
)

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOL_NAME, create_if_missing=True)


def _net_wh(width: int, height: int, side: int = 518, patch: int = 14) -> tuple[int, int]:
    if width >= height:
        nw = side
        nh = max(patch, int(round(side * height / width / patch) * patch))
    else:
        nh = side
        nw = max(patch, int(round(side * width / height / patch) * patch))
    return nw, nh


@app.function(image=image, cpu=2, timeout=FETCH_TIMEOUT_S, volumes={"/vol": volume})
def fetch_weights() -> dict:
    """CPU fetch so the GPU function does not pay to download multi-GB weights."""
    import os
    import time

    os.environ["HF_HOME"] = "/vol/hf"
    os.makedirs("/vol/hf", exist_ok=True)
    from huggingface_hub import snapshot_download

    started = time.perf_counter()
    repos = ("yyfz233/Pi3", "depth-anything/DA3-LARGE-1.1", "Ruicheng/moge-2-vits-normal")
    for repo in repos:
        snapshot_download(repo, local_dir=f"/vol/hf/{repo.replace('/', '--')}")
    volume.commit()
    return {"fetched": list(repos), "cpu_s": round(time.perf_counter() - started, 1)}


@app.function(image=image, gpu=GPU, cpu=2, timeout=GPU_TIMEOUT_S, volumes={"/vol": volume})
def dense_and_depth(max_frames: int = 4) -> dict:
    """One π³ window and one MoGe-2 depth map. DA3 is loaded and released, not fused into the cloud.

    Frames are read from the weights volume at ``/vol/in`` so the client does not upload multi-megabyte blobs as the call payload.
    """
    import os
    import time

    volume.reload()
    os.environ["HF_HOME"] = "/vol/hf"
    os.environ["HUGGINGFACE_HUB_CACHE"] = "/vol/hf/hub"
    os.environ.setdefault("HSS_DENSE_WEIGHTS", "/vol/hf")
    started = time.perf_counter()
    import numpy as np
    import torch
    from PIL import Image

    if not torch.cuda.is_available():
        return {"ok": False, "error": "cuda_unavailable"}
    paths = sorted(p for p in Path("/vol/in").glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    images = []
    for path in paths[: max(3, min(int(max_frames), MAX_FRAMES))]:
        img = Image.open(path).convert("RGB")
        nw, nh = _net_wh(img.width, img.height)
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        images.append(np.asarray(img))
    if len(images) < 3:
        return {"ok": False, "error": "need_at_least_3_frames"}

    from pi3.models.pi3 import Pi3

    weights = dense_params_from_env().resolved_weights()

    t0 = time.perf_counter()
    pi3 = Pi3.from_pretrained(pretrained_source(weights, PI3_REPO)).cuda().eval()
    batch = torch.from_numpy(np.stack(images)).permute(0, 3, 1, 2).float().cuda() / 255.0
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        out = pi3(batch[None])
    if not isinstance(out, dict):
        out = {k: getattr(out, k) for k in ("points", "conf", "camera_poses") if hasattr(out, k)}
    points = out["points"].detach().float().cpu().numpy()
    pi3_s = round(time.perf_counter() - t0, 3)
    del pi3, batch, out
    torch.cuda.empty_cache()

    from moge.model.v2 import MoGeModel

    t1 = time.perf_counter()
    moge = MoGeModel.from_pretrained(pretrained_source(weights, MOGE_WEIGHTS)).cuda().eval()
    tensor = torch.from_numpy(images[0]).permute(2, 0, 1).float().cuda() / 255.0
    with torch.inference_mode():
        depth_out = moge.infer(tensor, resolution_level=9, fov_x=60.0, use_fp16=True)
    depth = depth_out["depth"].detach().float().cpu().numpy()
    moge_s = round(time.perf_counter() - t1, 3)
    del moge, tensor, depth_out
    torch.cuda.empty_cache()

    t2 = time.perf_counter()
    da3_loaded = False
    da3_error = None
    try:
        from depth_anything_3.api import DepthAnything3

        da3 = DepthAnything3.from_pretrained(pretrained_source(weights, DA3_REPO)).cuda().eval()
        da3_loaded = True
        del da3
        torch.cuda.empty_cache()
    except Exception as exc:  # missing DA3 deps must not drop the π³ and MoGe results
        da3_error = f"{type(exc).__name__}: {exc}"
    da3_s = round(time.perf_counter() - t2, 3)
    elapsed = time.perf_counter() - started
    cost = round(elapsed * GPU_USD_PER_S, 4)
    return {
        "ok": True,
        "gpu": torch.cuda.get_device_name(0),
        "frames": len(images),
        "pi3_points": int(np.isfinite(points).all(-1).sum()) if points.ndim >= 2 else int(points.size),
        "pi3_s": pi3_s,
        "depth_shape": list(depth.shape),
        "depth_finite": int(np.isfinite(depth).sum()),
        "moge_s": moge_s,
        "da3_loaded": da3_loaded,
        "da3_error": da3_error,
        "da3_s": da3_s,
        "gpu_s": round(elapsed, 1),
        "estimated_gpu_usd": cost,
    }


def _write_measurement_wall(poses_dir: Path, write_ply) -> None:
    """SfM stand-in in front of the walking cameras. Image depth stays coverage-only."""
    xs = np.linspace(0.0, 46.0, 90)
    ys = np.linspace(0.2, 2.6, 24)
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    wall = np.stack([xx.ravel(), yy.ravel(), np.full(xx.size, -3.0)], axis=1)
    gx = np.linspace(0.0, 46.0, 40)
    gz = np.linspace(-2.5, -0.4, 8)
    gxx, gzz = np.meshgrid(gx, gz, indexing="ij")
    ground = np.stack([gxx.ravel(), np.zeros(gxx.size), gzz.ravel()], axis=1)
    poses_dir.mkdir(parents=True, exist_ok=True)
    write_ply(poses_dir / "sparse.ply", np.vstack([wall, ground]).astype(np.float32))


def _image_paths(folder: Path) -> list[Path]:
    return sorted(path for path in Path(folder).iterdir() if path.suffix.lower() in {".jpg", ".jpeg", ".png"})


def _packet_and_cameras(paths: list[Path], flow: str) -> tuple[dict, list[dict], list]:
    """Walking cameras, all facing the same way, so windows stay same-facing."""
    import numpy as np
    from PIL import Image

    cameras = []
    images = []
    keyframes = []
    for index, path in enumerate(paths):
        rgb = Image.open(path).convert("RGB")
        width, height = rgb.size
        images.append(np.asarray(rgb))
        pose = np.eye(4)
        pose[0, 3] = index * 0.3
        pose[1, 3] = 1.5
        cameras.append(
            {
                "keyframe_id": path.stem,
                "pose": pose.tolist(),
                "intrinsics": [float(max(width, height)), float(max(width, height)), width / 2, height / 2],
                "w": width,
                "h": height,
                "pose_source": "sfm",
            }
        )
        keyframes.append({"id": path.stem, "img": path.name})
    packet = {
        "packetId": paths[0].parent.name if paths else "packet",
        "flow": flow,
        "keyframes": keyframes,
        "taps": [
            {"label": "meter", "keyframe": keyframes[0]["id"], "pixel": [cameras[0]["w"] / 2, cameras[0]["h"] / 2]}
        ],
    }
    return packet, cameras, images


@app.function(image=image, gpu=GPU, cpu=4, memory=16384, timeout=PROVE_TIMEOUT_S, volumes={"/vol": volume})
def prove_gates(run_id: str) -> dict:
    """L4 timings and a scene this package wrote. Cached weights only; no download.

    Frames come from ``/vol/in/<run_id>/`` only, so an older upload cannot shadow this run.
    """
    import os
    import time

    import numpy as np

    volume.reload()
    os.environ["HF_HOME"] = "/vol/hf"
    os.environ["HUGGINGFACE_HUB_CACHE"] = "/vol/hf/hub"
    os.environ["HSS_DENSE_WEIGHTS"] = "/vol/hf"
    # Open3D's CPU VoxelBlockGrid loads EGL. The L4 image has no display.
    os.environ["EGL_PLATFORM"] = "surfaceless"
    os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
    os.environ["HSS_DENSE_GEOMETRY"] = "image"
    started = time.perf_counter()
    steps: dict[str, float] = {}

    def mark(name: str, **extra: object) -> None:
        """Flush a per-step clock so a later timeout still shows where the run was."""
        steps[name] = round(time.perf_counter() - started, 3)
        payload = {"checkpoint": name, "elapsed_s": steps[name], "steps": dict(steps), **extra}
        print(json.dumps(payload), flush=True)
        path = Path("/vol/out") / run_id / "gates-steps.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n")
        volume.commit()

    import torch

    from server.common.params import scene_params_from_env as coverage_params
    from server.coverage.run import run_coverage
    from server.dense.lidar_fuse import tsdf_points
    from server.dense.models import DA3_REPO, MOGE_WEIGHTS, PI3_REPO, local_snapshot
    from server.dense.params import DenseParams
    from server.dense.run import run_dense
    from server.eval.sceneio import read_ply, sha256_file, write_ply
    from server.scene.params import scene_params_from_env as scene_params
    from server.scene.run import build_scene

    if not torch.cuda.is_available():
        return {"ok": False, "error": "cuda_unavailable"}
    weights = Path("/vol/hf")
    missing = [repo for repo in (PI3_REPO, DA3_REPO, MOGE_WEIGHTS) if local_snapshot(weights, repo) is None]
    if missing:
        return {"ok": False, "error": "weights_missing", "missing": missing, "downloaded": False}

    root = Path("/vol/in") / run_id
    scale = {"source": "stub", "scale_factor": 1.0, "origin_m": [0.0, 0.0, 0.0], "rel_bound_95": 0.05}
    # Probe Open3D before the frame sets so a missing library does not burn the L4 pass.
    plane = np.full((64, 64), 2.0, np.float32)
    try:
        plane_vertices = int(
            len(
                tsdf_points(
                    [plane],
                    [np.eye(4)],
                    [np.array([50.0, 50.0, 32.0, 32.0])],
                    [(64, 64)],
                    DenseParams(),
                )
            )
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "downloaded": False, "tsdf_probe": False}
    if plane_vertices <= 0:
        return {"ok": False, "error": "tsdf_plane_empty", "downloaded": False, "plane_vertices": plane_vertices}
    mark("tsdf_plane", plane_vertices=plane_vertices)

    def one_dense(name: str, flow: str, opinion: str, limit: int) -> dict:
        folder = root / name
        paths = _image_paths(folder)[:limit]
        if len(paths) < 3:
            return {"ok": False, "error": "need_frames", "found": len(paths), "folder": str(folder)}
        packet, cameras, images = _packet_and_cameras(paths, flow)
        out = Path("/vol/out") / run_id / name / "dense"
        params = DenseParams(
            geometry="image",
            second_opinion=opinion,
            weights_dir=weights,
            max_depth_frames=100 if opinion == "none" else 76,
            # Walking stand-in poses are not a COLMAP solve, so the pose-consistency
            # flag would drop every window. Inference still runs; the cloud is kept
            # so coverage and the scene writer have points.
            window_rms_max_m=1.0e6,
            window_scale_dev_max=1.0e6,
        )
        poses_dir = out.parent / "poses"
        if name == "yt":
            # Image points never measure (SD-65). A src-0 wall lets the scene stage fit.
            _write_measurement_wall(poses_dir, write_ply)
        report = run_dense(
            packet,
            poses_dir,
            scale,
            out,
            params=params,
            models={"cameras": cameras, "images": images, "flow": flow, "device": "cuda", "packet_dir": str(folder)},
        )
        return {
            "ok": True,
            "frames": len(paths),
            "dense_s": report.timings.get("dense_s"),
            "second_opinion": report.second_opinion,
            "geometry_role": report.geometry_role,
            "warnings": report.warnings,
            "counts": report.counts,
            "dense_dir": str(out),
            "packet": packet,
            "cameras": cameras,
        }

    yt = one_dense("yt", "guided", "none", 153)
    if not yt.get("ok"):
        return {"ok": False, "stage": "yt", "steps": steps, **{k: v for k, v in yt.items() if k != "packet"}}
    mark(
        "yt_dense",
        dense_s=yt.get("dense_s"),
        counts=yt.get("counts"),
        warnings=yt.get("warnings"),
    )

    # Open3D TSDF on the MoGe maps the stage wrote. Photo depth is often past the
    # 5 m LiDAR cap, so this diagnostic fuse uses a wider truncation.
    dense_dir = Path(yt["dense_dir"])
    depth_paths = sorted((dense_dir / "depth").glob("*.npz"))[:3]
    moge_vertices = 0
    moge_depth = {}
    if depth_paths:
        depths, poses, intrinsics, whs = [], [], [], []
        by_id = {cam["keyframe_id"]: cam for cam in yt["cameras"]}
        for path in depth_paths:
            data = np.load(path)
            cam = by_id[path.stem]
            depth = np.asarray(data["depth"], np.float32)
            finite = depth[np.isfinite(depth)]
            moge_depth[path.stem] = {
                "shape": list(depth.shape),
                "finite": int(finite.size),
                "min": float(finite.min()) if finite.size else None,
                "max": float(finite.max()) if finite.size else None,
            }
            depths.append(depth[::4, ::4])
            poses.append(np.asarray(cam["pose"], float))
            intrinsics.append(np.asarray(cam["intrinsics"], float))
            whs.append((int(cam["w"]), int(cam["h"])))
        try:
            moge_vertices = int(
                len(
                    tsdf_points(
                        depths,
                        poses,
                        intrinsics,
                        whs,
                        DenseParams(weights_dir=weights, lidar_max_m=8.0),
                    )
                )
            )
        except Exception as exc:
            moge_depth["error"] = f"{type(exc).__name__}: {exc}"
    mark("moge_tsdf", moge_vertices=moge_vertices)
    cov_dir = Path("/vol/out") / run_id / "yt" / "coverage"
    try:
        run_coverage(
            yt["packet"],
            dense_dir.parent / "poses",
            dense_dir,
            scale,
            cov_dir,
            params=coverage_params(),
            models={"flow": "guided"},
        )
        coverage_doc = json.loads((cov_dir / "coverage_report.json").read_text())
        mark("coverage", coverage_s=float(coverage_doc["timings"]["coverage_s"]))
        scene_dir = Path("/vol/out") / run_id / "yt" / "scene"
        scene_path = build_scene(
            yt["packet"],
            cov_dir,
            dense_dir,
            dense_dir.parent / "poses",
            scale,
            None,
            scene_dir,
            params=scene_params(),
        )
        scene = json.loads(Path(scene_path).read_text())
        ply_path = scene_dir / "points.ply"
        ply = read_ply(ply_path)
        asset = next(row for row in scene["assets"] if row.get("path") == "points.ply")
        digest = sha256_file(ply_path)
        build_doc = json.loads((scene_dir / "build_report.json").read_text())
        dense_ply = read_ply(scene_dir / "dense.ply")
        flagged_ply = read_ply(scene_dir / "flagged.ply")
        mark("scene", scene_s=float(build_doc["timings"]["scene_s"]))
    except Exception as exc:
        elapsed = time.perf_counter() - started
        volume.commit()
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "downloaded": False,
            "run_id": run_id,
            "steps": steps,
            "gpu_s": round(elapsed, 1),
            "estimated_gpu_usd": round(elapsed * GPU_USD_PER_S, 4),
            "yt": {
                key: yt[key] for key in ("frames", "dense_s", "second_opinion", "geometry_role", "warnings", "counts")
            },
            "tsdf": {
                "moge_vertices": moge_vertices,
                "moge_depth": moge_depth,
                "plane_vertices": plane_vertices,
                "open3d": True,
            },
        }

    partial = {
        "yt": {key: yt[key] for key in ("frames", "dense_s", "second_opinion", "geometry_role", "warnings", "counts")},
        "tsdf": {
            "moge_vertices": moge_vertices,
            "moge_depth": moge_depth,
            "plane_vertices": plane_vertices,
            "open3d": True,
        },
        "scene": {
            "points_dtype": str(ply["x"].dtype),
            "sha256": digest,
            "asset_sha256": asset["sha256"],
            "sha256_match": digest == asset["sha256"],
            "coverage_s": float(coverage_doc["timings"]["coverage_s"]),
            "scene_s": float(build_doc["timings"]["scene_s"]),
        },
    }
    partial_path = Path("/vol/out") / run_id / "gates-partial.json"
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path.write_text(json.dumps(partial) + "\n")
    volume.commit()
    try:
        facade = one_dense("facade", "photo", "da3", 76)
    except Exception as exc:
        facade = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    mark("facade", ok=bool(facade.get("ok")), dense_s=facade.get("dense_s"), error=facade.get("error"))
    elapsed = time.perf_counter() - started
    volume.commit()
    yt_s = float(yt["dense_s"])
    facade_s = float(facade.get("dense_s") or 0.0)
    coverage_s = float(coverage_doc["timings"]["coverage_s"])
    scene_s = float(build_doc["timings"]["scene_s"])
    return {
        "ok": bool(facade.get("ok"))
        and plane_vertices > 0
        and digest == asset["sha256"]
        and str(ply["x"].dtype) == "float32",
        "downloaded": False,
        "run_id": run_id,
        "steps": steps,
        "gpu": torch.cuda.get_device_name(0),
        "gpu_s": round(elapsed, 1),
        "estimated_gpu_usd": round(elapsed * GPU_USD_PER_S, 4),
        "yt": {key: yt[key] for key in ("frames", "dense_s", "second_opinion", "geometry_role", "warnings", "counts")},
        "facade": {
            key: facade.get(key)
            for key in ("ok", "frames", "dense_s", "second_opinion", "geometry_role", "warnings", "counts", "error")
        },
        "tsdf": {
            "moge_vertices": moge_vertices,
            "moge_depth": moge_depth,
            "plane_vertices": plane_vertices,
            "open3d": True,
        },
        "scene": {
            "points_dtype": str(ply["x"].dtype),
            "sha256": digest,
            "asset_sha256": asset["sha256"],
            "sha256_match": digest == asset["sha256"],
            "dense_dtype": str(dense_ply["x"].dtype),
            "flagged_rows": int(len(flagged_ply["x"])),
            "coverage_s": coverage_s,
            "scene_s": scene_s,
        },
        "caps": {
            "yt_dense_le_90": yt_s <= 90.0,
            "facade_dense_le_150": facade_s <= 150.0,
            "coverage_le_60": coverage_s <= 60.0,
            "scene_le_30": scene_s <= 30.0,
        },
    }


@app.local_entrypoint()
def main(images_dir: str = "", max_frames: int = 4, prove: int = 0, run_id: str = "") -> None:
    if prove:
        worst = PROVE_TIMEOUT_S * GPU_USD_PER_S
        if worst > SPEND_STOP_USD:
            raise SystemExit(f"refusing launch: worst-case ${worst:.2f} exceeds ${SPEND_STOP_USD:.0f}")
        if not run_id:
            run_id = f"gates-{int(time.time())}"
        stage = Path("/tmp") / run_id
        houses = {
            "yt": (
                Path(
                    "/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/data/houses/yt-LCPBf86oloE/frames"
                ),
                153,
            ),
            "facade": (
                Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/data/houses/eth3d-facade/frames"),
                76,
            ),
        }
        for name, (source, limit) in houses.items():
            dest = stage / name
            if dest.is_dir() and _image_paths(dest):
                continue
            dest.mkdir(parents=True, exist_ok=True)
            for path in _image_paths(source)[:limit]:
                shutil.copyfile(path, dest / path.name)
            subprocess.check_call(
                [sys.executable, "-m", "modal", "volume", "put", "--force", VOL_NAME, str(dest), f"in/{run_id}/{name}"]
            )
        result = prove_gates.remote(run_id)
        print(json.dumps(result, indent=1))
        return
    n = max(3, min(int(max_frames), MAX_FRAMES))
    folder = Path(images_dir)
    paths = sorted(p for p in folder.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})[:n]
    if len(paths) < 3:
        raise SystemExit(f"need at least 3 images in {folder}")
    # Worst-case GPU bill for the hard timeout. Refuse before launch if it would cross the stop line.
    worst = GPU_TIMEOUT_S * GPU_USD_PER_S + FETCH_TIMEOUT_S * 2 * CPU_USD_PER_S
    if worst > SPEND_STOP_USD:
        raise SystemExit(f"refusing launch: worst-case ${worst:.2f} exceeds ${SPEND_STOP_USD:.0f}")
    for path in paths:
        subprocess.check_call(
            [sys.executable, "-m", "modal", "volume", "put", "--force", VOL_NAME, str(path), f"in/{path.name}"]
        )
    fetched = fetch_weights.remote()
    result = dense_and_depth.remote(n)
    result["weight_fetch"] = fetched
    result["images"] = [p.name for p in paths]
    print(json.dumps(result, indent=1))
