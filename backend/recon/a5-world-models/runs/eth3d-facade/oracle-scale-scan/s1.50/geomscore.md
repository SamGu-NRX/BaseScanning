# Geometry score: eth3d-facade/A5-scan-1.50 vs eth3d-facade/gt-laser

Producer `A5-scan-1.50`. Status counts: {'scored': 23, 'missing': 33, 'extent_limited': 1, 'phantom': 13, 'scale_reference': 0}.

**Key distances (23 scored):** median 14.97 in, p90 29.45 in, max 56.77 in; ≤2 in 0%, ≤4 in 0%, ≤6 in 9%; within the recon's own bound 74%.

**Scale:** KD ratio median 0.88817 (n=4, error -11.183 %), landmark Umeyama 0.98803. Alignment: {'metric_declared': False, 'method': 'given transform'}.

**Anchor:** ICP translation 0.1746 m [0.0697, -0.0692, -0.1444], yaw -0.053°, tilt 1.041°.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.6934 | 6.669 in | 0.216 | True | – / – |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 25.8381 | 393.232 in | 0.27 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | missing | -12.2164 |  |   |  |  | – / – |
| `opening.edge_s[win1:right]` | missing | -10.6588 |  |   |  |  | – / – |
| `opening.bottom[win1]` | missing | 0.9144 |  |   |  |  | – / – |
| `opening.top[win1]` | missing | 3.3882 |  |   |  |  | – / – |
| `opening.width[win1]` | missing | 1.5576 |  |   |  |  | – / – |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.8006 | 4.626 in | 0.321 | True | – / – |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -7.9822 | -25.752 in | 0.321 | False | – / – |
| `opening.bottom[win2]` | scored | 0.8755 | 1.5244 | 25.547 in | 0.321 | False | – / – |
| `opening.top[win2]` | scored | 3.4292 | 3.0716 | -14.079 in | 0.321 | False | – / – |
| `opening.width[win2]` | scored | 1.59 | 0.8184 | -30.378 in | 0.642 | False | – / – |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -4.2236 | 56.768 in | 1.469 | True | – / – |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.192 | 33.504 in | 1.469 | True | – / – |
| `opening.bottom[win3]` | scored | 0.958 | 1.4422 | 19.063 in | 1.469 | True | – / – |
| `opening.top[win3]` | scored | 3.5282 | 3.1851 | -13.508 in | 1.469 | True | – / – |
| `opening.width[win3]` | scored | 1.6225 | 1.0316 | -23.264 in | 2.938 | True | – / – |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -1.8237 | 25.752 in | 0.466 | False | – / – |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.6505 | 5.22 in | 0.466 | True | – / – |
| `opening.bottom[win4]` | scored | 1.0116 | 1.3938 | 15.047 in | 0.466 | True | – / – |
| `opening.top[win4]` | scored | 3.5756 | 3.1954 | -14.969 in | 0.466 | True | – / – |
| `opening.width[win4]` | scored | 1.6947 | 1.1732 | -20.531 in | 0.932 | True | – / – |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.6169 | -6.24 in | 0.44 | True | – / – |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 1.9053 | -19.976 in | 0.44 | False | – / – |
| `opening.bottom[win5]` | scored | 1.0732 | 1.347 | 10.78 in | 0.44 | True | – / – |
| `opening.top[win5]` | scored | 3.6508 | 3.2927 | -14.098 in | 0.44 | True | – / – |
| `opening.width[win5]` | scored | 1.6373 | 1.2884 | -13.736 in | 0.88 | True | – / – |
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
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.3683 | -7.752 in | 1.935 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.2674 | -11.461 in | 0.906 | True | – / – |
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
| `opening.edge_s[rc:win2:left]` | phantom |  | -6.6457 |   |  |  | – / – |
| `opening.edge_s[rc:win2:right]` | phantom |  | -5.72 |   |  |  | – / – |
| `opening.bottom[rc:win2]` | phantom |  | 1.4504 |   |  |  | – / – |
| `opening.top[rc:win2]` | phantom |  | 3.1188 |   |  |  | – / – |
| `opening.width[rc:win2]` | phantom |  | 0.9257 |   |  |  | – / – |
| `opening.edge_s[rc:door:left]` | phantom |  | 3.6044 |   |  |  | – / – |
| `opening.edge_s[rc:door:right]` | phantom |  | 6.5469 |   |  |  | – / – |
| `opening.bottom[rc:door]` | phantom |  | 0.1048 |   |  |  | – / – |
| `opening.top[rc:door]` | phantom |  | 4.6102 |   |  |  | – / – |
| `opening.width[rc:door]` | phantom |  | 2.9425 |   |  |  | – / – |
| `opening.gap[win2~rc:win2]` | phantom |  | 1.3365 |   |  |  | – / – |
| `opening.gap[rc:win2~win3]` | phantom |  | 1.4964 |   |  |  | – / – |
| `opening.gap[win5~rc:door]` | phantom |  | 1.6991 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 1%/5%/13% | 1%/4%/12% | 0.6746 | None | None | None, None, None | None | None |
| wall_face | 58856 | 1%/7%/17% | 2%/7%/15% | 0.6619 | None | None | None, None, None | None | None |
| ground_strip | 206971 | 16%/46%/83% | 27%/52%/76% | 0.0387 | None | None | None, None, None | None | None |
| overhead | 1602 | 0%/8%/33% | 0%/1%/13% | 0.8485 | None | None | None, None, None | None | None |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera None, in own unknown voxels 0.9643, flagged generated 0.0.
