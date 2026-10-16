# Geometry score: eth3d-facade/A8-brick_modular vs eth3d-facade/gt-laser

Producer `A8-brick_modular`. Status counts: {'scored': 35, 'missing': 21, 'extent_limited': 1, 'phantom': 10, 'scale_reference': 0}.

**Key distances (35 scored):** median 9.53 in, p90 45.0 in, max 163.44 in; ≤2 in 0%, ≤4 in 9%, ≤6 in 20%; within the recon's own bound 60%.

**Scale:** KD ratio median 0.90142 (n=8, error -9.858 %), landmark Umeyama 0.90081. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 1.111111, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': -10.0, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 0.9987, 'cameras': 76}.

**Anchor:** ICP translation 0.1485 m [0.1031, 0.0918, -0.0549], yaw -0.221°, tilt 0.362°.

**On truly observed elements:** 31 KDs, median 10.16 in, p90 47.37 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.266 | -10.157 in | 0.244 | False | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 14.1827 | -65.642 in | 1.034 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -11.0131 | 47.374 in | 0.526 | False | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -9.6062 | 41.441 in | 0.526 | False | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.8221 | -3.634 in | 0.526 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 3.0347 | -13.917 in | 0.526 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.4069 | -5.933 in | 1.052 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.0392 | 34.602 in | 0.437 | False | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -6.6038 | 28.516 in | 0.437 | False | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.7618 | -4.476 in | 0.437 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 3.0457 | -15.098 in | 0.437 | True | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.4354 | -6.087 in | 0.874 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.1083 | 21.937 in | 0.349 | False | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.6443 | 15.697 in | 0.349 | False | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.8111 | -5.783 in | 0.349 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.1093 | -16.492 in | 0.349 | False | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.464 | -6.24 in | 0.698 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.2341 | 9.594 in | 0.263 | True | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.7065 | 3.016 in | 0.263 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 0.8352 | -6.945 in | 0.263 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.1266 | -17.677 in | 0.263 | False | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.5276 | -6.579 in | 0.526 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.6965 | -3.106 in | 0.261 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.1706 | -9.531 in | 0.261 | True | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 0.8658 | -8.165 in | 0.261 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.168 | -19.008 in | 0.261 | False | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.4741 | -6.425 in | 0.522 | True | observed / observed |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / grazing |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / grazing |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / grazing |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / grazing |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / grazing |
| `opening.edge_s[door:left]` | scored | 7.2624 | 9.5053 | 88.303 in | 0.408 | False | grazing / observed |
| `opening.edge_s[door:right]` | scored | 9.4522 | 7.5437 | -75.138 in | 0.408 | False | grazing / observed |
| `opening.bottom[door]` | scored | 0.2116 | 0.0603 | -5.957 in | 0.408 | True | grazing / observed |
| `opening.top[door]` | scored | 4.074 | 3.4927 | -22.886 in | 0.408 | False | grazing / observed |
| `opening.width[door]` | scored | 2.1898 | -1.9616 | -163.441 in | 0.816 | False | grazing / observed |
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.567 | -6.839 in | 0.963 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.4955 | -6.579 in | 0.786 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.4102 | -6.102 in | 0.612 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.403 | -6.122 in | 0.524 | True | – / – |
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
| `corner.s[rc:c1]` | phantom |  | -11.732 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 90.27 |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | 2.4507 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | 44.08 |   |  |  | – / – |
| `corner.s[rc:c3]` | phantom |  | 7.0371 |   |  |  | – / – |
| `corner.turn[rc:c3]` | phantom |  | 135.42 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 6.6622 |   |  |  | – / – |
| `wall.length[rc:w3]` | phantom |  | 4.5864 |   |  |  | – / – |
| `wall.length[rc:w4]` | phantom |  | 3.0401 |   |  |  | – / – |
| `opening.gap[win5~door]` | phantom |  | 7.3347 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 6%/44%/83% | 17%/52%/84% | 0.3682 | 0.9995 | 0.0002 | observed, 0.9992, 0.0007 | 0.9857 | 0.0008 |
| wall_face | 58856 | 6%/38%/71% | 15%/44%/73% | 0.3451 | 0.902 | 0.0091 | grazing, 0.8953, 0.0664 | 0.8925 | 0.0104 |
| ground_strip | 206971 | 0%/1%/4% | 0%/1%/5% | 0.1945 | 0.0327 | 0.1931 | partial, 0.1921, 0.3229 | 0.2335 | 0.1201 |
| overhead | 1602 | 0%/0%/4% | 0%/2%/20% | 0.4683 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9808 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0335, in own unknown voxels 0.1103, flagged generated 0.0.
