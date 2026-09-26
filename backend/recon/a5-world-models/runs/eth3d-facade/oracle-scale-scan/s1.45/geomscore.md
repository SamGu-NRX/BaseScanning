# Geometry score: eth3d-facade/A5-scan-1.45 vs eth3d-facade/gt-laser

Producer `A5-scan-1.45`. Status counts: {'scored': 23, 'missing': 33, 'extent_limited': 1, 'phantom': 13, 'scale_reference': 0}.

**Key distances (23 scored):** median 19.08 in, p90 29.39 in, max 56.96 in; ≤2 in 0%, ≤4 in 0%, ≤6 in 9%; within the recon's own bound 87%.

**Scale:** KD ratio median 1.08095 (n=4, error 8.095 %), landmark Umeyama 1.03042. Alignment: {'metric_declared': False, 'method': 'given transform'}.

**Anchor:** ICP translation 0.1233 m [0.0445, -0.0493, -0.1039], yaw -0.044°, tilt 0.971°.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.6263 | 4.028 in | 0.424 | True | – / – |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 18.6002 | 108.276 in | 0.692 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | missing | -12.2164 |  |   |  |  | – / – |
| `opening.edge_s[win1:right]` | missing | -10.6588 |  |   |  |  | – / – |
| `opening.bottom[win1]` | missing | 0.9144 |  |   |  |  | – / – |
| `opening.top[win1]` | missing | 3.3882 |  |   |  |  | – / – |
| `opening.width[win1]` | missing | 1.5576 |  |   |  |  | – / – |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.3778 | 21.272 in | 0.532 | False | – / – |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -7.6196 | -11.476 in | 0.532 | True | – / – |
| `opening.bottom[win2]` | scored | 0.8755 | 1.4897 | 24.181 in | 0.532 | False | – / – |
| `opening.top[win2]` | scored | 3.4292 | 2.9445 | -19.083 in | 0.532 | True | – / – |
| `opening.width[win2]` | scored | 1.59 | 0.7582 | -32.748 in | 1.064 | True | – / – |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -6.3574 | -27.24 in | 1.489 | True | – / – |
| `opening.edge_s[win3:right]` | scored | -4.043 | -5.4897 | -56.957 in | 1.489 | True | – / – |
| `opening.bottom[win3]` | scored | 0.958 | 1.4117 | 17.862 in | 1.489 | True | – / – |
| `opening.top[win3]` | scored | 3.5282 | 2.9912 | -21.142 in | 1.489 | True | – / – |
| `opening.width[win3]` | scored | 1.6225 | 0.8677 | -29.717 in | 2.978 | True | – / – |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -1.7649 | 28.067 in | 0.669 | False | – / – |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.6323 | 5.937 in | 0.669 | True | – / – |
| `opening.bottom[win4]` | scored | 1.0116 | 1.3395 | 12.909 in | 0.669 | True | – / – |
| `opening.top[win4]` | scored | 3.5756 | 3.0749 | -19.713 in | 0.669 | True | – / – |
| `opening.width[win4]` | scored | 1.6947 | 1.1326 | -22.13 in | 1.338 | True | – / – |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.6015 | -6.846 in | 0.641 | True | – / – |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 1.8682 | -21.437 in | 0.641 | True | – / – |
| `opening.bottom[win5]` | scored | 1.0732 | 1.2864 | 8.394 in | 0.641 | True | – / – |
| `opening.top[win5]` | scored | 3.6508 | 3.1792 | -18.567 in | 0.641 | True | – / – |
| `opening.width[win5]` | scored | 1.6373 | 1.2667 | -14.591 in | 1.282 | True | – / – |
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
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.2622 | -15.764 in | 2.021 | True | – / – |
| `opening.gap[win3~win4]` | missing | 1.5652 |  |   |  |  | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.2338 | -12.783 in | 1.31 | True | – / – |
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
| `opening.edge_s[rc:win3:left]` | phantom |  | -4.0611 |   |  |  | – / – |
| `opening.edge_s[rc:win3:right]` | phantom |  | -3.081 |   |  |  | – / – |
| `opening.bottom[rc:win3]` | phantom |  | 1.3946 |   |  |  | – / – |
| `opening.top[rc:win3]` | phantom |  | 3.0583 |   |  |  | – / – |
| `opening.width[rc:win3]` | phantom |  | 0.9801 |   |  |  | – / – |
| `opening.edge_s[rc:door:left]` | phantom |  | 3.5661 |   |  |  | – / – |
| `opening.edge_s[rc:door:right]` | phantom |  | 6.4761 |   |  |  | – / – |
| `opening.bottom[rc:door]` | phantom |  | 0.0771 |   |  |  | – / – |
| `opening.top[rc:door]` | phantom |  | 4.4862 |   |  |  | – / – |
| `opening.width[rc:door]` | phantom |  | 2.91 |   |  |  | – / – |
| `opening.gap[win3~rc:win3]` | phantom |  | 1.4286 |   |  |  | – / – |
| `opening.gap[rc:win3~win4]` | phantom |  | 1.3161 |   |  |  | – / – |
| `opening.gap[win5~rc:door]` | phantom |  | 1.6979 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 1%/3%/13% | 1%/4%/12% | 0.7075 | None | None | None, None, None | None | None |
| wall_face | 58856 | 1%/6%/17% | 2%/8%/15% | 0.692 | None | None | None, None, None | None | None |
| ground_strip | 206971 | 17%/48%/83% | 26%/52%/78% | 0.0439 | None | None | None, None, None | None | None |
| overhead | 1602 | 1%/7%/31% | 0%/0%/13% | 0.8684 | None | None | None, None, None | None | None |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera None, in own unknown voxels 0.965, flagged generated 0.0.
