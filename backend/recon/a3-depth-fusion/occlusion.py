"""Occlusion test: does a depth test with predicted (or fused) depth fix the coverage over-claim?

    python occlusion.py [--models moge2-vits,...] [--scale feat]

PR #12's electro wall and per-sample truth (`evals.coverage.setup_scene`, laser-scan visibility)
with the app's CoverageMap logic replicated in Python instead of the Swift driver (no macOS here):
`app_rows`/`candidate_cells` are PR #12's replicas of `visibleRows`/`candidateIndices`, and
`record`/`coveredIntervals` are ported below from CoverageMap.swift at beede15, with that commit's
default CoverageConfig. The port must reproduce PR #12's published numbers (19.3 ft claimed, 1.1 ft
false-observed, 0 missed for the app; 14.6 / 0.0 / 3.3 for the true-depth test, 3 rows) before any
other row is reported.

Depth test (PR #12's option, `coverage_options.depth_hidden`): a keyframe's row is dropped when a
sample is behind the depth source by more than max(relief, rel * z), or the depth source has no
value there (unseen means unsure), or it is beyond the reach. Sources: the laser scan (PR #12's
depth buffer: the reference), each model's scaled per-image depth, and the fused TSDF mesh rendered
from each keyframe. Ground-truth poses throughout (the wall frame comes from taps in the same world).
"""

from __future__ import annotations

import argparse
import json
import math

import cv2
import numpy as np
import open3d as o3d

from common import WORK  # noqa: I001 (puts PR #12 on sys.path)
import evals.coverage as cov
from fuse import DepthSource, fuse

CFG = {
    "cellWidth": 0.1524, "wallBandHeight": 1.9812, "groundBandDepth": 1.2, "maxDistance": 6.0,
    "maxAngleFromNormal": 65 * math.pi / 180, "imageMargin": 0.03, "rowsPerBand": 3,
    "coveringBaseline": 0.25, "fogAhead": 2.5,
}
cov.app_config = lambda: dict(CFG)
SCENE = "electro"
OUT = WORK / "occlusion"


def run_app(wall, cams: dict, variant: dict | None = None, hidden: list | None = None) -> dict:
    """CoverageMap.observe for every keyframe in order, then coveredIntervals, as the driver returns."""
    cfg = {**CFG, **(variant or {})}
    drop = {(h["keyframe"], h["band"], h["index"]): set(h["rows"]) for h in hidden or []}
    sightings, cells = [], {}
    for name, cam in cams.items():
        pos = cam[0][:3, 3]
        for band in ("wall", "ground"):
            for index in cov.candidate_cells(wall, cam, cfg):
                rows, _ = cov.app_rows(wall, band, index, cam, cfg)
                if not rows:
                    continue
                sightings.append({"keyframe": name, "band": band, "index": index, "rows": sorted(rows)})
                cell = cells.setdefault((band, index), [[] for _ in range(cfg["rowsPerBand"])])
                for r in sorted(rows - drop.get((name, band, index), set())):
                    held = cell[r]
                    if len(held) < 2 and all(np.linalg.norm(p - pos) >= cfg["coveringBaseline"] for p in held):
                        held.append(pos)
    w = cfg["cellWidth"]
    covered = {}
    for band in ("wall", "ground"):
        idx = sorted(i for (b, i), rows in cells.items() if b == band and all(len(h) >= 2 for h in rows))
        runs: list[list[float]] = []
        for i in idx:
            lo, hi = i * w, (i + 1) * w
            if runs and abs(runs[-1][1] - lo) < w * 0.01:
                runs[-1][1] = hi
            else:
                runs.append([lo, hi])
        if runs:
            runs[0][0] = max(runs[0][0], wall.left)
            runs[-1][1] = min(runs[-1][1], wall.right)
        covered[band] = runs
    return {"covered": covered, "sightings": sightings}


def depth_hidden(setup, sightings, zbufs, missing, rows, reach, relief, rel) -> list[dict]:
    """PR #12's `depth_hidden` with the depth source swapped in (zbufs/missing per keyframe)."""
    cfg = {**setup.cfg, "rowsPerBand": rows}
    names = [v.name for v in setup.views]
    old = cov.HIDE_REL
    cov.HIDE_REL = rel
    out = []
    try:
        for x in sightings:
            i = names.index(x["keyframe"])
            pts = cov.row_points(setup.wall, x["band"], x["index"], cfg)[x["rows"]]
            t = cov.photo_truth(setup.views[i], setup.R, zbufs[i], missing[i], pts, True,
                                setup.cfg["maxDistance"] if reach is None else reach, relief)
            bad = (t.hidden | ~t.in_range).any(axis=1)
            if bad.any():
                out.append({**x, "rows": [r for r, b in zip(x["rows"], bad, strict=True) if b]})
    finally:
        cov.HIDE_REL = old
    return out


