"""The worker: a capture in; a 3D model, geometry, coverage, scene.json and the server's answer out.

Stages, each a function of the one before, so any of them can move to another machine:
capture (adapters) -> depth maps (LiDAR, or MoGe-2 rescaled by the AR poses) -> fused volume and
mesh -> ground and wall geometry -> occlusion-aware coverage -> scene.json -> placement server.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

from recon import capture as cap
from recon import coverage, depth, fusion, geometry, scene, server
from recon.capture import FEET, Capture
from recon.glb import write_glb

# The fitted wall line's `source` in scene.json, by depth source. The server gives a wall without
# `plus_minus_ft` its source's default error plus drift per foot walked from the meter (mesh 0.5 ft
# or plane 0.75 ft, plus 0.16 ft/ft, in server/rules.yaml on t3/server), so the worker sends no
# wall bound: neither depth path has a measured error for a wall longer than the 1 to 3 m spans
# the evals measured. "plane" is the schema's non-LiDAR source, and its default is the larger one.
WALL_SOURCE = {"lidar": "mesh", "moge2-triangulated": "plane"}

# Error bars on the facing gaps and overhead clearances the worker measures, by depth source.
# These are short distances out from or up from the wall, not positions along it, and the server
# adds no drift to them. MoGe-2: the upper end of the 95% interval of the ETH3D surface p90 over
# 1 to 3 m spans at an assumed 2% pose error, 6.7 in (experiments/evals README section 3 on
# t3/evals). LiDAR: no measured value exists here; 0.5 ft is the server's default for mesh
# measurements (errors.mesh_ft, a day-1 estimate).
PLUS_MINUS_FT = {"moge2-triangulated": 0.56, "lidar": 0.5}


def _log(msg: str) -> None:
    print(f"recon: {msg}", file=sys.stderr, flush=True)


def reconstruct(
    capture: Capture, depths: dict, depth_report: dict, move_meter: bool = False
) -> dict:
    """Everything after depth, up to coverage, for the writers and the acceptance checks."""
    t0 = time.perf_counter()
    source = depth_report["source"]
    poses = {f.id: f.cam_to_world for f in capture.frames}
    vol = fusion.integrate(depths, poses)
    surface = fusion.mesh(vol)
    _log(f"fused at {vol.voxel * 100:.1f} cm voxels: {len(surface.vertices)} vertices")
    ground = geometry.fit_ground(surface, capture.ground_y)
    cams = np.array([f.center for f in capture.frames])
    lines = geometry.wall_lines(surface, ground, cams, np.random.default_rng(0))
    line = geometry.choose_wall(lines, capture, move_meter)
    wall = geometry.wall_frame(line, capture, ground, move_meter)
    # Only frames with a depth map can see anything (depth.depth_maps: LiDAR frames saved
    # without depth get none).
    seeing = [f for f in capture.frames if f.id in depths]
    # The fitted ground goes in so ground samples sit on the surface it fit, not on the horizontal
    # plane through the meter's foot: on sloping ground that plane hangs above or below the real
    # surface, and ground the scan saw read as unobserved.
    cov = coverage.compute(wall, seeing, depths, vol, surface, ground)
    _log(f"coverage over {len(cov.cells)} cells ({time.perf_counter() - t0:.0f} s)")
    return {
        "depths": depths,
        "depth_report": depth_report,
        "source": source,
        "volume": vol,
        "mesh": surface,
        "ground": ground,
        "lines": lines,
        "line": line,
        "wall": wall,
        "coverage": cov,
    }


def geometry_doc(r: dict) -> dict:
    """Walls and ground for the rules engine: the scene frame in feet, and the meter's frame."""
    wall, line, ground = r["wall"], r["line"], r["ground"]
    lo, hi = wall.s_range
    ends = [wall.world(s, 0.0) for s in (lo, hi)]
    return {
        "units": "ft",
        "depth_source": r["source"],
        "accuracy": depth.ACCURACY_NOTE[r["source"]],
        "meter": {"pos": [round(float(v) / FEET, 4) for v in wall.meter]},
        "walls": [
            {
                "id": scene.WALL_ID,
                "baseline": [
                    [round(float(p[0]) / FEET, 4), round(float(p[2]) / FEET, 4)] for p in ends
                ],
                "s_range_ft": [round(lo / FEET, 3), round(hi / FEET, 3)],
                "along": [round(float(v), 5) for v in wall.along],
                "outward": [round(float(v), 5) for v in wall.outward],
                "fit_rms_in": round(line.rms_m / FEET * 12, 2),
            }
        ],
        "other_walls": [
            {
                "baseline": [
                    [round(float(p[0]) / FEET, 4), round(float(p[2]) / FEET, 4)]
                    for p in (w.foot + w.along * w.extent[0], w.foot + w.along * w.extent[1])
                ],
                "fit_rms_in": round(w.rms_m / FEET * 12, 2),
            }
            for w in r["lines"]
            if w is not line
        ],
        "ground": {
            "height_ft": round(wall.ground_y / FEET, 4),  # the plane at the meter's foot
            "normal": [round(float(v), 5) for v in ground.normal],
            "tilt_deg": round(float(np.degrees(np.arccos(min(1.0, ground.normal[1])))), 2),
            "fit_rms_in": round(ground.rms_m / FEET * 12, 2),
        },
    }


