"""Check a replay session (contract C3, Measure Lab session format v2) before anything trusts it.

    uv run python -m hsverify.replaycheck ~/house-scanning-data/replays/<session>

Two parts:

1. Format: the fields the app and server read are present, every listed JPEG exists with the
   stated size in landscape sensor orientation, poses are rigid transforms, timestamps increase,
   and each `motion` keyframe respects the spacing gate the session declares.
2. Pose accuracy, when `ground_truth.json` sits beside `session.json`. The two worlds share
   gravity but not heading or origin, so the ground truth is aligned with a rotation about
   the vertical axis plus a translation. Reported two ways:
   - best fit over all keyframes: the error left after the best possible alignment;
   - anchored at the first keyframe: aligned using only the first keyframe's pose, the way
     the app anchors everything to the meter. This is the error a result placed from the
     start of the walk would carry, reported against distance walked (ground-truth feet).
   The ratio of ARKit's path length to the ground truth's is reported separately, since a
   scale disagreement dominates both errors when present.

Exits non-zero when a format check fails. Accuracy numbers are reported, not judged.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np

REQUIRED_KEYFRAME_FIELDS = ("id", "img", "w", "h", "intrinsics", "pose", "timestamp", "tracking")


def pose_matrix(pose: list[float]) -> np.ndarray:
    """16 numbers, column by column (simd_float4x4 layout), to a 4x4 array."""
    return np.asarray(pose, dtype=float).reshape(4, 4).T


def numbers(value: Any, count: int) -> list[float] | None:
    """`value` as `count` finite numbers, or None if it is anything else."""
    if not isinstance(value, list) or len(value) != count:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return None
    try:
        converted = [float(v) for v in value]
    except OverflowError:
        return None
    return converted if all(math.isfinite(v) for v in converted) else None


def rigid_problems(m: np.ndarray, tol: float = 1e-3) -> list[str]:
    problems = []
    r = m[:3, :3]
    if not np.allclose(r.T @ r, np.eye(3), atol=tol):
        problems.append("rotation is not orthonormal")
    if abs(np.linalg.det(r) - 1) > tol:
        problems.append(f"rotation determinant {np.linalg.det(r):.4f}, expected 1")
    if not np.allclose(m[3], [0, 0, 0, 1], atol=tol):
        problems.append("bottom row is not [0, 0, 0, 1]")
    return problems


def rotation_angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    rel = a[:3, :3].T @ b[:3, :3]
    cos = np.clip((np.trace(rel) - 1) / 2, -1.0, 1.0)
    return float(np.degrees(np.arccos(cos)))


def jpeg_size(path: Path) -> tuple[int, int]:
    """(width, height) from a JPEG's start-of-frame marker."""
    data = path.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise ValueError("no start-of-frame marker")


def check_format(folder: Path, session: dict) -> tuple[list[str], dict]:
    errors: list[str] = []
    if session.get("format") != "measure-lab-session" or session.get("formatVersion") != 2:
        errors.append(
            f"format {session.get('format')!r} version {session.get('formatVersion')!r}, "
            "expected 'measure-lab-session' version 2"
        )
    for key in ("session", "gates", "keyframes"):
        if key not in session:
            errors.append(f"missing top-level field {key!r}")
    keyframes = session.get("keyframes", [])
    gates = session.get("gates", {})
    spacing_m = gates.get("keyframeSpacingMeters")
    spacing_deg = gates.get("keyframeSpacingDegrees")
    if spacing_m and spacing_deg is None:
        errors.append("gates set keyframeSpacingMeters without keyframeSpacingDegrees")
        spacing_m = None
    previous = None
    previous_motion = None
    gate_violations = 0
    tracking_counts: dict[str, int] = {}
    for kf in keyframes:
        name = kf.get("id", "?")
        missing = [f for f in REQUIRED_KEYFRAME_FIELDS if f not in kf]
        if missing:
            errors.append(f"{name}: missing {', '.join(missing)}")
            continue
        # ReplayKeyframe.intrinsics is defined in the unrotated landscape JPEG's pixels
        # (ios/HouseScanKit/Sources/HouseScanKit/Replay/ReplaySession.swift).
        if kf["w"] < kf["h"]:
            errors.append(f"{name}: portrait keyframe; replay requires a landscape sensor image")
        tracking_counts[kf["tracking"]] = tracking_counts.get(kf["tracking"], 0) + 1
        image = folder / kf["img"]
        if not image.exists():
            errors.append(f"{name}: image {kf['img']} not found")
        else:
            try:
                size = jpeg_size(image)
                if size != (kf["w"], kf["h"]):
                    errors.append(f"{name}: JPEG is {size}, session says {(kf['w'], kf['h'])}")
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        intrinsics, pose = numbers(kf["intrinsics"], 4), numbers(kf["pose"], 16)
        if intrinsics is None or pose is None:
            errors.append(f"{name}: intrinsics must be 4 finite numbers and pose 16")
            continue
        fx, fy, cx, cy = intrinsics
        if not (fx > 0 and fy > 0 and 0 < cx < kf["w"] and 0 < cy < kf["h"]):
            errors.append(f"{name}: intrinsics {kf['intrinsics']} outside the image")
        m = pose_matrix(pose)
        errors += [f"{name}: {p}" for p in rigid_problems(m)]
        if previous is not None and kf["timestamp"] <= previous["timestamp"]:
            errors.append(f"{name}: timestamp does not increase")
        if kf.get("reason") == "motion" and previous_motion is not None and spacing_m:
            moved = float(np.linalg.norm(m[:3, 3] - previous_motion[:3, 3]))
            turned = rotation_angle_deg(previous_motion, m)
            if moved < spacing_m * 0.99 and turned < spacing_deg * 0.99:
                gate_violations += 1
        if kf.get("reason") == "motion":
            previous_motion = m
        previous = kf
    if gate_violations:
        errors.append(f"{gate_violations} motion keyframes closer than the declared spacing gate")
    summary = {
        "keyframes": len(keyframes),
        "tracking": tracking_counts,
        "taps": len(session.get("taps", [])),
        "walls": len(session.get("walls", [])),
    }
    return errors, summary