def buffers_from_depth(depths: list[np.ndarray | None]) -> tuple[list, list]:
    """A depth map per keyframe -> (5x5 minimum-filtered depth buffer, no-depth mask)."""
    zb, miss = [], []
    for d in depths:
        if d is None:
            zb.append(np.full((2, 2), 1e6, np.float32))
            miss.append(np.ones((2, 2), bool))
            continue
        buf = np.where(np.isfinite(d) & (d > 0), d, 1e6).astype(np.float32)
        buf = cv2.erode(buf, np.ones((5, 5), np.uint8))
        zb.append(buf)
        miss.append(buf >= 1e5)
    return zb, miss


def render_mesh_depth(mesh, src: DepthSource, names: list[str]) -> list[np.ndarray]:
    rc = o3d.t.geometry.RaycastingScene()
    rc.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    out = []
    for n in names:
        K, T = src.K(n), src.T[n]
        h = src.raw[n].shape[0] if n in src.raw else round(1024 * src.views[n].height / src.views[n].width)
        u, v = np.meshgrid(np.arange(1024), np.arange(h))
        d = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u, float)], -1).reshape(-1, 3)
        dw = d @ T[:3, :3].T
        rays = o3d.core.Tensor(np.c_[np.broadcast_to(T[:3, 3], dw.shape), dw].astype(np.float32))
        t = rc.cast_rays(rays)["t_hit"].numpy().reshape(h, 1024)
        out.append(np.where(np.isfinite(t), t, np.nan).astype(np.float32))
    return out


def row(label, setup, app) -> dict:
    b = setup.score(app, causes=False)
    w = b["wall"]
    return {"option": label, "claimed_ft": round(w["claimed_ft"], 2), "false_observed_ft": round(w["false_observed_ft"], 2),
            "missed_ft": round(w["missed_ft"], 2), "passes": bool(w["false_observed_ft"] <= cov.PASS_FT)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="moge2-vits,unidepth2-vits,metric3d2-vits,moge2-vitl,depthpro")
    ap.add_argument("--scale", default="feat")
    args = ap.parse_args()
    setup = cov.setup_scene(SCENE)
    names = [v.name for v in setup.views]
    rows = []
    app = run_app(setup.wall, setup.cams)
    rows.append(row("app today: frustum only (no occlusion)", setup, app))
    first = app["sightings"]

    def option(label, zbufs, missing, relief=cov.HIDE_ABS_M, rel=cov.HIDE_REL, reach=None):
        hidden = depth_hidden(setup, first, zbufs, missing, CFG["rowsPerBand"], reach, relief, rel)
        r = row(label, setup, run_app(setup.wall, setup.cams, hidden=hidden))
        r.update(relief_m=relief, rel=rel)
        rows.append(r)
        print(r, flush=True)

    option("depth test, laser scan (PR #12 true depth)", setup.zbufs, setup.missing)
    option("depth test, laser scan, 0.4 m relief", setup.zbufs, setup.missing, relief=0.4)
    for model in filter(None, args.models.split(",")):
        src = DepthSource(SCENE, model, "gt", args.scale)
        if len(src.raw) < len(names):
            print(model, "incomplete depth, skipped")
            continue
        zb, miss = buffers_from_depth([src.depth(n) for n in names])
        for relief, rel in ((cov.HIDE_ABS_M, cov.HIDE_REL), (0.4, cov.HIDE_REL), (0.4, 0.10)):
            option(f"depth test, {model} per-image depth ({args.scale})", zb, miss, relief, rel)
        mesh = fuse(src)
        zb, miss = buffers_from_depth(render_mesh_depth(mesh, src, names))
        for relief, rel in ((cov.HIDE_ABS_M, cov.HIDE_REL), (0.4, cov.HIDE_REL), (0.4, 0.10)):
            option(f"depth test, {model} fused TSDF mesh ({args.scale})", zb, miss, relief, rel)
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"scene": SCENE, "wall_ft": (setup.wall.right - setup.wall.left) / cov.FEET, "config": CFG, "rows": rows}
    (OUT / f"electro_{args.scale}.json").write_text(json.dumps(res, indent=1))
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
