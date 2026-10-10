"""The frozen scenario grid: rigs, actions, worlds.

Each scenario is an observation capability — a fixed rig of keyframes and a true
wall world — plus a claim about which facts survive. Rigs are stand-in walks:
nothing here simulates a phone (that is capture-coverage's job); the rigs state,
in scene-frame feet and raw-schema fields, which views the export carries. All
cameras use a 1920 x 1440 unrotated sensor image; fx is per camera and chosen so
each rig's intended blind spot is a real one, with margin (tests assert the
visibility patterns).

Actions each add keyframes to the observation set. `pan_pair` is the control:
panned copies of the rig's own cameras, which add no new camera centers and so
add no parallax by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .identifiability import evaluate
from .projection import Camera, make_camera
from .worlds import WallWorld

IMAGE_W, IMAGE_H = 1920, 1440

# The frozen fact grid, feet and degrees. Nominal values sit inside every range.
GRID: dict[str, tuple[float, ...]] = {
    "theta_deg": (-4.0, -2.0, 0.0, 2.0, 4.0),
    "d_ft": (18.0, 19.0, 20.0, 21.0, 22.0),
    "s0_ft": (-12.0, -11.0, -10.0, -9.0, -8.0),
    "s1_ft": (8.0, 9.0, 10.0, 11.0, 12.0, 14.0, 16.0),  # 16: the end-blind true world
    "h_ft": (8.0, 9.0, 10.0),
}

NOMINAL = WallWorld(theta_deg=0.0, d_ft=20.0, s0_ft=-10.0, s1_ft=10.0, h_ft=9.0)

FX_WIDE = 1000.0  # frame fits the whole wall from 14 ft with margin
FX_MID = 1200.0  # top-blind rig: ends in, top out
FX_NARROW = 1400.0  # tighter frames for the targeted actions and the end-blind rig


def cam(
    keyframe_id: str,
    pos: tuple[float, float, float],
    target: tuple[float, float, float],
    fx: float = FX_WIDE,
) -> Camera:
    return make_camera(keyframe_id, pos, target, fx=fx, width=IMAGE_W, height=IMAGE_H)


@dataclass(frozen=True)
class Action:
    name: str
    description: str
    cameras: tuple[Camera, ...]


def pan_pair_for(base: tuple[Camera, ...]) -> Action:
    """Yaw every base camera in place by +0.35 rad about the world vertical.

    Same camera centers, so no new parallax — but the frames move, and whatever
    new landmarks enter them is reported, not assumed away. (An earlier draft
    used this as the zero-information control; the top-blind scenario refutes
    that: panning brings a top corner into frame, and one sight of a corner on
    the ground-pinned wall plane settles height. The control is reobserve.)
    """
    phi = 0.35
    ry = np.array(
        [[np.cos(phi), 0.0, np.sin(phi)], [0.0, 1.0, 0.0], [-np.sin(phi), 0.0, np.cos(phi)]]
    )
    twins = []
    for c in base:
        rotated = Camera(
            keyframe_id=f"{c.keyframe_id}_pan",
            center=c.center,
            rotation=ry @ c.rotation,
            fx=c.fx,
            fy=c.fy,
            cx=c.cx,
            cy=c.cy,
            width=c.width,
            height=c.height,
        )
        twins.append(rotated)
    return Action(
        name="pan_pair",
        description="yaw every existing keyframe in place: no new parallax, "
        "but the frames move and may reveal new landmarks",
        cameras=tuple(twins),
    )


def reobserve_for(base: tuple[Camera, ...]) -> Action:
    """The control: re-observe every base keyframe from the same pose.

    Duplicated frames carry no new information of any kind — same centers, same
    aims, same coverage — so the extended rig must leave every verdict exactly
    where it was. Anything else is a bug in the study.
    """
    twins = tuple(
        Camera(
            keyframe_id=f"{c.keyframe_id}_again",
            center=c.center,
            rotation=c.rotation,
            fx=c.fx,
            fy=c.fy,
            cx=c.cx,
            cy=c.cy,
            width=c.width,
            height=c.height,
        )
        for c in base
    )
    return Action(
        name="reobserve",
        description="re-observe every existing keyframe from the same pose: no new "
        "information by construction (the zero-information control)",
        cameras=twins,
    )


ACTIONS: dict[str, Action] = {
    "stereo_step": Action(
        name="stereo_step",
        description="step to two new positions 14 ft out and frame the wall: "
        "adds parallax across the whole face",
        cameras=(
            cam("action_step_1", (-6.0, 5.0, -6.0), (0.0, 4.5, -20.0)),
            cam("action_step_2", (8.0, 5.0, -6.0), (2.0, 4.5, -20.0)),
        ),
    ),
    "tilt_pair": Action(
        name="tilt_pair",
        description="pitch up from two distinct positions, tight frame on the wall top",
        cameras=(
            cam("action_tilt_1", (0.0, 4.0, -6.0), (0.0, 8.0, -20.0), fx=1300.0),
            cam("action_tilt_2", (5.0, 4.0, -7.0), (3.0, 8.0, -20.0), fx=1300.0),
        ),
    ),
    "end_approach": Action(
        name="end_approach",
        description="walk right and frame the right end low, from two new positions",
        cameras=(
            cam("action_end_1", (2.0, 5.0, -6.0), (10.0, 3.0, -20.0), fx=FX_NARROW),
            cam("action_end_2", (8.0, 5.0, -6.0), (13.0, 3.0, -20.0), fx=FX_NARROW),
        ),
    ),
}

DEFAULT_ACTIONS = ("stereo_step", "tilt_pair", "end_approach", "pan_pair", "reobserve")


@dataclass(frozen=True)
class Scenario:
    name: str
    capability: str  # what the rig says about the walk, in one line
    true_world: WallWorld
    cameras: tuple[Camera, ...]
    actions: tuple[str, ...] = field(default=DEFAULT_ACTIONS)

    def run(self) -> dict:
        """Base compatibility, then each action's extended compatibility."""
        base = evaluate(self.true_world, list(self.cameras), GRID)
        built = {
            **ACTIONS,
            "pan_pair": pan_pair_for(self.cameras),
            "reobserve": reobserve_for(self.cameras),
        }
        extended: dict[str, dict] = {}
        for name in self.actions:
            action = built[name]
            cams = list(self.cameras) + list(action.cameras)
            post = evaluate(self.true_world, cams, GRID)
            extended[action.name] = {
                "description": action.description,
                "observation_hash": post.observation_hash,
                "compatible_count": post.compatible_count,
                "post_verdicts": {
                    v.fact: {"supported": v.supported, "count": v.count} for v in post.verdicts
                },
            }
        return {
            "name": self.name,
            "capability": self.capability,
            "true_world": self.true_world.facts(),
            "true_world_described": self.true_world.describe(),
            "cameras": [
                {
                    "id": c.keyframe_id,
                    "center_ft": [round(float(v), 4) for v in c.center],
                    "fx": c.fx,
                }
                for c in self.cameras
            ],
            "observation": base.observation,
            "observation_hash": base.observation_hash,
            "grid_count": base.grid_count,
            "compatible_count": base.compatible_count,
            "verdicts": [
                {
                    "fact": v.fact,
                    "supported": v.supported,
                    "compatible_value_count": v.count,
                    "values": list(v.values),
                    "pair": list(v.pair) if v.pair else None,
                }
                for v in base.verdicts
            ],
            "actions": extended,
        }


