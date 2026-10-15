"""Ground truth for the 3D-extent test: each door's edges read once from the ETH3D laser scan.

For each door, a rough box drawn by eye on one photo (ROUGH, pixels of the 1024 px copy) picks
out the door. Its wall plane is a RANSAC fit to the laser depth inside that box grown by 30%.
`render` then draws an orthographic elevation of the laser scan in that plane: 1 cm per pixel,
colored by distance in front of (red) or behind (blue) the plane, black where the scanner saw
nothing (glass, or occluded), with a 10 cm grid. Each edge is read off the elevation by eye
(EYE), then snapped to the sharpest change, within 12 cm, in the fraction of scan pixels lying on
the plane (within 3 cm): a door leaf between proud pillars, a niche behind the wall face, or a
glass door's frame against empty glass all change that fraction at the edge. An edge whose snap
runs to the 12 cm limit found no step, so it keeps the eye reading and is marked eye-only in the
results. The edges come from
the scan alone, never from a detector. What remains is the choice of which physical edge counts:
the outer edge of the frame or leaf, as a detector's box would draw it. Door A is left out
because it sits flush in its wall, so the scan shows no edge. The right gray double door is in
no photo whole.

The plane frames and edges are saved to DATA/extent_gt.json, outside git: ETH3D's license allows
accuracy testing, not redistribution.

Usage: python -m autodetect.extent_gt render    # elevations to DATA/preview/elev_<door>.png
       python -m autodetect.extent_gt save      # DATA/extent_gt.json from EYE, snapped
"""

from __future__ import annotations

import json
import sys

import numpy as np
from PIL import Image, ImageDraw

from .electro import WallFrame, fit_vertical_plane, photos, scan
from .paths import DATA

# door id -> (reference photo, rough box [x0, y0, x1, y1] in pixels of the 1024 x 682 copy)
ROUGH = {
    "B_open_niche": ("p00001", [300, 262, 434, 528]),
    "C_gray_double": ("p00001", [497, 232, 724, 552]),
    "E_glass_64": ("p00005", [357, 262, 512, 476]),
    "F_glass_double": ("p00005", [657, 256, 818, 463]),
}
COPY_W, COPY_H = 1024, 682
RES = 0.01  # m per elevation pixel
MARGIN = 0.5  # m shown around the rough extent

# door id -> edges read by eye from the elevations, meters in that door's wall frame:
# [along_left, along_right, height_bottom, height_top]
EYE: dict[str, list[float]] = {
    "B_open_niche": [-1.58, 0.46, 0.0, 2.98],  # niche opening in the wall face
    "C_gray_double": [-1.13, 0.94, 0.0, 3.0],  # door leaves between the pillars, under the canopy
    "E_glass_64": [-1.35, 1.2, 0.1, 3.2],  # outer edge of the door frame; head below the transom bar
    "F_glass_double": [-1.33, 0.85, 0.1, 3.2],  # left leaf's frame is not in the scan: threshold end
}
SNAP = 0.12  # m
ON_PLANE = 0.03  # m


def frame_for(door: str) -> tuple[WallFrame, np.ndarray]:
    """The door's wall frame and its rough extent [along0, along1, h0, h1] in that frame."""
    pid, (x0, y0, x1, y1) = ROUGH[door]
    ph = photos()[pid]
    bx = np.array([x0, y0, x1, y1]) / [COPY_W, COPY_H, COPY_W, COPY_H]
    gw, gh = 0.3 * (bx[2] - bx[0]), 0.3 * (bx[3] - bx[1])
    pts = ph.depth_points(bx[0] - gw, bx[1] - gh, bx[2] + gw, bx[3] + gh)
    frame, _ = fit_vertical_plane(pts, toward=ph.pose[:3, 3])
    corners_u = np.array([bx[0], bx[2], bx[0], bx[2]]) * ph.W
    corners_v = np.array([bx[1], bx[1], bx[3], bx[3]]) * ph.H
    o, d = ph.ray(corners_u, corners_v)
    c = frame.coords(frame.intersect(o, d))
    return frame, np.array([c[:, 0].min(), c[:, 0].max(), c[:, 1].min(), c[:, 1].max()])


def raster(door: str) -> tuple[np.ndarray, tuple[float, float, float, float], WallFrame]:
    """Elevation raster (nearest-to-camera offset per 1 cm pixel, NaN = no point) and its extent."""
    frame, (a0, a1, h0, h1) = frame_for(door)
    a0, a1, h0, h1 = a0 - MARGIN, a1 + MARGIN, max(h0 - MARGIN, -0.3), h1 + MARGIN
    c = frame.coords(scan())
    sel = (c[:, 0] >= a0) & (c[:, 0] < a1) & (c[:, 1] >= h0) & (c[:, 1] < h1) & (np.abs(c[:, 2]) < 0.6)
    c = c[sel]
    W, H = int((a1 - a0) / RES) + 1, int((h1 - h0) / RES) + 1
    img = np.full((H, W), np.nan)
    col = ((c[:, 0] - a0) / RES).astype(int)
    row = ((h1 - c[:, 1]) / RES).astype(int)
    order = np.argsort(c[:, 2])  # nearest to the camera side wins
    img[row[order], col[order]] = c[order, 2]
    return img, (a0, a1, h0, h1), frame


