# Geometry score: eth3d-facade/A5-scan-1.40 vs eth3d-facade/gt-laser

Producer `A5-scan-1.40`. Status counts: {'scored': 23, 'missing': 33, 'extent_limited': 1, 'phantom': 13, 'scale_reference': 0}.

**Key distances (23 scored):** median 19.09 in, p90 34.14 in, max 57.73 in; ≤2 in 0%, ≤4 in 4%, ≤6 in 9%; within the recon's own bound 56%.

**Scale:** KD ratio median 0.90174 (n=4, error -9.826 %), landmark Umeyama 1.01645. Alignment: {'metric_declared': False, 'method': 'given transform'}.

**Anchor:** ICP translation 0.2071 m [0.1369, -0.0448, -0.1489], yaw -0.069°, tilt 0.912°.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.5811 | 2.248 in | 0.197 | True | – / – |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 24.6904 | 348.047 in | 0.234 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | missing | -12.2164 |  |   |  |  | – / – |
| `opening.edge_s[win1:right]` | missing | -10.6588 |  |   |  |  | – / – |
| `opening.bottom[win1]` | missing | 0.9144 |  |   |  |  | – / – |
| `opening.top[win1]` | missing | 3.3882 |  |   |  |  | – / – |
| `opening.width[win1]` | missing | 1.5576 |  |   |  |  | – / – |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -9.2108 | -11.524 in | 0.252 | False | – / – |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -8.2239 | -35.268 in | 0.252 | False | – / – |
| `opening.bottom[win2]` | scored | 0.8755 | 1.3603 | 19.087 in | 0.252 | False | – / – |
| `opening.top[win2]` | scored | 3.4292 | 3.0416 | -15.26 in | 0.252 | False | – / – |
| `opening.width[win2]` | scored | 1.59 | 0.9869 | -23.744 in | 0.504 | False | – / – |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -4.1991 | 57.732 in | 1.308 | False | – / – |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.1158 | 36.504 in | 1.308 | True | – / – |
| `opening.bottom[win3]` | scored | 0.958 | 1.3199 | 14.248 in | 1.308 | True | – / – |
| `opening.top[win3]` | scored | 3.5282 | 3.0657 | -18.209 in | 1.308 | True | – / – |
| `opening.width[win3]` | scored | 1.6225 | 1.0833 | -21.228 in | 2.616 | True | – / – |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -1.7726 | 27.764 in | 0.414 | False | – / – |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.6408 | 5.602 in | 0.414 | True | – / – |
| `opening.bottom[win4]` | scored | 1.0116 | 1.2927 | 11.067 in | 0.414 | True | – / – |
| `opening.top[win4]` | scored | 3.5756 | 3.0127 | -22.161 in | 0.414 | False | – / – |
| `opening.width[win4]` | scored | 1.6947 | 1.1318 | -22.161 in | 0.828 | True | – / – |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.528 | -9.74 in | 0.411 | True | – / – |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 1.6607 | -29.606 in | 0.411 | False | – / – |
| `opening.bottom[win5]` | scored | 1.0732 | 1.2623 | 7.445 in | 0.411 | True | – / – |
| `opening.top[win5]` | scored | 3.6508 | 3.0282 | -24.512 in | 0.411 | False | – / – |
| `opening.width[win5]` | scored | 1.6373 | 1.1327 | -19.866 in | 0.822 | True | – / – |
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
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.3432 | -8.74 in | 1.722 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.1688 | -15.343 in | 0.825 | True | – / – |
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
| `opening.edge_s[rc:win2:left]` | phantom |  | -6.7727 |   |  |  | – / – |
| `opening.edge_s[rc:win2:right]` | phantom |  | -5.7278 |   |  |  | – / – |
| `opening.bottom[rc:win2]` | phantom |  | 1.304 |   |  |  | – / – |
| `opening.top[rc:win2]` | phantom |  | 3.0501 |   |  |  | – / – |
| `opening.width[rc:win2]` | phantom |  | 1.0449 |   |  |  | – / – |
| `opening.edge_s[rc:door:left]` | phantom |  | 3.069 |   |  |  | – / – |
| `opening.edge_s[rc:door:right]` | phantom |  | 5.3061 |   |  |  | – / – |
| `opening.bottom[rc:door]` | phantom |  | 0.23 |   |  |  | – / – |
| `opening.top[rc:door]` | phantom |  | 4.0349 |   |  |  | – / – |
| `opening.width[rc:door]` | phantom |  | 2.2371 |   |  |  | – / – |
| `opening.gap[win2~rc:win2]` | phantom |  | 1.4512 |   |  |  | – / – |
| `opening.gap[rc:win2~win3]` | phantom |  | 1.5287 |   |  |  | – / – |
| `opening.gap[win5~rc:door]` | phantom |  | 1.4083 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 1%/5%/14% | 1%/5%/13% | 0.7396 | None | None | None, None, None | None | None |
| wall_face | 58856 | 2%/9%/17% | 3%/9%/16% | 0.7188 | None | None | None, None, None | None | None |
| ground_strip | 206971 | 28%/55%/88% | 32%/51%/69% | 0.0488 | None | None | None, None, None | None | None |
| overhead | 1602 | 0%/5%/26% | 0%/0%/15% | 0.8591 | None | None | None, None, None | None | None |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera None, in own unknown voxels 0.9648, flagged generated 0.0.
