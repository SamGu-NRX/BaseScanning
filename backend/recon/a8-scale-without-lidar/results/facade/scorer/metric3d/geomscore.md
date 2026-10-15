# Geometry score: eth3d-facade/A8-metric3d vs eth3d-facade/gt-laser

Producer `A8-metric3d`. Status counts: {'scored': 23, 'missing': 33, 'extent_limited': 1, 'phantom': 1, 'scale_reference': 0}.

**Key distances (23 scored):** median 20.11 in, p90 48.65 in, max 71.96 in; ≤2 in 0%, ≤4 in 0%, ≤6 in 4%; within the recon's own bound 44%.

**Scale:** KD ratio median 1.04277 (n=4, error 4.277 %), landmark Umeyama 0.92839. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 0.831255, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': 20.3, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 2.0272, 'cameras': 76}.

**Anchor:** ICP translation 0.8168 m [-0.7112, -0.3975, -0.0579], yaw -0.176°, tilt 0.406°.

**On truly observed elements:** 21 KDs, median 22.16 in, p90 49.34 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 2.257 | 28.858 in | 0.297 | False | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 15.7789 | -2.799 in | 1.18 | True | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -10.7507 | 57.705 in | 0.575 | False | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -8.8311 | 71.957 in | 0.575 | False | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 1.0495 | 5.319 in | 0.575 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 4.103 | 28.142 in | 0.575 | False | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.9196 | 14.252 in | 1.15 | True | observed / observed |
| `opening.edge_s[win2:left]` | missing | -8.9181 |  |   |  |  | – / observed |
| `opening.edge_s[win2:right]` | missing | -7.3281 |  |   |  |  | – / observed |
| `opening.bottom[win2]` | missing | 0.8755 |  |   |  |  | – / observed |
| `opening.top[win2]` | missing | 3.4292 |  |   |  |  | – / observed |
| `opening.width[win2]` | missing | 1.59 |  |   |  |  | – / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -6.8314 | -45.902 in | 0.458 | False | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -4.8739 | -32.713 in | 0.458 | False | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 1.3303 | 14.657 in | 0.458 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 4.4026 | 34.425 in | 0.458 | False | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.9575 | 13.189 in | 0.916 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.9886 | -20.11 in | 0.342 | False | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.9466 | -6.437 in | 0.342 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 1.5746 | 22.165 in | 0.342 | False | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 4.6373 | 41.799 in | 0.342 | False | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 2.042 | 13.673 in | 0.684 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.9286 | 6.031 in | 0.34 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.8987 | 19.134 in | 0.34 | False | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 1.8273 | 29.689 in | 0.34 | False | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 4.9041 | 49.343 in | 0.34 | False | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.9701 | 13.102 in | 0.68 | True | observed / observed |
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
| `opening.gap[win1~win2]` | missing | 1.7407 |  |   |  |  | – / – |
| `opening.gap[win2~win3]` | missing | 1.6626 |  |   |  |  | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.8853 | 12.602 in | 0.8 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.8752 | 12.469 in | 0.682 | True | – / – |
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
| `opening.gap[win1~win3]` | phantom |  | 1.9997 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 3%/17%/60% | 7%/31%/70% | 0.152 | 0.9995 | 0.0 | observed, 0.9992, 0.0007 | 0.8754 | 0.0005 |
| wall_face | 58856 | 2%/15%/51% | 6%/27%/60% | 0.1668 | 0.902 | 0.0202 | grazing, 0.8953, 0.0664 | 0.7795 | 0.0004 |
| ground_strip | 206971 | 0%/0%/0% | 0%/1%/3% | 0.0 | 0.0327 | 0.0 | partial, 0.1921, 0.3229 | 0.1382 | 0.0566 |
| overhead | 1602 | 0%/1%/16% | 0%/2%/6% | 0.3175 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9345 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0641, in own unknown voxels 0.3184, flagged generated 0.0.
