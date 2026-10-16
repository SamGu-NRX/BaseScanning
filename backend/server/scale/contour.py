"""Cover, ring and base ellipses on a meter close-up."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from server.scale.meter_table import TableMatch


@dataclass
class Ellipse:
    cx: float
    cy: float
    a: float
    b: float
    theta_deg: float
    inlier_frac: float
    strength: float


@dataclass
class ContourSet:
    cover: Ellipse | None
    ring: Ellipse | None
    base: Ellipse | None
    lone: bool
    code: str | None


def fit_meter_contours(
    gray: np.ndarray,
    box_px,
    *,
    n_rays: int = 180,
    search_px: float = 25.0,
    ransac_px: float = 1.5,
    min_inlier: float = 0.6,
    seed: int = 0,
) -> list[Ellipse]:
    image = np.asarray(gray)
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(image, (0, 0), 1.0)
    height, width = blurred.shape[:2]
    box = _roi(box_px, width, height)
    x0, y0, x1, y1 = box
    roi = blurred[y0:y1, x0:x1]
    if roi.size == 0:
        return []
    found = _canny_ellipses(roi, (x0, y0), n_rays, search_px, ransac_px, min_inlier, seed)
    if len(_dedupe(found)) >= 3:
        return _dedupe(found)
    radial = _radial_ellipses(roi, (x0, y0), n_rays, ransac_px, min_inlier)
    return _dedupe(found + radial)


def label_contours(
    ells: list[Ellipse],
    match: TableMatch,
    arkit_hint: float | None,
    *,
    ratio_tol: float = 0.02,
    arkit_rel_sd: float = 0.03,
) -> ContourSet:
    if not ells:
        return ContourSet(None, None, None, False, "scale_reference_edge_not_found")
    diameters = _diameters(match)
    if len(ells) == 1:
        return _lone(ells[0], diameters, arkit_hint, arkit_rel_sd)
    ordered = sorted(ells, key=lambda item: item.a)
    cover = ordered[0]
    ring = base = None
    used: set[str] = set()
    targets = _ratio_targets(diameters)
    for ell in ordered[1:]:
        ratio = ell.a / cover.a if cover.a else 0.0
        best_name = ""
        best_err = ratio_tol
        for name, target in targets.items():
            if name in used:
                continue
            err = abs(ratio - target)
            if err < best_err or (best_name == "" and err <= ratio_tol):
                if err <= ratio_tol and err <= best_err:
                    best_name = name
                    best_err = err
        if best_name == "ring":
            ring = ell
            used.add("ring")
        elif best_name == "base":
            base = ell
            used.add("base")
    if ring is None and base is None:
        return _lone(cover, diameters, arkit_hint, arkit_rel_sd)
    return ContourSet(cover, ring, base, False, None)


def write_overlay(path, gray: np.ndarray, contours: ContourSet, box_px) -> None:
    image = np.asarray(gray)
    if image.ndim == 2:
        canvas = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    else:
        canvas = image.copy()
    for ell, color in (
        (contours.cover, (40, 220, 40)),
        (contours.base, (40, 180, 220)),
        (contours.ring, (40, 40, 220)),
    ):
        if ell is None:
            continue
        cv2.ellipse(
            canvas,
            (int(round(ell.cx)), int(round(ell.cy))),
            (max(1, int(round(ell.a))), max(1, int(round(ell.b)))),
            ell.theta_deg,
            0,
            360,
            color,
            2,
        )
    _blur_plate(canvas, contours.cover)
    cv2.imwrite(str(path), canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 90])


def _roi(box_px, width: int, height: int) -> tuple[int, int, int, int]:
    if box_px is None:
        return int(0.2 * width), int(0.2 * height), int(0.8 * width), int(0.8 * height)
    x0, y0, x1, y1 = (float(v) for v in box_px)
    return (
        int(np.clip(np.floor(x0), 0, width - 1)),
        int(np.clip(np.floor(y0), 0, height - 1)),
        int(np.clip(np.ceil(x1), 1, width)),
        int(np.clip(np.ceil(y1), 1, height)),
    )


def _canny_ellipses(roi, origin, n_rays, search_px, ransac_px, min_inlier, seed) -> list[Ellipse]:
    edges = cv2.Canny(roi, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    roi_w = roi.shape[1]
    kept: list[Ellipse] = []
    for contour in contours:
        if len(contour) < 40:
            continue
        (cx, cy), axes, angle = cv2.fitEllipse(contour)
        major = max(float(axes[0]), float(axes[1]))
        minor = min(float(axes[0]), float(axes[1]))
        if major < 0.25 * roi_w or major > 0.90 * roi_w or minor < 0.5 * major:
            continue
        raw = _from_fit(((cx, cy), axes, angle), 1.0, float(len(contour)))
        points = [(float(point[0][0]), float(point[0][1])) for point in contour]
        frac = _axis_inliers(
            points, float(cx), float(cy), float(axes[0]) / 2, float(axes[1]) / 2, float(angle), ransac_px
        )
        if frac >= min_inlier:
            raw.inlier_frac = frac
            kept.append(_shift(raw, origin))
            continue
        refined = _refine(
            roi,
            cx,
            cy,
            float(axes[0]) / 2,
            float(axes[1]) / 2,
            float(angle),
            n_rays,
            search_px,
            ransac_px,
            min_inlier,
            seed,
        )
        if refined is None:
            continue
        kept.append(_shift(refined, origin))
    return kept


def _radial_ellipses(roi, origin, n_rays, ransac_px, min_inlier) -> list[Ellipse]:
    gx = cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    height, width = mag.shape
    cx, cy = width / 2, height / 2
    radii: list[float] = []
    samples: list[tuple[float, float, float]] = []
    r_max = 0.48 * min(width, height)
    for index in range(n_rays):
        theta = 2 * np.pi * index / n_rays
        direction = np.array([np.cos(theta), np.sin(theta)])
        column = []
        rs = []
        radius = 8.0
        while radius < r_max:
            point = np.array([cx, cy]) + radius * direction
            value = _bilinear(mag, point[0], point[1])
            column.append(value)
            rs.append(radius)
            radius += 1.0
        peaks = _local_peaks(column, rs)
        for peak_r, strength in peaks:
            if strength < 8:
                continue
            radii.append(peak_r)
            samples.append((cx + peak_r * direction[0], cy + peak_r * direction[1], peak_r))
    if not radii:
        return []
    bands = _bands(np.array(radii), gap=6.0)
    ellipses: list[Ellipse] = []
    roi_w = float(width)
    for low, high in bands:
        points = [(x, y) for x, y, radius in samples if low <= radius <= high]
        if len(points) < 40:
            continue
        ell = _fit_points(points, ransac_px, min_inlier)
        if ell is None:
            continue
        major = 2 * ell.a
        if major < 0.25 * roi_w or major > 0.90 * roi_w or ell.b < 0.5 * ell.a:
            continue
        ellipses.append(_shift(ell, origin))
    return ellipses


def _refine(roi, cx, cy, axis_x, axis_y, angle_deg, n_rays, search_px, ransac_px, min_inlier, seed) -> Ellipse | None:
    del seed
    gx = cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    points = []
    for index in range(n_rays):
        theta = 2 * np.pi * index / n_rays
        direction = np.array([np.cos(theta), np.sin(theta)])
        radius = _ellipse_radius(axis_x, axis_y, angle_deg, theta)
        window = []
        start = max(4.0, radius - search_px)
        stop = radius + search_px
        radius_i = start
        while radius_i <= stop:
            point = np.array([cx, cy]) + radius_i * direction
            value = _bilinear(mag, point[0], point[1])
            window.append((radius_i, value))
            radius_i += 1.0
        best_r = _nearest_peak(window, radius)
        if best_r is None:
            continue
        best_r = _subpixel(window, best_r)
        points.append(tuple(np.array([cx, cy]) + best_r * direction))
    return _fit_points(points, ransac_px, min_inlier)


def _ellipse_radius(axis_x: float, axis_y: float, angle_deg: float, ray_rad: float) -> float:
    phi = ray_rad - np.deg2rad(angle_deg)
    cosine, sine = np.cos(phi), np.sin(phi)
    denom = np.hypot(axis_y * cosine, axis_x * sine)
    if denom < 1e-6:
        return max(axis_x, axis_y)
    return float(axis_x * axis_y / denom)


def _fit_points(points: list[tuple[float, float]], ransac_px: float, min_inlier: float) -> Ellipse | None:
    if len(points) < 5:
        return None
    cloud = np.asarray(points, np.float32).reshape(-1, 1, 2)
    ell = _from_fit(cv2.fitEllipse(cloud), 1.0, float(len(points)))
    residual = _residuals(points, ell)
    inliers = [point for point, error in zip(points, residual, strict=True) if error <= ransac_px]
    frac = len(inliers) / len(points)
    if len(inliers) >= 5 and frac >= min_inlier:
        refit = _from_fit(cv2.fitEllipse(np.asarray(inliers, np.float32).reshape(-1, 1, 2)), frac, float(len(inliers)))
        return refit
    if frac >= min_inlier:
        ell.inlier_frac = frac
        return ell
    return None


def _from_fit(fitted, frac: float, strength: float) -> Ellipse:
    (cx, cy), axes, angle = fitted
    major_axis = float(axes[0])
    minor_axis = float(axes[1])
    theta = float(angle)
    if minor_axis > major_axis:
        major_axis, minor_axis = minor_axis, major_axis
        theta += 90.0
    return Ellipse(float(cx), float(cy), major_axis / 2, minor_axis / 2, theta % 180.0, frac, strength)


def _axis_inliers(
    points, cx: float, cy: float, axis_x: float, axis_y: float, angle_deg: float, ransac_px: float
) -> float:
    theta = np.deg2rad(angle_deg)
    rotation = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
    scale = max(axis_x, axis_y, 1e-6)
    hits = 0
    for x, y in points:
        local = rotation @ np.array([x - cx, y - cy])
        rho = np.hypot(local[0] / max(axis_x, 1e-6), local[1] / max(axis_y, 1e-6))
        if abs(rho - 1.0) * scale <= ransac_px:
            hits += 1
    return hits / max(len(points), 1)


def _residuals(points, ell: Ellipse) -> list[float]:
    theta = np.deg2rad(ell.theta_deg)
    rotation = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
    out = []
    for x, y in points:
        local = rotation @ np.array([x - ell.cx, y - ell.cy])
        rho = np.hypot(local[0] / max(ell.a, 1e-6), local[1] / max(ell.b, 1e-6))
        out.append(abs(rho - 1.0) * ell.a)
    return out


def _nearest_peak(window: list[tuple[float, float]], radius: float) -> float | None:
    """Stay on this circle. A stronger neighbour inside the search window is a different edge."""

    best_r = None
    best_score = 1e9
    for index in range(1, len(window) - 1):
        left, center, right = window[index - 1][1], window[index][1], window[index + 1][1]
        if center < left or center < right or center < 8:
            continue
        distance = abs(window[index][0] - radius)
        if distance < best_score:
            best_score = distance
            best_r = window[index][0]
    if best_r is None or best_score > 8:
        return None
    return best_r


def _subpixel(window: list[tuple[float, float]], peak: float) -> float:
    for index in range(1, len(window) - 1):
        if abs(window[index][0] - peak) > 0.6:
            continue
        left, center, right = window[index - 1][1], window[index][1], window[index + 1][1]
        denom = left - 2 * center + right
        if abs(denom) < 1e-6:
            return peak
        shift = 0.5 * (left - right) / denom
        return float(window[index][0] + np.clip(shift, -1, 1))
    return peak


def _local_peaks(values: list[float], radii: list[float]) -> list[tuple[float, float]]:
    peaks = []
    for index in range(1, len(values) - 1):
        if values[index] >= values[index - 1] and values[index] > values[index + 1]:
            shift = _subpixel(
                [
                    (radii[index - 1], values[index - 1]),
                    (radii[index], values[index]),
                    (radii[index + 1], values[index + 1]),
                ],
                radii[index],
            )
            peaks.append((shift, values[index]))
    peaks.sort(key=lambda item: item[1], reverse=True)
    kept: list[tuple[float, float]] = []
    for radius, strength in peaks:
        if all(abs(radius - previous) > 5 for previous, _ in kept):
            kept.append((radius, strength))
        if len(kept) == 4:
            break
    return kept


def _bands(radii: np.ndarray, gap: float) -> list[tuple[float, float]]:
    ordered = np.sort(radii)
    if len(ordered) == 0:
        return []
    groups = [[float(ordered[0])]]
    for value in ordered[1:]:
        if value - groups[-1][-1] <= gap:
            groups[-1].append(float(value))
        else:
            groups.append([float(value)])
    bands = []
    for group in groups:
        if len(group) < 40:
            continue
        center = float(np.median(group))
        bands.append((center - gap, center + gap))
    return bands


def _bilinear(image: np.ndarray, x: float, y: float) -> float:
    height, width = image.shape
    if x < 0 or y < 0 or x >= width - 1 or y >= height - 1:
        return 0.0
    x0, y0 = int(x), int(y)
    dx, dy = x - x0, y - y0
    patch = image[y0 : y0 + 2, x0 : x0 + 2]
    return float(
        (1 - dx) * (1 - dy) * patch[0, 0]
        + dx * (1 - dy) * patch[0, 1]
        + (1 - dx) * dy * patch[1, 0]
        + dx * dy * patch[1, 1]
    )


def _shift(ell: Ellipse, origin: tuple[int, int]) -> Ellipse:
    return Ellipse(ell.cx + origin[0], ell.cy + origin[1], ell.a, ell.b, ell.theta_deg, ell.inlier_frac, ell.strength)


def _dedupe(ells: list[Ellipse]) -> list[Ellipse]:
    kept: list[Ellipse] = []
    for ell in sorted(ells, key=lambda item: item.strength, reverse=True):
        if any(
            abs(ell.a - previous.a) / max(previous.a, 1) < 0.012 and abs(ell.cx - previous.cx) < 15 for previous in kept
        ):
            continue
        kept.append(ell)
    return kept


def _diameters(match: TableMatch) -> dict[str, float]:
    out = {"cover": float(match.cover_hypotheses[0][0])}
    if match.ring_od_m:
        out["ring"] = float(match.ring_od_m)
    if match.base_od_m:
        out["base"] = float(match.base_od_m)
    return out


def _ratio_targets(diameters: dict[str, float]) -> dict[str, float]:
    cover = diameters["cover"]
    targets = {}
    if "ring" in diameters:
        targets["ring"] = diameters["ring"] / cover
    if "base" in diameters:
        targets["base"] = diameters["base"] / cover
    return targets


def _lone(ell: Ellipse, diameters: dict[str, float], hint: float | None, rel_sd: float) -> ContourSet:
    if hint is None:
        return ContourSet(None, None, None, True, "meter_contour_ambiguous")
    hits = [name for name, diameter in diameters.items() if abs(hint - diameter) / diameter <= 2 * rel_sd]
    if len(hits) != 1:
        return ContourSet(None, None, None, True, "meter_contour_ambiguous")
    name = hits[0]
    cover = ell if name == "cover" else None
    ring = ell if name == "ring" else None
    base = ell if name == "base" else None
    return ContourSet(cover, ring, base, True, None)


def _blur_plate(canvas: np.ndarray, cover: Ellipse | None) -> None:
    if cover is None:
        return
    height, width = canvas.shape[:2]
    x0 = int(np.clip(cover.cx - 0.22 * cover.a, 0, width - 1))
    x1 = int(np.clip(cover.cx + 0.22 * cover.a, 1, width))
    y0 = int(np.clip(cover.cy - 0.16 * cover.b, 0, height - 1))
    y1 = int(np.clip(cover.cy + 0.16 * cover.b, 1, height))
    if x1 <= x0 or y1 <= y0:
        return
    plate = canvas[y0:y1, x0:x1]
    canvas[y0:y1, x0:x1] = cv2.GaussianBlur(plate, (0, 0), 7)
