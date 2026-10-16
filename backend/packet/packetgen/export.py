"""Standalone packet export, plus a measure-lab-session v2 view (PR #7) so PR #10's replay
(`-replay <folder>`), PR #9's converter and PR #12's field tools read a packet unchanged."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

PR7_UNITS = {
    "length": "meters", "angle": "degrees",
    "time": "seconds of device uptime, the clock of ARFrame.timestamp", "image": "pixels of the saved JPEG",
}
PR7_CONVENTIONS = {
    "world": "ARKit world frame with worldAlignment .gravity: right-handed, y up (away from gravity), origin and heading fixed where the session started",
    "camera": "ARKit camera frame: +x right and +y up in the unrotated sensor image, the camera looks along -z",
    "pose": "camera-to-world 4x4 matrix, 16 numbers column by column (simd_float4x4 layout)",
    "intrinsics": "[fx, fy, cx, cy] in pixels of the saved, unrotated landscape JPEG",
    "pixel": "[u, v] continuous image coordinates: (0, 0) is the top-left corner of the JPEG, v grows down",
    "ray": "origin is the camera position; direction is a unit vector through the tapped pixel",
}
PR7_REASONS = {"motion", "tap", "freeze"}


def to_measure_lab_v2(pk: dict) -> dict:
    """PR #7 session.json. v2 has no epochs, so only the epoch with the most keyframes is kept."""
    if pk["session"]["tier"] == "photo_only":
        raise ValueError("photo_only packets have no ARKit keyframes to convert")
    counts: dict[str, int] = {}
    for k in pk["keyframes"]:
        counts[k["epoch"]] = counts.get(k["epoch"], 0) + 1
    epoch = max(counts, key=counts.get)
    s = pk["session"]
    kfs = [k for k in pk["keyframes"] if k["epoch"] == epoch]
    keep = {k["id"] for k in kfs}
    return {
        "format": "measure-lab-session", "formatVersion": 2,
        "units": PR7_UNITS, "conventions": PR7_CONVENTIONS,
        "session": {
            "id": s["id"], "startedAt": s["startedAt"], "startedAtUptime": s["startedAtUptime"],
            "appVersion": s["appVersion"], "deviceModel": s["deviceModel"], "systemVersion": s["systemVersion"],
            "lidarAvailable": s["lidarAvailable"], "meshReconstructionSupported": s.get("meshReconstructionSupported", False),
            "sceneDepthEnabled": s["sceneDepthEnabled"],
        },
        "gates": s.get("gates", {}),
        "keyframes": [
            {"id": k["id"], "img": k["img"], "w": k["w"], "h": k["h"], "intrinsics": k["intrinsics"], "pose": k["pose"],
             "timestamp": k["timestamp"], "tracking": k["tracking"],
             "reason": k["reason"] if k["reason"] in PR7_REASONS else "motion",
             "depth": None if not k.get("depth") else {kk: k["depth"][kk] for kk in ("file", "confidenceFile", "w", "h")}}
            for k in kfs
        ],
        "taps": [
            {"id": t["id"], "time": t["time"], "tool": t.get("tool", "mark"), "step": t.get("step", t["label"]),
             "keyframe": t["keyframe"], "frozen": t.get("frozen", False), "pixel": t["pixel"],
             "rayOrigin": t["rayOrigin"], "rayDirection": t["rayDirection"],
             "displayMappingCheck": t.get("displayMappingCheck"), "point": None, "refusal": t.get("refusal")}
            for t in pk.get("taps", []) if t["keyframe"] in keep
        ],
        "points": [], "walls": [], "measurements": [], "refusals": [],
        "tracking": [{"time": e["time"], "state": e["state"]} for e in pk.get("tracking", []) if e.get("epoch", epoch) == epoch],
    }


def export(manifest: Path, out: Path) -> Path:
    """Copy one variant's files into `out` (a folder, or a .zip) as packet.json + session.json."""
    root = manifest.parent
    pk = json.loads(manifest.read_text())
    files = [f["path"] for f in pk["files"]]
    compat = None if pk["session"]["tier"] == "photo_only" else to_measure_lab_v2(pk)
    if out.suffix == ".zip":
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
            for rel in files:
                z.write(root / rel, rel)
            if compat:
                z.writestr("session.json", json.dumps(compat, indent=1))
            z.writestr("packet.json", json.dumps(pk, indent=1))
        return out
    out.mkdir(parents=True, exist_ok=True)
    for rel in files:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, out / rel)
    if compat:
        (out / "session.json").write_text(json.dumps(compat, indent=1))
    (out / "packet.json").write_text(json.dumps(pk, indent=1))
    return out
