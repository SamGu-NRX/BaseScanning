# Geometry score: eth3d-facade/A8-oracle vs eth3d-facade/gt-laser

Producer `A8-oracle`. Status counts: {'scored': 31, 'missing': 25, 'extent_limited': 1, 'phantom': 3, 'scale_reference': 0}.

**Key distances (31 scored):** median 0.43 in, p90 7.52 in, max 30.63 in; ≤2 in 68%, ≤4 in 74%, ≤6 in 84%; within the recon's own bound 97%.

**Scale:** KD ratio median 1.00172 (n=6, error 0.172 %), landmark Umeyama 1.00187. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 1.0, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': 0.0, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 0.0175, 'cameras': 76}.

**Anchor:** ICP translation 0.1029 m [-0.0224, -0.0807, -0.0597], yaw -0.212°, tilt 0.078°.

**On truly observed elements:** 26 KDs, median 0.66 in, p90 7.13 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.6425 | 4.665 in | 0.266 | True | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 15.9693 | 4.697 in | 1.164 | True | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -12.2404 | -0.945 in | 0.588 | True | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -10.6762 | -0.685 in | 0.588 | True | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.6715 | -9.563 in | 0.588 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 3.1307 | -10.138 in | 0.588 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.5642 | 0.26 in | 1.176 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.9342 | -0.634 in | 0.489 | True | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -7.3386 | -0.413 in | 0.489 | True | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.7431 | -5.213 in | 0.489 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 3.2812 | -5.827 in | 0.489 | True | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.5956 | 0.22 in | 0.978 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.6765 | -0.433 in | 0.391 | True | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -4.0493 | -0.248 in | 0.391 | True | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.9343 | -0.933 in | 0.391 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.4881 | -1.579 in | 0.391 | True | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.6272 | 0.185 in | 0.782 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.4822 | -0.173 in | 0.296 | True | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.7848 | -0.067 in | 0.296 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 1.0958 | 3.315 in | 0.296 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.6417 | 2.602 in | 0.296 | True | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.6974 | 0.106 in | 0.592 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.774 | -0.055 in | 0.294 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.4116 | -0.043 in | 0.294 | True | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 1.2643 | 7.524 in | 0.294 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.8219 | 6.736 in | 0.294 | True | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.6376 | 0.012 in | 0.588 | True | observed / observed |
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
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.742 | 0.051 in | 1.077 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.6621 | -0.02 in | 0.88 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.5671 | 0.075 in | 0.687 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.5588 | 0.012 in | 0.59 | True | – / – |
| `opening.gap[win5~win_w2_arch]` | missing | 0.91 |  |   |  |  | – / – |
| `opening.gap[win_w2_arch~door]` | missing | 2.2633 |  |   |  |  | – / – |
| `facing.depth[w1@2.79]` | missing | 0.309 |  |   |  |  | – / – |
| `facing.depth[w2@3.14]` | missing | 0.315 |  |   |  |  | – / – |
| `facing.depth[w3@6.68]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@6.93]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@9.94]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.19]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.44]` | missing | 0.708 |  |   |  |  | – / – |
| `overhead.clearance[w1@2.79]` | scored | 2.136 | 1.358 | -30.63 in | 0.05 | False | – / – |
| `corner.s[rc:c1]` | phantom |  | -13.0337 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 90.19 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 4.2034 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 6%/43%/92% | 18%/56%/93% | 0.3312 | 0.9995 | 0.0 | observed, 0.9992, 0.0007 | 0.9674 | 0.0001 |
| wall_face | 58856 | 6%/40%/84% | 17%/52%/85% | 0.3255 | 0.902 | 0.0183 | grazing, 0.8953, 0.0664 | 0.8619 | 0.0001 |
| ground_strip | 206971 | 0%/0%/2% | 0%/1%/3% | 0.0014 | 0.0327 | 0.0014 | partial, 0.1921, 0.3229 | 0.1892 | 0.0589 |
| overhead | 1602 | 5%/22%/74% | 4%/18%/66% | 0.4544 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9934 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0009, in own unknown voxels 0.1542, flagged generated 0.0.
