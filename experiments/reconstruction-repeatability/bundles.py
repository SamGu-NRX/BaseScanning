"""Synthetic scan bundles and the stubbed MoGe-2 model for the repeatability experiment.

Nothing here is a capture of a real home: every surface is a plane, every depth map is written by
an analytic stub, and no private rule or real-world coordinate enters a fixture. The bundles use
the app's scan-bundle layout (scene.json, keyframe JPEGs), so a case can go through
`pipeline.run`, the caller a real bundle takes, with `--depth moge`.

The MoGe-2 model is stubbed, never run: `make_stub_model` replaces the subprocess
`recon.depth.moge` would start (`uv run --project recon/models python models/moge_depth.py`) with
a function that reads the worker's own cache manifest and writes an analytic depth map per frame:
a wall plane 2 m along each camera's axis, the ground strip in front of it, and a fixed per-frame
ripple. The stub is a deterministic function of the frame id and the fx the worker's manifest
carries (the fixture's pixels are square, fy = fx), so two runs on the same inputs write
byte-identical maps. No weights are downloaded, no GPU is touched, and nothing here measures
MoGe-2's accuracy: the depth model is not exercised by this experiment.

`recon.depth.rescale` is replaced with an identity pass (see run.py): the triangulated rescale is
a deterministic function of the depths and poses and holds no history, so leaving it out does not
hide any history effect; it is recorded as a limitation.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import cv2
import numpy as np

FEET = 0.3048
W, H = 320, 240
K = (100.0, 100.0, 160.0, 120.0)  # fx, fy, cx, cy in pixels of the 320 x 240 image
D_WALL = 2.0  # the stubbed wall sits this far along each camera's axis
CAM_Y = 1.0  # cameras stand this high above the ground
RIPPLE_M = 0.004
XS = (0.5, 1.0, 1.5, 2.0)  # camera x positions in metres; cameras look along -z from z = 2
WALL_X = (0.1, 2.6)  # the wall's x extent at z = 0
BASELINE_X = (-0.6, 3.2)  # the phone's mark, claiming more than the frames saw
METER_X = 1.0
POISON_M = 0.05  # the same-size cache edit's depth shift


def synthetic_depth(fid: str, fx: float, poison_m: float = 0.0) -> np.ndarray:
    """The stub's map: the wall plane at D_WALL, the ground strip in front of it, a fixed ripple.
    Rows at or above the wall's base edge see the wall; rows below it see the ground, whose depth
    along the camera axis is fy / (v - cy + 0.5) with the camera CAM_Y above the ground. `poison_m`
    shifts the whole map, the same-size edit's corruption."""
    below = np.arange(H, dtype=np.float64)[:, None] + 0.5 - K[3]  # pixels below the horizon
    wall_edge = fx * CAM_Y / D_WALL
    depth = np.where(below > wall_edge, fx / np.maximum(below, 1e-9), D_WALL)
    u = np.arange(W, dtype=np.float64)[None, :] + 0.5
    ripple = RIPPLE_M * np.sin(u / 6.0 + (zlib.crc32(fid.encode()) % 7))
    return (depth + ripple + poison_m).astype(np.float32)


def make_stub_model(fail_after: int | None = None):
    """A stand-in for the MoGe-2 subprocess. Reads the cache manifest the worker wrote (its last
    command-line argument), writes each frame's npz, and raises after `fail_after` maps to
    simulate a model process killed mid-run. Returns the function and its call counter."""
    calls = {"n": 0, "wrote": 0}

    def run(cmd, check=True, env=None, **kw):  # matches the one subprocess.run call in depth.moge
        calls["n"] += 1
        manifest = json.loads(Path(cmd[-1]).read_text())
        for entry in manifest:
            out = Path(entry["out"])
            fid = out.name[: -len(".moge2.npz")]
            fx = float(entry["fx"])
            np.savez(out, depth=synthetic_depth(fid, fx))
            calls["wrote"] += 1
            if fail_after is not None and calls["wrote"] > fail_after:
                raise RuntimeError(f"simulated: model process killed after {fail_after} maps")
        if check:
            return 0
        raise AssertionError("the worker calls the model with check=True")

    return run, calls


