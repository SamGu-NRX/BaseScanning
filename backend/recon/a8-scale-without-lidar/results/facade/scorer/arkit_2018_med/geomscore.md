# Geometry score: eth3d-facade/A8-arkit_2018_med vs eth3d-facade/gt-laser

Producer `A8-arkit_2018_med`. Status counts: {'scored': 35, 'missing': 21, 'extent_limited': 1, 'phantom': 10, 'scale_reference': 0}.

**Key distances (35 scored):** median 6.67 in, p90 30.98 in, max 165.87 in; ≤2 in 3%, ≤4 in 11%, ≤6 in 46%; within the recon's own bound 69%.

**Scale:** KD ratio median 0.9322 (n=8, error -6.78 %), landmark Umeyama 0.93156. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 1.075269, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': -7.0, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 0.6992, 'cameras': 76}.

**Anchor:** ICP translation 0.0901 m [-0.0151, 0.0679, -0.0573], yaw -0.183°, tilt 0.623°.

**On truly observed elements:** 31 KDs, median 8.24 in, p90 32.6 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.3146 | -8.244 in | 0.247 | True | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 14.6554 | -47.031 in | 1.07 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -11.3883 | 32.602 in | 0.543 | False | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -9.9334 | 28.559 in | 0.543 | False | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.8649 | -1.949 in | 0.543 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 3.1529 | -9.264 in | 0.543 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.4549 | -4.043 in | 1.086 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.3133 | 23.811 in | 0.451 | False | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -6.8289 | 19.654 in | 0.451 | False | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.7999 | -2.976 in | 0.451 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 3.1615 | -10.539 in | 0.451 | True | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.4844 | -4.157 in | 0.902 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.2831 | 15.055 in | 0.36 | False | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.7691 | 10.783 in | 0.36 | True | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.8482 | -4.323 in | 0.36 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.2247 | -11.949 in | 0.36 | True | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.514 | -4.272 in | 0.72 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.311 | 6.567 in | 0.271 | True | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.7314 | 2.035 in | 0.271 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 0.8705 | -5.555 in | 0.271 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.2399 | -13.217 in | 0.271 | False | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.5796 | -4.531 in | 0.542 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.7191 | -2.217 in | 0.269 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.2434 | -6.665 in | 0.269 | True | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 0.8995 | -6.839 in | 0.269 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.28 | -14.598 in | 0.269 | False | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.5243 | -4.449 in | 0.538 | True | observed / observed |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / grazing |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / grazing |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / grazing |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / grazing |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / grazing |
| `opening.edge_s[door:left]` | scored | 7.2624 | 9.8394 | 101.457 in | 0.395 | False | grazing / observed |
| `opening.edge_s[door:right]` | scored | 9.4522 | 7.8161 | -64.413 in | 0.395 | False | grazing / observed |
| `opening.bottom[door]` | scored | 0.2116 | 0.0623 | -5.878 in | 0.395 | True | grazing / observed |
| `opening.top[door]` | scored | 4.074 | 3.6169 | -17.996 in | 0.395 | False | grazing / observed |
| `opening.width[door]` | scored | 2.1898 | -2.0233 | -165.87 in | 0.79 | False | grazing / observed |
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.6201 | -4.748 in | 0.994 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.5458 | -4.598 in | 0.811 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.4581 | -4.217 in | 0.631 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.4505 | -4.252 in | 0.54 | True | – / – |
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
| `corner.s[rc:c1]` | phantom |  | -12.1215 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 90.3 |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | 2.5339 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | 44.18 |   |  |  | – / – |
| `corner.s[rc:c3]` | phantom |  | 7.2891 |   |  |  | – / – |
| `corner.turn[rc:c3]` | phantom |  | 135.94 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 6.8844 |   |  |  | – / – |
| `wall.length[rc:w3]` | phantom |  | 4.7553 |   |  |  | – / – |
| `wall.length[rc:w4]` | phantom |  | 3.1485 |   |  |  | – / – |
| `opening.gap[win5~door]` | phantom |  | 7.596 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 6%/45%/86% | 18%/55%/89% | 0.3593 | 0.9995 | 0.0003 | observed, 0.9992, 0.0007 | 0.9912 | 0.0008 |
| wall_face | 58856 | 5%/38%/74% | 16%/47%/77% | 0.3442 | 0.902 | 0.0106 | grazing, 0.8953, 0.0664 | 0.8918 | 0.0056 |
| ground_strip | 206971 | 0%/0%/3% | 0%/1%/3% | 0.1671 | 0.0327 | 0.1664 | partial, 0.1921, 0.3229 | 0.2103 | 0.1048 |
| overhead | 1602 | 0%/0%/9% | 0%/0%/10% | 0.498 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9921 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0299, in own unknown voxels 0.1092, flagged generated 0.0.
