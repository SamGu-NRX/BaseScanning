"""One Modal app, house-scanning, composed on WS-00's stub.

The stub's names stay: APP_NAME, VOL_NAME, the rate card, image, app, volume,
smoke_cpu. The capture API publishes on the light CPU image. GPU functions use
a prebuilt image id (`HSS_GPU_IMAGE_ID`) so this deploy does not compile the
COLMAP Dockerfile. WS-01's api_app and WS-03's functions are included when they exist.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess

import modal

from server.modal_images import CPU_IMAGE as CPU_IMAGE
from server.modal_images import gpu_image

# Default stays the shared app. A live probe sets MODAL_APP_NAME to a throwaway
# name so it does not replace the house-scanning deploy teammates use.
APP_NAME = os.environ.get("MODAL_APP_NAME", "house-scanning")
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

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOL_NAME, create_if_missing=True)
ENV = os.environ.get("SMP_ENVIRONMENT", "dev")


def _pipeline_image() -> modal.Image:
    """Image for GPU functions.

    `gpu_image()` without `HSS_GPU_IMAGE_ID` is `Image.from_dockerfile`. Modal
    builds every referenced image before the deployment commits, so that call
    would hold the capture-api web endpoint until COLMAP finishes compiling.
    The Dockerfile is built by `python -m server.prebuild_gpu_image` and passed
    in as an id. With no id, these functions sit on the CPU image and the web
    endpoint still publishes.
    """
    if os.environ.get("HSS_GPU_IMAGE_ID", "").strip():
        return gpu_image()
    return CPU_IMAGE


image = _pipeline_image()
GPU_IMAGE = image
# Canonical names (SD-73). WS-03 reads these keys. Do not alias them to
# gpu / min_containers / max_containers.
KNOBS = {
    "pipeline_gpu": os.environ.get("HSS_PIPELINE_GPU", "L4"),
    "pipeline_min_containers": int(os.environ.get("HSS_PIPELINE_MIN_CONTAINERS", "0")),
    "pipeline_max_containers": int(os.environ.get("HSS_PIPELINE_MAX_CONTAINERS", "3")),
    "pipeline_timeout_s": 1200,
    "pipeline_scaledown_s": 900,
}
CPU = 8
MEMORY_MIB = 24576

# SD-59. Local-dir trees live at HSS_DENSE_WEIGHTS. from_pretrained uses the
# hub cache under HF_HOME. HOUSE_WEIGHTS is the same root WS-06 prepare_cache
# reads today.
_WEIGHTS_ENV = {
    "HSS_DENSE_WEIGHTS": "/vol/hf",
    "HOUSE_WEIGHTS": "/vol/hf",
    "HF_HOME": "/vol/hf",
    "HUGGINGFACE_HUB_CACHE": "/vol/hf/hub",
    "TORCH_HOME": "/vol/hf/torch",
}
_PIPELINE_KNOBS = {
    "HSS_POSES_COLMAP_BIN": "/usr/local/bin/colmap",
    "HSS_DENSE_GEOMETRY": "auto",
    "HSS_SCALE_METER_MODELS_PATH": "/root/data/meter-models/meter_models.v1.json",
}


def _plain(obj: object) -> object:
    """JSON round-trip so the local client never unpickles torch or numpy types."""

    def default(value: object) -> object:
        item = getattr(value, "item", None)
        if callable(item):
            try:
                return item()
            except Exception:
                return str(value)
        return str(value)

    return json.loads(json.dumps(obj, default=default))


def secret_names(service: str) -> list[str]:
    # hss-<env>-vlm is not created yet. Attaching a missing Modal secret fails deploy.
    names = [f"hss-{ENV}-gcp"]
    if service in {"capture-api", "early-worker"}:
        names.append(f"hss-{ENV}-api")
    return names


# I-5 / SD-73. Dev only, until every stage package is merged. Prod keeps
# Settings defaults (hooks "null", stage stubs off).
_DEV_STAGE_ENV = {
    "HSS_INGEST_HOOKS": "auto",
    "HSS_ORCH_STAGE_STUBS": "1",
}


def deploy_env(service: str) -> dict[str, str]:
    fields = {
        "SMP_PRODUCT_ID": "house-scanning",
        "SMP_ENVIRONMENT": ENV,
        "SMP_SERVICE_NAME": service,
        "GIT_SHA": os.environ.get("GIT_SHA", "unknown"),
    }
    if service in {"pipeline", "sweeper"}:
        fields.update(_WEIGHTS_ENV)
    if service == "pipeline":
        fields.update(_PIPELINE_KNOBS)
    if service == "capture-api":
        fields["STORAGE_BACKEND"] = "gcs"
        fields["VOLUME_ROOT"] = "/vol"
        if ENV == "dev":
            # SD-86. The setting WS-01 already owns is HSS_AUTH_MODE.
            fields["HSS_AUTH_MODE"] = "none"
    if ENV == "dev":
        fields.update(_DEV_STAGE_ENV)
    return fields


def secrets_for(service: str) -> list[modal.Secret]:
    return [modal.Secret.from_name(name) for name in secret_names(service)] + [
        modal.Secret.from_dict(deploy_env(service))
    ]


def colmap_probe() -> tuple[str | None, str]:
    """Locate colmap. The CPU image has no binary, so this does not spawn one."""
    binary = shutil.which("colmap")
    if not binary:
        return None, ""
    version = subprocess.run([binary, "-h"], check=False, text=True, capture_output=True)
    return binary, (version.stdout or version.stderr)[:400]


@app.function(
    image=image,
    cpu=2,
    timeout=1200,
    volumes={"/vol": volume},
    secrets=secrets_for("pipeline"),
)
def smoke_cpu() -> dict:
    """Stub import check. No GPU is requested.

    On the prebuilt COLMAP image when `HSS_GPU_IMAGE_ID` is set, and on the
    CPU image otherwise, so a publish never compiles the Dockerfile.
    """
    colmap, colmap_help = colmap_probe()
    mods: dict[str, str] = {}
    for name in ("pycolmap", "torch", "pi3", "moge", "depth_anything_3"):
        try:
            importlib.import_module(name)
            mods[name] = "ok"
        except Exception as exc:
            mods[name] = f"{type(exc).__name__}: {exc}"
    torch_version = None
    cuda_build = None
    try:
        import torch

        torch_version = str(torch.__version__)
        cuda_build = None if torch.version.cuda is None else str(torch.version.cuda)
    except Exception as exc:
        mods["torch"] = f"{type(exc).__name__}: {exc}"
    try:
        from server.poses.colmap_prior import prior_binding_ok

        prior = prior_binding_ok()
    except ImportError:
        prior = None
    return _plain(
        {
            "ok": True,
            "app": APP_NAME,
            "env": ENV,
            "colmap": colmap,
            "colmap_help_head": colmap_help,
            "imports": mods,
            "torch": torch_version,
            "cuda_build": cuda_build,
            "prior_binding": prior,
        }
    )


@app.function(image=GPU_IMAGE, cpu=1, timeout=300, secrets=secrets_for("pipeline"))
def image_pin() -> dict:
    """Check the pinned GPU image. No GPU is requested.

    The image itself is prebuilt (`python -m server.prebuild_gpu_image`).
    This function does not compile COLMAP.

    pycolmap and torch are imported here because they exist only in the GPU
    image. Importing them at module level would break `import server.modal_app`
    on a checkout that has not installed the gpu extra.
    """
    import subprocess

    import pycolmap
    import torch

    completed = subprocess.run(["colmap", "-h"], check=False, capture_output=True, text=True)
    text = completed.stdout or completed.stderr
    first = text.splitlines()[0] if text else ""
    return {
        "pycolmap": pycolmap.__version__,
        "has_cuda": bool(pycolmap.has_cuda),
        "torch": torch.__version__,
        "colmap": first,
    }


if os.environ.get("HSS_GPU_IMAGE_ID", "").strip():

    @app.function(image=GPU_IMAGE, gpu="L4", cpu=2, timeout=180, secrets=secrets_for("pipeline"))
    def colmap_cuda() -> dict:
        """Assert the CUDA pycolmap build on an L4. Not a reconstruction bench.

        WS-04 calls pycolmap.extract_features / match_* with Device.auto. That
        stays on CPU when the PyPI wheel's has_cuda is false. This image
        installs pycolmap-cuda12 instead. The colmap CLI remains at
        HSS_POSES_COLMAP_BIN for callers that shell out.
        """
        import shutil
        import subprocess

        import pycolmap
        import torch

        binary = os.environ.get("HSS_POSES_COLMAP_BIN", "/usr/local/bin/colmap")
        if not pycolmap.has_cuda:
            raise RuntimeError("pycolmap.has_cuda is false")
        if not torch.cuda.is_available():
            raise RuntimeError("torch.cuda is not available")
        if shutil.which(binary) is None and not os.path.isfile(binary):
            raise RuntimeError(f"COLMAP binary missing: {binary}")
        completed = subprocess.run([binary, "-h"], check=False, capture_output=True, text=True)
        text = completed.stdout or completed.stderr
        return {
            "pycolmap": pycolmap.__version__,
            "has_cuda": True,
            "device": str(pycolmap.Device.cuda),
            "torch": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "gpu_name": torch.cuda.get_device_name(0),
            "colmap_bin": binary,
            "colmap": text.splitlines()[0] if text else "",
        }


def _include_present_modules() -> None:
    global api_app
    try:
        from server.api.modal_entry import api_app as loaded
    except ImportError:
        api_app = None
    else:
        api_app = loaded
        app.include(api_app)
    try:
        importlib.import_module("server.orchestrator.modal_functions")
    except ImportError:
        return


api_app = None
_include_present_modules()
