# Geometry score: eth3d-facade/A5-scan-1.20 vs eth3d-facade/gt-laser

Producer `A5-scan-1.20`. Status counts: {'scored': 23, 'missing': 33, 'extent_limited': 1, 'phantom': 19, 'scale_reference': 0}.

**Key distances (23 scored):** median 24.34 in, p90 38.99 in, max 67.75 in; ≤2 in 4%, ≤4 in 4%, ≤6 in 9%; within the recon's own bound 61%.

**Scale:** KD ratio median 0.92353 (n=4, error -7.647 %), landmark Umeyama 0.87799. Alignment: {'metric_declared': False, 'method': 'given transform'}.

**Anchor:** ICP translation 0.1209 m [0.0977, -0.0106, -0.0704], yaw -0.106°, tilt 0.615°.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.365 | -6.26 in | 0.215 | True | – / – |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 25.9317 | 396.917 in | 0.276 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | missing | -12.2164 |  |   |  |  | – / – |
| `opening.edge_s[win1:right]` | missing | -10.6588 |  |   |  |  | – / – |
| `opening.bottom[win1]` | missing | 0.9144 |  |   |  |  | – / – |
| `opening.top[win1]` | missing | 3.3882 |  |   |  |  | – / – |
| `opening.width[win1]` | missing | 1.5576 |  |   |  |  | – / – |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -7.1972 | 67.752 in | 0.28 | False | – / – |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -6.5167 | 31.945 in | 0.28 | False | – / – |
| `opening.bottom[win2]` | scored | 0.8755 | 1.2149 | 13.362 in | 0.28 | False | – / – |
| `opening.top[win2]` | scored | 3.4292 | 2.49 | -36.976 in | 0.28 | False | – / – |
| `opening.width[win2]` | scored | 1.59 | 0.6805 | -35.807 in | 0.56 | False | – / – |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.4263 | 9.417 in | 1.049 | True | – / – |
| `opening.edge_s[win3:right]` | scored | -4.043 | -4.6613 | -24.343 in | 1.049 | True | – / – |
| `opening.bottom[win3]` | scored | 0.958 | 1.157 | 7.835 in | 1.049 | True | – / – |
| `opening.top[win3]` | scored | 3.5282 | 2.5275 | -39.398 in | 1.049 | True | – / – |
| `opening.width[win3]` | scored | 1.6225 | 0.765 | -33.76 in | 2.098 | True | – / – |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -1.4957 | 38.665 in | 0.393 | False | – / – |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.5403 | 9.559 in | 0.393 | True | – / – |
| `opening.bottom[win4]` | scored | 1.0116 | 1.1183 | 4.201 in | 0.393 | True | – / – |
| `opening.top[win4]` | scored | 3.5756 | 2.5863 | -38.949 in | 0.393 | False | – / – |
| `opening.width[win4]` | scored | 1.6947 | 0.9554 | -29.106 in | 0.786 | True | – / – |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.4815 | -11.571 in | 0.373 | True | – / – |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 1.5202 | -35.138 in | 0.373 | False | – / – |
| `opening.bottom[win5]` | scored | 1.0732 | 1.0836 | 0.409 in | 0.373 | True | – / – |
| `opening.top[win5]` | scored | 3.6508 | 2.6602 | -39.0 in | 0.373 | False | – / – |
| `opening.width[win5]` | scored | 1.6373 | 1.0387 | -23.567 in | 0.746 | True | – / – |
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
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.0904 | -22.528 in | 1.329 | True | – / – |
| `opening.gap[win3~win4]` | missing | 1.5652 |  |   |  |  | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.0218 | -21.13 in | 0.766 | True | – / – |
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
| `corner.s[rc:c1]` | phantom |  | -19.4311 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | -88.75 |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | -13.0297 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | -106.47 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 14.7855 |   |  |  | – / – |
| `wall.length[rc:w2]` | phantom |  | 6.4014 |   |  |  | – / – |
| `opening.edge_s[rc:win3:left]` | phantom |  | -3.45 |   |  |  | – / – |
| `opening.edge_s[rc:win3:right]` | phantom |  | -2.603 |   |  |  | – / – |
| `opening.bottom[rc:win3]` | phantom |  | 1.154 |   |  |  | – / – |
| `opening.top[rc:win3]` | phantom |  | 2.5803 |   |  |  | – / – |
| `opening.width[rc:win3]` | phantom |  | 0.847 |   |  |  | – / – |
| `opening.edge_s[rc:door:left]` | phantom |  | 2.881 |   |  |  | – / – |
| `opening.edge_s[rc:door:right]` | phantom |  | 5.2142 |   |  |  | – / – |
| `opening.bottom[rc:door]` | phantom |  | 0.0883 |   |  |  | – / – |
| `opening.top[rc:door]` | phantom |  | 3.7096 |   |  |  | – / – |
| `opening.width[rc:door]` | phantom |  | 2.3332 |   |  |  | – / – |
| `opening.gap[win3~rc:win3]` | phantom |  | 1.2113 |   |  |  | – / – |
| `opening.gap[rc:win3~win4]` | phantom |  | 1.1073 |   |  |  | – / – |
| `opening.gap[win5~rc:door]` | phantom |  | 1.3608 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 2%/8%/17% | 2%/8%/17% | 0.7776 | None | None | None, None, None | None | None |
| wall_face | 58856 | 2%/9%/20% | 3%/11%/20% | 0.7518 | None | None | None, None, None | None | None |
| ground_strip | 206971 | 18%/44%/74% | 27%/54%/83% | 0.0453 | None | None | None, None, None | None | None |
| overhead | 1602 | 2%/13%/38% | 0%/3%/26% | 0.8849 | None | None | None, None, None | None | None |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera None, in own unknown voxels 0.8833, flagged generated 0.0.
