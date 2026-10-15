"""Horizontal slices of an occupancy grid at given heights above a reference z (grey unknown,
white free, black occupied)."""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c2vol.grid import Grid  # noqa: E402

g = Grid.load(sys.argv[1])
zref = float(sys.argv[2])
heights = [float(h) for h in sys.argv[3].split(",")]
out = sys.argv[4]
cmap = matplotlib.colors.ListedColormap(["#999999", "#ffffff", "#000000"])
fig, axs = plt.subplots(1, len(heights), figsize=(7 * len(heights), 7))
ext = [g.origin[0], g.origin[0] + g.shape[0] * g.res, g.origin[1], g.origin[1] + g.shape[1] * g.res]
for ax, h in zip(np.atleast_1d(axs), heights):
    k = int((zref + h - g.origin[2]) / g.res)
    ax.imshow(g.state[:, :, k].T, origin="lower", extent=ext, cmap=cmap, vmin=0, vmax=2,
              interpolation="nearest")
    ax.set_title(f"z = ref + {h:.2f} m")
    ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(out, dpi=80)
