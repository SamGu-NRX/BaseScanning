"""Figures of the best A1 facade run (incremental COLMAP with ARKit position priors, non-LiDAR
profile, seed 0) for media/colmap-facade/.

    python render_media.py [--no-video]

Frame: the harness's meter-anchored scene frame (metres, y up, meter wall w1 along x at z = 0,
+z out of the wall). The recon is placed there exactly as the shared scorer does for a metric
cloud: rigid fit of its camera centres to the GT cameras (scale error kept), then the GT scene's
eth3d->scene transform. Per-point error = distance to the nearest laser point (2 cm voxels).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import open3d as o3d  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Line3DCollection  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

from common import M_TO_IN, STORE, WORK, read_text_model, umeyama  # noqa: E402

sys.path.insert(0, str(STORE / "internal/eval-harness"))
from evalharness.frame import project_arkit  # noqa: E402

RUN = "arkit_nonlidar_modern_s0_prior"
OUT = STORE / "media/colmap-facade"
FRAMES = STORE / "internal/data/houses/eth3d-facade/frames"
GT_SCENE = Path.home() / "a1/facade-gt/scene.json"
SCORE = STORE / f"internal/eval-harness/results/eth3d-facade/A1-colmap/{RUN}_sparse/geomscore.json"
TITLE = "ETH3D facade · COLMAP incremental SfM with simulated non-LiDAR ARKit position priors"


def load():
    d = WORK / "facade"
    cams, rec = read_text_model(d / "sparse" / RUN)
    _, gt = read_text_model(d / "gt_model")
    names = sorted(rec)
    _, R, t = umeyama(np.array([rec[n]["center"] for n in names]), np.array([gt[n]["center"] for n in names]), False)
    sc = json.loads(GT_SCENE.read_text())
    Ts = np.asarray(sc["frame"]["from_source"], float).reshape(4, 4, order="F")
    A = Ts[:3, :3] @ R
    b = Ts[:3, :3] @ t + Ts[:3, 3]
    pts, rgb = [], []
    for ln in (d / "sparse" / RUN / "points3D.txt").read_text().splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        p = ln.split()
        pts.append([float(v) for v in p[1:4]])
        rgb.append([int(v) for v in p[4:7]])
    P = np.array(pts) @ A.T + b
    cam_list = []
    for n in names:
        im = rec[n]
        model, w, h, prm = cams[im["camera_id"]]
        R_wc = A @ im["R_cw"].T
        cam_list.append({"name": n, "C": A @ im["center"] + b, "R_wc": R_wc, "f": prm[0], "w": w, "h": h})
    return sc, P, np.array(rgb) / 255.0, cam_list


def frustum_segments(cams, depth=0.9):
    segs = []
    for c in cams:
        hw, hh = c["w"] / 2 / c["f"] * depth, c["h"] / 2 / c["f"] * depth
        corners = [c["C"] + c["R_wc"] @ np.array([sx * hw, sy * hh, depth]) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        for k in range(4):
            segs.append((c["C"], corners[k]))
            segs.append((corners[k], corners[(k + 1) % 4]))
    return segs


def crop(P, cams, margin=4.0):
    """Courtyard box: camera extent plus a margin in plan, clipped to where the points are; y from
    just below the ground to the roofline."""
    C = np.array([c["C"] for c in cams])
    lo = np.maximum(C.min(0) - margin, np.percentile(P, 1, 0))
    hi = np.minimum(C.max(0) + margin, np.percentile(P, 99, 0))
    lo[0], hi[0] = min(lo[0], -14.0), max(hi[0], 4.0)
    lo[2] = min(lo[2], -1.0)
    lo[1], hi[1] = -1.0, min(np.percentile(P[:, 1], 99.5), 18.0)
    return lo, hi, np.all((P >= lo) & (P <= hi), 1)


def to_plot(X):
    """Scene (x, y up, z out) -> matplotlib 3D (x, z, y) so the plot's vertical axis is up."""
    X = np.asarray(X)
    return X[..., [0, 2, 1]]


