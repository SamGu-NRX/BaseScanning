"""Elevation view of the laser points near a wall line: along-wall s (m) against height, coloured by
offset from the line (negative = behind). Recessed doors, glass (no returns), boxes and canopies
show up here; used to hand-place labels in the scan frame.

    python elevation.py SCENE_DATA_DIR ax,ay,bx,by OUT.png [--band -1.5,1.5] [--zmin Z]
"""

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c2vol.eth3d import load_scans  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("data")
ap.add_argument("line")
ap.add_argument("out")
ap.add_argument("--band", default="-1.0,1.5")
ap.add_argument("--zmax", type=float, default=6.0)
a = ap.parse_args()
ax_, ay, bx, by = map(float, a.line.split(","))
A, B = np.array([ax_, ay]), np.array([bx, by])
d = (B - A) / np.linalg.norm(B - A)
n = np.array([d[1], -d[0]])
lo = np.minimum(A, B) - 3
hi = np.maximum(A, B) + 3
scans = load_scans(Path(a.data) / "dslr_scan_eval", ((lo[0], lo[1]), (hi[0], hi[1])))
X = np.concatenate([s.points for s in scans])
s = (X[:, :2] - A) @ d
o = (X[:, :2] - A) @ n
b0, b1 = map(float, a.band.split(","))
zg = np.percentile(X[:, 2], 1)
m = (o > b0) & (o < b1) & (s > -1) & (s < np.linalg.norm(B - A) + 1) & (X[:, 2] < zg + a.zmax)
fig, ax = plt.subplots(figsize=(22, 7))
sc = ax.scatter(s[m][::3], X[m, 2][::3] - zg, c=o[m][::3], s=0.3, cmap="coolwarm", vmin=b0, vmax=b1)
fig.colorbar(sc, label="offset from line (m, + = outward)")
ax.set_xticks(np.arange(-1, np.linalg.norm(B - A) + 1, 0.5))
ax.set_yticks(np.arange(0, a.zmax, 0.25))
ax.tick_params(labelsize=6)
ax.grid(True, alpha=0.4)
ax.set_aspect("equal")
ax.set_title(f"elevation along {A}->{B}, outward n={n.round(3)}; ground z={zg:.3f}")
fig.tight_layout()
fig.savefig(a.out, dpi=100)
print("ground z", zg, "length", np.linalg.norm(B - A), "outward", n)