def snap(door: str) -> tuple[list[float], list[bool]]:
    """Each EYE edge moved to the largest step in on-plane fraction within SNAP of it, and
    whether it snapped (False: the step sat at the search limit, so the eye reading stands)."""
    img, (a0, a1, h0, h1), _ = raster(door)
    on = np.abs(np.nan_to_num(img, nan=1.0)) < ON_PLANE
    left, right, bottom, top = EYE[door]
    # vertical edges: fraction per column over the middle 60% of the door's height
    rows = slice(int((h1 - (bottom + 0.8 * (top - bottom))) / RES), int((h1 - (bottom + 0.2 * (top - bottom))) / RES))
    cols = slice(int((left + 0.2 * (right - left) - a0) / RES), int((left + 0.8 * (right - left) - a0) / RES))
    col_prof = on[rows].mean(0)
    row_prof = on[:, cols].mean(1)
    def step(profile: np.ndarray, pos_px: float) -> float:
        k = np.convolve(profile, np.ones(3) / 3, mode="same")
        diff = np.abs(np.diff(k))
        lo, hi = int(max(pos_px - SNAP / RES, 0)), int(min(pos_px + SNAP / RES, len(diff)))
        return lo + int(np.argmax(diff[lo:hi])) + 1.0  # boundary between pixel i and i+1
    found = [
        a0 + step(col_prof, (left - a0) / RES) * RES,
        a0 + step(col_prof, (right - a0) / RES) * RES,
        h1 - step(row_prof, (h1 - bottom) / RES) * RES,
        h1 - step(row_prof, (h1 - top) / RES) * RES,
    ]
    snapped = [bool(abs(f - e) < SNAP - 0.015) for f, e in zip(found, EYE[door])]
    return [round(float(f if ok else e), 3) for f, e, ok in zip(found, EYE[door], snapped)], snapped


def render(door: str) -> str:
    img, (a0, a1, h0, h1), _ = raster(door)
    H, W = img.shape
    rgb = np.zeros((H, W, 3), np.uint8)
    v = ~np.isnan(img)
    z = np.clip(img[v] / 0.3, -1, 1)
    rgb[v, 0] = (140 + 115 * np.clip(z, 0, 1) - 100 * np.clip(-z, 0, 1)).astype(np.uint8)
    rgb[v, 1] = (140 - 100 * np.abs(z)).astype(np.uint8)
    rgb[v, 2] = (140 + 115 * np.clip(-z, 0, 1) - 100 * np.clip(z, 0, 1)).astype(np.uint8)
    im = Image.fromarray(rgb).resize((2 * W, 2 * H), Image.Resampling.NEAREST)
    d = ImageDraw.Draw(im)
    for g in np.arange(np.ceil(a0 * 10) / 10, a1, 0.1):
        x = 2 * (g - a0) / RES
        major = abs(g * 2 - round(g * 2)) < 1e-6
        d.line([(x, 0), (x, 2 * H)], fill=(255, 255, 255) if major else (90, 90, 90))
        if major:
            d.text((x + 2, 2), f"{g:.1f}", fill=(255, 255, 0))
    for g in np.arange(np.ceil(h0 * 10) / 10, h1, 0.1):
        y = 2 * (h1 - g) / RES
        major = abs(g * 2 - round(g * 2)) < 1e-6
        d.line([(0, y), (2 * W, y)], fill=(255, 255, 255) if major else (90, 90, 90))
        if major:
            d.text((2, y - 11), f"{g:.1f}", fill=(255, 255, 0))
    out = DATA / "preview" / f"elev_{door}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return str(out)


def save() -> None:
    out = {}
    for door, eye in EYE.items():
        frame, _ = frame_for(door)
        edges, snapped = snap(door)
        moved = np.abs(np.array(edges) - eye)
        print(f"{door}: eye {eye} -> {edges}, snapped {snapped} (moved up to {moved.max() * 100:.0f} cm)")
        out[door] = {
            "photo": ROUGH[door][0],
            "origin": frame.origin.tolist(),
            "normal": frame.normal.tolist(),
            "edges_m": dict(zip(("left", "right", "bottom", "top"), edges)),
            "eye_m": dict(zip(("left", "right", "bottom", "top"), eye)),
            "snapped": dict(zip(("left", "right", "bottom", "top"), snapped)),
        }
    (DATA / "extent_gt.json").write_text(json.dumps(out, indent=1))
    print(f"{len(out)} doors")


if __name__ == "__main__":
    if sys.argv[1] == "render":
        for door in sys.argv[2:] or ROUGH:
            print(render(door))
    elif sys.argv[1] == "save":
        save()
