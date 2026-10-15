"""Build one corrupted packet per error code. Nothing here is committed."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np


def corrupt_case(manifest: Path, case: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    # Copy listed files so size and hash checks see the originals, then break one thing.
    pk = json.loads(manifest.read_text(encoding="utf-8"))
    root = manifest.parent
    for entry in pk.get("files") or []:
        src = root / entry["path"]
        if src.is_file():
            dest = out / entry["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(src, dest)
    if case == "sha256_mismatch":
        _flip_jpeg(pk, out)
    elif case == "file_size_mismatch":
        _truncate_features(pk, out)
    elif case == "depth_size":
        _shrink_depth(pk, out)
    elif case == "tap_replay":
        pk["taps"][0]["pixel"][0] = float(pk["taps"][0]["pixel"][0]) + 5
    elif case == "tap_unknown_keyframe":
        pk["taps"][0]["keyframe"] = "missing-keyframe"
    elif case == "unknown_epoch":
        pk["keyframes"][0]["epoch"] = "missing-epoch"
    elif case == "pose_not_rigid":
        pk["keyframes"][0]["pose"][0] = float(pk["keyframes"][0]["pose"][0]) * 1.1
    elif case == "keyframe_timestamp_conflict":
        pk["keyframes"][1]["timestamp"] = pk["keyframes"][0]["timestamp"]
        pose = pk["keyframes"][1]["pose"]
        pose[12] = float(pose[12]) + 0.05
    elif case == "stream_header":
        cols = pk["streams"]["imuRaw"]["columns"]
        cols[0], cols[1] = cols[1], cols[0]
    elif case == "file_not_listed":
        img = pk["keyframes"][0]["img"]
        pk["files"] = [entry for entry in pk["files"] if entry["path"] != img]
    elif case == "image_format":
        _replace_with_heic(pk, out)
    elif case == "scale_still_unknown":
        pk.setdefault("scaleReference", {}).setdefault("meterCloseUp", {})["captured"] = True
        pk["scaleReference"]["meterCloseUp"]["still"] = "missing-still"
    elif case == "tier_rule":
        pk["session"]["tier"] = "photo_only"
        if not pk.get("stills"):
            still = {
                "id": "s-tier",
                "purpose": "house_overview",
                "img": pk["keyframes"][0]["img"],
                "w": pk["keyframes"][0]["w"],
                "h": pk["keyframes"][0]["h"],
                "timestamp": 0,
                "orientation": 1,
            }
            pk["stills"] = [still]
    elif case == "world_alignment_camera":
        pk["session"]["worldAlignment"] = "camera"
    else:
        raise SystemExit(f"unknown case {case}")
    dest = out / "packet.json"
    dest.write_text(json.dumps(pk), encoding="utf-8")
    return dest


def _flip_jpeg(pk: dict, out: Path) -> None:
    for entry in pk["files"]:
        if entry["role"] in {"keyframe", "still"}:
            path = out / entry["path"]
            data = bytearray(path.read_bytes())
            data[-1] ^= 0x01
            path.write_bytes(data)
            return


def _truncate_features(pk: dict, out: Path) -> None:
    for frame in pk["keyframes"]:
        features = frame.get("features") or {}
        if features.get("file"):
            path = out / features["file"]
            if path.is_file() and path.stat().st_size > 4:
                path.write_bytes(path.read_bytes()[:-4])
                return
    # No features file: synthesize one and point a keyframe at a truncated copy.
    rel = "features/f0.bin"
    path = out / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # On-disk size matches count*20, so the only break is the listed byte count.
    payload = b"\x00" * 20
    path.write_bytes(payload)
    pk["keyframes"][0]["features"] = {"file": rel, "count": 1}
    pk["files"].append({"path": rel, "bytes": 16, "sha256": "0" * 64, "role": "features"})


def _shrink_depth(pk: dict, out: Path) -> None:
    for frame in pk["keyframes"]:
        depth = frame.get("depth") or {}
        if not depth.get("file"):
            continue
        path = out / depth["file"]
        if not path.is_file():
            continue
        data = path.read_bytes()[:-4]
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        for entry in pk["files"]:
            if entry["path"] == depth["file"]:
                entry["bytes"] = len(data)
                entry["sha256"] = digest
        return
    raise SystemExit("no depth file")


def _replace_with_heic(pk: dict, out: Path) -> None:
    rel = pk["keyframes"][0]["img"]
    path = out / rel
    # A minimal HEIC-branded header. The format check fires before decode.
    payload = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    for entry in pk["files"]:
        if entry["path"] == rel:
            entry["bytes"] = len(payload)
            entry["sha256"] = digest


def make_depth(root: Path, w: int = 4, h: int = 4) -> None:
    """Helper used by tests that need a depth file of the right size."""
    path = root / "depth/d0.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    values = np.full(w * h, 1.5, dtype="<f4")
    path.write_bytes(values.tobytes())
    conf = root / "depth/c0.bin"
    conf.write_bytes(bytes([2]) * (w * h))
