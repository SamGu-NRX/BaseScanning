"""A synthetic field session in FIELD_SHEET.md's tap order, with the templates filled in, so
`make field-dryrun` proves the team's exact command, templates and map on this machine.

Scene (true meters, y up): a textured wall in the plane z = 0 facing +z and textured ground at
y = 0. Twelve keyframes 6 m out at chest height look straight at the wall, with every tapped point
in frame. The session's AR world
is the true one shrunk by `AR_SCALE` (poses, points and every rig value), so the report should find
the rig reading 1.5% short; the tape readings are the true lengths to the nearest 1/16 in. The
learned rows run real MoGe-2 on rendered images and mean nothing beyond proving the plumbing.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

FIELD_KIT = Path(__file__).resolve().parents[1] / "field"
FT = 0.3048
AR_SCALE = 0.985
W, H, F = 640, 480, 500.0
CAMERAS = [np.array([x, 1.5, 6.0]) for x in np.arange(-1.0, 11.0)]

# Point id -> (true position, tool, keyframes tapped). Order and ids follow FIELD_SHEET.md.
X = np.array([4.0, 0.0, 2.0])
POINTS = {
    "P1": (X, "ground"),
    "P2": (np.array([0.0, 0.0, 0.0]), "wall"),  # contact A
    "P3": (np.array([9.144, 0.0, 0.0]), "wall"),  # contact B, 30 ft on
    "P4": (np.array([5.0, 0.0, 0.0]), "wall"),  # validation contact
    "P5": (np.array([3.0, 1.4, 0.0]), "wallPoint"),  # scale cross S1
    "P6": (np.array([4.5, 1.4, 0.0]), "wallPoint"),  # scale cross S2
    "P7": (np.array([6.0, 1.3, 0.0]), "wallPoint"),  # opening, left edge
    "P8": (np.array([7.0, 1.3, 0.0]), "wallPoint"),  # opening, right edge
    "P9": (np.array([6.5, 0.9, 0.0]), "wallPoint"),  # sill
    "P10": (np.array([2.0, 1.2, 0.0]), "wallPoint"),  # meter's bottom edge
    "P11": (np.array([5.0, 0.0, 1.2]), "ground"),  # fence-base mark F
    "P12": (np.array([8.0, 2.6, 0.3]), "twoView"),  # overhead corner
    "P13": (X + np.array([0.02, 0.0, 0.01]), "ground"),  # reference X again, 2 cm off
    "P14": (np.array([9.144, 0.0, 0.0]), "ground"),  # mark B
    "P15": (np.array([0.0, 0.0, 0.0]), "ground"),  # mark A
    "P16": (np.array([0.0, 0.0, 1.5]), "ground"),  # long-span mark L1
    "P17": (np.array([7.0, 0.0, 1.5]), "ground"),  # long-span mark L2
}
# (M id, from, to, compared quantity, survey id)
MEASUREMENTS = [
    ("M1", "P2", "P3", "alongWall", "span-30-ab"),
    ("M2", "P5", "P6", "straight", "scale-ref"),
    ("M3", "P7", "P8", "alongWall", "opening-width"),
    ("M4", "P9", "W1", "heightAboveGround", "sill-height"),
    ("M5", "P10", "W1", "heightAboveGround", "meter-height"),
    ("M6", "P11", "W1", "gapToWall", "facing-gap"),
    ("M7", "P12", "W1", "heightAboveGround", "overhead-height"),
    ("M8", "P1", "P13", "straight", "return-gap"),
    ("M9", "P14", "P15", "straight", "span-30-ba"),
    ("M10", "P16", "P17", "straight", "span-20"),
]


def value(a: np.ndarray, b: np.ndarray | None, key: str) -> float:
    """The quantity in Measure Lab's sense for this scene (wall along +x at z = 0, ground y = 0)."""
    if key == "alongWall":
        return abs(b[0] - a[0])
    if key == "straight":
        return float(np.linalg.norm(b - a))
    if key == "heightAboveGround":
        return float(a[1])
    if key == "gapToWall":
        return float(a[2])
    raise ValueError(key)


def tape_text(meters: float) -> str:
    """The reading a tape shows, to the nearest 1/16 in: '30 0 1/16'."""
    sixteenths = round(meters / FT * 12 * 16)
    feet, rest = divmod(sixteenths, 12 * 16)
    inches, frac = divmod(rest, 16)
    return f"{feet} {inches}" + (f" {Fraction(frac, 16)}" if frac else "")


def pixel(X: np.ndarray, cam: np.ndarray) -> tuple[list[float], float]:
    x, y, z = X - cam
    return [W / 2 + F * x / -z, H / 2 - F * y / -z], -z


