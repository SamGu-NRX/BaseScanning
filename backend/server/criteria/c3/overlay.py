"""Draw the chosen footprint into a keyframe. Used only when the judge actually runs."""

from __future__ import annotations

from server.criteria.harness import load


def project_box(scene: dict, keyframe: dict, corners_m: list[list[float]]) -> list[tuple[float, float]]:
    h = load()
    pose = h.frame.mat4_from_list(keyframe["pose"])
    k = keyframe["intrinsics"]
    pts = []
    for x, y, z in corners_m:
        uv, _, _ = h.frame.project_arkit(pose, k, [x, y, z])
        pts.append((float(uv[0]), float(uv[1])))
    return pts
