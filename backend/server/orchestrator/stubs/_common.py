"""Shared writers for the eleven stage stubs. Seed 0; no network."""

from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path

import numpy as np

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.config import load_orch

_PLY = b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n"


def sleep_local_delay() -> None:
    """Pause one scripted stage when local pacing is configured."""
    delay = load_orch().local_stage_delay_s
    if delay > 0:
        time.sleep(delay)


def write_files(ctx: StageContext, files: dict[str, bytes], *, sleep: bool = True) -> StageResult:
    # The dev-only scripted delay: each stub sleeps so the app sees the stage
    # move through processing. load_orch refuses a stray value at boot.
    if sleep:
        sleep_local_delay()
    artifacts: dict[str, str] = {}
    for key, data in files.items():
        path = ctx.work_dir / key
        if key.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            (path / ".keep").write_bytes(data or b"ok")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        artifacts[key] = key
    return StageResult(
        artifacts=artifacts,
        metrics={"duration_s": 0.01, "enter_s": 0.0, "vlm_cost_usd": 0.0},
        warnings=[],
    )


def json_bytes(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True).encode()


def ply() -> bytes:
    return _PLY


def tracks() -> bytes:
    buffer = io.BytesIO()
    np.savez(buffer, tracks=np.zeros((0, 2), dtype=np.int32))
    return buffer.getvalue()


def scene_document(scene_id: str) -> dict:
    return {
        "format": "base-scene/v0",
        "scene_id": scene_id,
        "provenance": {
            "data": "synthetic",
            "site": scene_id,
            "producer": {"path": "ws-03-stub", "params": {"scale": {"scale_factor": 1.0}}},
            "scale_source": "arkit_poses",
            "gravity_source": "arkit",
        },
        "frame": {
            "units": "m",
            "convention": "meter_anchored/v0",
            "anchor": {"element_id": "meter"},
        },
        "walls": [
            {
                "id": "w1",
                "role": "house",
                "baseline_m": [[-4, 0], [4, 0]],
                "height_m": 3,
                "plus_minus_m": 0.05,
                "evidence": "observed",
            }
        ],
        "chain": {
            "wall_ids": ["w1"],
            "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
        },
        "ground": {"surface": {"kind": "plane", "normal": [0, 1, 0], "offset_m": 0}, "plus_minus_m": 0.05},
        "objects": [
            {
                "id": "meter",
                "class": "meter",
                "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [-0.1, 0.1], "bottom_m": 1.4, "top_m": 1.8},
                "confidence": 1,
                "plus_minus_m": 0.02,
                "source": {"kind": "reconstruction"},
                "evidence": "observed",
            }
        ],
        "coverage": {"method": "stub"},
    }


def verdict_document(profile: str, scene_id: str) -> dict:
    return {
        "format": "base-verdict/v0",
        "verdict_id": f"{scene_id}-{profile}",
        "scene": {"scene_id": scene_id, "site": scene_id, "data": "synthetic"},
        "evaluator": {"path": "ws-03-stub", "rules_profile": profile},
        "site": {"decision": "manual_review", "reasons": ["stub"]},
        "criteria": [
            {
                "id": "stub",
                "scope": "site",
                "outcome": "unsure",
                "unsure_cause": "not_evaluated",
                "reason": "stage stub",
                "measured": None,
                "threshold": None,
            }
        ],
        "views_needed": [],
    }


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scripted_outcome() -> str:
    """The dev-only scripted terminal state, or the default manual_review."""
    return load_orch().local_outcome


_SCRIPTED_VIEW = {
    "id": "stub-wall-band",
    "kind": "band",
    "criteria": ["stub"],
    "why": "Local test stand-in: this retake request is scripted, not measured.",
    "band": "wall",
    "wall_id": "w1",
}


def scripted_verdict(profile: str, scene_id: str, outcome: str) -> dict:
    """The stub verdict for a scripted local outcome (dev + stage stubs only).

    Changes only the site decision and views_needed, so the outcome table's
    rows 11-14 land the capture on the chosen state. manual_review keeps the
    plain stub document byte-identical, and failed never reaches criteria
    (the validate stub ends the run on row 1).
    """
    verdict = verdict_document(profile, scene_id)
    if outcome == "eligible":
        verdict["site"]["decision"] = "pass"
        verdict["site"]["reasons"] = ["scripted:eligible"]
    elif outcome == "not_eligible":
        verdict["site"]["decision"] = "reject"
        verdict["site"]["reasons"] = ["scripted:not_eligible"]
    elif outcome == "needs_more_photos":
        verdict["site"]["reasons"] = ["scripted:needs_more_photos"]
        verdict["views_needed"] = [dict(_SCRIPTED_VIEW)]
    return verdict


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