def setup_3d(fig, pos, lo, hi, elev, azim):
    ax = fig.add_subplot(pos, projection="3d")
    a, b = to_plot(lo), to_plot(hi)
    ax.set_xlim(a[0], b[0])
    ax.set_ylim(a[1], b[1])
    ax.set_zlim(a[2], b[2])
    ax.set_box_aspect(b - a, zoom=1.35)
    ax.view_init(elev=elev, azim=azim)
    ax.set_xlabel("x along meter wall (m)")
    ax.set_ylabel("z out from wall (m)")
    ax.set_zlabel("height (m)")
    return ax


def draw_cams_3d(ax, cams, color="red"):
    segs = [tuple(map(tuple, to_plot(np.array(s)))) for s in frustum_segments(cams)]
    ax.add_collection3d(Line3DCollection(segs, colors=color, linewidths=0.6))


def label_wall_3d(ax, sc):
    w1 = np.array(sc["walls"][0]["baseline_m"])
    ax.plot([w1[0, 0], w1[1, 0]], [w1[0, 1], w1[1, 1]], [0, 0], color="orange", lw=2)
    ax.text(-5.0, 0.0, 6.2, "w1 = meter wall", color="darkorange", fontsize=13, weight="bold")
    ax.scatter([0], [0], [1.524], marker="*", s=220, color="magenta", depthshade=False)
    ax.text(0.3, 0.0, 1.9, "meter", color="magenta", fontsize=12, weight="bold")


def save(fig, path, dpi=110):
    fig.savefig(path.with_suffix(".png"), dpi=dpi, facecolor="white", bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)
    img = cv2.imread(str(path.with_suffix(".png")))
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    path.with_suffix(".png").unlink()


