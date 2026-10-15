"""Where CLIP, SigLIP and OWLv2 run.

`auto` uses CUDA when this process can see a GPU, and CPU otherwise. Tests and
machines without a GPU stay on CPU. Half precision is CUDA only: the CPU path
stays fp32 so the committed parity numbers still match.
"""

from __future__ import annotations

import gc
import os

# CLIP / SigLIP stack cleanly. OWLv2 boxes are larger; 4 keeps an L4 under its memory cap.
_GATE_BATCH = 8
_OWL_BATCH = 4


def resolve_device(requested: str | None = None) -> str:
    """`cpu`, or `cuda` when a CUDA device is actually available."""
    raw = requested if requested is not None else os.environ.get("HSS_OBJECTS_DEVICE", "auto")
    raw = (raw or "auto").strip().lower()
    if raw == "cpu":
        return "cpu"
    if raw not in {"auto", "cuda"}:
        raise ValueError(f"unknown HSS_OBJECTS_DEVICE {raw}")
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def use_half(device: str) -> bool:
    return device == "cuda"


def gate_batch(device: str) -> int:
    return _GATE_BATCH if device == "cuda" else 1


def owl_batch(device: str) -> int:
    return _OWL_BATCH if device == "cuda" else 1


def move_batch(batch: dict, device: str, half: bool) -> dict:
    import torch

    moved = {}
    for key, value in batch.items():
        if not torch.is_tensor(value):
            moved[key] = value
            continue
        value = value.to(device)
        if half and value.is_floating_point():
            value = value.half()
        moved[key] = value
    return moved


def to_cpu_float(outputs):
    """Postprocess on CPU fp32. Half GPU tensors are not what the box decoder was measured with."""
    import torch

    for key, value in list(outputs.items()):
        if torch.is_tensor(value) and value.is_floating_point():
            outputs[key] = value.detach().float().cpu()
        elif torch.is_tensor(value):
            outputs[key] = value.detach().cpu()
    return outputs


def release_cuda() -> None:
    gc.collect()
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def memory_snapshot() -> dict[str, float]:
    """Peak RSS, and CUDA allocated bytes when a GPU was used. Both are for the report, not a limit."""
    import resource

    # Linux reports kilobytes. The stage report stores that number as-is.
    snap = {"rss_kb": float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)}
    try:
        import torch
    except ImportError:
        return snap
    if torch.cuda.is_available():
        snap["cuda_peak_bytes"] = float(torch.cuda.max_memory_allocated())
    return snap
