# Edge geometry

## Question

Clearances start at edges, and learned depth is 8 in or worse there. How much of that error lands along the wall, and how does it depend on the angle a photo sees the edge from? It compares four ways to place an edge (M0 to M3), plus the app's plane from one patch (M1-anchor), on ETH3D electro's laser truth. [METHODS.md](METHODS.md) defines them.

## Pass criteria, fixed before the run (397507f)

- **P1, angle.** M0's along-wall p90 at 0–15° is at most half its p90 beyond 30°.
- **P2.** M1's along-wall p90 on in-plane edges is at most 4 in. Reported beside it, not graded: M1-anchor, and its error against s·ψ·tan(view angle).
- **P3.** With `modern_assumed` poses, M2's along-wall p90 on in-plane edges is at most 2 in.
- **P4.** With `modern_assumed` poses, M3's 3-D p90 is at most 2 in, and at least 70% of points match.
- **Kill.** If M0 is already within 4 in along the wall at every angle, edges don't limit along-wall clearances.

## Result

P1 passes; P2 to P4 fail ([tables](results/edge_geometry.md)).

- **Angle.** In-plane edges are 0.9 in p90 along the wall within 15° of face-on and 24 in beyond 45°.
- **Ray to plane (M1).** 6.7 in overall, 0.5 in within 15°.
- **The app's plane (M1-anchor).** Its yaw error is 2.2° median and 6.7° p90.
- **M2 and M3.** Tap, snap and median gives 12.6 in, because the learned plane's own error dominates. Two-photo matching finds 41% of points. Pairing each point with the widest photo pair instead of the narrowest is no better (added after review).

**Run:** `uv run python run.py`: 2 min, 0.9 GB. Needs the evals checkout at 190ed33 and its ETH3D and MoGe-2 caches.

**What it changes:** ask for each edge from within about 20° of face-on, record the tap's view angle, and fit the wall plane across the walk.
