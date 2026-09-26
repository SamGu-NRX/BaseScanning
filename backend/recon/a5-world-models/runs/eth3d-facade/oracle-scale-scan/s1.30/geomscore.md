# Geometry score: eth3d-facade/A5-scan-1.30 vs eth3d-facade/gt-laser

Producer `A5-scan-1.30`. Status counts: {'scored': 33, 'missing': 23, 'extent_limited': 1, 'phantom': 4, 'scale_reference': 0}.

**Key distances (32 scored):** median 3.94 in, p90 37.38 in, max 608.47 in; ≤2 in 19%, ≤4 in 50%, ≤6 in 72%; within the recon's own bound 75%.

**Scale:** KD ratio median 0.91195 (n=8, error -8.805 %), landmark Umeyama 0.9428. Alignment: {'metric_declared': False, 'method': 'given transform'}.

**Anchor:** ICP translation 0.2045 m [0.1742, -0.0761, 0.0753], yaw -0.29°, tilt 1.252°.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.4646 | -2.339 in | 0.187 | True | – / – |
| `corner.s[c1]` | scored | 3.0179 | 2.5614 | -17.972 in | 0.23 | False | – / – |
| `corner.turn[c1]` | scored | 91.11 | 77.7 | -13.41 deg | 13.604 | True | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 11.9419 | -153.862 in | 0.218 | False | – / – |
| `wall.length[w2]` | scored | 3.5426 | 2.5384 | -39.536 in | 0.242 | False | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | missing | -12.2164 |  |   |  |  | – / – |
| `opening.edge_s[win1:right]` | missing | -10.6588 |  |   |  |  | – / – |
| `opening.bottom[win1]` | missing | 0.9144 |  |   |  |  | – / – |
| `opening.top[win1]` | missing | 3.3882 |  |   |  |  | – / – |
| `opening.width[win1]` | missing | 1.5576 |  |   |  |  | – / – |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.7676 | 5.925 in | 0.465 | True | – / – |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -7.2318 | 3.791 in | 0.465 | True | – / – |
| `opening.bottom[win2]` | scored | 0.8755 | 0.9779 | 4.031 in | 0.465 | True | – / – |
| `opening.top[win2]` | scored | 3.4292 | 3.4035 | -1.012 in | 0.465 | True | – / – |
| `opening.width[win2]` | scored | 1.59 | 1.5358 | -2.134 in | 0.93 | True | – / – |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.5248 | 5.539 in | 0.502 | True | – / – |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.9622 | 3.181 in | 0.502 | True | – / – |
| `opening.bottom[win3]` | scored | 0.958 | 1.0327 | 2.941 in | 0.502 | True | – / – |
| `opening.top[win3]` | scored | 3.5282 | 3.4523 | -2.988 in | 0.502 | True | – / – |
| `opening.width[win3]` | scored | 1.6225 | 1.5626 | -2.358 in | 1.004 | True | – / – |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.3496 | 5.047 in | 0.153 | True | – / – |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.7608 | 0.878 in | 0.153 | True | – / – |
| `opening.bottom[win4]` | scored | 1.0116 | 1.0371 | 1.004 in | 0.153 | True | – / – |
| `opening.top[win4]` | scored | 3.5756 | 3.4049 | -6.72 in | 0.153 | False | – / – |
| `opening.width[win4]` | scored | 1.6947 | 1.5888 | -4.169 in | 0.306 | True | – / – |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.6776 | -3.85 in | 0.177 | True | – / – |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.1973 | -8.48 in | 0.177 | False | – / – |
| `opening.bottom[win5]` | scored | 1.0732 | 1.0358 | -1.472 in | 0.177 | True | – / – |
| `opening.top[win5]` | scored | 3.6508 | 3.4099 | -9.484 in | 0.177 | False | – / – |
| `opening.width[win5]` | scored | 1.6373 | 1.5197 | -4.63 in | 0.354 | True | – / – |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / – |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / – |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / – |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / – |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / – |
| `opening.edge_s[door:left]` | scored | 7.2624 | 5.3539 | -75.138 in | 0.454 | False | – / – |
| `opening.edge_s[door:right]` | scored | 9.4522 | 7.6403 | -71.335 in | 0.454 | False | – / – |
| `opening.bottom[door]` | scored | 0.2116 | 0.1161 | -3.76 in | 0.454 | True | – / – |
| `opening.top[door]` | scored | 4.074 | 3.8525 | -8.72 in | 0.454 | True | – / – |
| `opening.width[door]` | scored | 2.1898 | 2.2864 | 3.803 in | 0.908 | True | – / – |
| `opening.gap[win1~win2]` | missing | 1.7407 |  |   |  |  | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.707 | 1.748 in | 0.967 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.6126 | 1.866 in | 0.655 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.4384 | -4.728 in | 0.33 | True | – / – |
| `opening.gap[win5~win_w2_arch]` | missing | 0.91 |  |   |  |  | – / – |
| `opening.gap[win_w2_arch~door]` | missing | 2.2633 |  |   |  |  | – / – |
| `facing.depth[w1@2.79]` | missing | 0.309 |  |   |  |  | – / – |
| `facing.depth[w2@3.14]` | scored | 0.315 | 15.77 | 608.465 in | 0.05 | False | – / – |
| `facing.depth[w3@6.68]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@6.93]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@9.94]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.19]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.44]` | missing | 0.708 |  |   |  |  | – / – |
| `overhead.clearance[w1@2.79]` | missing | 2.136 |  |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | 5.0998 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | -84.39 |   |  |  | – / – |
| `wall.length[rc:w3]` | phantom |  | 5.3092 |   |  |  | – / – |
| `opening.gap[win5~door]` | phantom |  | 3.1566 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 11%/33%/76% | 14%/39%/83% | 0.9018 | None | None | None, None, None | None | None |
| wall_face | 58856 | 13%/34%/73% | 15%/40%/79% | 0.8284 | None | None | None, None, None | None | None |
| ground_strip | 206971 | 36%/72%/99% | 29%/52%/67% | 0.1097 | None | None | None, None, None | None | None |
| overhead | 1602 | 0%/4%/25% | 0%/0%/15% | 0.9173 | None | None | None, None, None | None | None |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera None, in own unknown voxels 0.706, flagged generated 0.0.
