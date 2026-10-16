"""Cover diameter in the close-ups. Error-budget terms are constants, not table fields."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from server.common.geometry import pixel_ray_arkit
from server.scale.contour import ContourSet
from server.scale.meter_table import TableMatch

TWO_VIEW_REL_SD = float(np.sqrt(0.003**2 + (0.15 / 39) ** 2 + 0.0025**2 + 0.005**2))
ONE_VIEW_REL_SD = float(np.sqrt(0.003**2 + (0.3 / 39) ** 2 + 0.0025**2 + 0.005**2))
WALK_REL_SD = 0.03
CIRCLE_RMS_PX = 1.5
CIRCLE_OUT_OF_PLANE_M = 0.001
ELLIPSE_SAMPLES = 72
WALL_CELL_PX = 8
WALL_BAND_RADII = 2.5


@dataclass
class CoverMeasurement:
    method: str
    scale: float
    rel_sd: float
    radius_source: float
    centre_source: np.ndarray
    normal: np.ndarray
    view_angles_deg: list[float]
    single_view_scales: list[float]
    rms_px: float
    out_of_plane_m: float | None
    code: str | None
    warnings: list[str]
    lone: bool = False
    depth_source: float | None = None


def measure_cover(views: list[tuple], match: TableMatch, points: np.ndarray, tracks) -> CoverMeasurement:
    del points
    labelled = [(camera, contours) for camera, contours in views if contours.cover is not None]
    angles = [_view_angle(contours) for _, contours in labelled]
    warnings = _angle_warnings(labelled, angles)
    diameter = float(match.cover_hypotheses[0][0])
    if len(labelled) >= 2:
        measured = _two_view(labelled[0], labelled[1], diameter)
        if measured is not None and measured.code is None:
            measured.warnings = warnings
            return measured
        if measured is not None and measured.code == "scale_reference_views_disagree":
            fallback = _one_view(labelled[0], match, tracks)
            if fallback is not None and fallback.code is None:
                fallback.rel_sd = WALK_REL_SD
                fallback.code = "scale_reference_views_disagree"
                fallback.warnings = warnings
                return fallback
            return measured
    if len(labelled) == 1 and match.rim_offset_m is not None:
        measured = _one_view(labelled[0], match, tracks)
        if measured is not None:
            measured.warnings = warnings
            return measured
        return _failed("scale_plane_fit_failed")
    if len(labelled) == 1:
        return _failed("scale_plane_fit_failed")
    return _failed("scale_reference_edge_not_found")


def _two_view(head, oblique, diameter: float) -> CoverMeasurement | None:
    cam_a, set_a = head
    cam_b, set_b = oblique
    cover_a, cover_b = set_a.cover, set_b.cover
    if cover_a is None or cover_b is None:
        return None
    origin_a, direction_a = pixel_ray_arkit(cam_a.pose, cam_a.intrinsics, np.array([cover_a.cx, cover_a.cy]))
    origin_b, direction_b = pixel_ray_arkit(cam_b.pose, cam_b.intrinsics, np.array([cover_b.cx, cover_b.cy]))
    centre, gap = _closest(origin_a, direction_a, origin_b, direction_b)
    baseline = float(np.linalg.norm(cam_a.pose[:3, 3] - cam_b.pose[:3, 3]))
    if baseline <= 1e-6:
        return _failed("scale_reference_views_disagree")
    z_head = _axis_depth(cam_a, centre)
    z_oblique = _axis_depth(cam_b, centre)
    if z_head <= 0.05 or z_oblique <= 0.05:
        return _failed("scale_reference_views_disagree")
    radius = cover_a.a * z_head / float(cam_a.intrinsics[0])
    normal = _normal(cam_a)
    centre, normal, radius, rms_px, out_of_plane_m = _refine_circle(
        cam_a, cover_a, cam_b, cover_b, centre, normal, radius, diameter
    )
    z_head = _axis_depth(cam_a, centre)
    z_oblique = _axis_depth(cam_b, centre)
    scale = diameter / (2 * radius)
    scales = [
        diameter / (2 * cover_a.a * z_head / float(cam_a.intrinsics[0])),
        diameter / (2 * cover_b.a * z_oblique / float(cam_b.intrinsics[0])),
    ]
    code = None
    if (
        z_head <= 0.05
        or z_oblique <= 0.05
        or gap > 0.01 * baseline
        or abs(scales[0] / scales[1] - 1) > 0.01
        or rms_px > CIRCLE_RMS_PX
        or out_of_plane_m > CIRCLE_OUT_OF_PLANE_M
    ):
        code = "scale_reference_views_disagree"
    return CoverMeasurement(
        "two_view",
        float(scale),
        TWO_VIEW_REL_SD,
        float(radius),
        centre,
        normal,
        [_view_angle(set_a), _view_angle(set_b)],
        scales,
        float(rms_px),
        float(out_of_plane_m),
        code,
        [],
        depth_source=float(z_head),
    )


def _one_view(view, match: TableMatch, tracks) -> CoverMeasurement | None:
    camera, contours = view
    cover = contours.cover
    if cover is None or match.rim_offset_m is None:
        return None
    cloud = _wall_band(_track_points(tracks, camera.image), camera, cover)
    plane = _fit_plane(cloud)
    if plane is None:
        return _failed("scale_plane_fit_failed")
    origin, direction = pixel_ray_arkit(camera.pose, camera.intrinsics, np.array([cover.cx, cover.cy]))
    point = _ray_plane(origin, direction, plane)
    if point is None:
        return _failed("scale_plane_fit_failed")
    z_wall = _axis_depth(camera, point)
    if z_wall <= 0.05:
        return _failed("scale_plane_fit_failed")
    focal = float(camera.intrinsics[0])
    metric = diameter_over(match) * focal / (2 * cover.a) + float(match.rim_offset_m)
    scale = metric / z_wall
    # The plane is the wall. The cover is rim_offset metres in front of it.
    z_cover = z_wall - float(match.rim_offset_m) / scale
    if z_cover <= 0.05:
        return _failed("scale_plane_fit_failed")
    radius = cover.a * z_cover / focal
    return CoverMeasurement(
        "one_view_rim_offset",
        float(scale),
        ONE_VIEW_REL_SD,
        float(radius),
        point,
        plane[1],
        [_view_angle(contours)],
        [float(scale)],
        0.0,
        None,
        None,
        [],
        depth_source=float(z_cover),
    )


def diameter_over(match: TableMatch) -> float:
    return float(match.cover_hypotheses[0][0])


def _failed(code: str) -> CoverMeasurement:
    return CoverMeasurement(
        "one_view_rim_offset",
        1.0,
        ONE_VIEW_REL_SD,
        0.0,
        np.zeros(3),
        np.array([0.0, 0.0, 1.0]),
        [],
        [],
        0.0,
        None,
        code,
        [],
    )


def _view_angle(contours: ContourSet) -> float:
    cover = contours.cover
    if cover is None or cover.a <= 0:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(cover.b / cover.a, -1, 1))))


def _angle_warnings(views, angles: list[float]) -> list[str]:
    warnings = []
    for index, angle in enumerate(angles):
        head_on = index == 0
        if head_on and angle > 30:
            warnings.append("closeup_angle_out_of_spec")
        if not head_on and not 15 <= angle <= 50:
            warnings.append("closeup_angle_out_of_spec")
    return warnings


def _closest(origin_a, direction_a, origin_b, direction_b):
    matrix = np.array(
        [
            [np.dot(direction_a, direction_a), -np.dot(direction_a, direction_b)],
            [np.dot(direction_a, direction_b), -np.dot(direction_b, direction_b)],
        ]
    )
    rhs = np.array(
        [
            np.dot(origin_b - origin_a, direction_a),
            np.dot(origin_b - origin_a, direction_b),
        ]
    )
    try:
        times = np.linalg.solve(matrix, rhs)
    except np.linalg.LinAlgError:
        times = np.zeros(2)
    point_a = origin_a + times[0] * direction_a
    point_b = origin_b + times[1] * direction_b
    return (point_a + point_b) / 2, float(np.linalg.norm(point_a - point_b))


def _axis_depth(camera, point: np.ndarray) -> float:
    local = camera.pose[:3, :3].T @ (np.asarray(point, float) - camera.pose[:3, 3])
    return float(-local[2])


def _normal(camera) -> np.ndarray:
    return camera.pose[:3, :3] @ np.array([0.0, 0.0, 1.0])


def _refine_circle(cam_a, ell_a, cam_b, ell_b, centre, normal, radius, diameter: float):
    """Fit a circle to 72 samples of each ellipse. Returns centre, normal, radius, rms, plane error."""
    bundles = [_view_rays(cam_a, ell_a), _view_rays(cam_b, ell_b)]
    seed_normal = np.asarray(normal, float)
    seed_normal = seed_normal / max(float(np.linalg.norm(seed_normal)), 1e-9)
    seed_radius = max(float(radius), 1e-6)
    seed = np.concatenate([np.asarray(centre, float), seed_normal, [seed_radius]])

    def pixel_residuals(values):
        return _circle_pixel_residuals(bundles, values[:3], values[3:6], float(values[6]))

    try:
        result = least_squares(pixel_residuals, seed, loss="soft_l1", f_scale=1.0, max_nfev=40)
        solved = result.x
    except (ValueError, np.linalg.LinAlgError):
        solved = seed
    centre_v = solved[:3]
    normal_v = solved[3:6]
    norm = float(np.linalg.norm(normal_v))
    normal_v = normal_v / norm if norm > 1e-9 else seed_normal
    radius_v = abs(float(solved[6]))
    if not np.isfinite(radius_v) or radius_v <= 1e-6:
        radius_v = seed_radius
    errs = _circle_pixel_residuals(bundles, centre_v, normal_v, radius_v)
    rms = float(np.sqrt(np.mean(np.square(errs))))
    seed_errs = pixel_residuals(seed)
    seed_rms = float(np.sqrt(np.mean(np.square(seed_errs))))
    if seed_rms < rms:
        centre_v = seed[:3]
        normal_v = seed_normal
        radius_v = seed_radius
        rms = seed_rms
    scale = float(diameter) / (2 * radius_v)
    out_of_plane_m = _circle_plane_residual(bundles, centre_v, normal_v, radius_v) * scale
    return centre_v, normal_v, radius_v, rms, float(out_of_plane_m)


def _view_rays(camera, ellipse):
    pixels = _ellipse_samples(ellipse)
    fx, fy, cx, cy = (float(value) for value in camera.intrinsics)
    camera_dirs = np.stack(
        [(pixels[:, 0] - cx) / fx, -(pixels[:, 1] - cy) / fy, np.full(len(pixels), -1.0)],
        axis=1,
    )
    directions = (camera.pose[:3, :3] @ camera_dirs.T).T
    directions /= np.maximum(np.linalg.norm(directions, axis=1, keepdims=True), 1e-12)
    origins = np.repeat(np.asarray(camera.pose[:3, 3], float)[None, :], len(pixels), axis=0)
    return camera, origins, directions


def _ellipse_samples(ellipse, count: int = ELLIPSE_SAMPLES) -> np.ndarray:
    theta = np.deg2rad(ellipse.theta_deg)
    angles = np.linspace(0, 2 * np.pi, count, endpoint=False)
    local_x = ellipse.a * np.cos(angles)
    local_y = ellipse.b * np.sin(angles)
    ca, sa = np.cos(theta), np.sin(theta)
    u = ellipse.cx + ca * local_x - sa * local_y
    v = ellipse.cy + sa * local_x + ca * local_y
    return np.stack([u, v], axis=1)


def _circle_pixel_residuals(bundles, centre, normal, radius) -> np.ndarray:
    norm = float(np.linalg.norm(normal))
    if norm < 1e-9 or not np.isfinite(radius):
        return np.full(sum(len(origins) for _, origins, _ in bundles), 50.0)
    normal = np.asarray(normal, float) / norm
    radius = abs(float(radius))
    chunks = []
    for camera, origins, directions in bundles:
        denom = directions @ normal
        times = ((centre - origins) @ normal) / np.where(np.abs(denom) < 1e-8, np.nan, denom)
        hits = origins + times[:, None] * directions
        radial = np.linalg.norm(hits - centre, axis=1) - radius
        local = (camera.pose[:3, :3].T @ (hits - camera.pose[:3, 3]).T).T
        depth = -local[:, 2]
        err = radial * float(camera.intrinsics[0]) / depth
        bad = ~np.isfinite(err) | (times <= 0) | (depth <= 0.05)
        chunks.append(np.where(bad, 50.0, err))
    return np.concatenate(chunks)


def _circle_plane_residual(bundles, centre, normal, radius) -> float:
    """RMS distance from each ray's nearest circle point to the plane, in source units."""
    norm = float(np.linalg.norm(normal))
    if norm < 1e-9:
        return 1.0
    normal = np.asarray(normal, float) / norm
    distances = []
    for _camera, origins, directions in bundles:
        denom = directions @ normal
        times = ((centre - origins) @ normal) / np.where(np.abs(denom) < 1e-8, np.nan, denom)
        hits = origins + times[:, None] * directions
        delta = hits - centre
        in_plane = delta - np.outer(delta @ normal, normal)
        lengths = np.linalg.norm(in_plane, axis=1)
        circle = centre + radius * in_plane / np.maximum(lengths[:, None], 1e-12)
        nearest_t = np.sum((circle - origins) * directions, axis=1)
        nearest = origins + nearest_t[:, None] * directions
        distances.append(np.abs((nearest - centre) @ normal))
    stacked = np.concatenate(distances)
    stacked = stacked[np.isfinite(stacked)]
    if len(stacked) == 0:
        return 1.0
    return float(np.sqrt(np.mean(np.square(stacked))))


