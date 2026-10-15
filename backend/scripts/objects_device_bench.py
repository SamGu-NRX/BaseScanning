"""Per-frame CLIP / SigLIP / OWLv2 timing on the facade keyframes.

Throwaway Modal app only. Never deploys ``house-scanning``.

    uv run modal run -e dev scripts/objects_device_bench.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import modal
import torch

from server.objects.config import FrameRef
from server.objects.device import memory_snapshot, resolve_device
from server.objects.gate import embed_frames, release_gates
from server.objects.proposals import run_owlv2

APP_NAME = "hss-ws07-objects-cuda"
if APP_NAME == "house-scanning":
    raise SystemExit("refusing to run on the shared app")

app = modal.App(APP_NAME)
volume = modal.Volume.from_name("house-capture-pipeline", create_if_missing=False)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("torch==2.5.1", "torchvision==0.20.1", index_url="https://download.pytorch.org/whl/cu124")
    .pip_install(
        "transformers==4.51.3",
        "open_clip_torch==2.32.0",
        "pillow==12.3.0",
        "numpy==2.5.3",
        "pydantic>=2.13.5",
        "sentencepiece==0.2.0",
        "scipy==1.18.1",
    )
    .pip_install("torch==2.5.1", "torchvision==0.20.1", index_url="https://download.pytorch.org/whl/cu124")
    .env({"HF_HOME": "/vol/hf", "HUGGINGFACE_HUB_CACHE": "/vol/hf/hub", "TORCH_HOME": "/vol/hf/torch"})
    .add_local_dir(".fixtures/packets/eth3d-facade/keyframes", remote_path="/frames")
    .add_local_python_source("server")
)


def _cgroup_memory() -> dict[str, str]:
    found = {}
    for name in ("memory.max", "memory.peak", "memory.current"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            found[name] = path.read_text().strip()
    return found


def _time(device: str, ids: list[str]) -> dict:
    os.environ["HSS_OBJECTS_DEVICE"] = device
    frames = []
    for fid in ids:
        path = Path("/frames") / f"{fid}.jpg"
        if not path.is_file():
            continue
        frames.append(FrameRef(fid, path, 0, 0, "keyframe"))
    per: dict[str, dict[str, float]] = {}
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    for model in ("clip", "siglip"):
        timing: dict[str, float] = {}
        embed_frames(frames, model, device=device, timing=timing)
        release_gates()
        per[model] = timing
    owl: dict[str, float] = {}
    run_owlv2(frames, device=device, timing=owl)
    per["owlv2"] = owl
    summary = {}
    for model, rows in per.items():
        vals = list(rows.values())
        summary[model] = {
            "frames": len(vals),
            "mean_s": (sum(vals) / len(vals)) if vals else None,
            "max_s": max(vals) if vals else None,
            "per_frame_s": rows,
        }
    return {
        "requested_device": device,
        "device": resolve_device(device),
        "cuda": bool(torch.cuda.is_available()),
        "torch": torch.__version__,
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "models": summary,
        "memory": memory_snapshot(),
        "cgroup": _cgroup_memory(),
    }


@app.function(image=image, cpu=2, memory=16384, timeout=60 * 60, volumes={"/vol": volume})
def time_cpu(ids: list[str]) -> dict:
    return _time("cpu", ids)


@app.function(image=image, gpu="L4", cpu=8, memory=24576, timeout=60 * 30, volumes={"/vol": volume})
def time_gpu(ids: list[str]) -> dict:
    return _time("cuda", ids)


@app.local_entrypoint()
def main(cpu_limit: int = 8) -> None:
    packet = json.loads(Path("data/fixtures/packets/eth3d-facade/packet.arkit-nonlidar.json").read_text())
    ids = [row["id"] for row in packet["keyframes"]]
    cpu_ids = ids[:cpu_limit]
    cpu = time_cpu.remote(cpu_ids)
    gpu = time_gpu.remote(ids)
    print(json.dumps({"app": APP_NAME, "cpu": cpu, "gpu": gpu}, indent=1))
