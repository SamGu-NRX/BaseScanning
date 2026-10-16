"""Check a packet the way the server will on arrival (spec section 6)."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import jsonschema
import numpy as np

from .conventions import from_column_major, pixel_ray_arkit

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "capture-packet.schema.json"


def open_stream(path: Path):
    """Streams are gzip-compressed (streams/<name>.csv.gz, Appendix D); plain CSV is still read."""
    return gzip.open(path, "rt", newline="") if path.suffix == ".gz" else open(path, newline="")


CLOSE_UP_DISTANCE_M = (0.3, 3.0)
HEAD_ON_MAX_ANGLE_DEG = 30.0
OBLIQUE_ANGLE_DEG = (15.0, 50.0)


def _check_close_up(label: str, mc: dict | None, purpose: str, angle_ok, stills: dict, errors, warnings) -> None:
    if not mc or not mc["captured"]:
        return
    s = stills.get(mc["still"])
    if s is None:
        errors.append(f"scale: {label}.still {mc['still']} is not a still")
        return
    if s["purpose"] != purpose:
        warnings.append(f"scale: {label}.still {mc['still']} has purpose {s['purpose']}, expected {purpose}")
    if not s.get("keyframe"):
        warnings.append(f"scale: {label} {mc['still']} has no keyframe link, so it was not taken inside the ARKit session")
    d = mc.get("arkitDistanceM")
    if d is not None and not CLOSE_UP_DISTANCE_M[0] <= d <= CLOSE_UP_DISTANCE_M[1]:
        warnings.append(f"scale: {label} taken from {d:.2f} m; about 1 m (3 ft) is expected")
    ang = mc.get("viewAngleDeg")
    if ang is not None and not angle_ok(ang):
        warnings.append(f"scale: {label} viewing angle {ang:.0f} deg is outside the intended range")


def check_scale_reference(pk: dict, kfs: dict, errors: list[str], warnings: list[str], root: Path | None = None) -> None:
    """Semantic rules for the zero-effort scale inputs (v0.4, A9) that the schema cannot express."""
    sr = pk.get("scaleReference")
    if sr is None:
        return
    stills = {x["id"]: x for x in pk.get("stills", [])}
    frames = {**{k: (v["w"], v["h"]) for k, v in kfs.items()}, **{k: (v["w"], v["h"]) for k, v in stills.items()}}
    _check_close_up("meterCloseUp", sr.get("meterCloseUp"), "meter_close", lambda a: a <= HEAD_ON_MAX_ANGLE_DEG, stills, errors, warnings)
    ext = sr.get("ext") or {}
    _check_close_up("obliqueCloseUp", ext.get("obliqueCloseUp"), "meter_oblique",
                    lambda a: OBLIQUE_ANGLE_DEG[0] <= a <= OBLIQUE_ANGLE_DEG[1], stills, errors, warnings)
    mi = sr.get("meterIdentity")
    if mi and mi["source"] == "on_device_text" and not (mi.get("make") or mi.get("model") or mi.get("rawText")):
        warnings.append("scale: meterIdentity says on_device_text but carries no text")
    for dv in ext.get("doorViews", []):
        wh = frames.get(dv["frame"])
        if wh is None:
            errors.append(f"scale: door view on unknown frame {dv['frame']}")
        elif "box" in dv:
            u0, v0, u1, v1 = dv["box"]
            if not (0 <= u0 < u1 <= wh[0] and 0 <= v0 < v1 <= wh[1]):
                errors.append(f"scale: door box {dv['box']} not inside {dv['frame']}")
    if ext.get("doorOnMeterSide") and not ext.get("doorViews"):
        warnings.append("scale: doorOnMeterSide is true but no door view was saved")
    td = ext.get("trueDepthScan") or {}
    if td.get("captured"):
        if td.get("depthDataAccuracy") == "relative":
            warnings.append("scale: TrueDepth scan has relative depth accuracy and cannot give scale")
        listed = {f["path"] for f in pk.get("files", [])}
        for fr in td.get("frames", []):
            for rel in (fr["depth"], fr.get("image")):
                if rel and rel not in listed:
                    errors.append(f"scale: TrueDepth file {rel} not listed in files")
            if root is not None and (root / fr["depth"]).exists() and (root / fr["depth"]).stat().st_size != fr["w"] * fr["h"] * 4:
                errors.append(f"scale: TrueDepth depth {fr['depth']} is not w*h*4 bytes")


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
    check_scale_reference(pk, kfs, errors, warnings, root)
    for name, st in (pk.get("streams") or {}).items():
        need(st["file"], f"stream {name}")
        p = root / st["file"]
        if p.exists():
            with open_stream(p) as f:
                header = f.readline().strip().split(",")
            if header != st["columns"]:
                errors.append(f"stream {name}: CSV header does not match columns")
    return {
        "packet": str(manifest), "tier": pk["session"]["tier"], "keyframes": len(kfs), "stills": len(pk.get("stills", [])),
        "taps": len(tap_errors), "max_tap_replay_error": max(tap_errors) if tap_errors else None,
        "errors": errors, "warnings": warnings, "ok": not errors,
    }
