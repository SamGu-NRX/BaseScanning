"""Check a packet the way the server will on arrival (spec section 6)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import numpy as np

from .conventions import from_column_major, pixel_ray_arkit

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "capture-packet.schema.json"


def validate_packet(manifest: Path, check_hashes: bool = True) -> dict:
    root = manifest.parent
    pk = json.loads(manifest.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    errors, warnings = [], []
    v = jsonschema.Draft202012Validator(schema)
    for e in sorted(v.iter_errors(pk), key=lambda e: list(e.path)):
        errors.append(f"schema: {'/'.join(map(str, e.path))}: {e.message[:200]}")

    listed = {f["path"]: f for f in pk.get("files", [])}
    for rel, f in listed.items():
        p = root / rel
        if not p.exists():
            errors.append(f"files: {rel} missing")
            continue
        if p.stat().st_size != f["bytes"]:
            errors.append(f"files: {rel} is {p.stat().st_size} bytes, manifest says {f['bytes']}")
        elif check_hashes and hashlib.sha256(p.read_bytes()).hexdigest() != f["sha256"]:
            errors.append(f"files: {rel} sha256 mismatch")

    def need(rel, what):
        if rel is not None and rel not in listed:
            errors.append(f"{what}: {rel} not listed in files")

    epochs = {e["id"] for e in pk.get("epochs", [])}
    kfs = {k["id"]: k for k in pk.get("keyframes", [])}
    last_t: dict[str, float] = {}
    for k in pk.get("keyframes", []):
        need(k["img"], f"keyframe {k['id']}")
        T = from_column_major(k["pose"])
        R = T[:3, :3]
        if not np.all(np.isfinite(T)):
            errors.append(f"keyframe {k['id']}: non-finite pose")
        elif np.abs(R.T @ R - np.eye(3)).max() > 1e-4 or abs(np.linalg.det(R) - 1) > 1e-4:
            errors.append(f"keyframe {k['id']}: rotation not orthonormal with det +1")
        if not np.allclose(T[3], [0, 0, 0, 1]):
            errors.append(f"keyframe {k['id']}: last pose row is not [0, 0, 0, 1]")
        fx, fy, cx, cy = k["intrinsics"]
        if fx <= 0 or fy <= 0 or not (0 < cx < k["w"] and 0 < cy < k["h"]):
            errors.append(f"keyframe {k['id']}: implausible intrinsics {k['intrinsics']} for {k['w']}x{k['h']}")
        if k["epoch"] not in epochs:
            errors.append(f"keyframe {k['id']}: unknown epoch {k['epoch']}")
        if k["timestamp"] <= last_t.get(k["epoch"], -np.inf):
            warnings.append(f"keyframe {k['id']}: timestamp not increasing within epoch {k['epoch']}")
        last_t[k["epoch"]] = k["timestamp"]
        d = k.get("depth")
        if d:
            need(d["file"], f"keyframe {k['id']} depth")
            p = root / d["file"]
            if p.exists() and p.stat().st_size != d["w"] * d["h"] * 4:
                errors.append(f"keyframe {k['id']}: depth file is not w*h*4 bytes")
            if d.get("confidenceFile"):
                need(d["confidenceFile"], f"keyframe {k['id']} confidence")
                c = root / d["confidenceFile"]
                if c.exists() and c.stat().st_size != d["w"] * d["h"]:
                    errors.append(f"keyframe {k['id']}: confidence file is not w*h bytes")
        ft = k.get("features")
        if ft:
            need(ft["file"], f"keyframe {k['id']} features")
            p = root / ft["file"]
            if p.exists() and p.stat().st_size != 20 * ft["count"]:
                errors.append(f"keyframe {k['id']}: features file is not 20*count bytes")
    if pk["session"]["tier"] != "photo_only" and pk["session"].get("sceneDepthEnabled"):
        missing = [k["id"] for k in pk["keyframes"] if not k.get("depth")]
        if missing:
            errors.append(f"sceneDepthEnabled but {len(missing)} keyframes have no depth")

    tap_errors = []
    for t in pk.get("taps", []):
        k = kfs.get(t["keyframe"])
        if k is None:
            errors.append(f"tap {t['id']}: unknown keyframe {t['keyframe']}")
            continue
        o, d = pixel_ray_arkit(from_column_major(k["pose"]), tuple(k["intrinsics"]), np.array(t["pixel"]))
        err = max(np.abs(o - t["rayOrigin"]).max(), np.abs(d - t["rayDirection"]).max())
        tap_errors.append(float(err))
        if err > 1e-3:
            errors.append(f"tap {t['id']}: ray replay differs by {err:.2e} (conventions not followed)")
    for s in pk.get("stills", []):
        need(s["img"], f"still {s['id']}")
        if s.get("keyframe") and s["keyframe"] not in kfs:
            errors.append(f"still {s['id']}: unknown keyframe {s['keyframe']}")
    for name, st in (pk.get("streams") or {}).items():
        need(st["file"], f"stream {name}")
        p = root / st["file"]
        if p.exists():
            header = p.open().readline().strip().split(",")
            if header != st["columns"]:
                errors.append(f"stream {name}: CSV header does not match columns")
    return {
        "packet": str(manifest), "tier": pk["session"]["tier"], "keyframes": len(kfs), "stills": len(pk.get("stills", [])),
        "taps": len(tap_errors), "max_tap_replay_error": max(tap_errors) if tap_errors else None,
        "errors": errors, "warnings": warnings, "ok": not errors,
    }
