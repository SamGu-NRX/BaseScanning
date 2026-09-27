# vendored from internal/modal-pipeline/housepipe/models.py (store, 2026-09-26)
"""Load π³, DA3-Large and MoGe-2, caching Hugging Face weights on the volume."""

from __future__ import annotations

import os
import time

WEIGHTS = os.environ.get("HOUSE_WEIGHTS", "/vol/hf")


def prepare_cache() -> None:
    os.environ["HF_HOME"] = WEIGHTS
    os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(WEIGHTS, "hub")
    os.environ["TORCH_HOME"] = os.path.join(WEIGHTS, "torch")
    os.makedirs(WEIGHTS, exist_ok=True)
    import torch

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True


def load_models(which: tuple[str, ...] = ("pi3", "moge", "da3")) -> dict:
    prepare_cache()
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    models = {}
    timings = {}
    if "pi3" in which:
        from server.dense.dense import load_pi3

        t0 = time.perf_counter()
        models["pi3"] = load_pi3(device)
        timings["pi3_load_s"] = round(time.perf_counter() - t0, 3)
    if "moge" in which:
        from server.coverage.occlusion import load_moge

        t0 = time.perf_counter()
        models["moge"] = load_moge(device)
        timings["moge_load_s"] = round(time.perf_counter() - t0, 3)
    if "da3" in which:
        from server.dense.dense import load_da3

        t0 = time.perf_counter()
        models["da3"] = load_da3(device)
        timings["da3_load_s"] = round(time.perf_counter() - t0, 3)
    models["_load_s"] = timings
    return models
