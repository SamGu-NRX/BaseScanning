"""Pose backbone. ``run`` is the stage entry point (SD-24)."""

from __future__ import annotations

from pathlib import Path

from server.contracts.jobs import StageContext, StageResult
from server.poses import host_pin as _host_pin
from server.poses.packet_io import load_packet
from server.poses.params import PoseParams
from server.poses.run import run_poses

# host_pin loads pycolmap under the OpenBLAS pin, then restores the process
# environment. Keep that import above ``server.poses.run``. Poses shares
# run_stage_impl and the CPU and GPU images with the other stages, so the
# variables are not set on the image.
_ = _host_pin.OPENBLAS_CORETYPE

STAGE = "poses"


def _published_pose_file(path: Path) -> bool:
    """Pipeline artifacts under ``poses/``. Sidecars and scratch dirs stay out."""
    if not path.is_file():
        return False
    if any(part == "_images" or part.startswith("_tmp") for part in path.parts):
        return False
    name = path.name
    if name.startswith("_") or name.endswith("-wal") or name.endswith("-shm"):
        return False
    return True


def run(ctx: StageContext) -> StageResult:
    packet_dir = Path(ctx.inputs["packet_dir"])
    packet_json = packet_dir / "packet.json"
    if not packet_json.is_file():
        normalized = packet_dir / "normalized.json"
        packet_json = normalized if normalized.is_file() else packet_json
    view = load_packet(packet_json)
    params = PoseParams.from_env()
    if ctx.gpu is None:
        params = PoseParams(**{**params.__dict__, "gpu": False})
    report = run_poses(view, ctx.work_dir, params=params, heartbeat=ctx.heartbeat)
    poses = ctx.work_dir / "poses"
    # WS-00 §3.9: every file under poses/ is a pipeline artifact, including
    # database.db, sparse.ply, tracks.npz, and sparse/0/points3D.*. The 5 MB
    # cap in scripts/check_repo_hygiene.py is for git, not this publish step.
    artifacts = {
        str(path.relative_to(ctx.work_dir)): str(path.relative_to(ctx.work_dir))
        for path in poses.rglob("*")
        if _published_pose_file(path)
    }
    return StageResult(
        artifacts=artifacts,
        metrics={
            "frames_registered": float(report["frames_registered"]),
            "registered_fraction": float(report["registered_fraction"]),
            "mapper_s": float(report["timings_s"]["mapper_s"]),
        },
        warnings=list(report["warnings"]) + list(report["actions"]),
    )
