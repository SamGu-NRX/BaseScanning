# Geometry score: eth3d-facade/A8-tape vs eth3d-facade/gt-laser

Producer `A8-tape`. Status counts: {'scored': 31, 'missing': 25, 'extent_limited': 1, 'phantom': 3, 'scale_reference': 0}.

**Key distances (31 scored):** median 1.18 in, p90 11.74 in, max 25.24 in; ≤2 in 55%, ≤4 in 77%, ≤6 in 77%; within the recon's own bound 97%.

**Scale:** KD ratio median 1.00749 (n=6, error 0.749 %), landmark Umeyama 1.00756. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 0.994036, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': 0.6, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 0.0624, 'cameras': 76}.

**Anchor:** ICP translation 0.0793 m [-0.0256, 0.0457, -0.0595], yaw -0.16°, tilt 0.088°.

**On truly observed elements:** 26 KDs, median 1.91 in, p90 11.72 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.4624 | -2.425 in | 0.253 | True | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 16.0713 | 8.713 in | 1.142 | True | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -12.31 | -3.685 in | 0.577 | True | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -10.7372 | -3.087 in | 0.577 | True | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.5147 | -15.736 in | 0.577 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 2.9879 | -15.76 in | 0.577 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.5728 | 0.598 in | 1.154 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.9855 | -2.654 in | 0.477 | True | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -7.381 | -2.083 in | 0.477 | True | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.5782 | -11.705 in | 0.477 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 3.131 | -11.74 in | 0.477 | True | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.6045 | 0.571 in | 0.954 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.7094 | -1.728 in | 0.379 | True | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -4.073 | -1.181 in | 0.379 | True | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.7623 | -7.705 in | 0.379 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.331 | -7.764 in | 0.379 | True | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.6364 | 0.547 in | 0.758 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.4968 | -0.748 in | 0.283 | True | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.7894 | -0.248 in | 0.283 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 0.9165 | -3.744 in | 0.283 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.4775 | -3.862 in | 0.283 | True | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.7074 | 0.5 in | 0.566 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.7786 | 0.126 in | 0.28 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.4261 | 0.528 in | 0.28 | True | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 1.0779 | 0.185 in | 0.28 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.6508 | 0.0 in | 0.28 | True | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.6475 | 0.402 in | 0.56 | True | observed / observed |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / grazing |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / grazing |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / grazing |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / grazing |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / grazing |
| `opening.edge_s[door:left]` | missing | 7.2624 |  |   |  |  | – / observed |
| `opening.edge_s[door:right]` | missing | 9.4522 |  |   |  |  | – / observed |
| `opening.bottom[door]` | missing | 0.2116 |  |   |  |  | – / observed |
| `opening.top[door]` | missing | 4.074 |  |   |  |  | – / observed |
| `opening.width[door]` | missing | 2.1898 |  |   |  |  | – / observed |
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.7517 | 0.433 in | 1.054 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.6716 | 0.354 in | 0.856 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.5762 | 0.433 in | 0.662 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.568 | 0.374 in | 0.563 | True | – / – |
| `opening.gap[win5~win_w2_arch]` | missing | 0.91 |  |   |  |  | – / – |
| `opening.gap[win_w2_arch~door]` | missing | 2.2633 |  |   |  |  | – / – |
| `facing.depth[w1@2.79]` | missing | 0.309 |  |   |  |  | – / – |
| `facing.depth[w2@3.14]` | missing | 0.315 |  |   |  |  | – / – |
| `facing.depth[w3@6.68]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@6.93]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@9.94]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.19]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.44]` | missing | 0.708 |  |   |  |  | – / – |
| `overhead.clearance[w1@2.79]` | scored | 2.136 | 1.495 | -25.236 in | 0.05 | False | – / – |
| `corner.s[rc:c1]` | phantom |  | -13.1131 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 90.17 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 4.2238 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 6%/44%/90% | 17%/56%/92% | 0.3503 | 0.9995 | 0.0002 | observed, 0.9992, 0.0007 | 0.9978 | 0.0007 |
| wall_face | 58856 | 6%/42%/83% | 17%/52%/84% | 0.3622 | 0.902 | 0.0282 | grazing, 0.8953, 0.0664 | 0.8911 | 0.0021 |
| ground_strip | 206971 | 0%/1%/3% | 0%/1%/3% | 0.112 | 0.0327 | 0.112 | partial, 0.1921, 0.3229 | 0.1826 | 0.0297 |
| overhead | 1602 | 9%/28%/68% | 6%/20%/65% | 0.4775 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9954 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0233, in own unknown voxels 0.1491, flagged generated 0.0.
