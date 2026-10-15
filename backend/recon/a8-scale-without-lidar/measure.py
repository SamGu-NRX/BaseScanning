"""Measure every annotated reference segment in the scale-free reconstruction and on the laser.

    python measure.py SCENE [--qa]   ->  results/<scene>/segments.json (+ /tmp/a8qa_<scene>_<obj>_<view>.jpg)

annotations/<scene>.json:
  [{"id": "door2_h", "type": "door_height", "n": 1, "mode": "band"|"local",
    "views": {"DSC_9272": {"a": [[x, y], [x, y]], "b": [[x, y], [x, y]]}, ...}}]
"n" is the number of repeats spanned (brick courses, siding boards); the unit size is length / n.
For each view: endpoints snapped to image edges, then
  L_rec   = rays of both endpoints on a RANSAC plane of the SfM points (recon units)
  L_laser = same pixels, GT camera, plane of the laser points (metres)
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from common import HERE, dump, to_gt, line_separation, load_scene, measure_segment, segment_band
from refine import endpoints, overlay


def band_between(A, B, lateral=0.25, min_px=30.0, ext=-0.08):
    return segment_band(A, B, lateral=lateral, ext=ext, min_px=min_px)


def local_box(P, half=40.0):
    P = np.asarray(P, float)
    return np.array([P + [-half, -half], P + [half, -half], P + [half, half], P + [-half, half]])


_FRUSTUM: dict = {}


def in_view(cam, P, key):
    """P cropped to the camera's image (cached per camera), so each plane fit projects few points."""
    k = (key, cam.name)
    if k not in _FRUSTUM:
        uv, z = cam.project(P)
        m = (z > 0.3) & (uv[:, 0] > -50) & (uv[:, 0] < cam.w + 50) & (uv[:, 1] > -50) & (uv[:, 1] < cam.h + 50)
        _FRUSTUM.clear() if len(_FRUSTUM) > 6 else None
        _FRUSTUM[k] = P[m]
    return _FRUSTUM[k]


def measure(cam, pts, A, B, mode: str, la=None, lb=None):
    if mode in ("band", "wide"):
        band = band_between(A, B) if mode == "band" else band_between(A, B, lateral=0.6, ext=0.4)
        r = measure_segment(cam, pts, A, B, band=band)
        if r is not None and la is not None:
            r["length_pts"] = r["length"]
            r["length"] = line_separation(cam, r["plane"], la, lb, A, B)
        return r
    # local: each endpoint on its own small plane
    ra = measure_segment(cam, pts, A, A + [1, 0], band=local_box(A))
    rb = measure_segment(cam, pts, B, B + [1, 0], band=local_box(B))
    if ra is None or rb is None:
        return None
    return {"length": float(np.linalg.norm(rb["A"] - ra["A"])), "A": ra["A"], "B": rb["A"],
            "n_pts": min(ra["n_pts"], rb["n_pts"]), "inliers": min(ra["inliers"], rb["inliers"]),
            "depth": float((ra["depth"] + rb["depth"]) / 2)}


def transfer(sc, ref, e, view):
    """Rough edges of `ref` lifted onto the recon plane of the segment and projected into `view`."""
    cr, cv = sc.rec_cams.get(ref), sc.rec_cams.get(view)
    if cr is None or cv is None:
        return None
    a, b = np.asarray(e["a"], float), np.asarray(e["b"], float)
    r = measure_segment(cr, sc.rec_pts, a.mean(0), b.mean(0), band=band_between(a.mean(0), b.mean(0), lateral=0.4))
    if r is None:
        return None
    from common import ray_plane
    n, d = r["plane"]
    X = np.array([ray_plane(cr.C, cr.ray(p), n, d) for p in np.vstack([a, b])])
    uv, z = cv.project(X)
    if (z <= 0).any() or (uv < 0).any() or (uv[:, 0] > cv.w).any() or (uv[:, 1] > cv.h).any():
        return None
    return {"a": uv[:2].tolist(), "b": uv[2:].tolist(), "R": e.get("R", 12.0) * 1.5, "transferred_from": ref}


