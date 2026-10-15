"""The app's 3D map (Map3D, t3/ios-map3d) against the same truth as CoverageMap (METHODS.md section 7c).

The app's code runs unmodified through `map3d_driver/`, built against a read-only checkout of
HouseScanKit at `KIT_COMMIT`. Its LiDAR path is fed one depth frame per ETH3D photo, 256 x 192,
rendered from the laser scan at the photo's pose. Its coverage along section 7's wall is scored
with section 7's truth; its facing and overhead claims with a seen-empty test on the same laser
depth; its measured wall chain against the laser's wall line.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from evals.coverage import (
    FEET,
    HIDE_ABS_M,
    HIDE_REL,
    MASK_WIDTH,
    MISSING_FROM_SCAN,
    RESULTS,
    Setup,
    band_samples,
    face_offsets,
    in_intervals,
    photo_truth,
    project,
    setup_scene,
    two_positions,
)
from evals.eth3d import excluded_pixels, occluder_points, scan_points
from evals.pairs import results_json
from evals.paths import EVALS_DIR

KIT_COMMIT = "66cdcbaaca5de3ca4812e7073fd5490b477713a2"  # t3/ios-map3d, draft PR #21
KIT_CHECKOUT = EVALS_DIR / f"housescankit-{KIT_COMMIT[:7]}"
KIT_DIR = KIT_CHECKOUT / "ios" / "HouseScanKit"
DRIVER_DIR = Path(__file__).resolve().parents[1] / "map3d_driver"
DRIVER_BIN = DRIVER_DIR / ".build" / "release" / "map3d-driver"
WORK = EVALS_DIR / "map3d"
SCENE = "electro"
DEPTH_W, DEPTH_H = 256, 192  # ARKit's sceneDepth size
PASS_FT = 0.5
VIEW_RANGE_M = 5.0  # Map3D's maxViewDistance, the range the seen-empty truth allows
HEADROOM_M = 1.9812
VOXEL_M = 0.1  # Map3DConfig.voxelSize: Map3D judges space from one voxel out from the wall
OVERHEAD_DEPTH_M = 0.5588
GROUND_CLEARANCE_M = 0.15
MAX_WALL_GAP_M = 0.35  # Map3DConfig.maxWallGap, for the laser wall's extent


def prepare() -> None:
    """Checks out HouseScanKit at the pinned commit, read-only, and builds the driver."""
    repo = Path(__file__).resolve().parents[3]
    if not KIT_CHECKOUT.exists():
        # Fetch the commit itself: the branch can move or be rebased away from it.
        have = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{KIT_COMMIT}^{{commit}}"], check=False
        )
        if have.returncode:
            subprocess.run(
                ["git", "-C", str(repo), "fetch", "--no-tags", "origin", KIT_COMMIT], check=True
            )
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(KIT_CHECKOUT), KIT_COMMIT],
            check=True,
        )
    subprocess.run(
        ["swift", "build", "-c", "release", "--package-path", str(DRIVER_DIR)],
        env={**os.environ, "HOUSESCANKIT_DIR": str(KIT_DIR)},
        check=True,
    )


def laser_depth(view, blockers: np.ndarray, missing: np.ndarray) -> np.ndarray:
    """A 256 x 192 depth frame for the photo: nearest scan depth along the camera's axis, holes
    closed by a 3-pixel minimum filter, 0 where nothing was scanned or where ETH3D masks an object
    the scanner missed. The photo is 3:2, so pixels scale by 256 / W across and 192 / H down, as
    `DepthFrame(photo:)` scales the intrinsics."""
    u, v, z, ok = project(view, blockers)
    iu = (u[ok] * DEPTH_W / view.width).astype(np.int64)
    iv = (v[ok] * DEPTH_H / view.height).astype(np.int64)
    inside = (iu >= 0) & (iu < DEPTH_W) & (iv >= 0) & (iv < DEPTH_H)
    buf = np.full((DEPTH_H, DEPTH_W), 1e6, np.float32)
    np.minimum.at(buf, (iv[inside], iu[inside]), z[ok][inside].astype(np.float32))
    buf = cv2.erode(buf, np.ones((3, 3), np.uint8))
    m = cv2.resize(missing.astype(np.uint8), (DEPTH_W, DEPTH_H), interpolation=cv2.INTER_NEAREST)
    buf[(buf >= 1e5) | m.astype(bool)] = 0
    return buf


def run_driver(setup: Setup) -> dict:
    blockers = np.concatenate([scan_points(SCENE), occluder_points(SCENE)])
    WORK.mkdir(parents=True, exist_ok=True)
    frames = []
    for view in setup.views:
        missing = excluded_pixels(
            view,
            MASK_WIDTH,
            round(view.height * MASK_WIDTH / view.width),
            labels=(MISSING_FROM_SCAN,),
        )
        path = WORK / f"{view.name}.depth.f32"
        laser_depth(view, blockers, missing).astype("<f4").tofile(path)
        pose, intrinsics, size = setup.cams[view.name]
        frames.append(
            {
                "id": view.name,
                "pose": pose.T.ravel().tolist(),
                "intrinsics": intrinsics,
                "size": size,
                "depthFile": str(path),
                "width": DEPTH_W,
                "height": DEPTH_H,
            }
        )
    wall = setup.wall
    payload = {
        "wall": {
            "meter": wall.meter.tolist(),
            "outward": wall.outward.tolist(),
            "groundY": wall.ground_y,
        },
        "frames": frames,
    }
    done = subprocess.run(
        [str(DRIVER_BIN)], input=json.dumps(payload), capture_output=True, text=True, check=False
    )
    if done.returncode:
        raise RuntimeError(f"map3d-driver failed: {done.stderr.strip()}")
    return json.loads(done.stdout)


def seen_empty(setup: Setup, points: np.ndarray, tol_abs: float = HIDE_ABS_M) -> np.ndarray:
    """Per point (levelled world): some photo within 5 m frames it and the laser depth at its
    pixel lies beyond it by more than the tolerance, so that photo's ray passed through it."""
    X = points @ setup.R  # levelled -> ETH3D world
    seen = np.zeros(len(points), bool)
    for view, zbuf, missing in zip(setup.views, setup.zbufs, setup.missing, strict=True):
        u, v, z, framed = project(view, X)
        near = np.linalg.norm(X - view.center, axis=1) <= VIEW_RANGE_M
        zh, zw = zbuf.shape
        cu = np.clip(np.nan_to_num(u * zw / view.width).astype(np.int64), 0, zw - 1)
        cv_ = np.clip(np.nan_to_num(v * zh / view.height).astype(np.int64), 0, zh - 1)
        mh, mw = missing.shape
        mu = np.clip(np.nan_to_num(u * mw / view.width).astype(np.int64), 0, mw - 1)
        mv = np.clip(np.nan_to_num(v * mh / view.height).astype(np.int64), 0, mh - 1)
        nearest = zbuf[cv_, cu]
        # No scan point near the pixel is no evidence of empty space, not proof of it.
        beyond = (nearest < 1e5) & (nearest > z + np.maximum(tol_abs, HIDE_REL * z))
        seen |= framed & near & beyond & ~missing[mv, mu]
    return seen


