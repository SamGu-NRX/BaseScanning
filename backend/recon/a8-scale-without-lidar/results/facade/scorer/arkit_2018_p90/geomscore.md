# Geometry score: eth3d-facade/A8-arkit_2018_p90 vs eth3d-facade/gt-laser

Producer `A8-arkit_2018_p90`. Status counts: {'scored': 35, 'missing': 21, 'extent_limited': 1, 'phantom': 10, 'scale_reference': 0}.

**Key distances (35 scored):** median 13.57 in, p90 67.71 in, max 158.11 in; ≤2 in 0%, ≤4 in 0%, ≤6 in 9%; within the recon's own bound 46%.

**Scale:** KD ratio median 0.82754 (n=8, error -17.246 %), landmark Umeyama 0.82759. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 1.212121, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': -17.5, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 1.7476, 'cameras': 76}.

**Anchor:** ICP translation 0.424 m [0.4167, 0.0612, -0.0492], yaw -0.154°, tilt 0.317°.

**On truly observed elements:** 31 KDs, median 16.78 in, p90 72.47 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.1793 | -13.571 in | 0.234 | False | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 12.97 | -113.386 in | 0.966 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -10.1101 | 82.925 in | 0.494 | False | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -8.8181 | 72.469 in | 0.494 | False | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.7597 | -6.091 in | 0.494 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 2.7909 | -23.516 in | 0.494 | False | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.292 | -10.457 in | 0.988 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -7.3799 | 60.559 in | 0.412 | False | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -6.0619 | 49.85 in | 0.412 | False | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.708 | -6.594 in | 0.412 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 2.8044 | -24.598 in | 0.412 | False | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.318 | -10.709 in | 0.824 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -4.6899 | 38.409 in | 0.332 | False | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.3458 | 27.449 in | 0.332 | False | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.7567 | -7.925 in | 0.332 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 2.8662 | -26.063 in | 0.332 | False | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.3441 | -10.961 in | 0.664 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.0516 | 16.78 in | 0.252 | False | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.6494 | 5.264 in | 0.252 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 0.7823 | -9.028 in | 0.252 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 2.8853 | -27.177 in | 0.252 | False | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.4022 | -11.516 in | 0.504 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.6378 | -5.417 in | 0.251 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 1.992 | -16.563 in | 0.251 | False | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 0.8139 | -10.209 in | 0.251 | False | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 2.9265 | -28.516 in | 0.251 | False | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.3542 | -11.146 in | 0.502 | True | observed / observed |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / grazing |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / grazing |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / grazing |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / grazing |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / grazing |
| `opening.edge_s[door:left]` | scored | 7.2624 | 8.5914 | 52.323 in | 0.425 | False | grazing / observed |
| `opening.edge_s[door:right]` | scored | 9.4522 | 6.7652 | -105.787 in | 0.425 | False | grazing / observed |
| `opening.bottom[door]` | scored | 0.2116 | 0.0708 | -5.543 in | 0.425 | True | grazing / observed |
| `opening.top[door]` | scored | 4.074 | 3.2388 | -32.882 in | 0.425 | False | grazing / observed |
| `opening.width[door]` | scored | 2.1898 | -1.8262 | -158.11 in | 0.85 | False | grazing / observed |
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.4382 | -11.909 in | 0.906 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.372 | -11.441 in | 0.744 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.2942 | -10.669 in | 0.584 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.2872 | -10.681 in | 0.503 | True | – / – |
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
| `corner.s[rc:c1]` | phantom |  | -10.7393 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 89.89 |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | 2.2307 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | 44.23 |   |  |  | – / – |
| `corner.s[rc:c3]` | phantom |  | 6.3922 |   |  |  | – / – |
| `corner.turn[rc:c3]` | phantom |  | 133.98 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 8.8125 |   |  |  | – / – |
| `wall.length[rc:w3]` | phantom |  | 4.1615 |   |  |  | – / – |
| `wall.length[rc:w4]` | phantom |  | 2.799 |   |  |  | – / – |
| `opening.gap[win5~door]` | phantom |  | 6.5994 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 6%/41%/74% | 15%/42%/70% | 0.3416 | 0.9995 | 0.0003 | observed, 0.9992, 0.0007 | 0.9804 | 0.0007 |
| wall_face | 58856 | 5%/35%/63% | 12%/36%/61% | 0.3274 | 0.902 | 0.0184 | grazing, 0.8953, 0.0664 | 0.896 | 0.0172 |
| ground_strip | 206971 | 1%/2%/6% | 0%/1%/5% | 0.2329 | 0.0327 | 0.222 | partial, 0.1921, 0.3229 | 0.289 | 0.1523 |
| overhead | 1602 | 0%/0%/0% | 1%/5%/18% | 0.4557 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9511 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0399, in own unknown voxels 0.1095, flagged generated 0.0.
