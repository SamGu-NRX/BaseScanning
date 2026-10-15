"""Stub ``poses`` stage."""

from __future__ import annotations

import json

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, ply, tracks, write_files


def cameras_bytes() -> bytes:
    """WS-04 ``cameras.json``: a JSON list ``load_cameras`` can iterate."""

    pose = [0.0] * 16
    pose[0] = pose[5] = pose[10] = pose[15] = 1.0
    row = {
        "image": "k0.jpg",
        "keyframe_id": "k0",
        "epoch": "e1",
        "pose": pose,
        "intrinsics": [100.0, 100.0, 50.0, 50.0],
        "w": 100,
        "h": 100,
        "pose_source": "arkit",
        "reproj_px": 0.0,
        "points3D": 0,
    }
    return json.dumps([row], sort_keys=True).encode()


def run(ctx: StageContext) -> StageResult:
    return write_files(
        ctx,
        {
            "poses/cameras.json": cameras_bytes(),
            "poses/sparse.ply": ply(),
            "poses/tracks.npz": tracks(),
            "poses/registration_report.json": json_bytes({"status": "ok", "registered_fraction": 1.0, "actions": []}),
            "poses/sparse/0/": b"stub",
        },
    )
