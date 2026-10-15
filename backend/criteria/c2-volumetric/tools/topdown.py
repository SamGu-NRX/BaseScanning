"""Top-down overview of an ETH3D scan: height of the lowest wall-band point per cell, scanner origins
and camera centres. Used to pick regions and hand-place labels."""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c2vol.eth3d import load_scans, read_cameras  # noqa: E402

scene, data_root, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
roi = None
if len(sys.argv) > 4:
    x0, y0, x1, y1 = map(float, sys.argv[4].split(","))
    roi = ((x0, y0), (x1, y1))
scans = load_scans(data_root / scene / "dslr_scan_eval", roi)
cams = read_cameras(data_root / scene / "dslr_calibration_undistorted")
X = np.concatenate([s.points[::4] for s in scans])
zg = np.percentile(X[:, 2], 2)
res = 0.05
lo = X[:, :2].min(0)
ij = ((X[:, :2] - lo) / res).astype(int)
H, W = ij.max(0) + 1
zmax = np.full((H, W), np.nan)
np.fmax.at(zmax, (ij[:, 0], ij[:, 1]), X[:, 2] - zg)
band = (X[:, 2] - zg > 0.3) & (X[:, 2] - zg < 2.0)
wall = np.zeros((H, W))
np.add.at(wall, (ij[band, 0], ij[band, 1]), 1)

fig, axs = plt.subplots(1, 2, figsize=(22, 11))
ext = [lo[0], lo[0] + H * res, lo[1], lo[1] + W * res]
axs[0].imshow(np.clip(zmax.T, 0, 12), origin="lower", extent=ext, cmap="viridis")
axs[0].set_title(f"{scene}: max height above z={zg:.2f} (clip 12 m)")
axs[1].imshow(np.log1p(wall.T), origin="lower", extent=ext, cmap="gray_r")
axs[1].set_title("points 0.3-2.0 m above ground (walls, obstacles)")
for ax in axs:
    for s in scans:
        ax.plot(*s.origin[:2], "r*", ms=14)
        ax.annotate(s.name, s.origin[:2], color="r")
    C = np.array([c.center for c in cams.values()])
    ax.plot(C[:, 0], C[:, 1], "c.", ms=4)
    ax.grid(True, alpha=0.3)
    ax.set_xticks(np.arange(np.floor(ext[0]), ext[1], 2))
    ax.set_yticks(np.arange(np.floor(ext[2]), ext[3], 2))
    ax.tick_params(labelsize=6)
fig.tight_layout()
fig.savefig(out, dpi=90)
print("ground z (2nd pct)", zg, "extent", ext, "n", len(X))
for s in scans:
    print(s.name, s.origin.round(2), len(s.points))