def write_model(r: dict, path: Path) -> None:
    """The mesh in the meter's frame (origin at the meter's foot, axes as the world's)."""
    m = r["mesh"]
    write_glb(path, m.vertices - r["wall"].origin, m.faces, m.colors, m.normals)


def run(
    bundle: Path, out: Path, work: Path, depth_mode: str, server_url: str | None, move_meter: bool
) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    capture = cap.load(bundle, work)
    _log(f"{capture.source}: {len(capture.frames)} frames from {bundle}")
    t0 = time.perf_counter()
    depths, depth_report = depth.depth_maps(capture, work, depth_mode)
    _log(f"depth from {depth_report['source']} ({time.perf_counter() - t0:.0f} s)")
    r = reconstruct(capture, depths, depth_report, move_meter)
    write_model(r, out / "model.glb")
    geo = geometry_doc(r)
    (out / "geometry.json").write_text(json.dumps(geo, indent=1))
    cov = r["coverage"]
    src = r["source"]
    doc = scene.build(capture, r["wall"], cov, WALL_SOURCE[src], PLUS_MINUS_FT[src])
    (out / "scene.json").write_text(json.dumps(doc, indent=1))
    (out / "coverage.json").write_text(json.dumps(_coverage_doc(cov), indent=1))
    result = None
    if server_url:
        result = server.place(doc, server_url, out)
        _log(f"server: {result.get('decision')}")
    report = _report(capture, r, geo, doc, result)
    (out / "report.md").write_text(report)
    return {"report": report, "result": result}


def _coverage_doc(cov: coverage.CellCoverage) -> dict:
    ft = lambda a: [None if not np.isfinite(x) else round(float(x) / FEET, 3) for x in a]  # noqa: E731
    ends = cov.cells + coverage.CELL_M if cov.ends is None else cov.ends
    return {
        "cell_ft": round(coverage.CELL_M / FEET, 3),
        "cells_s_ft": ft(cov.cells),
        "cells_end_ft": ft(ends),  # a cell clipped to the fit's end is narrower than cell_ft
        "wall_observed": cov.wall.tolist(),
        "ground_out_ft": ft(cov.ground_out),
        "facing_gap_ft": ft(cov.facing_gap),
        "facing_clear_ft": ft(cov.facing_clear),
        "overhead_clearance_ft": ft(cov.overhead_clearance),
        "overhead_clear_ft": ft(cov.overhead_clear),
    }


def _report(capture: Capture, r: dict, geo: dict, doc: dict, result: dict | None) -> str:
    cov, dr = r["coverage"], r["depth_report"]
    wall, ground = geo["walls"][0], geo["ground"]
    lo, hi = wall["s_range_ft"]
    lines = [
        f"# Reconstruction of {capture.root.name}",
        "",
        f"- Input: {capture.source}, {len(capture.frames)} frames.",
        f"- Depth: {r['source']}. {depth.ACCURACY_NOTE[r['source']]}",
    ]
    if r["source"] == "moge2-triangulated":
        a, b = dr["scale_range"]
        lines.append(
            f"- Scale fitted for {dr['fitted']} of {dr['frames']} frames: MoGe-2 x "
            f"{dr['median_scale']:.3f} median ({a:.3f} to {b:.3f}); the rest take the median."
        )
    widths_ft = (cov.cells + coverage.CELL_M if cov.ends is None else cov.ends) - cov.cells
    facing = np.isfinite(cov.facing_gap) @ widths_ft / FEET
    over = np.isfinite(cov.overhead_clearance) @ widths_ft / FEET
    obs = doc["coverage"]["observed"]
    lines += [
        f"- Model: {len(r['mesh'].vertices)} vertices at {r['volume'].voxel * 100:.1f} cm voxels.",
        f"- Wall: {lo:.1f} to {hi:.1f} ft of s, plane fit RMS {wall['fit_rms_in']:.1f} in; "
        f"{len(geo['other_walls'])} other wall stretches.",
        f"- Ground: {ground['height_ft']:+.2f} ft, tilt {ground['tilt_deg']:.1f} deg, "
        f"fit RMS {ground['fit_rms_in']:.1f} in.",
        f"- Wall band observed: {widths_ft[cov.wall].sum() / FEET:.1f} of "
        f"{widths_ft.sum() / FEET:.1f} ft.",
        f"- Ground seen 4 ft out or more: "
        f"{widths_ft[cov.ground_out >= 4 * FEET].sum() / FEET:.1f} ft.",
        f"- Facing gap measured over {facing:.1f} ft; overhead clearance over {over:.1f} ft.",
        f"- scene.json: {len(obs)} observed entries, {len(doc['facing'])} facing, "
        f"{len(doc['overheads'])} overheads.",
    ]
    lines += [f"- Note: {n}" for n in capture.notes]
    if result is not None:
        lines += ["", f"## Server: {result.get('decision')}", "", result.get("summary", ""), ""]
        lines += ["| Check | Outcome | Reason |", "| --- | --- | --- |"]
        for c in result.get("checks", []):
            reason = str(c.get("reason", "")).replace("|", "/")
            lines.append(f"| {c.get('id', c.get('check'))} | {c.get('outcome')} | {reason} |")
    return "\n".join(lines) + "\n"
