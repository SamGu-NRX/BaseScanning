# Geometry score: eth3d-facade/A8-moge2 vs eth3d-facade/gt-laser

Producer `A8-moge2`. Status counts: {'scored': 35, 'missing': 21, 'extent_limited': 1, 'phantom': 10, 'scale_reference': 0}.

**Key distances (35 scored):** median 9.32 in, p90 56.46 in, max 161.23 in; ≤2 in 0%, ≤4 in 14%, ≤6 in 20%; within the recon's own bound 54%.

**Scale:** KD ratio median 0.8763 (n=8, error -12.37 %), landmark Umeyama 0.87567. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 1.142857, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': -12.5, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 1.2484, 'cameras': 76}.

**Anchor:** ICP translation 0.2346 m [0.2234, 0.0574, -0.0427], yaw -0.248°, tilt 0.284°.

**On truly observed elements:** 31 KDs, median 12.05 in, p90 59.45 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.2873 | -9.319 in | 0.24 | True | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 13.7688 | -81.937 in | 1.018 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -10.7065 | 59.445 in | 0.518 | False | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -9.3387 | 51.972 in | 0.518 | False | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.8255 | -3.5 in | 0.518 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 2.9765 | -16.209 in | 0.518 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.3678 | -7.472 in | 1.036 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -7.8153 | 43.417 in | 0.432 | False | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -6.4199 | 35.756 in | 0.432 | False | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.7757 | -3.929 in | 0.432 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 2.9959 | -17.059 in | 0.432 | False | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.3954 | -7.661 in | 0.864 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -4.966 | 27.539 in | 0.346 | False | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.5427 | 19.697 in | 0.346 | False | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.8322 | -4.953 in | 0.346 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.0664 | -18.181 in | 0.346 | False | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.4233 | -7.843 in | 0.692 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.1718 | 12.047 in | 0.262 | False | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.6868 | 3.791 in | 0.262 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 0.8642 | -5.803 in | 0.262 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.0917 | -19.051 in | 0.262 | False | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.485 | -8.256 in | 0.524 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.677 | -3.874 in | 0.26 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.1101 | -11.913 in | 0.26 | False | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 0.9025 | -6.72 in | 0.26 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.1405 | -20.091 in | 0.26 | False | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.4331 | -8.039 in | 0.52 | True | observed / observed |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / grazing |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / grazing |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / grazing |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / grazing |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / grazing |
| `opening.edge_s[door:left]` | scored | 7.2624 | 9.2129 | 76.791 in | 0.394 | False | grazing / observed |
| `opening.edge_s[door:right]` | scored | 9.4522 | 7.3074 | -84.441 in | 0.394 | False | grazing / observed |
| `opening.bottom[door]` | scored | 0.2116 | 0.1228 | -3.496 in | 0.394 | True | grazing / observed |
| `opening.top[door]` | scored | 4.074 | 3.4583 | -24.24 in | 0.394 | False | grazing / observed |
| `opening.width[door]` | scored | 2.1898 | -1.9055 | -161.232 in | 0.788 | False | grazing / observed |
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.5234 | -8.555 in | 0.95 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.4539 | -8.217 in | 0.778 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.3709 | -7.65 in | 0.608 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.3638 | -7.665 in | 0.522 | True | – / – |
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
| `corner.s[rc:c1]` | phantom |  | -11.3949 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 89.96 |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | 2.3739 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | 44.18 |   |  |  | – / – |
| `corner.s[rc:c3]` | phantom |  | 6.8291 |   |  |  | – / – |
| `corner.turn[rc:c3]` | phantom |  | 135.38 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 8.0898 |   |  |  | – / – |
| `wall.length[rc:w3]` | phantom |  | 4.4552 |   |  |  | – / – |
| `wall.length[rc:w4]` | phantom |  | 2.9391 |   |  |  | – / – |
| `opening.gap[win5~door]` | phantom |  | 7.1028 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 7%/45%/81% | 16%/48%/78% | 0.36 | 0.9995 | 0.0004 | observed, 0.9992, 0.0007 | 0.9813 | 0.0007 |
| wall_face | 58856 | 6%/38%/69% | 14%/42%/69% | 0.3298 | 0.902 | 0.0086 | grazing, 0.8953, 0.0664 | 0.8933 | 0.0147 |
| ground_strip | 206971 | 0%/2%/5% | 0%/1%/4% | 0.1598 | 0.0327 | 0.1589 | partial, 0.1921, 0.3229 | 0.2621 | 0.1359 |
| overhead | 1602 | 0%/0%/8% | 1%/8%/30% | 0.4828 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9742 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0239, in own unknown voxels 0.1097, flagged generated 0.0.