def contact_sheet():
    names = sorted(p.name for p in FRAMES.glob("*.jpg"))
    cols, tw = 10, 240
    th = int(round(tw * 1364 / 2048))
    lab = 22
    rows = int(np.ceil(len(names) / cols))
    head = 90
    sheet = np.full((head + rows * (th + lab) + 10, cols * tw + 20, 3), 255, np.uint8)
    cv2.putText(sheet, f"ETH3D facade: all {len(names)} input photos (Nikon D3X DSLR, undistorted)", (12, 38),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(sheet, "Used at 2048 x 1364 px (downsampled from 6201 x 4130); known pinhole intrinsics; filename order",
                (12, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 60), 1, cv2.LINE_AA)
    for i, n in enumerate(names):
        r, c = divmod(i, cols)
        img = cv2.resize(cv2.imread(str(FRAMES / n)), (tw - 6, th - 4), interpolation=cv2.INTER_AREA)
        y, x = head + r * (th + lab), 10 + c * tw
        sheet[y:y + th - 4, x + 3:x + tw - 3] = img
        cv2.putText(sheet, Path(n).stem, (x + 6, y + th + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(OUT / "inputs-contact-sheet.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])


def sparse_views(sc, P, RGB, cams):
    lo, hi, k = crop(P, cams)
    fig = plt.figure(figsize=(19.2, 11))
    ax = setup_3d(fig, 111, lo, hi, elev=22, azim=62)
    Q = to_plot(P[k])
    ax.scatter(Q[:, 0], Q[:, 1], Q[:, 2], c=RGB[k], s=0.6, depthshade=False, linewidths=0)
    draw_cams_3d(ax, cams)
    label_wall_3d(ax, sc)
    ax.set_title(f"{TITLE}\nSparse cloud: {k.sum():,} points coloured by image colour; 76 registered cameras (red frustums)",
                 fontsize=14)
    save(fig, OUT / "sparse-oblique.jpg", dpi=175)

    # Landscape plan: horizontal = z (out from the meter wall), vertical = x (along it).
    fig, ax = plt.subplots(figsize=(19.2, 10))
    ax.scatter(P[k, 2], P[k, 0], c=RGB[k], s=1.2, linewidths=0)
    for s in frustum_segments(cams, depth=0.7):
        a, b = np.array(s)
        ax.plot([a[2], b[2]], [a[0], b[0]], color="red", lw=0.7)
    for w in sc["walls"]:
        bl = np.array(w["baseline_m"])
        ax.plot(bl[:, 1], bl[:, 0], color="orange", lw=3, alpha=0.85)
        m = bl.mean(0)
        ax.text(m[1] + (0.6 if w["id"] != "w2" else 0.2), m[0] + (0 if w["id"] != "w2" else 1.2),
                w["id"] + (" = meter wall" if w["id"] == "w1" else ""), color="darkorange", fontsize=14,
                weight="bold", rotation=90 if w["id"] == "w1" else 0, va="center")
    ax.plot([0], [0], marker="*", ms=20, color="magenta")
    ax.text(0.5, -1.2, "meter", color="magenta", fontsize=13, weight="bold")
    ax.set_xlim(lo[2], hi[2])
    ax.set_ylim(lo[0], hi[0])
    ax.set_aspect("equal")
    ax.set_xlabel("z out from the meter wall (m)")
    ax.set_ylabel("x along the meter wall (m)")
    ax.grid(alpha=0.3)
    ax.set_title(f"{TITLE}\nTop-down: sparse points (image colour), camera frustums (red), laser-fitted walls w1-w3 (orange)",
                 fontsize=14)
    save(fig, OUT / "sparse-topdown.jpg")


def sparse_vs_laser(sc, P, cams):
    # Full merged ETH3D scan (the harness laser.ply is cropped to the courtyard wing), 3 cm voxels.
    Ts = np.asarray(sc["frame"]["from_source"], float).reshape(4, 4, order="F")
    pc = o3d.io.read_point_cloud(str(WORK / "facade/gt_scan_1cm.ply")).voxel_down_sample(0.03)
    L = np.asarray(pc.points) @ Ts[:3, :3].T + Ts[:3, 3]
    d, _ = cKDTree(L).query(P, workers=4)
    lo, hi, k = crop(P, cams)
    L = L[np.all((L >= lo) & (L <= hi), 1)]
    rng = np.random.default_rng(0)
    Ls = L[rng.choice(len(L), min(len(L), 300000), replace=False)]
    # Points more than 1 m from any laser surface lie where the scanner saw nothing (trees, sky
    # reflections, unscanned buildings): no truth, drawn grey, left out of the stats.
    kk = k & (d < 1.0)
    notruth = k & ~(d < 1.0)
    # Shape-only reference: remove the run's metric scale error with a similarity fit on the cameras.
    _, gt_images = read_text_model(WORK / "facade/gt_model")
    C = np.array([c["C"] for c in cams])
    G = np.array([gt_images[c["name"]]["center"] for c in cams]) @ Ts[:3, :3].T + Ts[:3, 3]
    s, Rs, ts = umeyama(C, G, True)
    d_shape, _ = cKDTree(L).query(s * P[kk] @ Rs.T + ts, workers=4)
    shape_med = np.median(d_shape) * 100
    scale_err_pct = (1 / s - 1) * 100
    err_cm = np.clip(d[kk] * 100, 0, 30)
    med, p90 = np.median(d[kk]) * 100, np.percentile(d[kk], 90) * 100
    fig = plt.figure(figsize=(21, 11))
    ax = setup_3d(fig, 121, lo, hi, elev=22, azim=62)
    q = to_plot(Ls)
    ax.scatter(q[:, 0], q[:, 1], q[:, 2], c="0.8", s=0.12, depthshade=False, linewidths=0)
    qn = to_plot(P[notruth])
    ax.scatter(qn[:, 0], qn[:, 1], qn[:, 2], c="0.45", s=0.6, depthshade=False, linewidths=0)
    Q = to_plot(P[kk])
    sca = ax.scatter(Q[:, 0], Q[:, 1], Q[:, 2], c=err_cm, cmap="turbo", vmin=0, vmax=30, s=1.2, depthshade=False,
                     linewidths=0)
    label_wall_3d(ax, sc)
    ax.set_title("Oblique: laser scan (light grey) + COLMAP points (colour = distance to laser;\n"
                 f"dark grey = {notruth.sum():,} points > 1 m from any laser surface, no truth)", fontsize=13)
    ax2 = fig.add_subplot(122)
    band = lambda X: (np.abs(X[:, 2]) < 1.0) & (X[:, 0] > -13.5) & (X[:, 0] < 3.5)
    Lw = L[band(L)]
    ax2.scatter(Lw[:, 0], Lw[:, 1], c="0.7", s=0.2, linewidths=0)
    Pw = band(P) & kk
    ax2.scatter(P[Pw, 0], P[Pw, 1], c=np.clip(d[Pw] * 100, 0, 30), cmap="turbo", vmin=0, vmax=30, s=2, linewidths=0)
    for o in sc["objects"]:
        g = o["geometry"]
        if g.get("kind") == "wall_rect" and o["wall_id"] == "w1":
            s0, s1, b0, b1 = g["s_m"][0], g["s_m"][1], g["bottom_m"], g["top_m"]
            ax2.plot([s0, s1, s1, s0, s0], [b0, b0, b1, b1, b0], color="k", lw=0.8, ls="--")
    ax2.plot([0], [1.524], marker="*", ms=16, color="magenta")
    ax2.set_aspect("equal")
    ax2.set_xlim(-13.5, 3.5)
    ax2.set_ylim(-0.3, 7)
    ax2.set_xlabel("x along meter wall (m)")
    ax2.set_ylabel("height (m)")
    ax2.set_title("Front elevation of the meter wall (points within 1 m of it); dashed = window outlines", fontsize=13)
    cb = fig.colorbar(sca, ax=[ax, ax2], orientation="horizontal", fraction=0.04, pad=0.08, shrink=0.6)
    cb.set_label("COLMAP point -> nearest laser point (cm; clipped at 30)")
    fig.suptitle(f"{TITLE}\nMetric frame from ARKit priors, rigid camera alignment only (no ICP, scale error kept). "
                 f"Points with laser truth: {kk.sum():,}, median {med:.1f} cm ({med / 2.54:.1f} in), "
                 f"p90 {p90:.1f} cm ({p90 / 2.54:.1f} in).\nMost of it is this ARKit draw's {scale_err_pct:+.1f} % scale error "
                 f"growing with distance from the cameras; with scale removed the median is {shape_med:.1f} cm "
                 f"({shape_med / 2.54:.1f} in). Criteria distances near the meter are small, so they stay within about 2 in.",
                 fontsize=13)
    save(fig, OUT / "sparse-vs-laser.jpg")
    return med, p90


def keydistances(sc):
    g = json.loads(SCORE.read_text())
    rows = {r["id"]: r for r in g["key_distances"]["rows"]}
    objs = {o["id"]: o for o in sc["objects"]}
    pts = {"meter": np.array(objs["meter"]["geometry"]["pos_m"])}
    for w in ("win4", "win5"):
        q = objs[w]["geometry"]
        s0, s1, b0, b1 = q["s_m"][0], q["s_m"][1], q["bottom_m"], q["top_m"]
        pts[w] = np.array([[s0, b0, 0], [s1, b0, 0], [s1, b1, 0], [s0, b1, 0]])
    best = None
    for kf in sc["keyframes"]:
        pose = np.asarray(kf["pose"], float).reshape(4, 4, order="F")
        K = np.array(kf["intrinsics"]) * (2048 / kf["w"])
        X = np.vstack([pts["meter"][None], pts["win4"], pts["win5"], [[0, 0, 0]]])
        u, v, z = project_arkit(pose, K, X)
        if np.all(z > 0) and np.all((u > 60) & (u < 1988) & (v > 60) & (v < 1304)):
            frontal = -pose[:3, 2] @ np.array([0, 0, -1.0])
            span = u.max() - u.min()
            score = frontal + span / 2048
            if best is None or score > best[0]:
                best = (score, kf, pose, K)
    _, kf, pose, K = best
    img = cv2.imread(str(FRAMES / (kf["id"] + ".jpg")))

    def P2(X):
        u, v, _ = project_arkit(pose, K, np.atleast_2d(X))
        return np.c_[u, v].astype(int)

    def val(i):
        r = rows[i]
        e = (r["recon"] - r["gt"]) * M_TO_IN
        return f"COLMAP {r['recon']:.3f} m | laser {r['gt']:.3f} m | {e:+.1f} in"

    for w in ("win4", "win5"):
        cv2.polylines(img, [P2(pts[w])], True, (80, 220, 80), 2, cv2.LINE_AA)
    items = []
    m = pts["meter"]
    items.append(("meter height", P2([m[0], 0, 0])[0], P2(m)[0], val("meter.height"), "v"))
    for w in ("win4", "win5"):
        q = objs[w]["geometry"]
        yb = q["bottom_m"] + 0.25
        items.append((f"{w} width", P2([q["s_m"][0], yb, 0])[0], P2([q["s_m"][1], yb, 0])[0], val(f"opening.width[{w}]"), "h"))
    q5 = objs["win5"]["geometry"]
    xs = q5["s_m"][1] - 0.2
    items.append(("win5 sill height", P2([xs, 0, 0])[0], P2([xs, q5["bottom_m"], 0])[0], val("opening.bottom[win5]"), "v"))
    xs2 = q5["s_m"][0] + 0.25
    items.append(("win5 head height", P2([xs2, 0, 0])[0], P2([xs2, q5["top_m"], 0])[0], val("opening.top[win5]"), "v"))
    q4 = objs["win4"]["geometry"]
    items.append(("gap win4-win5", P2([q4["s_m"][1], 2.3, 0])[0], P2([q5["s_m"][0], 2.3, 0])[0], val("opening.gap[win4~win5]"), "h"))
    colors = [(255, 0, 255), (0, 200, 255), (0, 140, 255), (255, 160, 0), (255, 80, 80), (60, 60, 255)]
    font = cv2.FONT_HERSHEY_SIMPLEX
    for (lab, a, b, txt, _), col in zip(items, colors):
        cv2.line(img, tuple(map(int, a)), tuple(map(int, b)), col, 4, cv2.LINE_AA)
        for p in (a, b):
            cv2.circle(img, tuple(map(int, p)), 7, col, -1, cv2.LINE_AA)
    # legend box
    x0, y0, lh = 20, 20, 44
    box_h = 70 + lh * len(items)
    overlay = img.copy()
    cv2.rectangle(overlay, (x0, y0), (x0 + 1000, y0 + box_h), (255, 255, 255), -1)
    img = cv2.addWeighted(overlay, 0.82, img, 0.18, 0)
    cv2.putText(img, f"{kf['id']}: key distances on the meter wall (shared scorer)", (x0 + 14, y0 + 38), font, 0.85,
                (0, 0, 0), 2, cv2.LINE_AA)
    for i, ((lab, a, b, txt, _), col) in enumerate(zip(items, colors)):
        y = y0 + 80 + i * lh
        cv2.rectangle(img, (x0 + 14, y - 22), (x0 + 44, y + 4), col, -1)
        cv2.putText(img, f"{lab}: {txt}", (x0 + 56, y), font, 0.72, (0, 0, 0), 2, cv2.LINE_AA)
    note = ("Truth = laser-fitted wall + ground; openings hand-clicked (PR #13); meter position invented. "
            "COLMAP = ARKit-prior SfM, non-LiDAR profile.")
    cv2.rectangle(img, (0, 1364 - 44), (2048, 1364), (255, 255, 255), -1)
    cv2.putText(img, note, (14, 1364 - 14), font, 0.72, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.imwrite(str(OUT / "keydistances.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return kf["id"]


def turntable(sc, P, RGB, cams, n=96):
    lo, hi, k = crop(P, cams)
    Q = to_plot(P[k])
    segs = [tuple(map(tuple, to_plot(np.array(s)))) for s in frustum_segments(cams)]
    tmp = Path(tempfile.mkdtemp())
    for i in range(n):
        fig = plt.figure(figsize=(16, 9))
        ax = setup_3d(fig, 111, lo, hi, elev=24, azim=62 + 360 * i / n)
        ax.scatter(Q[:, 0], Q[:, 1], Q[:, 2], c=RGB[k], s=0.5, depthshade=False, linewidths=0)
        ax.add_collection3d(Line3DCollection(segs, colors="red", linewidths=0.5))
        label_wall_3d(ax, sc)
        ax.set_title("COLMAP sparse cloud + 76 cameras (ETH3D facade, ARKit-prior SfM)", fontsize=14)
        fig.savefig(tmp / f"f{i:03d}.png", dpi=100, facecolor="white")
        plt.close(fig)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "16", "-i", str(tmp / "f%03d.png"),
                    "-vf", "scale=1600:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26",
                    str(OUT / "turntable.mp4")], check=True)
    shutil.rmtree(tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-video", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    sc, P, RGB, cams = load()
    contact_sheet()
    sparse_views(sc, P, RGB, cams)
    med, p90 = sparse_vs_laser(sc, P, cams)
    kf = keydistances(sc)
    print({"points": len(P), "err_med_cm": med, "err_p90_cm": p90, "keyframe": kf})
    if not a.no_video:
        turntable(sc, P, RGB, cams)


if __name__ == "__main__":
    main()