def count_repeats(scene, view, A, B, min_period_px=4.0):
    """Number of equal-period intervals (siding boards, brick courses) between A and B. The period is the
    first autocorrelation peak of the edge profile above min_period_px, refined on the highest-order
    multiple that fits in the profile (sub-pixel), so a 7 px course is resolved to ~0.1 %."""
    from refine import gray, sample
    img = gray(scene, view)
    L = np.linalg.norm(B - A)
    ups = 8
    t = np.linspace(0, 1, int(L * ups))
    prof = sample(img, A[None] + t[:, None] * (B - A)[None])
    g = np.abs(np.gradient(prof))
    g = g - g.mean()
    ac = np.correlate(g, g, "full")[len(g) - 1:]
    ac = ac / ac[0]
    lo = int(min_period_px * ups)
    # first local maximum above lo that is at least 60 % of the strongest one in range
    seg = ac[lo:len(g) // 2]
    strong = seg.max()
    k0 = None
    for i in range(1, len(seg) - 1):
        if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0.6 * strong:
            k0 = lo + i
            break
    k0 = k0 or lo + int(np.argmax(seg))
    m = max(1, int((len(g) * 0.6) // k0))
    w = max(2, k0 // 3)
    c = m * k0
    j = c - w + int(np.argmax(ac[c - w:c + w + 1]))
    if 0 < j < len(ac) - 1:
        a_, b_, c_ = ac[j - 1], ac[j], ac[j + 1]
        den = a_ - 2 * b_ + c_
        j = j + (0.5 * (a_ - c_) / den if den != 0 else 0.0)
    period_px = j / m / ups
    return float(L / period_px), float(period_px)


def view_tracks(scene):
    """Per-image SfM points actually observed in that image (occlusion-safe); {} if the model has no tracks."""
    import pycolmap
    from common import WORK
    path = WORK / "barn/undistorted/sparse" if scene == "barn" else WORK / scene / "inc"
    rec = pycolmap.Reconstruction(str(path))
    out = {}
    for img in rec.images.values():
        ids = [p.point3D_id for p in img.points2D if p.has_point3D()]
        if ids:
            out[img.name.rsplit(".", 1)[0]] = np.array([rec.points3D[i].xyz for i in ids])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("--qa", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    ann = json.loads((HERE / "annotations" / f"{a.scene}.json").read_text())
    if a.only:
        ann = [o for o in ann if o["id"] in a.only.split(",")]
    sc = load_scene(a.scene)
    tracks = view_tracks(a.scene)
    out = []
    for o in ann:
        mode = o.get("mode", "band")
        views = dict(o["views"])
        extra = views.pop("auto", [])
        if extra:
            ref, er = next(iter(views.items()))
            for v in extra:
                t = transfer(sc, ref, er, v)
                if t is not None:
                    views[v] = t
        for view, e in views.items():
            row = {"id": o["id"], "type": o["type"], "n": o.get("n", 1), "view": view, "mode": mode}
            if view not in sc.rec_cams or view not in sc.gt_cams:
                row["error"] = "view not registered"
                out.append(row)
                continue
            A, B, la, lb = endpoints(a.scene, view, e["a"], e["b"], R=e.get("R", 12.0))
            Ar, Br, lar, lbr = endpoints(a.scene, view, e["a"], e["b"], raw=True)
            row.update({"A_uv": A, "B_uv": B, "px": float(np.linalg.norm(B - A)),
                        "snap_inliers": [la["inlier_frac"], lb["inlier_frac"]],
                        "snap_shift_px": float(abs(np.linalg.norm(B - A) - np.linalg.norm(Br - Ar)))})
            Pr = tracks.get(view, sc.rec_pts) if tracks else sc.rec_pts
            Lv = in_view(sc.gt_cams[view], sc.laser, "laser") if sc.laser is not None else to_gt(sc, Pr)
            rr = measure(sc.rec_cams[view], Pr, A, B, mode, la, lb)
            rg = measure(sc.gt_cams[view], Lv, A, B, mode, la, lb)
            rraw = measure(sc.rec_cams[view], Pr, Ar, Br, mode, lar, lbr)
            grawl = measure(sc.gt_cams[view], Lv, Ar, Br, mode, lar, lbr)
            if rr is None or rg is None:
                row["error"] = f"plane fit failed (rec {rr is not None}, laser {rg is not None})"
            else:
                row.update({"L_rec": rr["length"], "L_rec_m": rr["length"] * sc.s_true, "L_laser": rg["length"],
                            "rec_pts": rr["n_pts"], "depth_m": rg["depth"],
                            "rec_err_pct": 100 * (rr["length"] * sc.s_true / rg["length"] - 1)})
                if rraw is not None and grawl is not None:
                    row.update({"L_rec_raw": rraw["length"], "L_laser_raw": grawl["length"]})
            if o.get("n") == "auto" and "error" not in row:
                nf, per = count_repeats(a.scene, view, A, B)
                row["n_float"], row["period_px"] = nf, per
                row["n"] = int(round(nf))
            out.append(row)
            print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items() if k not in ("A_uv", "B_uv")}, flush=True)
            if a.qa:
                overlay(a.scene, view, [(A, B, la, lb, o["id"])], f"/tmp/a8qa_{a.scene}_{o['id']}_{view}.jpg")
    dump(HERE / "results" / a.scene / "segments.json", {"scene": a.scene, "s_true": sc.s_true, "sim3_rms_m": sc.sim3_rms_m, "rows": out})


if __name__ == "__main__":
    main()
