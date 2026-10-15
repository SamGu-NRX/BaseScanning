"""Scene stage: validate base-scene/v0 and copy the assets it references."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np

from server.common.stage import export_run
from server.contracts.jobs import StageResult
from server.scene.build import attach_assets
from server.scene.contract import check_scene
from server.scene.errors import SceneError
from server.scene.harness import load as load_harness
from server.scene.merge import merge_objects
from server.scene.objects2d import apply_detections
from server.scene.params import SceneParams, scene_params_from_env
from server.scene.render import render_topdown


def build_scene(
    packet: dict,
    coverage_dir: Path,
    dense_dir: Path,
    poses_dir: Path,
    scale: dict,
    objects_path: Path | None,
    out_dir: Path,
    *,
    params: SceneParams,
    heartbeat=None,
) -> Path:
    del packet, scale, params
    started = time.perf_counter()
    dense_dir = Path(dense_dir)
    poses_dir = Path(poses_dir)
    if heartbeat:
        heartbeat("objects")
    coverage_dir = Path(coverage_dir)
    ready = coverage_dir / "scene_ready.json"
    if not ready.is_file():
        raise SceneError("input_missing:scene_ready", f"missing {ready.name}")
    scene = json.loads(ready.read_text())
    if objects_path is not None and Path(objects_path).is_file():
        apply_detections(scene, json.loads(Path(objects_path).read_text()))
    loaded = _load_grid(coverage_dir, scene)
    points = _load_points(coverage_dir)
    src = _load_src(coverage_dir, len(points))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    attach_assets(scene, out_dir, loaded, points, src)
    check_scene(scene)
    for name in ("dense.ply", "flagged.ply"):
        source = dense_dir / name
        if source.is_file():
            shutil.copyfile(source, out_dir / name)
    sparse = poses_dir / "sparse.ply"
    if sparse.is_file():
        shutil.copyfile(sparse, out_dir / "sparse.ply")
    (out_dir / "scale.json").write_text(json.dumps(scene["provenance"]["producer"]["params"]["scale"], indent=1) + "\n")
    render_topdown(scene, loaded, out_dir / "coverage_topdown.png")
    scene_path = out_dir / "scene.json"
    load_harness()["sceneio"].save_json(scene, scene_path)
    (out_dir / "build_report.json").write_text(
        json.dumps(
            {
                "warnings": scene["provenance"].get("notes", []),
                "walls": len(scene["walls"]),
                "timings": {"scene_s": round(time.perf_counter() - started, 3)},
            },
            indent=1,
        )
        + "\n"
    )
    return scene_path


def _load_grid(coverage_dir: Path, scene: dict):
    del scene
    sceneio = load_harness()["sceneio"]
    data = np.load(coverage_dir / "coverage.npz")
    return sceneio.CoverageGrid(
        data["state"],
        data["views"] if "views" in data else None,
        np.asarray(data["origin"], float),
        float(data["voxel"]),
    )


def _load_points(coverage_dir: Path):
    ply = load_harness()["sceneio"].read_ply(coverage_dir / "points.ply")
    return np.stack([ply["x"], ply["y"], ply["z"]], axis=1)


def _load_src(coverage_dir: Path, n: int):
    ply = load_harness()["sceneio"].read_ply(coverage_dir / "points.ply")
    if "src" in ply:
        return np.asarray(ply["src"], int)
    return np.zeros(n, int)


def run(ctx) -> StageResult:
    params = scene_params_from_env()
    packet_dir = Path(ctx.inputs["packet_dir"])
    packet = json.loads((packet_dir / "packet.json").read_text()) if (packet_dir / "packet.json").is_file() else {}
    work = Path(ctx.work_dir)
    objects = ctx.inputs.get("objects/objects.json")
    scale = {}
    path = build_scene(
        packet,
        work / "coverage",
        work / "dense",
        Path(ctx.inputs.get("poses_dir", work / "poses")),
        scale,
        Path(objects) if objects else None,
        work / "scene",
        params=params,
        heartbeat=getattr(ctx, "heartbeat", None),
    )
    scene = json.loads(Path(path).read_text())
    rel = Path(path).relative_to(work).as_posix()
    return StageResult(
        artifacts={rel: rel},
        metrics={"walls": float(len(scene.get("walls") or []))},
        warnings=list(scene.get("provenance", {}).get("notes") or []),
    )


__all__ = ["build_scene", "merge_objects", "run"]

export_run(run)
