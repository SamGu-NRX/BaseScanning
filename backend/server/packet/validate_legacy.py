# vendored from internal/modal-pipeline/housepipe/validate.py (store, 2026-09-26)
"""Packet checks from capture spec section 6 (and packetgen.validate).

Schema validation is semantic rather than the full JSON Schema: the stored
benchmark packets are format 0.1, while the generator schema has moved on.
Hashes, poses, epochs, depth sizes and tap replay are the checks the server
actually rejects on.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from server.common.geometry import from_column_major, pixel_ray_arkit


def validate_packet(manifest: Path, check_hashes: bool = True) -> dict:
    import json

    root = manifest.parent
    pk = json.loads(manifest.read_text())
    errors: list[str] = []
    warnings: list[str] = []

    if pk.get("format") != "house-capture-packet":
        errors.append(f"format: {pk.get('format')}")
    version = str(pk.get("formatVersion", ""))
    if not version.startswith("0."):
        errors.append(f"formatVersion: {version}")
    session = pk.get("session") or {}
    tier = session.get("tier")
    if tier not in ("arkit", "arkit_lidar", "photo_only"):
        errors.append(f"session.tier: {tier}")

    listed = {f["path"]: f for f in pk.get("files", [])}
    for rel, f in listed.items():
        path = root / rel
        if not path.is_file():
            errors.append(f"files: {rel} missing")
            continue
        if path.stat().st_size != f["bytes"]:
            errors.append(f"files: {rel} is {path.stat().st_size} bytes, manifest says {f['bytes']}")
        elif check_hashes and hashlib.sha256(path.read_bytes()).hexdigest() != f["sha256"]:
            errors.append(f"files: {rel} sha256 mismatch")

    def need(rel, what):
        if rel and rel not in listed:
            errors.append(f"{what}: {rel} not listed in files")

    epochs = {e["id"] for e in pk.get("epochs", [])}
    if tier in ("arkit", "arkit_lidar") and not epochs:
        errors.append("epochs: required for ARKit tiers")
    kfs = {k["id"]: k for k in pk.get("keyframes", [])}
    if tier in ("arkit", "arkit_lidar") and not kfs:
        errors.append("keyframes: required for ARKit tiers")
    last_t: dict[str, float] = {}
    for k in pk.get("keyframes", []):
        need(k.get("img"), f"keyframe {k.get('id')}")
        try:
            T = from_column_major(k["pose"])
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f"keyframe {k.get('id')}: pose {exc}")
            continue
        R = T[:3, :3]
        if not np.all(np.isfinite(T)):
            errors.append(f"keyframe {k['id']}: non-finite pose")
        elif np.abs(R.T @ R - np.eye(3)).max() > 1e-3 or abs(float(np.linalg.det(R)) - 1) > 1e-3:
            errors.append(f"keyframe {k['id']}: rotation not orthonormal with det +1")
        fx, fy, cx, cy = k["intrinsics"]
        if fx <= 0 or fy <= 0 or not (0 < cx < k["w"] and 0 < cy < k["h"]):
            errors.append(f"keyframe {k['id']}: implausible intrinsics")
        if epochs and k.get("epoch") not in epochs:
            errors.append(f"keyframe {k['id']}: unknown epoch {k.get('epoch')}")
        if k["timestamp"] <= last_t.get(k.get("epoch", ""), -np.inf):
            warnings.append(f"keyframe {k['id']}: timestamp not increasing within epoch")
        last_t[k.get("epoch", "")] = k["timestamp"]
        depth = k.get("depth")
        if depth:
            need(depth.get("file"), f"keyframe {k['id']} depth")
            blob = root / depth["file"]
            if blob.is_file() and blob.stat().st_size != depth["w"] * depth["h"] * 4:
                errors.append(f"keyframe {k['id']}: depth file is not w*h*4 bytes")
            if depth.get("confidenceFile"):
                need(depth["confidenceFile"], f"keyframe {k['id']} confidence")
                conf = root / depth["confidenceFile"]
                if conf.is_file() and conf.stat().st_size != depth["w"] * depth["h"]:
                    errors.append(f"keyframe {k['id']}: confidence file is not w*h bytes")

    if tier == "photo_only" and not pk.get("stills"):
        errors.append("stills: required for photo_only")

    tap_errors = []
    for tap in pk.get("taps", []):
        k = kfs.get(tap.get("keyframe"))
        if k is None:
            errors.append(f"tap {tap.get('id')}: unknown keyframe")
            continue
        origin, direction = pixel_ray_arkit(
            from_column_major(k["pose"]), tuple(k["intrinsics"]), np.array(tap["pixel"], float)
        )
        err = max(
            float(np.abs(origin - np.array(tap["rayOrigin"], float)).max()),
            float(np.abs(direction - np.array(tap["rayDirection"], float)).max()),
        )
        tap_errors.append(err)
        if err > 1e-3:
            errors.append(f"tap {tap.get('id')}: ray replay differs by {err:.2e}")

    for still in pk.get("stills", []):
        need(still.get("img"), f"still {still.get('id')}")

    return {
        "packet": str(manifest),
        "formatVersion": version,
        "tier": tier,
        "packetId": pk.get("packetId"),
        "keyframes": len(kfs),
        "stills": len(pk.get("stills") or []),
        "taps": len(tap_errors),
        "max_tap_replay_error": max(tap_errors) if tap_errors else None,
        "epochs": sorted(epochs),
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }
