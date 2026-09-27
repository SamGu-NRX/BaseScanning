"""Blur, exposure and gate scores. QA never drops a frame."""

from __future__ import annotations

import json

import cv2
import numpy as np

from server.contracts.api import HintData
from server.contracts.jobs import CommittedFile
from server.orchestrator.events import emit_hint
from server.orchestrator.services import Services
from server.orchestrator.stateio import put_json

BLUR_VAR = 60.0
DARK_MEAN = 40 / 255
BRIGHT_MEAN = 215 / 255
CLIPPED_SHARE = 0.15


def score_image(rgb) -> dict[str, float]:
    """``rgb`` is an ``H×W×3`` uint8 array."""
    array = np.asarray(rgb)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    mean = float(gray.mean()) / 255.0
    clipped = float(((gray <= 2) | (gray >= 253)).mean())
    return {
        "blur_var": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "mean_lum": mean,
        "clipped_share": clipped,
    }


def classify(stats: dict[str, float]) -> list[str]:
    codes: list[str] = []
    if stats["blur_var"] < BLUR_VAR:
        codes.append("frames_blurry")
    if stats["mean_lum"] < DARK_MEAN:
        codes.append("frames_dark")
    if stats["mean_lum"] > BRIGHT_MEAN or stats["clipped_share"] > CLIPPED_SHARE:
        codes.append("frames_overexposed")
    return codes


def on_commit(services: Services, capture_id: str, files: list[CommittedFile]) -> None:
    blurry_run = 0
    for item in files:
        if item.role not in {"keyframe", "still"}:
            continue
        stats = {"blur_var": BLUR_VAR, "mean_lum": 0.5, "clipped_share": 0.0}
        if services.storage.exists(item.key):  # type: ignore[attr-defined]
            stats = _stats_or_default(services.storage.get_bytes(item.key))  # type: ignore[attr-defined]
        codes = classify(stats)
        purpose = str((item.meta or {}).get("purpose") or "")
        if "frames_blurry" in codes and (purpose or item.role == "still"):
            emit_hint(
                services.state,
                capture_id,
                HintData(
                    code="frames_blurry", message="A photo looks blurry. Hold still and retake it.", frames=[item.path]
                ),
                clock=services.clock,
            )
        elif "frames_blurry" in codes:
            blurry_run += 1
            if blurry_run >= 3:
                emit_hint(
                    services.state,
                    capture_id,
                    HintData(code="frames_blurry", message="Several frames in a row look blurry.", frames=[item.path]),
                    clock=services.clock,
                )
                blurry_run = 0
        else:
            blurry_run = 0
        body = {"frame_id": item.path, "stats": stats, "codes": codes, "at": services.clock.iso()}
        put_json(services.state, f"early:{capture_id}:qa:{item.path}", body)
        services.storage.put_bytes(  # type: ignore[attr-defined]
            f"captures/{capture_id}/early/qa/{item.path}.json",
            json.dumps(body).encode(),
            "application/json",
        )


def _stats_or_default(data: bytes) -> dict[str, float]:
    try:
        array = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if array is None:
            return {"blur_var": BLUR_VAR, "mean_lum": 0.5, "clipped_share": 0.0}
        rgb = cv2.cvtColor(array, cv2.COLOR_BGR2RGB)
        return score_image(rgb)
    except Exception:
        return {"blur_var": BLUR_VAR, "mean_lum": 0.5, "clipped_share": 0.0}
