"""Equivalent surveyed straight walls for verifier representation regressions."""

import copy
import json
from pathlib import Path


def segmented_wall_scene(segments_per_wall: int) -> dict:
    fixture = json.loads((Path(__file__).parent / "fixtures/wide-public-pass.json").read_text())
    scene = copy.deepcopy(fixture["scene"])
    scene["walls"] = [
        {
            "id": f"w{i + 1}",
            "baseline": [
                [-40 + 4 * i + 4 * j / segments_per_wall, 0] for j in range(segments_per_wall + 1)
            ],
            "height_ft": 9,
        }
        for i in range(20)
    ]
    scene["meter"]["wall_id"] = "w11"
    for key in ("facing", "overheads"):
        scene[key] = [
            dict(fixture["scene"][key][0], wall_id=f"w{i + 1}", span_ft=[-40 + 4 * i, -36 + 4 * i])
            for i in range(20)
        ]
    return scene
