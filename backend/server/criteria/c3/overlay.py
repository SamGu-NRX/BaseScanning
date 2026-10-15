"""Draw the chosen footprint into a keyframe. Used only when the judge actually runs."""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw

from server.criteria.harness import load

LONG_SIDE = 1568


def project_box(scene: dict, keyframe: dict, corners_m: list[list[float]]) -> list[tuple[float, float]]:
    h = load()
    pose = h.frame.mat4_from_list(keyframe["pose"])
    k = keyframe["intrinsics"]
    pts = []
    for x, y, z in corners_m:
        uv, _, _ = h.frame.project_arkit(pose, k, [x, y, z])
        pts.append((float(uv[0]), float(uv[1])))
    return pts


def footprint_corners(placement: dict) -> list[list[float]]:
    """Scene-metre corners of the chosen box, then the cable route."""
    box = placement.get("box_scene_m") or {}
    center = box.get("center_m")
    size = box.get("size_m")
    if not center or not size or len(center) < 3 or len(size) < 3:
        return []
    yaw = math.radians(float(box.get("yaw_deg") or 0.0))
    hx, hz = float(size[0]) / 2.0, float(size[2]) / 2.0
    corners = []
    for sx, sz in ((-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz)):
        x = float(center[0]) + sx * math.cos(yaw) - sz * math.sin(yaw)
        z = float(center[2]) + sx * math.sin(yaw) + sz * math.cos(yaw)
        corners.append([round(x, 4), round(float(center[1]), 4), round(z, 4)])
    for point in placement.get("route_polyline_scene_m") or []:
        if len(point) >= 3:
            corners.append([float(point[0]), float(point[1]), float(point[2])])
    return corners


def outline_data_url(
    image_path: Path,
    points: list[tuple[float, float]],
    src_wh: tuple[int | None, int | None] = (None, None),
) -> str | None:
    if not image_path.is_file():
        return None
    image = Image.open(image_path).convert("RGB")
    src_w = src_wh[0] or image.size[0]
    src_h = src_wh[1] or image.size[1]
    scale = LONG_SIDE / max(image.size)
    if scale < 1:
        image = image.resize((max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale))))
        scale_x = image.size[0] / src_w
        scale_y = image.size[1] / src_h
    else:
        scale_x = image.size[0] / src_w
        scale_y = image.size[1] / src_h
    if len(points) >= 2:
        draw = ImageDraw.Draw(image)
        scaled = [(p[0] * scale_x, p[1] * scale_y) for p in points]
        draw.line(scaled, fill=(255, 64, 64), width=3)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=80)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def judge_photos(scene: dict, scene_dir, corners_m: list[list[float]], limit: int = 16) -> list[dict]:
    """Up to 16 keyframes with the placement drawn on, for one passing profile."""
    scene_dir = Path(scene_dir)
    photos = []
    for frame in scene.get("keyframes") or []:
        name = frame.get("image")
        if not name:
            continue
        points: list[tuple[float, float]] = []
        if corners_m and frame.get("pose") and frame.get("intrinsics"):
            try:
                points = project_box(scene, frame, corners_m)
            except Exception:
                points = []
        url = outline_data_url(scene_dir / name, points, (frame.get("w"), frame.get("h")))
        if not url:
            continue
        photos.append({"id": frame.get("id"), "url": url})
        if len(photos) >= limit:
            break
    return photos
