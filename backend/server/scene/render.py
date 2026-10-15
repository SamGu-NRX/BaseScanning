"""Plan-view coverage picture: observed voxels, chain walls, cameras."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def render_topdown(scene: dict, grid, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = grid.state
    # y index nearest 1.0 m
    y = int(np.clip(round((1.0 - float(grid.origin[1])) / grid.voxel), 0, state.shape[1] - 1))
    slab = state[:, y, :]
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.imshow(
        slab.T > 0,
        origin="lower",
        extent=(
            float(grid.origin[0]),
            float(grid.origin[0] + slab.shape[0] * grid.voxel),
            float(grid.origin[2]),
            float(grid.origin[2] + slab.shape[1] * grid.voxel),
        ),
        cmap="Greens",
        vmin=0,
        vmax=1,
        interpolation="nearest",
    )
    for wall in scene.get("walls", []):
        (x0, z0), (x1, z1) = wall["baseline_m"]
        style = "--" if wall.get("evidence") == "inferred" else "-"
        ax.plot([x0, x1], [z0, z1], style, color="black", lw=2)
    for frame in scene.get("keyframes") or []:
        pose = np.asarray(frame["pose"], float).reshape(4, 4, order="F")
        ax.scatter([pose[0, 3]], [pose[2, 3]], c="red", s=12)
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("z (m)")
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)