def scenarios() -> list[Scenario]:
    """The five capability scenarios, in run order."""
    return [
        Scenario(
            name="one-view",
            capability="one keyframe frames the whole wall: with metric poses, ground landmarks "
            "pin the face from this frame alone",
            true_world=NOMINAL,
            cameras=(cam("kf0", (0.0, 5.0, -6.0), (0.0, 4.5, -20.0)),),
        ),
        Scenario(
            name="two-view",
            capability="two keyframes from distinct positions, both framing the whole wall",
            true_world=NOMINAL,
            cameras=(
                cam("kf0", (0.0, 5.0, -6.0), (0.0, 4.5, -20.0)),
                cam("kf1", (5.0, 5.0, -6.0), (2.0, 4.5, -20.0)),
            ),
        ),
        Scenario(
            name="two-view-pan-only",
            capability="two keyframes at the same position, panned: the first frame already "
            "supports every fact",
            true_world=NOMINAL,
            cameras=(
                cam("kf0", (0.0, 5.0, -6.0), (0.0, 4.5, -20.0)),
                cam("kf1", (0.0, 5.0, -6.0), (8.0, 4.5, -20.0)),
            ),
        ),
        Scenario(
            name="two-view-top-blind",
            capability=(
                "two keyframes from distinct positions, pitched down: "
                "the wall top never enters the frame"
            ),
            true_world=NOMINAL,
            cameras=(
                cam("kf0", (0.0, 3.0, -6.0), (0.0, 0.0, -20.0), fx=FX_MID),
                cam("kf1", (4.0, 3.0, -6.0), (2.0, 0.0, -20.0), fx=FX_MID),
            ),
        ),
        Scenario(
            name="two-view-end-blind",
            capability="two keyframes from distinct positions aimed left of the right end",
            true_world=WallWorld(theta_deg=0.0, d_ft=20.0, s0_ft=-10.0, s1_ft=16.0, h_ft=9.0),
            cameras=(
                cam("kf0", (-6.0, 5.0, -6.0), (-2.0, 4.5, -20.0), fx=FX_NARROW),
                cam("kf1", (0.0, 5.0, -6.0), (-2.0, 4.5, -20.0), fx=FX_NARROW),
            ),
        ),
    ]