def render(cam: np.ndarray, texture: np.ndarray) -> np.ndarray:
    """The textured wall and ground as seen from `cam`, looking along -z."""
    u, v = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    d = np.stack([(u - W / 2) / F, -(v - H / 2) / F, -np.ones_like(u)], axis=-1)
    t_wall = -cam[2] / d[..., 2]
    t_ground = np.where(d[..., 1] < 0, -cam[1] / np.minimum(d[..., 1], -1e-9), np.inf)
    hit_wall = (cam + t_wall[..., None] * d)[..., 1] >= 0
    t = np.where(hit_wall, t_wall, t_ground)
    P = cam + t[..., None] * d
    a, b = P[..., 0], np.where(hit_wall, P[..., 1], P[..., 2] + 20)  # ground texture offset
    i = np.clip(((a + 5) * 20).astype(int), 0, texture.shape[1] - 1)
    j = np.clip(((b + 5) * 20).astype(int), 0, texture.shape[0] - 1)
    img = texture[j, i] * np.where(hit_wall, 1.0, 0.7)
    return np.clip(img, 0, 255).astype(np.uint8)


def session() -> dict:
    scale = AR_SCALE
    kfs, taps, points = [], [], []
    for k, cam in enumerate(CAMERAS, 1):
        pose = np.eye(4)
        pose[:3, 3] = cam * scale
        kfs.append(
            {
                "id": f"k{k:05d}",
                "img": f"keyframes/k{k:05d}.jpg",
                "w": W,
                "h": H,
                "intrinsics": [F, F, W / 2, H / 2],
                "pose": pose.T.reshape(-1).tolist(),
                "timestamp": 50.0 + k,
                "tracking": "normal",
            }
        )

    def nearest(X: np.ndarray) -> int:
        return int(np.argmin([abs(c[0] - X[0]) for c in CAMERAS]))

    for pid, (X, tool) in POINTS.items():
        views = [nearest(X)]
        if tool == "twoView":
            views.append(views[0] + 2)  # 2 m sideways: about 25 degrees between the rays
        ids = []
        for kf in views:
            tid = f"T{len(taps) + 1}"
            px, _ = pixel(X, CAMERAS[kf])
            taps.append(
                {"id": tid, "tool": tool, "keyframe": kfs[kf]["id"], "pixel": px, "point": pid}
            )
            ids.append(tid)
        points.append({"id": pid, "kind": tool, "position": (X * scale).tolist(), "taps": ids})

    pos = {pid: X for pid, (X, _) in POINTS.items()}
    measurements = []
    for n, (mid, a, b, key, _) in enumerate(MEASUREMENTS):
        true = value(pos[a], pos.get(b), key)
        measurements.append(
            {
                "id": mid,
                "time": 100.0 + 10 * n,
                "from": a,
                "to": b,
                "referenceWall": "W1" if key == "alongWall" else None,
                "values": {key: true * scale},
                "compared": key,
                "tape": None,
                "warnings": [],
                "accepted": True,
            }
        )
    return {
        "format": "measure-lab-session",
        "formatVersion": 2,
        "units": {"length": "meters"},
        "session": {"id": "dryrun-session", "startedAtUptime": 40.0, "deviceModel": "synthetic"},
        "keyframes": kfs,
        "taps": taps,
        "points": points,
        "walls": [
            {
                "id": "W1",
                "contacts": ["P2", "P3"],
                "cameraPosition": (CAMERAS[5] * scale).tolist(),
                "validations": [{"point": "P4", "passes": True}],
                "warnings": [],
            }
        ],
        "measurements": measurements,
        "refusals": [],
    }


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    texture = cv2.resize(rng.integers(40, 220, (60, 60)).astype(np.float32), (600, 600))
    texture += rng.normal(0, 25, (600, 600))
    folder = out / "dryrun-session"
    (folder / "keyframes").mkdir(parents=True, exist_ok=True)
    doc = session()
    (folder / "session.json").write_text(json.dumps(doc, indent=1))
    for kf, cam in zip(doc["keyframes"], CAMERAS, strict=True):
        cv2.imwrite(str(folder / kf["img"]), render(cam, texture))
    archive = out / "dryrun-session.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for f in sorted(folder.rglob("*")):
            z.write(f, f.relative_to(out))

    pos = {pid: X for pid, (X, _) in POINTS.items()}
    tape = {sid: tape_text(value(pos[a], pos.get(b), key)) for _, a, b, key, sid in MEASUREMENTS}
    survey = json.loads((FIELD_KIT / "survey.template.json").read_text())
    for m in survey["measurements"]:
        if m["value_ft"] == "FILL ft in":
            m["value_ft"] = tape[m["id"]]
    # Mark F's distance along the wall from mark A, derived from the scene.
    feet, inches = tape_text(value(POINTS["P15"][0], POINTS["P11"][0], "alongWall")).split(" ", 1)
    survey["candidates"][0]["location"] = f"{feet} ft {inches} in along the wall from mark A"
    (out / "survey.json").write_text(json.dumps(survey, indent=2) + "\n")
    (out / "map.json").write_text((FIELD_KIT / "map.template.json").read_text())
    print(f"{archive} sha256 {hashlib.sha256(archive.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
