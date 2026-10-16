"""Two supported single-wall source scenes for the real CPU downstream stages.

Callable (import by path — ``backend/tests`` has no package init):

    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "cpu_downstream_scenes",
        Path(__file__).parent / "fixtures" / "cpu_downstream" / "scenes.py",
    )
    scenes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scenes)
    dirs = scenes.supported_wall_variants(tmp_path)

Relative artifact layout written per variant (``scene-<variant>/``):

    scene-<variant>/scene.json     base-scene/v0, asset sha256 stamps filled
    scene-<variant>/laser.ply      the generator's binary laser point cloud
    scene-<variant>/coverage.npz   state/origin/voxel/views occupancy grid

Both variants reuse the synthetic flat-wall generator's geometry unchanged
(``server.criteria.tests.data.synthetic_flatwall.make``): a 12 m house wall,
observed concrete ground, 24 synthetic cameras, and the generator's own PLY and
coverage grid. ``provenance.data`` stays ``"synthetic"`` — nothing here is real
site data.

- ``clean`` — the generator's own positive-pass scene.
- ``hazard`` — the same geometry plus one observed gas-meter hazard at a known
  span (s 3.0–3.4 m, 0.5–1.0 m high) derived from the wall's known dimensions.

The placement bridge (``server.placement.scene_bridge``) refuses grid-bearing
coverage and keyframes by contract; these scenes carry the synthetic coverage
grid so the C2 sweep can run. ``original_scene`` hands back the generator's
verbatim scene dict for that boundary's diagnostic — never a reason to weaken
the bridge.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from server.criteria.tests.data.synthetic_flatwall import make as _flatwall

VARIANTS = ("clean", "hazard")

# Known dimensions of the observed hazard, in metres along w1 from the meter.
HAZARD_S_M = (3.0, 3.4)
HAZARD_BOTTOM_M = 0.5
HAZARD_TOP_M = 1.0


def hazard_object() -> dict:
    """The observed gas-meter hazard the ``hazard`` variant adds to the wall."""
    s0, s1 = HAZARD_S_M
    return {
        "id": "gas-hazard",
        "class": "gas_meter",
        "label": "gas meter",
        "geometry": {
            "kind": "wall_rect",
            "wall_id": "w1",
            "s_m": [s0, s1],
            "bottom_m": HAZARD_BOTTOM_M,
            "top_m": HAZARD_TOP_M,
        },
        "wall_id": "w1",
        "confidence": 0.95,
        "plus_minus_m": 0.05,
        "source": {"kind": "annotation"},
        "evidence": "observed",
    }


def original_scene() -> dict:
    """The synthetic flat-wall generator's scene dict, completely unchanged."""
    return _flatwall.scene_dict()


def write_variant(dest: Path, *, variant: str = "clean") -> Path:
    """Write one variant's scene.json, laser.ply and coverage.npz into ``dest``."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}")
    scene = _flatwall.scene_dict()
    scene["scene_id"] = f"synthetic-flatwall-{variant}"
    scene["provenance"]["site"] = scene["scene_id"]
    if variant == "hazard":
        scene["objects"].append(hazard_object())
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    from server.criteria.harness import load

    h = load()
    pts = _flatwall._points()
    h.sceneio.write_ply(dest / "laser.ply", pts)
    state, views = _flatwall._grid()
    np.savez_compressed(
        dest / "coverage.npz",
        state=state,
        origin=np.array([-7.0, -0.4, -0.4]),
        voxel=np.float64(0.1),
        views=views,
    )
    scene["assets"][0]["sha256"] = hashlib.sha256((dest / "laser.ply").read_bytes()).hexdigest()
    scene["assets"][0]["count"] = int(len(pts))
    scene["assets"][1]["sha256"] = hashlib.sha256((dest / "coverage.npz").read_bytes()).hexdigest()
    (dest / "scene.json").write_text(json.dumps(scene, indent=1) + "\n")
    return dest


def supported_wall_variants(dest: Path) -> dict[str, Path]:
    """Write both variants into sibling ``scene-<variant>`` directories under ``dest``."""
    return {name: write_variant(Path(dest) / f"scene-{name}", variant=name) for name in VARIANTS}
