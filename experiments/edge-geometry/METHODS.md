# Edge geometry: methods and full results

The README is the front door. This file keeps the method as it was pre-registered in 397507f, and the full result as the run reported it.

## Question

Clearances start at edges: a window jamb, the side of an AC unit. Learned depth is 8 in or worse there. How much of that error lands along the wall, where the rules measure, and how does it depend on the angle a photo sees the edge from? Can an edge be placed better without reading depth at it?

The data is ETH3D electro and facade: photos within 6 m, and the evals' depth-edge points taken on their near side, so they are object edges rather than the wall behind them. The points are split into in-plane edges, within 5 cm of a wall plane, and proud edges. "Along the wall" means the horizontal component of the error within that wall plane. The view angle is measured between the ray and the wall's normal.

- **M0, depth at the pixel.** MoGe-2 depth, with each photo's scale set from the laser scan so that only the edge's own error remains.
- **M1, ray to the wall plane.** The plane is fitted to the same photo's depth on wall interior pixels.
- **M2, tap, snap and median.** A tap is jittered by σ = 10 px, snapped to the strongest vertical image edge within 25 px, and intersected with the wall plane. The median is taken over every photo that sees the point.
- **M3, two photos.** The edge is matched along the epipolar line by patch correlation and triangulated.

- **M1-anchor, the app's way.** The app intersects every feature tap with one plane, built from its single raycast at the meter (`ScanEngine+Actions.swift` on `t3/ios-mvf`). M1-anchor fits that plane to a 0.3 m patch around a wall point 1 to 3 m from the edge. The fit's yaw error ψ should move the edge by about s·ψ·tan(view angle), which is the suspected cause of #30.

M2 and M3 run with `exact` and `modern_assumed` poses.

## Pass criteria, fixed before the run

- **P1, angle.** M0's along-wall p90 at 0–15° is at most half its p90 beyond 30°.
- **P2.** M1's along-wall p90 on in-plane edges is at most 4 in. Reported beside it, not graded: M1-anchor, and its error against s·ψ·tan(view angle).
- **P3.** With `modern_assumed` poses, M2's along-wall p90 on in-plane edges is at most 2 in.
- **P4.** With `modern_assumed` poses, M3's 3-D p90 is at most 2 in, and at least 70% of points match.
- **Kill.** If M0 is already within 4 in along the wall at every angle, edges don't limit along-wall clearances.

## Result

Electro: 3,000 edge points on 21 laser wall planes, 2,258 in-plane and 742 proud. Numbers are p90 in inches, from `results/edge_geometry.md`. M0 to M2 use the oracle scale and take wall pixels from the laser planes.

- **P1 passes.** M0 along the wall is 1.4 at 0–15° and 19.8 beyond 30°.
- **P2 fails.** M1 is 6.7: 0.5 at 0–15°, 15.0 beyond 45°. M1-anchor is 10.4. Its yaw error ψ has a median of 2.2° and a p90 of 6.7°. The error's median ratio to s·ψ·tan(angle) is 0.94, and the signs agree 74% of the time, but the signed fit explains only 2% of the variance.
- **P3 fails.** M2 is 12.6 (95% CI 11.7–13.8).
- **P4 fails.** 41% of points match, and the anchor-to-edge span's 3-D p90 is 155.
- **Kill** is not triggered: 1.4, 4.6, 8.9 and 24.4 by angle bin.

Added after the run: M2 with the true pixel still gives 11.5. The MoGe-2 wall plane misses the edge point by 10.0 at p90, where the laser plane misses it by 1.4. A snap started at the true pixel moves 8.8 px on vertical edges, so the snap's pixel error is ill-posed for these points. M3 matches within 2 px give 2.9 with exact poses and 8.9 with `modern_assumed`. Facade has 866 points but no wall within 6 m, so only M0 runs there.

Added after review: M3 pairs each point with the narrowest qualifying ray angle (at least 5°), which may triangulate worse than a wider pair. `uv run python run.py --pair=widest` reruns it with the widest angle ([results/edge_geometry_widest_pair.md](results/edge_geometry_widest_pair.md)). On electro it is no better: with `modern_assumed` poses M3's 3-D p90 is 158.7 in against 154.6, and 41% of points match either way. With exact poses the wide pair is worse, 156.4 in against 112.5, because fewer of its matches land within 2 px (35% against 49%). P4 fails under both, so the narrow pair is not what makes M3 look bad. The single-photo tables came out identical to the committed ones.

Run: `uv run python run.py`. It takes 2 minutes and 0.9 GB. It needs the evals checkout at `190ed33` (`EDGE_EVALS_DIR`) and its ETH3D, MoGe-2 and pose caches.

What it changes: the wall plane's depth error times tan(view angle) sets the edge error, so get the edge photo within 30° of head-on. A snapped tap can't beat the plane's error, and patch matching at edges fails on more than half the points.
