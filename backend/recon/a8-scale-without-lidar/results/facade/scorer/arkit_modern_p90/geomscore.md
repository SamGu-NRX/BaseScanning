# Geometry score: eth3d-facade/A8-arkit_modern_p90 vs eth3d-facade/gt-laser

Producer `A8-arkit_modern_p90`. Status counts: {'scored': 35, 'missing': 21, 'extent_limited': 1, 'phantom': 10, 'scale_reference': 0}.

**Key distances (35 scored):** median 4.72 in, p90 24.69 in, max 167.37 in; ≤2 in 11%, ≤4 in 49%, ≤6 in 57%; within the recon's own bound 83%.

**Scale:** KD ratio median 0.946 (n=8, error -5.4 %), landmark Umeyama 0.94537. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 1.059322, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': -5.6, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 0.5595, 'cameras': 76}.

**Anchor:** ICP translation 0.0701 m [0.0322, 0.0309, -0.0541], yaw -0.186°, tilt 0.259°.

**On truly observed elements:** 31 KDs, median 5.34 in, p90 25.97 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.4041 | -4.72 in | 0.248 | True | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 14.8838 | -38.039 in | 1.088 | True | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -11.5568 | 25.969 in | 0.552 | False | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -10.0805 | 22.768 in | 0.552 | False | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.8792 | -1.386 in | 0.552 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 3.201 | -7.37 in | 0.552 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.4763 | -3.201 in | 1.104 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.4365 | 18.961 in | 0.458 | False | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -6.9303 | 15.661 in | 0.458 | True | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.8331 | -1.669 in | 0.458 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 3.2298 | -7.85 in | 0.458 | True | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.5062 | -3.299 in | 0.916 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.3615 | 11.969 in | 0.366 | True | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.8251 | 8.579 in | 0.366 | True | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.9017 | -2.217 in | 0.366 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.3136 | -8.449 in | 0.366 | True | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.5364 | -3.39 in | 0.732 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.3454 | 5.213 in | 0.275 | True | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.7423 | 1.606 in | 0.275 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 0.9437 | -2.673 in | 0.275 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.3484 | -8.945 in | 0.275 | True | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.6031 | -3.606 in | 0.55 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.7299 | -1.791 in | 0.273 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.277 | -5.343 in | 0.273 | True | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 0.9925 | -3.177 in | 0.273 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.4086 | -9.535 in | 0.273 | True | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.5471 | -3.551 in | 0.546 | True | observed / observed |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / grazing |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / grazing |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / grazing |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / grazing |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / grazing |
| `opening.edge_s[door:left]` | scored | 7.2624 | 9.9918 | 107.457 in | 0.421 | False | grazing / observed |
| `opening.edge_s[door:right]` | scored | 9.4522 | 7.9303 | -59.917 in | 0.421 | False | grazing / observed |
| `opening.bottom[door]` | scored | 0.2116 | 0.154 | -2.268 in | 0.421 | True | grazing / observed |
| `opening.top[door]` | scored | 4.074 | 3.7614 | -12.307 in | 0.421 | True | grazing / observed |
| `opening.width[door]` | scored | 2.1898 | -2.0615 | -167.374 in | 0.842 | False | grazing / observed |
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.644 | -3.807 in | 1.01 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.5688 | -3.693 in | 0.824 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.4797 | -3.366 in | 0.641 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.4722 | -3.398 in | 0.548 | True | – / – |
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
| `corner.s[rc:c1]` | phantom |  | -12.306 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 90.31 |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | 2.5778 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | 44.06 |   |  |  | – / – |
| `corner.s[rc:c3]` | phantom |  | 7.3941 |   |  |  | – / – |
| `corner.turn[rc:c3]` | phantom |  | 135.46 |   |  |  | – / – |
| `wall.length[rc:w1]` | phantom |  | 6.6811 |   |  |  | – / – |
| `wall.length[rc:w3]` | phantom |  | 4.8163 |   |  |  | – / – |
| `wall.length[rc:w4]` | phantom |  | 3.2417 |   |  |  | – / – |
| `opening.gap[win5~door]` | phantom |  | 7.7148 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 7%/46%/89% | 18%/55%/90% | 0.3623 | 0.9995 | 0.0002 | observed, 0.9992, 0.0007 | 0.9926 | 0.0006 |
| wall_face | 58856 | 6%/39%/79% | 15%/47%/78% | 0.3432 | 0.902 | 0.0092 | grazing, 0.8953, 0.0664 | 0.8912 | 0.0037 |
| ground_strip | 206971 | 0%/1%/3% | 0%/1%/3% | 0.0942 | 0.0327 | 0.0942 | partial, 0.1921, 0.3229 | 0.2087 | 0.0965 |
| overhead | 1602 | 0%/4%/18% | 1%/6%/20% | 0.496 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9927 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.014, in own unknown voxels 0.1099, flagged generated 0.0.