def _wall_band(points: np.ndarray, camera, cover) -> np.ndarray:
    """Keep the front-most track in each 8 px cell of the wall band around the cover."""
    cloud = np.asarray(points, float)
    if len(cloud) == 0:
        return np.zeros((0, 3))
    local = (camera.pose[:3, :3].T @ (cloud - camera.pose[:3, 3]).T).T
    depth = -local[:, 2]
    fx, fy, cx, cy = (float(value) for value in camera.intrinsics)
    with np.errstate(divide="ignore", invalid="ignore"):
        u = fx * local[:, 0] / depth + cx
        v = cy - fy * local[:, 1] / depth
    finite = np.isfinite(u) & np.isfinite(v) & (depth > 0.05)
    theta = np.deg2rad(cover.theta_deg)
    ca, sa = np.cos(theta), np.sin(theta)
    du, dv = u - cover.cx, v - cover.cy
    ellipse_x = ca * du + sa * dv
    ellipse_y = -sa * du + ca * dv
    ellipse_r2 = (ellipse_x / max(float(cover.a), 1e-6)) ** 2 + (ellipse_y / max(float(cover.b), 1e-6)) ** 2
    band = finite & (ellipse_r2 >= 1.0) & (du**2 + dv**2 <= (WALL_BAND_RADII * float(cover.a)) ** 2)
    if int(band.sum()) < 8:
        return np.zeros((0, 3))
    chosen: dict[tuple[int, int], tuple[float, np.ndarray]] = {}
    for point, px, py, dist in zip(cloud[band], u[band], v[band], depth[band], strict=True):
        key = (int(np.floor(px)) // WALL_CELL_PX, int(np.floor(py)) // WALL_CELL_PX)
        current = chosen.get(key)
        if current is None or dist < current[0]:
            chosen[key] = (float(dist), point)
    if len(chosen) < 8:
        return np.zeros((0, 3))
    return np.stack([item[1] for item in chosen.values()])


def _track_points(tracks, image: str) -> np.ndarray:
    if tracks is None:
        return np.zeros((0, 3))
    if isinstance(tracks, dict):
        cloud = tracks.get(image)
        if cloud is None:
            return np.zeros((0, 3))
        return np.asarray(cloud, float)
    return np.asarray(tracks, float)


def _fit_plane(points: np.ndarray):
    cloud = np.asarray(points, float)
    if len(cloud) < 8:
        return None
    center = cloud.mean(0)
    _, _, vt = np.linalg.svd(cloud - center, full_matrices=False)
    normal = vt[-1]
    if normal[2] < 0:
        normal = -normal
    median = float(np.median((cloud - center) @ normal))
    keep = np.abs((cloud - center) @ normal - median) < 0.004 * max(np.linalg.norm(center), 1.0)
    if int(keep.sum()) < 8:
        return None
    kept = cloud[keep]
    center = kept.mean(0)
    _, _, vt = np.linalg.svd(kept - center, full_matrices=False)
    normal = vt[-1]
    return center, normal / max(np.linalg.norm(normal), 1e-9)


def _ray_plane(origin, direction, plane):
    center, normal = plane
    denom = float(np.dot(direction, normal))
    if abs(denom) < 1e-8:
        return None
    time = float(np.dot(center - origin, normal) / denom)
    if time <= 0:
        return None
    return origin + time * direction
