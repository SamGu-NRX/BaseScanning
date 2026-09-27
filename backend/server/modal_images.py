"""Modal images for the composed app.

The capture API uses the light CPU image only. The COLMAP image is built on its
own (`python -m server.prebuild_gpu_image`) and deployed by id, so publishing
the web function does not compile COLMAP.
"""

from __future__ import annotations

import os
from pathlib import Path

import modal


def _ignore_docker_context(path: object) -> bool:
    name = getattr(path, "name", None)
    if not isinstance(name, str):
        name = str(path).rsplit("/", 1)[-1]
    return name not in {"pyproject.toml", "uv.lock"}


def dockerfile_image() -> modal.Image:
    """COLMAP / torch image. No application source. This layer is the slow build."""
    return modal.Image.from_dockerfile(
        "Dockerfile",
        context_dir=".",
        ignore=_ignore_docker_context,
    )


def gpu_image() -> modal.Image:
    """GPU function image. `HSS_GPU_IMAGE_ID` selects a finished prebuild."""
    image_id = os.environ.get("HSS_GPU_IMAGE_ID")
    base = modal.Image.from_id(image_id) if image_id else dockerfile_image()
    image = base.add_local_python_source("server")
    meter_models = Path("data/meter-models")
    if meter_models.is_dir():
        image = image.add_local_dir("data/meter-models", remote_path="/root/data/meter-models")
    return image


def cpu_base_image() -> modal.Image:
    """Dependencies for the capture API. No COLMAP toolchain."""
    return modal.Image.debian_slim(python_version="3.12").apt_install("libgl1", "libglib2.0-0").uv_sync(extras=["api"])


CPU_IMAGE = cpu_base_image().add_local_python_source("server")