def yaw_translation_fit(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotation about +y and translation that best map src points onto dst (least squares)."""
    sc, dc = src.mean(axis=0), dst.mean(axis=0)
    a, b = src - sc, dst - dc
    # Rotation about y: x' = c x + s z, z' = -s x + c z. Maximise sum b . R a.
    num = np.sum(a[:, 0] * b[:, 2] - a[:, 2] * b[:, 0])
    den = np.sum(a[:, 0] * b[:, 0] + a[:, 2] * b[:, 2])
    theta = np.arctan2(-num, den)
    c, s = np.cos(theta), np.sin(theta)
    r = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return r, dc - r @ sc


def yaw_of(r: np.ndarray) -> np.ndarray:
    """The rotation about +y closest to `r`, from where it sends the camera's forward axis."""
    forward = r @ np.array([0.0, 0.0, -1.0])
    theta = np.arctan2(-forward[0], -forward[2])
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def accuracy(session: dict, truth: dict) -> dict:
    by_id = {entry["keyframe"]: entry for entry in truth["keyframes"]}
    ar, gt = [], []
    for kf in session["keyframes"]:
        if kf["id"] in by_id:
            ar.append(pose_matrix(kf["pose"]))
            gt.append(pose_matrix(by_id[kf["id"]]["pose"]))
    if len(ar) < 2:
        return {"matched_keyframes": len(ar)}
    ar_pos = np.array([m[:3, 3] for m in ar])
    gt_pos = np.array([m[:3, 3] for m in gt])

    # Both worlds claim gravity alignment: each camera axis should have the same vertical part.
    tilt = [float(np.max(np.abs(a[1, :3] - g[1, :3]))) for a, g in zip(ar, gt, strict=True)]

    r, t = yaw_translation_fit(gt_pos, ar_pos)
    fit_err = np.linalg.norm((gt_pos @ r.T + t) - ar_pos, axis=1)

    # Anchor at the first keyframe: heading from its orientations, origin from its position.
    r0 = yaw_of(ar[0][:3, :3]) @ yaw_of(gt[0][:3, :3]).T
    t0 = ar_pos[0] - r0 @ gt_pos[0]
    anchored = np.linalg.norm((gt_pos @ r0.T + t0) - ar_pos, axis=1)
    walked = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(gt_pos, axis=0), axis=1))])

    def at(distance_m: float) -> float | None:
        idx = np.searchsorted(walked, distance_m)
        return None if idx >= len(walked) else round(float(anchored[idx]), 3)

    ar_walked = float(np.sum(np.linalg.norm(np.diff(ar_pos, axis=0), axis=1)))
    # Same fit after scaling the ground truth to ARKit's path length: what remains is shape
    # and heading drift, with the scale disagreement taken out.
    scale = ar_walked / walked[-1]
    rs, ts = yaw_translation_fit(gt_pos * scale, ar_pos)
    scaled_err = np.linalg.norm((gt_pos * scale @ rs.T + ts) - ar_pos, axis=1)

    ft = 0.3048
    return {
        "matched_keyframes": len(ar),
        "path_length_m": round(float(walked[-1]), 2),
        "arkit_path_length_m": round(ar_walked, 2),
        "arkit_to_truth_length_ratio": round(float(scale), 3),
        "max_vertical_axis_mismatch": round(max(tilt), 4),
        "best_fit_error_m": {
            "median": round(float(np.median(fit_err)), 3),
            "p90": round(float(np.percentile(fit_err, 90)), 3),
            "max": round(float(fit_err.max()), 3),
        },
        "best_fit_error_after_scale_m": {
            "median": round(float(np.median(scaled_err)), 3),
            "p90": round(float(np.percentile(scaled_err, 90)), 3),
        },
        "anchored_error_m_after_ft_walked": {
            f"{d}ft": at(d * ft) for d in (3, 10, 20, 30, 60, 100)
        },
        "anchored_error_m_final": round(float(anchored[-1]), 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, help="session folder containing session.json")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    folder = args.folder.expanduser()
    session = json.loads((folder / "session.json").read_text())
    errors, summary = check_format(folder, session)
    report: dict = {"session": str(folder), "format_errors": errors, "summary": summary}
    truth_path = folder / "ground_truth.json"
    if truth_path.exists() and not errors:
        report["pose_accuracy"] = accuracy(session, json.loads(truth_path.read_text()))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{folder.name}: {summary}")
        print("Format: " + ("ok" if not errors else f"{len(errors)} problems"))
        for e in errors[:30]:
            print(f"  - {e}")
        if "pose_accuracy" in report:
            print("Pose accuracy against ground truth:")
            for k, v in report["pose_accuracy"].items():
                print(f"  {k}: {v}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
