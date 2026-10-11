# Fact-by-capability table

Compatible-world counts per fact. A count of 1 means every grid world compatible
with the observations agrees on the fact (supported). A count above 1 means the
observations fit that many distinct fact values (UNKNOWN). A cell marked
UNKNOWN (family) means the grid left one compatible world but the continuous
family probe refit a materially different world with the identical observation:
the count of 1 is a grid artifact, not identification (README.md, A5).

| fact | one-view | two-view | two-view-pan-only | two-view-top-blind | one-view-tops-only | two-view-end-blind |
|---|---|---|---|---|---|---|
| orientation (wall yaw) | 1 — supported | 1 — supported | 1 — supported | 1 — supported | 1 — supported | 1 — supported |
| distance (face offset from the meter) | 1 — supported | 1 — supported | 1 — supported | 1 — supported | UNKNOWN (family) | 1 — supported |
| extent, left end (s0) | 1 — supported | 1 — supported | 1 — supported | 1 — supported | UNKNOWN (family) | 1 — supported |
| extent, right end (s1) | 1 — supported | 1 — supported | 1 — supported | 1 — supported | UNKNOWN (family) | UNKNOWN (3) |
| height (wall top) | 1 — supported | 1 — supported | 1 — supported | UNKNOWN (3) | UNKNOWN (family) | 1 — supported |

## Extra actions that distinguish the worlds

- **one-view**: nothing to distinguish; every fact already supported.
- **two-view**: nothing to distinguish; every fact already supported.
- **two-view-pan-only**: nothing to distinguish; every fact already supported.
- **two-view-top-blind** — two keyframes from distinct positions, pitched down: the wall top never enters the frame
  - height (wall top): UNKNOWN — 3 distinct grid values fit. Equivalent pair: yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 10.0] ft, top 9.0 ft vs yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 10.0] ft, top 8.0 ft.
    Actions that distinguish the worlds: end_approach, pan_pair, stereo_step, tilt_pair.
- **one-view-tops-only** — one keyframe aimed above the wall: only the top corners are in frame, no ground landmark ever is
  - distance (face offset from the meter): UNKNOWN — the grid left one compatible world but a continuous family of worlds shares the observation. Equivalent pair: yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 10.0] ft, top 9.0 ft vs yaw -0.0 deg, face 23.9 ft from the meter, s in [-12.8, 12.8] ft, top 10.1 ft.
    Actions that distinguish the worlds: end_approach, stereo_step, tilt_pair.
  - extent, left end (s0): UNKNOWN — the grid left one compatible world but a continuous family of worlds shares the observation. Equivalent pair: yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 10.0] ft, top 9.0 ft vs yaw -0.0 deg, face 23.9 ft from the meter, s in [-12.8, 12.8] ft, top 10.1 ft.
    Actions that distinguish the worlds: end_approach, stereo_step, tilt_pair.
  - extent, right end (s1): UNKNOWN — the grid left one compatible world but a continuous family of worlds shares the observation. Equivalent pair: yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 10.0] ft, top 9.0 ft vs yaw -0.0 deg, face 23.9 ft from the meter, s in [-12.8, 12.8] ft, top 10.1 ft.
    Actions that distinguish the worlds: end_approach, stereo_step, tilt_pair.
  - height (wall top): UNKNOWN — the grid left one compatible world but a continuous family of worlds shares the observation. Equivalent pair: yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 10.0] ft, top 9.0 ft vs yaw -0.0 deg, face 23.9 ft from the meter, s in [-12.8, 12.8] ft, top 10.1 ft.
    Actions that distinguish the worlds: end_approach, stereo_step, tilt_pair.
- **two-view-end-blind** — two keyframes from distinct positions aimed left of the right end
  - extent, right end (s1): UNKNOWN — 3 distinct grid values fit. Equivalent pair: yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 16.0] ft, top 9.0 ft vs yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 12.0] ft, top 9.0 ft.
    Actions that distinguish the worlds: end_approach, stereo_step.

## Unknown outcomes

6 fact-by-capability cells stay UNKNOWN: the observations fit more than one fact value there. Counts are grid-relative (README.md, assumption A5).
