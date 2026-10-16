"""Top-down picture of a C2 sweep: grid slice 0.5 m above the terrain (grey unknown, white free,
black occupied), candidate spots coloured by outcome (or by one check with --check), labelled
objects, the meter, the reference wall chain and the best spot's route.

    python plot_sweep.py SCENE.json GRID.npz SWEEP.json OUT.png [--check ID] [--xlim a,b --ylim a,b]
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c2vol.grid import Grid  # noqa: E402
from c2vol.sweep import Maps, effective_state  # noqa: E402

COL = {"pass": "#1a9850", "unsure": "#fdae61", "fail": "#d73027"}
OBJ = {"window": "#4575b4", "door": "#762a83", "garage_door": "#762a83", "gas_meter": "#e08214",
       "ac": "#35978f", "elec_box": "#8c510a", "fence": "#666666", "driveway": "#bababa", "pool": "#74add1"}

ap = argparse.ArgumentParser()
ap.add_argument("scene")
ap.add_argument("grid")
ap.add_argument("sweep")
ap.add_argument("out")
ap.add_argument("--check")
ap.add_argument("--xlim")
ap.add_argument("--ylim")
ap.add_argument("--title", default="")
a = ap.parse_args()
sc = json.loads(Path(a.scene).read_text())
sw = json.loads(Path(a.sweep).read_text())
g0 = Grid.load(a.grid)
g = Grid(g0.origin, g0.res, effective_state(g0.state)[0])
M = Maps(g)
kz = np.arange(g.shape[2])
zc = g.origin[2] + (kz + 0.5) * g.res
k = np.clip(np.round((M.terrain + 0.5 - g.origin[2]) / g.res - 0.5).astype(int), 0, g.shape[2] - 1)
sl = np.take_along_axis(g.state, k[:, :, None], 2)[:, :, 0]
fig, ax = plt.subplots(figsize=(12, 11))
ext = [g.origin[0], g.origin[0] + g.shape[0] * g.res, g.origin[1], g.origin[1] + g.shape[1] * g.res]
cmap = matplotlib.colors.ListedColormap(["#bdbdbd", "#ffffff", "#000000"])
ax.imshow(sl.T, origin="lower", extent=ext, cmap=cmap, vmin=0, vmax=2, interpolation="nearest")
for o in sc.get("objects", []):
    P = np.array(o["plan_polygon"] + o["plan_polygon"][:1])
    ax.fill(P[:, 0], P[:, 1], color=OBJ.get(o["type"], "#999999"), alpha=0.6)
    ax.plot(P[:, 0], P[:, 1], color=OBJ.get(o["type"], "#999999"), lw=1.5)
    c = P[:-1].mean(0)
    ax.annotate(o["id"] + ("" if o.get("real", True) else " (inv.)"), c, fontsize=6, color="navy")
for w in (sc.get("reference_chain") or {}).get("walls", []):
    ax.plot([w["a"][0], w["b"][0]], [w["a"][1], w["b"][1]], "--", color="purple", lw=0.8)
    ax.annotate(w["id"], ((w["a"][0] + w["b"][0]) / 2, (w["a"][1] + w["b"][1]) / 2), color="purple", fontsize=8)
for s in sw["spots"]:
    if a.check:
        c = next((c for c in s["checks"] if c["id"] == a.check), None)
        oc = c["outcome"] if c else "pass"
    else:
        oc = s["outcome"]
    x, y = s["rear_mid_xy"]
    ax.plot(x, y, "o", ms=2.2, color=COL[oc])
b = sw.get("best")
if b:
    F = np.array(b["footprint_xy"] + b["footprint_xy"][:1])
    ax.plot(F[:, 0], F[:, 1], color=COL[b["outcome"]], lw=2)
    if b["route"].get("polyline_xy"):
        R = np.array(b["route"]["polyline_xy"])
        ax.plot(R[:, 0], R[:, 1], color="magenta", lw=1.2)
m = sc["meter"]["pos"]
ax.plot(m[0], m[1], "*", ms=14, color="gold", mec="k")
if a.xlim:
    ax.set_xlim(*map(float, a.xlim.split(",")))
if a.ylim:
    ax.set_ylim(*map(float, a.ylim.split(",")))
ax.set_aspect("equal")
ax.set_title(a.title or f"{sw['scene']} {sw['grid']} {sw['rules']}: {sw['decision']} "
             f"({sw['counts']}){' check=' + a.check if a.check else ''}", fontsize=9)
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(a.out, dpi=90)
