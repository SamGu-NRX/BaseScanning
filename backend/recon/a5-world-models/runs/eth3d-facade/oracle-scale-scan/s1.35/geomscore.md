# Geometry score: eth3d-facade/A5-scan-1.35 vs eth3d-facade/gt-laser

Producer `A5-scan-1.35`. Status counts: {'scored': 6, 'missing': 51, 'extent_limited': 0, 'phantom': 7, 'scale_reference': 0}.

**Key distances (6 scored):** median 13.58 in, p90 26.99 in, max 27.17 in; ≤2 in 17%, ≤4 in 17%, ≤6 in 33%; within the recon's own bound 67%.

**Scale:** KD ratio median None (n=0, error None %), landmark Umeyama None. Alignment: {'metric_declared': False, 'method': 'given transform'}.

**Anchor:** ICP translation 0.1724 m [0.1462, -0.0241, -0.0882], yaw -0.212°, tilt 0.779°.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.5189 | -0.201 in | 0.202 | True | – / – |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | missing | 15.85 |  |   |  |  | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | missing | -12.2164 |  |   |  |  | – / – |
| `opening.edge_s[win1:right]` | missing | -10.6588 |  |   |  |  | – / – |
| `opening.bottom[win1]` | missing | 0.9144 |  |   |  |  | – / – |
| `opening.top[win1]` | missing | 3.3882 |  |   |  |  | – / – |
| `opening.width[win1]` | missing | 1.5576 |  |   |  |  | – / – |
| `opening.edge_s[win2:left]` | missing | -8.9181 |  |   |  |  | – / – |
| `opening.edge_s[win2:right]` | missing | -7.3281 |  |   |  |  | – / – |
| `opening.bottom[win2]` | missing | 0.8755 |  |   |  |  | – / – |
| `opening.top[win2]` | missing | 3.4292 |  |   |  |  | – / – |
| `opening.width[win2]` | missing | 1.59 |  |   |  |  | – / – |
| `opening.edge_s[win3:left]` | missing | -5.6655 |  |   |  |  | – / – |
| `opening.edge_s[win3:right]` | missing | -4.043 |  |   |  |  | – / – |
| `opening.bottom[win3]` | missing | 0.958 |  |   |  |  | – / – |
| `opening.top[win3]` | missing | 3.5282 |  |   |  |  | – / – |
| `opening.width[win3]` | missing | 1.6225 |  |   |  |  | – / – |
| `opening.edge_s[win4:left]` | missing | -2.4778 |  |   |  |  | – / – |
| `opening.edge_s[win4:right]` | missing | -0.7831 |  |   |  |  | – / – |
| `opening.bottom[win4]` | missing | 1.0116 |  |   |  |  | – / – |
| `opening.top[win4]` | missing | 3.5756 |  |   |  |  | – / – |
| `opening.width[win4]` | missing | 1.6947 |  |   |  |  | – / – |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.5576 | -8.575 in | 0.391 | True | – / – |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 1.7226 | -27.169 in | 0.391 | False | – / – |
| `opening.bottom[win5]` | scored | 1.0732 | 1.2043 | 5.161 in | 0.391 | True | – / – |
| `opening.top[win5]` | scored | 3.6508 | 2.97 | -26.803 in | 0.391 | False | – / – |
| `opening.width[win5]` | scored | 1.6373 | 1.165 | -18.594 in | 0.782 | True | – / – |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / – |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / – |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / – |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / – |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / – |
| `opening.edge_s[door:left]` | missing | 7.2624 |  |   |  |  | – / – |
| `opening.edge_s[door:right]` | missing | 9.4522 |  |   |  |  | – / – |
| `opening.bottom[door]` | missing | 0.2116 |  |   |  |  | – / – |
| `opening.top[door]` | missing | 4.074 |  |   |  |  | – / – |
| `opening.width[door]` | missing | 2.1898 |  |   |  |  | – / – |
| `opening.gap[win1~win2]` | missing | 1.7407 |  |   |  |  | – / – |
| `opening.gap[win2~win3]` | missing | 1.6626 |  |   |  |  | – / – |
| `opening.gap[win3~win4]` | missing | 1.5652 |  |   |  |  | – / – |
| `opening.gap[win4~win5]` | missing | 1.5585 |  |   |  |  | – / – |
| `opening.gap[win5~win_w2_arch]` | missing | 0.91 |  |   |  |  | – / – |
| `opening.gap[win_w2_arch~door]` | missing | 2.2633 |  |   |  |  | – / – |
| `facing.depth[w1@2.79]` | missing | 0.309 |  |   |  |  | – / – |
| `facing.depth[w2@3.14]` | missing | 0.315 |  |   |  |  | – / – |
| `facing.depth[w3@6.68]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@6.93]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@9.94]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.19]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.44]` | missing | 0.708 |  |   |  |  | – / – |
| `overhead.clearance[w1@2.79]` | missing | 2.136 |  |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 13.5552 |   |  |  | – / – |
| `opening.edge_s[rc:door:left]` | phantom |  | 3.2518 |   |  |  | – / – |
| `opening.edge_s[rc:door:right]` | phantom |  | 5.8816 |   |  |  | – / – |
| `opening.bottom[rc:door]` | phantom |  | 0.085 |   |  |  | – / – |
| `opening.top[rc:door]` | phantom |  | 4.1502 |   |  |  | – / – |
| `opening.width[rc:door]` | phantom |  | 2.6298 |   |  |  | – / – |
| `opening.gap[win5~rc:door]` | phantom |  | 1.5292 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 1%/4%/13% | 1%/5%/13% | 0.4974 | None | None | None, None, None | None | None |
| wall_face | 58856 | 1%/6%/17% | 2%/8%/16% | 0.515 | None | None | None, None, None | None | None |
| ground_strip | 206971 | 19%/49%/80% | 28%/53%/79% | 0.0383 | None | None | None, None, None | None | None |
| overhead | 1602 | 1%/6%/28% | 0%/0%/19% | 0.664 | None | None | None, None, None | None | None |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera None, in own unknown voxels 0.9755, flagged generated 0.0.
