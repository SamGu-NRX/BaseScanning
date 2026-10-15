"""Door, garage and footprint checks. They never move the estimate and never verify it."""

from __future__ import annotations

import numpy as np

from server.common.geometry import pixel_ray_arkit
from server.contracts.scale import ScaleEstimate
from server.scale.fuse import Source
from server.scale.rim import _fit_plane, _ray_plane, _track_points

INCH = 0.0254
FOOT = 0.3048
DOOR_INCHES = np.array([80, 78, 84, 96], float)
DOOR_WEIGHTS = np.array([0.80, 0.05, 0.10, 0.05])
GARAGE_SKUS = (
    (16, 7, 0.30),
    (16, 8, 0.10),
    (18, 7, 0.05),
    (18, 8, 0.03),
    (8, 7, 0.10),
    (9, 7, 0.25),
    (9, 8, 0.07),
    (10, 7, 0.04),
    (10, 8, 0.03),
    (8, 8, 0.03),
)


def check_estimates(
    packet: dict, *, footprint_enabled: bool, measured_kinds: set[str] | None = None
) -> list[ScaleEstimate]:
    measured = measured_kinds or set()
    estimates: list[ScaleEstimate] = []
    views = ((packet.get("scaleReference") or {}).get("ext") or {}).get("doorViews") or []
    for view in views:
        kind = view.get("kind")
        if kind == "entry_door":
            estimates.append(
                ScaleEstimate(
                    kind="door_sku",
                    rel_sd=0.016,
                    used=False,
                    role="check",
                    reason=None if "entry_door" in measured else "not_measured",
                )
            )
        elif kind == "garage_door":
            estimates.append(
                ScaleEstimate(
                    kind="garage_door_sku",
                    rel_sd=float(np.hypot(0.01, 0.006)),
                    used=False,
                    role="check",
                    reason=None if "garage_door" in measured else "not_measured",
                )
            )
    reason = "enabled" if footprint_enabled else "disabled"
    rel_sd = 0.05 if footprint_enabled else 0.0
    estimates.append(ScaleEstimate(kind="footprint", rel_sd=rel_sd, used=False, role="check", reason=reason))
    return estimates


def object_sources(packet: dict, cameras, tracks) -> list[tuple[str, Source]]:
    """Leaf and garage widths on the wall plane. Each source is a check, not a primary."""

    views = ((packet.get("scaleReference") or {}).get("ext") or {}).get("doorViews") or []
    by_id = {}
    for camera in cameras:
        by_id[camera.image] = camera
        if camera.keyframe_id:
            by_id[camera.keyframe_id] = camera
    sources = []
    for view in views:
        kind = view.get("kind")
        box = view.get("box")
        camera = by_id.get(view.get("frame"))
        if camera is None or kind not in {"entry_door", "garage_door"} or not box or len(box) < 4:
            continue
        width, height = _opening_size(camera, box, tracks)
        if width is None or width <= 0.05:
            continue
        if kind == "entry_door":
            sources.append(("entry_door", door_leaf_source(float(np.log((80 * INCH) / width)))))
        elif height is not None and height > 0.05:
            sources.append(("garage_door", _garage_source(width, height)))
    return sources


def door_leaf_source(log_obs: float) -> Source:
    """Mixture a measured leaf contributes. ``log_obs`` is log(metric scale)."""

    return Source(
        "door",
        float(log_obs),
        0.016,
        offsets=np.log(80 / DOOR_INCHES),
        weights=DOOR_WEIGHTS,
    )


def _garage_source(width_m: float, height_m: float) -> Source:
    widths = np.array([item[0] * FOOT for item in GARAGE_SKUS])
    aspects = np.array([item[0] / item[1] for item in GARAGE_SKUS])
    prior = np.array([item[2] for item in GARAGE_SKUS], float)
    prior = prior / prior.sum()
    aspect = float(width_m / height_m)
    log_weight = np.log(prior) - 0.5 * ((aspect / aspects - 1) / 0.014) ** 2
    weights = np.exp(log_weight - log_weight.max())
    weights = weights / weights.sum()
    reference = float(widths[0])
    return Source(
        "garage",
        float(np.log(reference / width_m)),
        float(np.hypot(0.01, 0.006)),
        offsets=np.log(reference / widths),
        weights=weights,
    )


def _opening_size(camera, box, tracks) -> tuple[float | None, float | None]:
    cloud = _track_points(tracks, camera.image)
    if len(cloud) < 8 and camera.keyframe_id:
        cloud = _track_points(tracks, camera.keyframe_id)
    plane = _fit_plane(np.asarray(cloud, float))
    if plane is None:
        return None, None
    x0, y0, x1, y1 = (float(value) for value in box[:4])
    mid_y = (y0 + y1) / 2
    mid_x = (x0 + x1) / 2
    left = _hit(camera, (x0, mid_y), plane)
    right = _hit(camera, (x1, mid_y), plane)
    top = _hit(camera, (mid_x, y0), plane)
    bottom = _hit(camera, (mid_x, y1), plane)
    width = None if left is None or right is None else float(np.linalg.norm(left - right))
    height = None if top is None or bottom is None else float(np.linalg.norm(top - bottom))
    return width, height


def _hit(camera, pixel, plane):
    origin, direction = pixel_ray_arkit(camera.pose, camera.intrinsics, np.asarray(pixel, float))
    return _ray_plane(origin, direction, plane)
