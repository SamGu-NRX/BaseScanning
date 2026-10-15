"""scene.json out: the reconstruction's wall line, coverage and measured gaps in the server's
contract (server/schemas/scene.schema.json and "What settles each check" in server/README.md on
t3/server). Feet, in the scene frame: the world with the ground at the wall at y = 0.

A scan bundle's scene is updated: the meter's wall takes the fitted line (over the union of its
fitted extent and the phone's baseline), `coverage.observed` is replaced by the occlusion-aware
coverage, and the target wall's measured facing gaps and overhead clearances are replaced.
A Measure Lab session gets a new scene with the same parts.

The fitted wall takes the source of the depth that built it (`pipeline.WALL_SOURCE`: "mesh" from
LiDAR, "plane" from photos alone) and no `plus_minus_ft`; a bundle's tapped bound is removed. The
server then applies that source's default error plus its drift per foot walked from the meter
(errors.mesh_ft, plane_ft and drift_per_ft in server/rules.yaml on t3/server). An explicit bound
would switch the drift off: the server adds none to a wall that carries one, so a LiDAR wall
30 ft from the meter would stay at 0.5 ft instead of about 5.3 ft. Keeping the old line's
provenance would be wrong too: a bundle's tap source and bound, or with none the tap default of
0.3 ft, describe a line the reconstruction replaced.
"""

from __future__ import annotations

import copy

import numpy as np

from recon.capture import FEET, Capture
from recon.coverage import CellCoverage, measurements, observed
from recon.geometry import WallFrame

WALL_ID = "wall"


def _plan_ft(p: np.ndarray) -> list[float]:
    return [round(float(p[0]) / FEET, 4), round(float(p[2]) / FEET, 4)]


def build(
    capture: Capture, wall: WallFrame, cov: CellCoverage, wall_source: str, plus_minus_ft: float
) -> dict:
    """The scene to send. `wall_source` labels the fitted wall line; `plus_minus_ft` is the error
    of the measured facing gaps and overhead clearances. For a bundle the scene frame is the
    bundle's own (its ground is y = 0 by the phone's estimate); for a session it is ARKit's world
    lowered to the fitted ground."""
    shift = 0.0 if capture.scene is not None else wall.ground_y
    lo, hi = wall.s_range
    if capture.scene is not None:
        doc = copy.deepcopy(capture.scene)
        wid = doc["meter"]["wall_id"]
        target = next(w for w in doc["walls"] if w["id"] == wid)
        if wall.meter_moved_m > 0.05:
            # The phone's marks were placed against a wall line the reconstruction does not have.
            doc["meter"]["pos"] = [round(float(v) / FEET, 4) for v in wall.meter]
            doc["objects"] = []
            doc["facing"] = []
            doc["overheads"] = []
        old = np.array(target["baseline"], dtype=np.float64) * FEET
        if wall.meter_moved_m <= 0.05:
            old_s = [(np.array([x, 0.0, z]) - wall.origin) @ wall.along for x, z in old]
            lo, hi = min(lo, *old_s), max(hi, *old_s)
    else:
        wid = WALL_ID
        m = wall.meter - np.array([0.0, shift, 0.0])
        doc = {
            "schema_version": "1.0",
            "meter": {"pos": [round(float(v) / FEET, 4) for v in m], "wall_id": wid},
            "walls": [{"id": wid, "baseline": []}],
            "objects": [],
            "ground": [],
            "coverage": {"ends": {"left": {"kind": "unexplored"}, "right": {"kind": "unexplored"}}},
            "keyframes": [
                {
                    "id": f.id,
                    "pose": _pose_ft(f.cam_to_world, shift),
                    "intrinsics": [round(float(v), 4) for v in f.intrinsics],
                    "w": f.width,
                    "h": f.height,
                    "img": f.image.name,
                }
                for f in capture.frames
            ],
        }
        target = doc["walls"][0]
    target["baseline"] = [_plan_ft(wall.world(s, 0.0)) for s in (lo, hi)]
    target["source"] = wall_source
    target.pop("plus_minus_ft", None)
    entries = observed(cov)
    # scene.schema.json caps observed entries at 500; a partial export hides valid observations.
    if len(entries) > 500:
        raise ValueError(
            f"coverage.observed has {len(entries)} entries; scene contract limit is 500"
        )
    doc.setdefault("coverage", {})["observed"] = entries
    facing, overheads = measurements(cov, wid, plus_minus_ft)
    # Replacing the wall invalidates its old spans even when the meter stays in place.
    doc["facing"] = [*[m for m in doc.get("facing", []) if m["wall_id"] != wid], *facing]
    doc["overheads"] = [*[m for m in doc.get("overheads", []) if m["wall_id"] != wid], *overheads]
    return doc


def _pose_ft(T: np.ndarray, shift: float) -> list[float]:
    P = T.copy()
    P[1, 3] -= shift
    P[:3, 3] /= FEET
    return [round(float(v), 4) for v in P.T.reshape(-1)]