def _frame_doc(root: Path, fid: str, pos: np.ndarray, k: tuple[float, ...]) -> dict:
    """One keyframe: JPEG and its scene.json entry (feet, column-major pose)."""
    u = np.arange(W)[None, :] + 0.5
    v = np.arange(H)[:, None] + 0.5
    img = np.clip(128 + 20 * np.sin((u + v) / 9.0), 0, 255).astype(np.uint8)
    cv2.imwrite(str(root / f"{fid}.jpg"), cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    pose = np.eye(4)
    pose[:3, 3] = pos
    pose[:3, 3] /= FEET  # scene.json translations are feet
    return {
        "id": fid,
        "pose": [round(float(x), 4) for x in pose.T.reshape(-1)],
        "intrinsics": [round(float(x), 4) for x in k],
        "w": W,
        "h": H,
        "img": f"{fid}.jpg",
    }


def write_bundle(
    root: Path,
    *,
    xs_shift: float = 0.0,
    reverse: bool = False,
    duplicate: bool = False,
    k: tuple[float, ...] = K,
) -> Path:
    """A bundle of planes: a target wall along +x at z = 0 and the floor at y = 0, seen by
    `len(XS)` cameras 1 m up, 2 m out, looking along -z. `xs_shift` moves every camera along x (a
    genuinely different capture, whose pose changes the depth cache key); `reverse` reorders the
    keyframes; `duplicate` repeats the first keyframe's record, id and all; `k` replaces the
    intrinsics. The phone's baseline `BASELINE_X` claims more wall than the frames saw."""
    root.mkdir(parents=True, exist_ok=True)
    kfs = [
        _frame_doc(root, f"k{i}", np.array([x + xs_shift, CAM_Y, 2.0]), k) for i, x in enumerate(XS)
    ]
    if reverse:
        kfs.reverse()
    if duplicate:
        kfs.append(dict(kfs[0]))
    doc = {
        "schema_version": "1.0",
        "meter": {
            "pos": [round(METER_X / FEET, 4), 4.0, 0.1],
            "wall_id": "wall",
        },
        "walls": [{"id": "wall", "baseline": [[x / FEET, 0.0] for x in BASELINE_X]}],
        "objects": [],
        "ground": [],
        "coverage": {"ends": {"left": {"kind": "unexplored"}, "right": {"kind": "unexplored"}}},
        "keyframes": kfs,
    }
    (root / "scene.json").write_text(json.dumps(doc, indent=1))
    return root


def edit_pose(bundle: Path, fid: str, dx_m: float) -> Path:
    """Move one keyframe's camera along x, the edit the stale-cache case makes after a first run."""
    doc = json.loads((bundle / "scene.json").read_text())
    kf = next(k for k in doc["keyframes"] if k["id"] == fid)
    pose = np.array(kf["pose"], dtype=np.float64).reshape(4, 4).T  # column-major in, row-major here
    pose[0, 3] += dx_m / FEET
    kf["pose"] = [round(float(x), 4) for x in pose.T.reshape(-1)]
    (bundle / "scene.json").write_text(json.dumps(doc, indent=1))
    return bundle


def reencode_jpegs(bundle: Path) -> None:
    """Decode and re-encode every keyframe JPEG in place: the same pixels, different bytes."""
    for path in sorted(bundle.glob("*.jpg")):
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        assert img is not None, path
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 95])


def poison_cache(work: Path, fid: str, shift_m: float = POISON_M) -> Path:
    """Overwrite one cached depth map with a shifted copy: same shape, same dtype, key.json
    untouched. Returns the tampered file."""
    hits = sorted(work.glob(f"**/{fid}.moge2.npz"))
    assert len(hits) == 1, f"expected one cached map for {fid}, found {len(hits)}"
    depth = np.load(hits[0])["depth"]
    np.savez(hits[0], depth=(depth + np.float32(shift_m)).astype(np.float32))
    return hits[0]
