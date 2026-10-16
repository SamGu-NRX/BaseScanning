"""Perfect taps and clicks: ground-truth elements projected through the true cameras.

Used when scoring a bare point cloud: the meter tap and the opening edge clicks come from the
GT scene, so every error the scorer reports belongs to the reconstruction, not to a detector.
Pixels are returned at the reconstruction's image size (`w_out`).
"""

from __future__ import annotations

import numpy as np

from server.eval.chain import OPENING_CLASSES, Chain, ground_y
from server.eval.frame import project_arkit
from server.eval.visibility import Camera


def _in_view(c: Camera, X: np.ndarray, margin: float = 0.03) -> bool:
    u, v, z = project_arkit(c.pose, c.K, np.atleast_2d(X))
    return bool(
        np.all(
            (z > 0.5) & (u > margin * c.w) & (u < (1 - margin) * c.w) & (v > margin * c.h) & (v < (1 - margin) * c.h)
        )
    )


def _facing(c: Camera, X: np.ndarray, normal3: np.ndarray, max_deg: float) -> float | None:
    to_cam = c.pose[:3, 3] - X
    d = float(np.linalg.norm(to_cam))
    return d if to_cam @ normal3 / d >= np.cos(np.radians(max_deg)) else None


def place_meter_tap(u: float, v: float, gt_width: int, recon_width: int) -> tuple[float, float]:
    """Keep a meter tap in the image that produced it.

    The old extractor multiplied the pixel by ``recon_width / gt_width``. That matches a uniform
    rescale of an unchanged field of view. A centred phone crop keeps the focal length and moves
    the principal point, so the product lands on the wrong pixel. Differing widths are an error.
    """
    if int(gt_width) != int(recon_width):
        raise ValueError(
            f"phone frame width {int(gt_width)} does not match the rendered camera width {int(recon_width)}; "
            "refusing to rescale the meter tap"
        )
    return float(u), float(v)


def _require_native_width(c: Camera, w_out: dict[int, int], i: int) -> None:
    """Pixels are in this camera's own image.

    A width ratio only matches a uniform rescale of an unchanged field of view. A centred crop
    or a phone field of view moves the principal point, so rescaling GT pixels by ``w_out / w_gt``
    lands the click on the wrong pixel (about 20 in on every A7 phone-FOV path).
    """
    if i in w_out and int(w_out[i]) != int(c.w):
        raise ValueError(
            f"camera {c.id!r} is {c.w}px wide but the reconstruction image is {w_out[i]}px; "
            "pass the reconstruction intrinsics and size (a width-ratio rescale is wrong for a crop)"
        )


def meter_taps(gt: dict, gt_cams: list[Camera], w_out: dict[int, int], n: int = 3) -> list[tuple[int, float, float]]:
    """(camera index, u, v) for the n nearest views that see the meter within 45 deg of its wall normal."""
    meter = next(o for o in gt["objects"] if o["id"] == gt["frame"]["anchor"]["element_id"])
    M = np.asarray(meter["geometry"]["pos_m"], float)
    cands = []
    for i, c in enumerate(gt_cams):
        if i not in w_out or not _in_view(c, M, 0.05):
            continue
        d = _facing(c, M, np.array([0.0, 0.0, 1.0]), 45)
        if d is not None:
            cands.append((d, i))
    out = []
    for _, i in sorted(cands)[:n]:
        _require_native_width(gt_cams[i], w_out, i)
        u, v, _ = project_arkit(gt_cams[i].pose, gt_cams[i].K, M[None])
        out.append((i, float(u[0]), float(v[0])))
    return out


def opening_clicks(gt: dict, gt_cams: list[Camera], w_out: dict[int, int], n: int = 3) -> list[dict]:
    """Extractor features: left/right/bottom/top edge midpoints of every GT opening, in up to n
    nearest views that see all four within 60 deg of the wall normal."""
    ch = Chain(gt)
    feats = []
    for o in gt["objects"]:
        g = o["geometry"]
        if o["class"] not in OPENING_CLASSES or g["kind"] != "wall_rect":
            continue
        w = ch.by_id[g["wall_id"]]
        s0, s1, b, t = g["s_m"][0], g["s_m"][1], g["bottom_m"], g["top_m"]
        sm, hm = (s0 + s1) / 2, (b + t) / 2
        pts = {}
        for role, (s, h) in {"left": (s0, hm), "right": (s1, hm), "bottom": (sm, b + 0.02), "top": (sm, t)}.items():
            q = w.plan_at(s)
            pts[role] = np.array([q[0], ground_y(gt, q[None])[0] + h, q[1]])
        P = np.array(list(pts.values()))
        n3 = np.array([w.outward[0], 0.0, w.outward[1]])
        cands = []
        for i, c in enumerate(gt_cams):
            if i not in w_out or not _in_view(c, P):
                continue
            d = _facing(c, P.mean(0), n3, 60)
            if d is not None:
                cands.append((d, i))
        views = {}
        for _, i in sorted(cands)[:n]:
            _require_native_width(gt_cams[i], w_out, i)
            u, v, _ = project_arkit(gt_cams[i].pose, gt_cams[i].K, P)
            views[i] = {role: (float(u[k]), float(v[k])) for k, role in enumerate(pts)}
        if views:
            feats.append(
                {
                    "id": o["id"],
                    "class": o["class"],
                    "label": o.get("label", o["class"]),
                    "points_px": views,
                    **({"attrs": o["attrs"]} if "attrs" in o else {}),
                }
            )
    return feats
