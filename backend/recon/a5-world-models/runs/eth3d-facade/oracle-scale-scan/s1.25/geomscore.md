# Geometry score: eth3d-facade/A5-scan-1.25 vs eth3d-facade/gt-laser

Producer `A5-scan-1.25`. Status counts: {'scored': 23, 'missing': 33, 'extent_limited': 1, 'phantom': 19, 'scale_reference': 0}.

**Key distances (23 scored):** median 26.64 in, p90 37.64 in, max 61.48 in; ≤2 in 0%, ≤4 in 9%, ≤6 in 9%; within the recon's own bound 61%.

**Scale:** KD ratio median 0.9401 (n=4, error -5.99 %), landmark Umeyama 0.892. Alignment: {'metric_declared': False, 'method': 'given transform'}.

**Anchor:** ICP translation 0.2484 m [0.2275, -0.0243, 0.0968], yaw -1.545°, tilt 0.63°.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.4319 | -3.626 in | 0.2 | True | – / – |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 23.6479 | 307.004 in | 0.244 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | missing | -12.2164 |  |   |  |  | – / – |
| `opening.edge_s[win1:right]` | missing | -10.6588 |  |   |  |  | – / – |
| `opening.bottom[win1]` | missing | 0.9144 |  |   |  |  | – / – |
| `opening.top[win1]` | missing | 3.3882 |  |   |  |  | – / – |
| `opening.width[win1]` | missing | 1.5576 |  |   |  |  | – / – |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -7.3564 | 61.484 in | 0.279 | False | – / – |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -6.6514 | 26.642 in | 0.279 | False | – / – |
| `opening.bottom[win2]` | scored | 0.8755 | 1.2799 | 15.921 in | 0.279 | False | – / – |
| `opening.top[win2]` | scored | 3.4292 | 2.5769 | -33.555 in | 0.279 | False | – / – |
| `opening.width[win2]` | scored | 1.59 | 0.705 | -34.843 in | 0.558 | False | – / – |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.5099 | 6.126 in | 1.122 | True | – / – |
| `opening.edge_s[win3:right]` | scored | -4.043 | -4.7293 | -27.02 in | 1.122 | True | – / – |
| `opening.bottom[win3]` | scored | 0.958 | 1.2257 | 10.539 in | 1.122 | True | – / – |
| `opening.top[win3]` | scored | 3.5282 | 2.6062 | -36.299 in | 1.122 | True | – / – |
| `opening.width[win3]` | scored | 1.6225 | 0.7806 | -33.146 in | 2.244 | True | – / – |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -1.4916 | 38.827 in | 0.416 | False | – / – |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.5527 | 9.071 in | 0.416 | True | – / – |
| `opening.bottom[win4]` | scored | 1.0116 | 1.1927 | 7.13 in | 0.416 | True | – / – |
| `opening.top[win4]` | scored | 3.5756 | 2.6353 | -37.02 in | 0.416 | False | – / – |
| `opening.width[win4]` | scored | 1.6947 | 0.9389 | -29.756 in | 0.832 | True | – / – |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.463 | -12.299 in | 0.403 | True | – / – |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 1.4647 | -37.323 in | 0.403 | False | – / – |
| `opening.bottom[win5]` | scored | 1.0732 | 1.159 | 3.378 in | 0.403 | True | – / – |
| `opening.top[win5]` | scored | 3.6508 | 2.6928 | -37.717 in | 0.403 | False | – / – |
| `opening.width[win5]` | scored | 1.6373 | 1.0017 | -25.024 in | 0.806 | True | – / – |
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
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.1415 | -20.516 in | 1.401 | True | – / – |
| `opening.gap[win3~win4]` | missing | 1.5652 |  |   |  |  | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.0157 | -21.37 in | 0.819 | True | – / – |
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
| `corner.s[rc:c1]` | phantom |  | -20.0686 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | -87.46 |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | -13.3285 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | -104.08 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 14.7579 |   |  |  | – / – |
| `wall.length[rc:w2]` | phantom |  | 6.7401 |   |  |  | – / – |
| `opening.edge_s[rc:win3:left]` | phantom |  | -3.4679 |   |  |  | – / – |
| `opening.edge_s[rc:win3:right]` | phantom |  | -2.6188 |   |  |  | – / – |
| `opening.bottom[rc:win3]` | phantom |  | 1.2263 |   |  |  | – / – |
| `opening.top[rc:win3]` | phantom |  | 2.6459 |   |  |  | – / – |
| `opening.width[rc:win3]` | phantom |  | 0.8491 |   |  |  | – / – |
| `opening.edge_s[rc:door:left]` | phantom |  | 2.7608 |   |  |  | – / – |
| `opening.edge_s[rc:door:right]` | phantom |  | 4.9425 |   |  |  | – / – |
| `opening.bottom[rc:door]` | phantom |  | 0.2119 |   |  |  | – / – |
| `opening.top[rc:door]` | phantom |  | 3.6813 |   |  |  | – / – |
| `opening.width[rc:door]` | phantom |  | 2.1817 |   |  |  | – / – |
| `opening.gap[win3~rc:win3]` | phantom |  | 1.2614 |   |  |  | – / – |
| `opening.gap[rc:win3~win4]` | phantom |  | 1.1272 |   |  |  | – / – |
| `opening.gap[win5~rc:door]` | phantom |  | 1.2961 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 2%/6%/14% | 2%/6%/14% | 0.7664 | None | None | None, None, None | None | None |
| wall_face | 58856 | 2%/7%/17% | 3%/10%/17% | 0.7445 | None | None | None, None, None | None | None |
| ground_strip | 206971 | 17%/40%/72% | 28%/53%/83% | 0.0319 | None | None | None, None, None | None | None |
| overhead | 1602 | 0%/6%/32% | 0%/1%/23% | 0.8836 | None | None | None, None, None | None | None |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera None, in own unknown voxels 0.8924, flagged generated 0.0.
