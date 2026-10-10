# Fact-by-capability table

Compatible-world counts per fact. A count of 1 means every grid world compatible
with the observations agrees on the fact (supported). A count above 1 means the
observations fit that many distinct fact values (UNKNOWN).

| fact | one-view | two-view | two-view-pan-only | two-view-top-blind | two-view-end-blind |
|---|---|---|---|---|---|
| orientation (wall yaw) | 1 — supported | 1 — supported | 1 — supported | 1 — supported | 1 — supported |
| distance (face offset from the meter) | 1 — supported | 1 — supported | 1 — supported | 1 — supported | 1 — supported |
| extent, left end (s0) | 1 — supported | 1 — supported | 1 — supported | 1 — supported | 1 — supported |
| extent, right end (s1) | 1 — supported | 1 — supported | 1 — supported | 1 — supported | UNKNOWN (3) |
| height (wall top) | 1 — supported | 1 — supported | 1 — supported | UNKNOWN (3) | 1 — supported |

## Extra actions that distinguish the worlds

- **one-view**: nothing to distinguish; every fact already supported.
- **two-view**: nothing to distinguish; every fact already supported.
- **two-view-pan-only**: nothing to distinguish; every fact already supported.
- **two-view-top-blind** — two keyframes from distinct positions, pitched down: the wall top never enters the frame
  - height (wall top): UNKNOWN, 3 distinct values fit. Equivalent pair: yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 10.0] ft, top 9.0 ft vs yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 10.0] ft, top 8.0 ft.
    Actions that distinguish the worlds: end_approach, pan_pair, stereo_step, tilt_pair.
- **two-view-end-blind** — two keyframes from distinct positions aimed left of the right end
  - extent, right end (s1): UNKNOWN, 3 distinct values fit. Equivalent pair: yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 16.0] ft, top 9.0 ft vs yaw +0.0 deg, face 20.0 ft from the meter, s in [-10.0, 12.0] ft, top 9.0 ft.
    Actions that distinguish the worlds: end_approach, stereo_step.

## Unknown outcomes

2 fact-by-capability cells stay UNKNOWN: the observations fit more than one fact value there. Counts are grid-relative (README.md, assumption A5).
