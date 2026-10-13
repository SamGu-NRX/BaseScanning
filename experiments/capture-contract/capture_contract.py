"""Validate and adapt measure-lab v2 camera metadata. No images or models are run."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
import tempfile
from math import hypot, isfinite
from pathlib import Path

ROTATION_TOLERANCE = 1e-4  # Float32 serialization tolerance, not field accuracy.
RAY_TOLERANCE = 1e-6      # Arithmetic agreement, not a measurement error bound.


def vector(value, size, label):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{label}: expected {size} numbers")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
        raise ValueError(f"{label}: numbers must be finite")
    try:
        numbers = tuple(float(x) for x in value)
    except OverflowError as error:
        raise ValueError(f"{label}: number exceeds floating-point range") from error
    if not all(isfinite(x) for x in numbers):
        raise ValueError(f"{label}: numbers must be finite")
    return numbers


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def norm(v):
    return hypot(*v)


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2],
            a[0]*b[1]-a[1]*b[0])


def unit(v):
    length = norm(v)
    if not isfinite(length) or length == 0:
        raise ValueError("direction must be finite and nonzero")
    return tuple(x / length for x in v)


def distance(a, b):
    return norm(tuple(x - y for x, y in zip(a, b)))


def camera_pose(value, label):
    p = vector(value, 16, label)
    if any(abs(p[i]-expected) > 1e-8
           for i, expected in zip((3, 7, 11, 15), (0, 0, 0, 1))):
        raise ValueError(f"{label}: expected column-major affine camera-to-world pose")
    axes = [p[i:i+3] for i in (0, 4, 8)]
    if (any(abs(norm(a)-1) > ROTATION_TOLERANCE for a in axes)
            or any(abs(dot(axes[i], axes[j])) > ROTATION_TOLERANCE
                   for i, j in ((0, 1), (0, 2), (1, 2)))
            or distance(cross(axes[0], axes[1]), axes[2]) > ROTATION_TOLERANCE):
        raise ValueError(f"{label}: expected a proper rigid rotation, no scale/reflection")
    return p


def positive_size(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label}: expected a positive integer")
    return value


def validate_frame(frame):
    label = f"keyframe {frame.get('id', '?')}"
    if not isinstance(frame.get("id"), str) or not frame["id"]:
        raise ValueError(f"{label}: missing id")
    width = positive_size(frame.get("w"), label + ".w")
    height = positive_size(frame.get("h"), label + ".h")
    intrinsics = vector(frame.get("intrinsics"), 4, label + ".intrinsics")
    if intrinsics[0] <= 0 or intrinsics[1] <= 0:
        raise ValueError(f"{label}: focal lengths must be positive")
    pose = camera_pose(frame.get("pose"), label + ".pose")
    vector([frame.get("timestamp")], 1, label + ".timestamp")
    if not isinstance(frame.get("tracking"), str):
        raise ValueError(f"{label}: tracking must be explicit")
    if not isinstance(frame.get("img"), str) or not frame["img"]:
        raise ValueError(f"{label}: image reference must be explicit")
    return width, height, intrinsics, pose


def ray_for_pixel(frame, pixel):
    width, height, (fx, fy, cx, cy), pose = validate_frame(frame)
    u, v = vector(pixel, 2, "pixel")
    if not (0 <= u < width and 0 <= v < height):
        raise ValueError("tap pixel lies outside its saved image")
    local = ((u-cx)/fx, -(v-cy)/fy, -1)
    # ARKit camera axes are columns; camera looks along -Z.
    direction = tuple(sum(pose[4*j+i]*local[j] for j in range(3))
                      for i in range(3))
    return pose[12:15], unit(direction)


def opencv_camera_to_world(pose):
    p = list(camera_pose(pose, "pose"))
    # Change CAMERA basis only. The session world stays right-handed, Y up.
    for column in (1, 2):
        for row in range(4):
            p[4*column+row] *= -1
    return [[p[4*column+row] for column in range(4)] for row in range(4)]


def crop_resize_intrinsics(intrinsics, crop_origin, scale):
    fx, fy, cx, cy = vector(intrinsics, 4, "intrinsics")
    x0, y0 = vector(crop_origin, 2, "crop origin")
    sx, sy = vector(scale, 2, "scale")
    if fx <= 0 or fy <= 0 or sx <= 0 or sy <= 0:
        raise ValueError("focal lengths and image scale must be positive")
    return list(vector([fx*sx, fy*sy, (cx-x0)*sx, (cy-y0)*sy],
                       4, "resized intrinsics"))


def validate_session(session):
    if session.get("format") != "measure-lab-session" or session.get("formatVersion") != 2:
        raise ValueError("expected measure-lab-session formatVersion 2")
    if session.get("units", {}).get("length") != "meters":
        raise ValueError("expected explicit meters; no implicit unit conversion")
    session_id = session.get("session", {}).get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session.id must be explicit")
    frames = {}
    for frame in session.get("keyframes", []):
        validate_frame(frame)
        if frame["id"] in frames:
            raise ValueError(f"duplicate keyframe id: {frame['id']}")
        frames[frame["id"]] = frame
    if not frames:
        raise ValueError("no listed keyframes")
    tap_checks = []
    tap_ids = set()
    for tap in session.get("taps", []):
        tap_id = tap.get("id")
        if not isinstance(tap_id, str) or not tap_id:
            raise ValueError("tap: missing id")
        if tap_id in tap_ids:
            raise ValueError(f"duplicate tap id: {tap_id}")
        tap_ids.add(tap_id)
        frame = frames.get(tap.get("keyframe"))
        if frame is None:
            raise ValueError(f"tap {tap.get('id')}: unknown keyframe")
        origin, direction = ray_for_pixel(frame, tap.get("pixel"))
        saved_origin = vector(tap.get("rayOrigin"), 3, "tap.rayOrigin")
        saved_direction = vector(tap.get("rayDirection"), 3, "tap.rayDirection")
        if abs(norm(saved_direction)-1) > RAY_TOLERANCE:
            raise ValueError(f"tap {tap.get('id')}: saved direction must be a unit vector")
        origin_error = distance(origin, saved_origin)
        direction_error = distance(direction, saved_direction)
        if origin_error > RAY_TOLERANCE or direction_error > RAY_TOLERANCE:
            raise ValueError(f"tap {tap.get('id')}: saved ray disagrees with its keyframe "
                             f"(origin delta={origin_error:g}, direction delta={direction_error:g})")
        tap_checks.append({"id": tap.get("id"), "origin_delta_m": origin_error,
                           "unit_direction_delta": direction_error})
    converted = []
    # Arrival order can differ from exposure order; source explicitly permits it.
    for frame in sorted(frames.values(), key=lambda item: (item["timestamp"], item["id"])):
        width, height, (fx, fy, cx, cy), pose = validate_frame(frame)
        converted.append({
            "id": frame["id"], "image_reference": frame["img"],
            "width": width, "height": height, "timestamp": frame["timestamp"],
            "intrinsics": [[fx, 0, cx], [0, fy, cy], [0, 0, 1]],
            "camera_to_world": opencv_camera_to_world(list(pose)),
            "tracking": frame["tracking"],
            "tracking_normal": frame["tracking"] == "normal",
        })
    return {
        "format": "capture-contract-probe-1",
        "world_frame": {"id": session_id, "axes": "ARKit session: right-handed, Y up",
                        "units": "meters as reported by ARKit; not independently calibrated",
                        "meter_anchored": False},
        "camera_axes": "OpenCV: X right, Y down, Z forward",
        "matrix_serialization": "nested rows; camera-to-world",
        "keyframes": converted, "tap_checks": tap_checks,
        "limits": ["metadata arithmetic only", "no image-size or image-content verification",
                   "no device accuracy, pose epoch or scale calibration established",
                   "tracking-normal does not establish measurement accuracy",
                   "no meter anchor exists in this source contract"],
    }


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"invalid JSON numeric constant: {value}")


def write_result(path, result):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.session.resolve() == args.out.resolve():
            raise ValueError("output must not overwrite the source manifest")
        raw = args.session.read_bytes()
        session = json.loads(raw, object_pairs_hook=no_duplicates,
                             parse_constant=reject_constant)
        result = validate_session(session)
        result["source_manifest_sha256"] = sha256(raw).hexdigest()
        result["image_reference_base"] = str(args.session.resolve().parent)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_result(args.out, result)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        parser.exit(2, f"capture contract: {error}\n")
    print(f"Checked {len(result['keyframes'])} frame(s), {len(result['tap_checks'])} tap(s). "
          f"Wrote {args.out}. Metadata only; physical accuracy untested.")


if __name__ == "__main__":
    main()
