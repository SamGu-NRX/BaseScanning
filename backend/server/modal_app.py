"""Modal image, app and volume for house-scanning.

WS-03 builds Pipeline on this app. smoke_cpu imports torch inside the function
because that wheel exists only on the Modal image.
"""

from __future__ import annotations

import base64
import json

import modal

APP_NAME = "house-scanning"
VOL_NAME = "house-capture-pipeline"
GPU_PRICES_USD_PER_S = {
    "L4": 0.000222,
    "A10G": 0.000306,
    "A100": 0.000583,
    "A100-40GB": 0.000583,
    "A100-80GB": 0.000694,
    "H100": 0.001097,
}
# CPU $0.0000131/core/s, memory $0.00000222/GiB/s, on top of the GPU.
CPU_USD_PER_S = 0.0000131
MEM_USD_PER_GIB_S = 0.00000222

COLMAP_BUILD = r"""
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  git cmake ninja-build build-essential \
  libboost-program-options-dev libboost-filesystem-dev libboost-graph-dev libboost-system-dev \
  libeigen3-dev libfreeimage-dev libmetis-dev libgoogle-glog-dev libgflags-dev \
  libsqlite3-dev libceres-dev libflann-dev libsuitesparse-dev libcurl4-openssl-dev \
  libglew-dev libcgal-dev
apt-get clean
rm -rf /var/lib/apt/lists/*
git clone --branch 3.11.1 --depth 1 https://github.com/colmap/colmap.git /tmp/colmap
cmake -S /tmp/colmap -B /tmp/colmap/build -GNinja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES="80;86;89;90" \
  -DGUI_ENABLED=OFF \
  -DTESTS_ENABLED=OFF \
  -DCMAKE_INSTALL_PREFIX=/usr/local
ninja -C /tmp/colmap/build -j3
ninja -C /tmp/colmap/build install
ldconfig
colmap -h > /tmp/colmap-help.txt || true
grep -q COLMAP /tmp/colmap-help.txt
head -n 5 /tmp/colmap-help.txt
rm -rf /tmp/colmap /tmp/colmap-help.txt
"""

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11")
    .env(
        {
            "DEBIAN_FRONTEND": "noninteractive",
            "HF_HOME": "/vol/hf",
            "HUGGINGFACE_HUB_CACHE": "/vol/hf/hub",
            "TORCH_HOME": "/vol/torch",
            "PYTHONUNBUFFERED": "1",
        }
    )
    .run_commands(
        # One Dockerfile RUN line. A multiline script is rejected by Modal's parser.
        "echo "
        + base64.b64encode(COLMAP_BUILD.encode()).decode()
        + " | base64 -d > /tmp/build-colmap.sh && bash /tmp/build-colmap.sh"
    )
    .pip_install(
        "torch==2.5.1",
        "torchvision==0.20.1",
        index_url="https://download.pytorch.org/whl/cu124",
    )
    .pip_install(
        "numpy==1.26.4",
        "pycolmap==3.11.1",
        "opencv-python-headless==4.10.0.84",
        "scipy==1.13.1",
        "huggingface_hub==0.26.2",
        "safetensors==0.4.5",
        "einops==0.8.0",
        "plyfile==1.1",
        "pillow==10.4.0",
        "tqdm==4.66.5",
    )
    .run_commands(
        # Tarball URLs, not git+https. The builder's git client prompts for
        # GitHub credentials and the old yyfz233/Pi3 path is gone.
        "pip install --no-cache-dir https://codeload.github.com/yyfz/Pi3/tar.gz/9fa3ddb3f8d53041f8b2738df404f62223bbaa7b",
        "pip install --no-cache-dir https://codeload.github.com/microsoft/MoGe/tar.gz/925b8ed835a7a9cdb7578ba15c658a0afc969030",
        "pip install --no-cache-dir https://codeload.github.com/ByteDance-Seed/Depth-Anything-3/tar.gz/3d835ec1a5802d64a8b8b15f817a1ab54809bfe4",
        # Those installs sometimes replace the CUDA wheel with a CPU torch.
        "pip install --no-cache-dir torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124",
    )
    .add_local_python_source("server")
)

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOL_NAME, create_if_missing=True)

CPU = 8
MEMORY_MIB = 24576


def _plain(obj):
    """JSON round-trip so the local client never unpickles torch or numpy types."""

    def default(value):
        item = getattr(value, "item", None)
        if callable(item):
            try:
                return item()
            except Exception:
                return str(value)
        return str(value)

    return json.loads(json.dumps(obj, default=default))


@app.function(image=image, cpu=2, timeout=20 * 60, volumes={"/vol": volume})
def smoke_cpu() -> dict:
    """Image import check. COLMAP's CUDA path is exercised on the GPU class."""
    import importlib
    import shutil
    import subprocess

    colmap = shutil.which("colmap")
    version = subprocess.run(["colmap", "-h"], check=False, text=True, capture_output=True)
    mods = {}
    for name in ("pycolmap", "torch", "pi3", "moge", "depth_anything_3"):
        try:
            importlib.import_module(name)
            mods[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            mods[name] = f"{type(exc).__name__}: {exc}"
    import torch

    from server.poses.colmap_prior import prior_binding_ok

    return _plain(
        {
            "colmap": colmap,
            "colmap_help_head": (version.stdout or version.stderr)[:400],
            "imports": mods,
            "torch": str(torch.__version__),
            "cuda_build": None if torch.version.cuda is None else str(torch.version.cuda),
            "prior_binding": prior_binding_ok(),
        }
    )
