# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2"]
# ///
"""Replay a House Scan scene.json's kept photos through the app's coverage rules.

For each kept photo, in order, prints how far the covered wall reaches on each side of the
meter under the simplified no-depth/no-skip rules below. Then prints, for
the cells either side of the meter, which photos saw each sample row.

The rules mirror `CoverageMap` and `GuidancePlanner.reach` on `t3/ios-mvf` at ff95f1c:
- 6 in cells;
- wall rows at 0, 3.25 and 6.5 ft up; ground rows at 0, 2 and 4 ft out;
- a row counts when its two samples, a quarter and three quarters across the cell, are in view:
  within 6 m, within 65 degrees of the surface normal, and 3 % inside the image edges;
- a cell is covered when every row was seen from two positions at least 0.25 m apart.

Limits: it uses the exported (refined) wall line and meter for every photo, cannot see
tracking state, ignores per-photo depth and explicitly skipped cells, and ignores marked ends.
The app accepts covered OR skipped cells for reach and uses depth when supplied. This replay
therefore does not establish reach equivalence in those states.

    uv run coverage_replay.py path/to/scene.json
"""

import argparse
import json
from pathlib import Path

import numpy as np

FT = 0.3048
CELL = 0.1524
ROWS = {"wall": (0.0, 0.9906, 1.9812), "ground": (0.0, 0.6, 1.2)}
MAX_DISTANCE = 6.0
MAX_ANGLE = np.radians(65)
MARGIN = 0.03
BASELINE = 0.25
MIN_DEPTH = 0.05


def load(path: Path):
    scene = json.loads(path.read_text())
    (x0, z0), (x1, z1) = scene["walls"][0]["baseline"]
    direction = np.array([x1 - x0, z1 - z0])
    direction /= np.linalg.norm(direction)
    along = np.array([direction[0], 0.0, direction[1]])
    outward = np.array([-direction[1], 0.0, direction[0]])
    meter = np.array(scene["meter"]["pos"]) * FT
    origin = np.array([meter[0], 0.0, meter[2]])
    cameras = []
    for frame in scene["keyframes"]:
        pose = np.array(frame["pose"], dtype=float).reshape(4, 4).T
        pose[:3, 3] *= FT
        cameras.append((frame["id"], pose, np.linalg.inv(pose), frame["intrinsics"], frame["w"], frame["h"]))
    # Point the outward normal at the cameras, as the app's wall frame does.
    mean_position = np.mean([c[1][:3, 3] for c in cameras], axis=0)
    if np.dot(mean_position - origin, outward) < 0:
        outward = -outward
    return origin, along, outward, cameras


def reach(covered: set[int], step: int) -> float:
    index, cells = (0 if step > 0 else -1), 0
    while index in covered:
        cells, index = cells + 1, index + step
    return cells * CELL / FT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scene", type=Path)
    parser.add_argument("--cells", type=int, default=45, help="cells to track on the right")
    # Match the existing right extent so left reach is not capped at 1.5 ft.
    parser.add_argument("--left-cells", type=int, default=45, help="cells to track on the left")
    args = parser.parse_args()
    if args.cells <= 0 or args.left_cells <= 0:
        parser.error("cell counts must be positive")

    origin, along, outward, cameras = load(args.scene)
    up = np.array([0.0, 1.0, 0.0])

    def point(band: str, s: float, offset: float) -> np.ndarray:
        return origin + along * s + (up * offset if band == "wall" else outward * offset)

    def sees(camera, p: np.ndarray, normal: np.ndarray) -> bool:
        _, pose, inverse, (fx, fy, cx, cy), w, h = camera
        to_camera = pose[:3, 3] - p
        distance = np.linalg.norm(to_camera)
        if distance == 0 or distance > MAX_DISTANCE or np.dot(to_camera / distance, normal) < np.cos(MAX_ANGLE):
            return False
        local = inverse @ np.append(p, 1.0)
        depth = -local[2]
        if depth < MIN_DEPTH:
            return False
        u, v = cx + fx * local[0] / depth, cy - fy * local[1] / depth
        return MARGIN * w <= u <= (1 - MARGIN) * w and MARGIN * h <= v <= (1 - MARGIN) * h

    def rows_seen(camera, band: str, index: int) -> list[int]:
        low = index * CELL
        samples = (low + CELL * 0.25, low + CELL * 0.75)
        normal = outward if band == "wall" else up
        return [r for r, offset in enumerate(ROWS[band]) if all(sees(camera, point(band, s, offset), normal) for s in samples)]

    indices = range(-args.left_cells, args.cells)
    positions = {(band, i): [[] for _ in ROWS[band]] for band in ROWS for i in indices}
    covered: set[int] = set()

    print("photo   x (ft)  reach left  reach right")
    for camera in cameras:
        position = camera[1][:3, 3]
        if np.dot(position - origin, outward) > 0:
            for band in ROWS:
                for i in indices:
                    for r in rows_seen(camera, band, i):
                        seen = positions[(band, i)][r]
                        if len(seen) < 2 and all(np.linalg.norm(q - position) >= BASELINE for q in seen):
                            seen.append(position)
            for i in indices:
                if all(len(row) >= 2 for band in ROWS for row in positions[(band, i)]):
                    covered.add(i)
        x = np.dot(position - origin, along) / FT
        print(f"{camera[0]}  {x:7.2f}  {reach(covered, -1):8.1f} ft  {reach(covered, 1):9.1f} ft")

    print("\nPhotos that saw each row of the cells either side of the meter")
    for i in (-1, 0):
        for band in ROWS:
            for r, offset in enumerate(ROWS[band]):
                who = [c[0] for c in cameras if r in rows_seen(c, band, i)]
                label = f"{offset / FT:.1f} ft {'up' if band == 'wall' else 'out'}"
                print(f"cell {i:+d} {band:6s} {label:11s} {', '.join(who) or '-'}")


if __name__ == "__main__":
    main()