def span_columns(lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    """2 cm columns that tile [lo, hi] exactly: their centres and widths in meters. The last column
    is the partial strip left over, so the widths sum to the span's length."""
    edges = np.append(np.arange(lo, hi - 1e-9, 0.02), hi)
    return (edges[:-1] + edges[1:]) / 2, np.diff(edges)


def within_bar(false_observed_ft: float) -> bool:
    """The pre-registered 0.5 ft bar, inclusive. Rounding to a micro-foot keeps a claim of exactly
    the bar from failing on floating-point sums of column widths."""
    return bool(round(false_observed_ft, 6) <= PASS_FT)


def space_claims(setup: Setup, spans: list[dict], kind: str, tol_abs: float = HIDE_ABS_M) -> dict:
    """False-observed length of Map3D's facing or overhead claims: claimed 2 cm columns where at
    least two 5 cm samples of the claimed space were never seen empty."""
    wall = setup.wall
    claimed_ft, bad_ft = 0.0, 0.0
    runs = []
    for sp in spans:
        lo, hi = sp["s"]
        reach = sp["out"]
        cols, w = span_columns(lo, hi)
        if kind == "facing":
            outs = np.arange(VOXEL_M, reach + 1e-6, 0.05)
            heights = np.arange(GROUND_CLEARANCE_M, HEADROOM_M + 1e-6, 0.05)
        else:
            outs = np.arange(VOXEL_M, OVERHEAD_DEPTH_M + 1e-6, 0.05)
            heights = np.arange(GROUND_CLEARANCE_M, reach + 1e-6, 0.05)
        if not len(cols) or not len(outs) or not len(heights):
            continue
        grid_s, grid_o, grid_h = np.meshgrid(cols, outs, heights, indexing="ij")
        pts = wall.world(grid_s, grid_h, grid_o).reshape(-1, 3)
        unseen = (~seen_empty(setup, pts, tol_abs)).reshape(len(cols), -1).sum(axis=1)
        bad = unseen >= 2
        claimed_ft += w.sum() / FEET
        bad_ft += w[bad].sum() / FEET
        if bad.any():
            runs.append([round(lo / FEET, 2), round(hi / FEET, 2), round(w[bad].sum() / FEET, 2)])
    return {
        "claimed_ft": round(claimed_ft, 2),
        "false_observed_ft": round(bad_ft, 2),
        "passes": within_bar(bad_ft),
        "spans_with_unseen_ft": runs,
    }


def ground_claims(setup: Setup, spans: list[dict]) -> dict:
    """Every exported ground reach, scored at its own depth: claimed 2 cm columns where at least
    two 5 cm ground samples from one voxel out to the reach were seen by no photo within 5 m,
    counting a sample only where the scan has a surface near it."""
    wall = setup.wall
    claimed_ft, bad_ft, runs = 0.0, 0.0, []
    for sp in spans:
        lo, hi = sp["s"]
        cols, w = span_columns(lo, hi)
        outs = np.arange(VOXEL_M, sp["out"] + 1e-6, 0.05)
        if not len(cols) or not len(outs):
            continue
        grid_s, grid_o = np.meshgrid(cols, outs, indexing="ij")
        pts = wall.world(grid_s, np.zeros_like(grid_s), grid_o).reshape(-1, 3)
        seen = np.zeros(len(pts), bool)
        for view, zbuf, missing, centre in zip(
            setup.views, setup.zbufs, setup.missing, setup.centres, strict=True
        ):
            t = photo_truth(
                view, setup.R, zbuf, missing, pts, centre[1] > wall.ground_y, VIEW_RANGE_M
            )
            seen |= t.saw & ~t.no_scan
        bad = (~seen).reshape(len(cols), -1).sum(axis=1) >= 2
        claimed_ft += w.sum() / FEET
        bad_ft += w[bad].sum() / FEET
        if bad.any():
            runs.append(
                [
                    round(lo / FEET, 2),
                    round(hi / FEET, 2),
                    round(sp["out"] / FEET, 2),
                    round(w[bad].sum() / FEET, 2),
                ]
            )
    return {
        "claimed_ft": round(claimed_ft, 2),
        "false_observed_ft": round(bad_ft, 2),
        "passes": within_bar(bad_ft),
        "spans_with_unseen_ft_s_s_reach_bad": runs,
    }


def retruth(setup: Setup, max_distance: float) -> Setup:
    """Section 7's truth for the setup's wall and faces, with the given range."""
    samples = {
        band: band_samples(setup.wall, band, setup.cfg, setup.faces)[2]
        for band in ("wall", "ground")
    }
    truths = {"wall": [], "ground": []}
    for view, zbuf, missing, centre in zip(
        setup.views, setup.zbufs, setup.missing, setup.centres, strict=True
    ):
        facing = {"wall": setup.wall.out_of(centre) > 0, "ground": centre[1] > setup.wall.ground_y}
        for band, pts in samples.items():
            truths[band].append(
                photo_truth(view, setup.R, zbuf, missing, pts, facing[band], max_distance)
            )
    return replace(setup, truths=truths)


def five_metre_truth(setup: Setup) -> Setup:
    """Section 7's truth with Map3D's 5 m range instead of 6 m."""
    return retruth(setup, VIEW_RANGE_M)


def widened(setup: Setup, left: float, right: float) -> Setup:
    """Section 7's truth over [left, right] of s instead of section 7's stretch, with the laser
    face (pilasters) found again over the new columns, so every wall Map3D claims has truth."""
    wall = replace(setup.wall, left=min(left, setup.wall.left), right=max(right, setup.wall.right))
    scan = scan_points(SCENE).astype(np.float64) @ setup.R.T
    faces = face_offsets(wall, scan, band_samples(wall, "wall", setup.cfg)[0])
    return retruth(replace(setup, wall=wall, faces=faces), setup.cfg["maxDistance"])


def chain_vs_laser(setup: Setup, out: dict) -> dict:
    """The measured wall piece through the meter against the laser's wall line (section 7's wall
    plane, the scan points within 15 cm of it between 0.3 and 2 m up)."""
    if out["meterIndex"] is None:
        return {"chain": None}
    wall = setup.wall
    pose = np.array(out["poseInWorld"]).reshape(4, 4).T
    piece = out["chain"][out["meterIndex"]]
    a = pose[:3, :3] @ np.array([piece["start"][0], 0.0, piece["start"][1]]) + pose[:3, 3]
    b = pose[:3, :3] @ np.array([piece["end"][0], 0.0, piece["end"][1]]) + pose[:3, 3]
    scan = scan_points(SCENE)[::2].astype(np.float64) @ setup.R.T
    rel = scan - wall.origin
    s, h, o = rel @ wall.along, rel[:, 1], rel @ wall.outward
    keep = (np.abs(o) < 0.15) & (h > 0.3) & (h < 2.0)
    slope, offset = np.polyfit(s[keep], o[keep], 1)
    # Extent: the wall face including pilasters up to 0.5 m proud, as Map3D's wall evidence has.
    face = (o > -0.15) & (o < 0.5) & (h > 0.3) & (h < 2.0)
    bins = np.unique(np.floor(s[face] / 0.05)) * 0.05
    gaps = np.flatnonzero(np.diff(bins) > MAX_WALL_GAP_M)
    starts, ends = np.r_[bins[0], bins[gaps + 1]], np.r_[bins[gaps], bins[-1] + 0.05]
    k = (
        int(np.flatnonzero((starts <= 0) & (ends >= 0))[0])
        if ((starts <= 0) & (ends >= 0)).any()
        else None
    )
    sa, sb = (a - wall.origin) @ wall.along, (b - wall.origin) @ wall.along
    oa, ob = (a - wall.origin) @ wall.outward, (b - wall.origin) @ wall.outward
    chain_angle = np.degrees(np.arctan2(ob - oa, sb - sa))
    chain_offset_at_meter = oa + (ob - oa) * (0 - sa) / (sb - sa)
    return {
        "pieces": len(out["chain"]),
        "meter_piece_length_ft": round(float(np.linalg.norm(b - a)) / FEET, 2),
        "meter_piece_s_ft": [round(float(sa) / FEET, 2), round(float(sb) / FEET, 2)],
        "laser_wall_s_ft": None
        if k is None
        else [round(float(starts[k]) / FEET, 2), round(float(ends[k]) / FEET, 2)],
        "laser_wall_length_ft": None if k is None else round(float(ends[k] - starts[k]) / FEET, 2),
        "angle_error_deg": round(float(chain_angle - np.degrees(np.arctan(slope))), 2),
        "offset_at_meter_in": round(float(chain_offset_at_meter - offset) / FEET * 12, 2),
    }


def evaluate() -> dict:
    setup = setup_scene(SCENE)
    if setup is None:
        raise SystemExit(f"{SCENE}: no wall within range")
    out = run_driver(setup)
    band = setup.cfg["groundBandDepth"]
    covered = {
        "wall": [sp["s"] for sp in out["wall"]],
        "ground": [sp["s"] for sp in out["ground"] if sp["out"] >= band - 1e-4],
    }
    bands = setup.score({"covered": covered, "sightings": []}, causes=False)
    bands5 = five_metre_truth(setup).score({"covered": covered, "sightings": []}, causes=False)
    claims = [x for span in covered["wall"] for x in span]
    wide = widened(
        setup, min(claims, default=setup.wall.left), max(claims, default=setup.wall.right)
    )
    bands_wide = wide.score({"covered": covered, "sightings": []}, causes=False)
    cols = band_samples(wide.wall, "wall", setup.cfg, wide.faces)[0]
    saw = np.stack([t.saw for t in wide.truths["wall"]], axis=-1)
    missed = ~in_intervals(cols, covered["wall"]) & two_positions(saw, wide.centres, 0.25).all(
        axis=1
    )
    missed_on_pilasters_ft = round(float((missed & (wide.faces > 0)).sum() * 0.02 / FEET), 2)
    keys = (
        "claimed_ft",
        "false_observed_ft",
        "false_observed_share",
        "missed_ft",
        "seen_two_view_ft",
        "claimed_samples_unknown_for_lack_of_scan",
    )
    stretch = (setup.wall.left, setup.wall.right)
    s_cols = np.arange(stretch[0] + 0.01, stretch[1], 0.02)
    return {
        "kit_commit": KIT_COMMIT,
        "wall_stretch_ft": round((stretch[1] - stretch[0]) / FEET, 1),
        "bands": {b: {k: bands[b][k] for k in keys} for b in ("wall", "ground")},
        "bands_5m_truth": {b: {k: bands5[b][k] for k in keys} for b in ("wall", "ground")},
        "wall_over_all_claims": {
            "s_ft": [round(wide.wall.left / FEET, 2), round(wide.wall.right / FEET, 2)],
            **{k: bands_wide["wall"][k] for k in keys},
            "missed_on_pilaster_faces_ft": missed_on_pilasters_ft,
        },
        "facing_3cm_truth": space_claims(setup, out["facing"], "facing", 0.03),
        "ground_reach_ft": sorted({round(sp["out"] / FEET, 2) for sp in out["ground"]}),
        "ground_every_reach": ground_claims(setup, out["ground"]),
        "wall_claimed_total_ft": round(sum(b - a for a, b in covered["wall"]) / FEET, 2),
        "wall_claimed_on_stretch_ft": round(
            float(in_intervals(s_cols, covered["wall"]).sum() * 0.02 / FEET), 2
        ),
        "facing": space_claims(setup, out["facing"], "facing"),
        "overhead": space_claims(setup, out["overhead"], "overhead"),
        "chain": chain_vs_laser(setup, out),
        "allocated_mb": round(out["allocatedBytes"] / 1e6, 1),
    }


def markdown(r: dict) -> str:
    b = r["bands"]
    c = r["chain"]
    lines = [
        "# The app's 3D map against laser visibility (generated by `make map3d`)",
        "",
        f"HouseScanKit Map3D at {r['kit_commit']} (t3/ios-map3d), unmodified, LiDAR path: one "
        "256 x 192 depth frame per ETH3D electro photo, rendered from the laser scan. Coverage read "
        f"along section 7's {r['wall_stretch_ft']} ft wall. Lengths in feet along the wall; "
        f"false-observed passes at {PASS_FT} ft or less. Ideal depth: exact, dense, no sensor "
        "noise, so this is an upper bound on what real LiDAR gives. Definitions: METHODS.md section 7c.",
        "",
        "| Band | Claimed | False-observed (share) | Pass | Missed | Seen from 2 positions |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label, bands in (("", b), (", 5 m truth", r["bands_5m_truth"])):
        for band in ("wall", "ground"):
            x = bands[band]
            passed = (
                "untested"
                if x["claimed_ft"] == 0
                else ("yes" if x["false_observed_ft"] <= PASS_FT else "no")
            )
            lines.append(
                f"| {band}, Map3D{label} | {x['claimed_ft']:.1f} | {x['false_observed_ft']:.1f} "
                f"({100 * x['false_observed_share']:.0f}%) | {passed} | {x['missed_ft']:.1f} | "
                f"{x['seen_two_view_ft']:.1f} |"
            )
    w = r["wall_over_all_claims"]
    lines.append(
        f"| wall, Map3D, truth over all its claims (s {w['s_ft'][0]} to {w['s_ft'][1]} ft) | "
        f"{w['claimed_ft']:.1f} | {w['false_observed_ft']:.1f} ({100 * w['false_observed_share']:.0f}%) | "
        f"{'yes' if w['false_observed_ft'] <= PASS_FT else 'no'} | {w['missed_ft']:.1f}, of it "
        f"{w['missed_on_pilaster_faces_ft']:.1f} on pilaster faces | {w['seen_two_view_ft']:.1f} |"
    )
    g = r["ground_every_reach"]
    passed = "untested" if g["claimed_ft"] == 0 else ("yes" if g["passes"] else "no")
    lines.append(
        f"| ground, Map3D, every reach at its own depth | {g['claimed_ft']:.1f} | {g['false_observed_ft']:.1f} | {passed} | n/a | n/a |"
    )
    f3 = r["facing_3cm_truth"]
    lines.append(
        f"| facing, Map3D, 3 cm floor on the truth's tolerance | {f3['claimed_ft']:.1f} | "
        f"{f3['false_observed_ft']:.1f} | see note | n/a | n/a |"
    )
    for kind in ("facing", "overhead"):
        x = r[kind]
        passed = "untested" if x["claimed_ft"] == 0 else ("yes" if x["passes"] else "no")
        lines.append(
            f"| {kind}, Map3D | {x['claimed_ft']:.1f} | {x['false_observed_ft']:.1f} | {passed} | n/a | n/a |"
        )
    lines += [
        "",
        f"Map3D's ground reaches (ft): {r['ground_reach_ft']}. Ground counts as claimed where the "
        "reach is at least 1.2 m (3.94 ft), the depth CoverageMap claims.",
        "",
        "Facing: Map3D's one claim is a cell seen clear 0.1 m out from a door face. The truth needs "
        "the laser surface beyond a point by max(tolerance, 4% of the distance), 12 to 20 cm at "
        "these ranges, so space 10 cm in front of a surface can be neither confirmed nor refuted.",
        "",
        "## Wall chain against the laser's wall line",
        "",
    ]
    if c.get("chain") is None and "pieces" not in c:
        lines.append("Map3D found no wall chain through the meter.")
    else:
        lines += [
            f"- Pieces in the chain: {c['pieces']}. The meter's piece: {c['meter_piece_length_ft']} ft, "
            f"s {c['meter_piece_s_ft'][0]} to {c['meter_piece_s_ft'][1]} ft.",
            f"- Laser wall through the meter (gaps up to 0.35 m bridged): {c['laser_wall_length_ft']} ft, "
            f"s {c['laser_wall_s_ft']}.",
            f"- Angle error {c['angle_error_deg']:+.2f} deg; offset at the meter {c['offset_at_meter_in']:+.1f} in.",
        ]
    lines += [
        "",
        f"Map3D claimed {r['wall_claimed_total_ft']} ft of wall in all, "
        f"{r['wall_claimed_on_stretch_ft']} ft of it on section 7's stretch; the row over all its "
        f"claims scores every foot. Map3D's voxels took {r['allocated_mb']} MB.",
    ]
    return "\n".join(lines) + "\n"


def check_kit() -> None:
    """The checkout must be the pinned commit, with HouseScanKit unmodified, and the driver built."""
    if not DRIVER_BIN.exists() or not KIT_DIR.is_dir():
        raise SystemExit("run `uv run python -m evals.map3d prepare` first")
    git = ["git", "-C", str(KIT_CHECKOUT)]
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        [*git, "status", "--porcelain", "--", "ios/HouseScanKit"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != KIT_COMMIT or dirty:
        raise SystemExit(
            f"{KIT_CHECKOUT} is at {head} with changes {dirty!r}; expected {KIT_COMMIT}, clean"
        )


def main() -> None:
    check_kit()
    r = evaluate()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "map3d.json").write_text(results_json(r, default=float))
    md = markdown(r)
    (RESULTS / "map3d.md").write_text(md)
    print(md)


if __name__ == "__main__":
    if sys.argv[1:] == ["prepare"]:
        prepare()
    else:
        main()
