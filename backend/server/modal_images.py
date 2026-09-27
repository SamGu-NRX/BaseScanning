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


def stage_package_ignore(path: Path) -> bool:
    """Return True to leave a file out of the stage image.

    ``add_local_python_source`` keeps ``.py`` only. The result stage renders
    ``server/result/review/template.html``, criteria reads ``profiles.yaml``,
    and several stages load JSON or YAML schemas beside the package.
    """
    if path.name.startswith(".") or "__pycache__" in path.parts:
        return True
    suffix = path.suffix.lower()
    if suffix == ".py":
        return False
    if "tests" in path.parts:
        return True
    if suffix in {".html", ".css"} and "review" in path.parts:
        return False
    if suffix in {".json", ".yaml", ".yml"} and "schemas" in path.parts:
        return False
    if suffix in {".yaml", ".yml"} and "criteria" in path.parts:
        return False
    return True


def with_stage_package_data(image: modal.Image) -> modal.Image:
    """Python package plus the non-Python files every stage image reads."""
    image = image.add_local_python_source("server", ignore=stage_package_ignore)
    meter_models = Path("data/meter-models")
    if meter_models.is_dir():
        image = image.add_local_dir(meter_models, remote_path="/root/data/meter-models")
    return image


def gpu_image() -> modal.Image:
    """GPU function image. `HSS_GPU_IMAGE_ID` selects a finished prebuild.

    The COLMAP prebuild syncs the `gpu` extra only. CLIP, SigLIP and OWLv2 need
    transformers, open_clip and sentencepiece (SigLIP's tokenizer) on that same
    image. transformers 4.49.0 loads SigLIP2's processor with a null vocab file;
    4.51.3 loads it. Pip may replace the CUDA torch wheel, so the cu124 pin is
    installed again afterwards.
    """
    image_id = os.environ.get("HSS_GPU_IMAGE_ID")
    base = modal.Image.from_id(image_id) if image_id else dockerfile_image()
    base = base.pip_install(
        "transformers==4.51.3",
        "open_clip_torch==2.32.0",
        "sentencepiece==0.2.0",
    ).pip_install(
        "torch==2.5.1",
        "torchvision==0.20.1",
        index_url="https://download.pytorch.org/whl/cu124",
    )
    # The prebuilt COLMAP image has no GCS client. Remote workers hydrate the packet from it.
    # Installed after the cu124 pin so this layer does not replace the CUDA torch wheel.
    base = base.pip_install("google-cloud-storage>=2.18", "google-auth>=2.30")
    return with_stage_package_data(base)


def cpu_base_image() -> modal.Image:
    """Dependencies for the capture API. No COLMAP toolchain."""
    return modal.Image.debian_slim(python_version="3.12").apt_install("libgl1", "libglib2.0-0").uv_sync(extras=["api"])


CPU_IMAGE = with_stage_package_data(cpu_base_image())
