# Geometry score: eth3d-facade/A8-minus1p5 vs eth3d-facade/gt-laser

Producer `A8-minus1p5`. Status counts: {'scored': 31, 'missing': 25, 'extent_limited': 1, 'phantom': 3, 'scale_reference': 0}.

**Key distances (31 scored):** median 1.85 in, p90 10.87 in, max 15.5 in; ≤2 in 52%, ≤4 in 64%, ≤6 in 81%; within the recon's own bound 97%.

**Scale:** KD ratio median 0.9875 (n=6, error -1.25 %), landmark Umeyama 0.98766. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 1.015228, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': -1.5, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 0.1508, 'cameras': 76}.

**Anchor:** ICP translation 0.0435 m [0.0065, 0.0047, -0.0428], yaw -0.326°, tilt 0.013°.

**On truly observed elements:** 26 KDs, median 2.3 in, p90 9.85 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.5637 | 1.563 in | 0.241 | True | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 15.7486 | -3.992 in | 1.118 | True | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -12.0675 | 5.862 in | 0.565 | True | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -10.5247 | 5.28 in | 0.565 | True | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.5702 | -13.551 in | 0.565 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 2.9944 | -15.504 in | 0.565 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.5428 | -0.583 in | 1.13 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.8074 | 4.358 in | 0.467 | True | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -7.234 | 3.705 in | 0.467 | True | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.6514 | -8.823 in | 0.467 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 3.1531 | -10.87 in | 0.467 | True | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.5734 | -0.654 in | 0.934 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.5958 | 2.744 in | 0.371 | True | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.9917 | 2.02 in | 0.371 | True | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.8503 | -4.24 in | 0.371 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.3673 | -6.335 in | 0.371 | True | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.6041 | -0.724 in | 0.742 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.4472 | 1.205 in | 0.277 | True | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.7743 | 0.346 in | 0.277 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 1.0198 | 0.323 in | 0.277 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.5286 | -1.85 in | 0.277 | True | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.6729 | -0.858 in | 0.554 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.7616 | -0.543 in | 0.274 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.3751 | -1.48 in | 0.274 | True | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 1.1962 | 4.843 in | 0.274 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.7162 | 2.575 in | 0.274 | True | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.6135 | -0.937 in | 0.548 | True | observed / observed |
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
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.7173 | -0.921 in | 1.032 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.6382 | -0.961 in | 0.838 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.5445 | -0.815 in | 0.648 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.5359 | -0.89 in | 0.551 | True | – / – |
| `opening.gap[win5~win_w2_arch]` | missing | 0.91 |  |   |  |  | – / – |
| `opening.gap[win_w2_arch~door]` | missing | 2.2633 |  |   |  |  | – / – |
| `facing.depth[w1@2.79]` | missing | 0.309 |  |   |  |  | – / – |
| `facing.depth[w2@3.14]` | missing | 0.315 |  |   |  |  | – / – |
| `facing.depth[w3@6.68]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@6.93]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@9.94]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.19]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.44]` | missing | 0.708 |  |   |  |  | – / – |
| `overhead.clearance[w1@2.79]` | scored | 2.136 | 1.769 | -14.449 in | 0.05 | False | – / – |
| `corner.s[rc:c1]` | phantom |  | -12.8526 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 90.24 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 4.1421 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 7%/45%/93% | 17%/57%/94% | 0.3637 | 0.9995 | 0.0 | observed, 0.9992, 0.0007 | 0.9828 | 0.0002 |
| wall_face | 58856 | 7%/43%/84% | 15%/52%/85% | 0.352 | 0.902 | 0.0184 | grazing, 0.8953, 0.0664 | 0.8762 | 0.0003 |
| ground_strip | 206971 | 0%/0%/3% | 0%/0%/2% | 0.0158 | 0.0327 | 0.0158 | partial, 0.1921, 0.3229 | 0.1919 | 0.0253 |
| overhead | 1602 | 2%/17%/65% | 2%/16%/61% | 0.4603 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9954 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0007, in own unknown voxels 0.1583, flagged generated 0.0.
