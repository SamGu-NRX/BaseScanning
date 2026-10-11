"""Wall worlds and the facts they carry.

A world is one finite tuple of wall facts in the scene frame of
server/schemas/scene.schema.json: feet, +y up, walls[] baselines as [x, z]
points. The meter sits at the origin (packet/README.md proposal 1 puts the
meter anchor's origin at the meter), so a fact's value is its scene-frame
value relative to the meter.

Five facts, the ones the placement rules argue about:

- orientation_deg — the wall baseline's yaw in plan. Baseline direction
  a(theta) = (cos t, 0, sin t); the outward normal follows the schema rule
  "each segment's outward side is its direction turned 90 degrees clockwise in
  plan seen from above" (recon/recon/capture.py::outward_of): n = (-sin t, 0, cos t).
- distance_ft — perpendicular gap from the meter (origin) to the wall face,
  along -n: the wall line is {-d n + s a}. This is the gap meter.pos projects
  over when the meter point is not exactly on the wall line.
- left_end_ft, right_end_ft — s of the wall's ends, ordered left to right as
  seen from outside (schema, walls[]).
- height_ft — wall top above the ground at the wall (ground y = 0; recon
  module docstring: the scene frame puts the ground at the wall at y = 0).

Landmarks are the wall points whose image positions the observations carry:
the two ends, two points at fixed s offsets past the left end (so a world can
move its right end without moving anything observed), and the two top corners.
Landmark identity across keyframes is a declared capability assumption
(README.md): the contract already carries feature-point identifiers
(packet/README.md, "Already in 0.4").
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

FACTS = ("orientation_deg", "distance_ft", "left_end_ft", "right_end_ft", "height_ft")

# Fixed s offsets of the interior landmarks past the left end, feet. Fixed
# offsets, not fractions of the span, so right-end worlds differ only where the
# right end is actually observed.
ALONG_OFFSETS_FT = (4.0, 9.0)

LANDMARK_IDS = (
    "end_left",
    "end_right",
    "along_1",
    "along_2",
    "top_left",
    "top_right",
)


@dataclass(frozen=True)
class WallWorld:
    theta_deg: float
    d_ft: float
    s0_ft: float
    s1_ft: float
    h_ft: float

    def facts(self) -> dict[str, float]:
        return {
            "orientation_deg": self.theta_deg,
            "distance_ft": self.d_ft,
            "left_end_ft": self.s0_ft,
            "right_end_ft": self.s1_ft,
            "height_ft": self.h_ft,
        }

    def describe(self) -> str:
        f = self.facts()
        return (
            f"yaw {f['orientation_deg']:+.1f} deg, face {f['distance_ft']:.1f} ft from the "
            f"meter, s in [{f['left_end_ft']:.1f}, {f['right_end_ft']:.1f}] ft, "
            f"top {f['height_ft']:.1f} ft"
        )


def baseline_dir(theta_deg: float) -> np.ndarray:
    t = np.radians(theta_deg)
    return np.array([np.cos(t), 0.0, np.sin(t)])


def outward_normal(theta_deg: float) -> np.ndarray:
    a = baseline_dir(theta_deg)
    return np.array([-a[2], 0.0, a[0]])


def wall_point(world: WallWorld, s_ft: float, y_ft: float) -> np.ndarray:
    """A point on the wall face: s along the baseline, y above the ground."""
    return (
        -world.d_ft * outward_normal(world.theta_deg)
        + s_ft * baseline_dir(world.theta_deg)
        + np.array([0.0, y_ft, 0.0])
    )


def landmarks(world: WallWorld) -> dict[str, np.ndarray]:
    """The six landmark points, in scene-frame feet, keyed by id."""
    pts = {
        "end_left": wall_point(world, world.s0_ft, 0.0),
        "end_right": wall_point(world, world.s1_ft, 0.0),
        "along_1": wall_point(world, world.s0_ft + ALONG_OFFSETS_FT[0], 0.0),
        "along_2": wall_point(world, world.s0_ft + ALONG_OFFSETS_FT[1], 0.0),
        "top_left": wall_point(world, world.s0_ft, world.h_ft),
        "top_right": wall_point(world, world.s1_ft, world.h_ft),
    }
    return {k: pts[k] for k in LANDMARK_IDS}


def grid_worlds(grid: dict) -> Iterator[WallWorld]:
    """Every world in the frozen grid, in a fixed order."""
    for theta in grid["theta_deg"]:
        for d in grid["d_ft"]:
            for s0 in grid["s0_ft"]:
                for s1 in grid["s1_ft"]:
                    for h in grid["h_ft"]:
                        yield WallWorld(theta, d, s0, s1, h)
