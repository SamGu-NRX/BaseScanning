# Geometry score: eth3d-facade/A8-arkit_modern_med vs eth3d-facade/gt-laser

Producer `A8-arkit_modern_med`. Status counts: {'scored': 35, 'missing': 21, 'extent_limited': 1, 'phantom': 7, 'scale_reference': 0}.

**Key distances (35 scored):** median 2.32 in, p90 10.4 in, max 170.07 in; ≤2 in 40%, ≤4 in 63%, ≤6 in 74%; within the recon's own bound 91%.

**Scale:** KD ratio median 0.97759 (n=8, error -2.241 %), landmark Umeyama 0.97721. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 1.02459, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': -2.4, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 0.2403, 'cameras': 76}.

**Anchor:** ICP translation 0.1101 m [0.0206, 0.0999, -0.0414], yaw -0.322°, tilt 0.078°.

**On truly observed elements:** 31 KDs, median 3.58 in, p90 10.67 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.4194 | -4.118 in | 0.265 | True | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 14.6227 | -48.319 in | 1.098 | False | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -11.9453 | 10.673 in | 0.579 | True | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -10.4189 | 9.445 in | 0.579 | True | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.9657 | 2.02 in | 0.579 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 3.3655 | -0.894 in | 0.579 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.5264 | -1.228 in | 1.158 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -8.7191 | 7.835 in | 0.482 | True | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -7.162 | 6.539 in | 0.482 | True | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.8923 | 0.661 in | 0.482 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 3.3693 | -2.358 in | 0.482 | True | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.5571 | -1.295 in | 0.964 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.54 | 4.941 in | 0.387 | True | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -3.952 | 3.583 in | 0.387 | True | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.9378 | -0.795 in | 0.387 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.4302 | -3.858 in | 0.387 | True | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.588 | -1.358 in | 0.774 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.4226 | 2.173 in | 0.294 | True | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.766 | 0.673 in | 0.294 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 0.9562 | -2.181 in | 0.294 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.441 | -5.299 in | 0.294 | True | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.6566 | -1.5 in | 0.588 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.7554 | -0.787 in | 0.291 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.3538 | -2.319 in | 0.291 | True | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 0.9816 | -3.606 in | 0.291 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.4779 | -6.807 in | 0.291 | True | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.5984 | -1.531 in | 0.582 | True | observed / observed |
| `opening.edge_s[win_w2_arch:left]` | missing | 3.3227 |  |   |  |  | – / grazing |
| `opening.edge_s[win_w2_arch:right]` | missing | 4.9991 |  |   |  |  | – / grazing |
| `opening.bottom[win_w2_arch]` | missing | 1.0668 |  |   |  |  | – / grazing |
| `opening.top[win_w2_arch]` | missing | 3.9624 |  |   |  |  | – / grazing |
| `opening.width[win_w2_arch]` | missing | 1.6764 |  |   |  |  | – / grazing |
| `opening.edge_s[door:left]` | scored | 7.2624 | 10.2795 | 118.783 in | 0.433 | False | grazing / observed |
| `opening.edge_s[door:right]` | scored | 9.4522 | 8.1494 | -51.291 in | 0.433 | False | grazing / observed |
| `opening.bottom[door]` | scored | 0.2116 | 0.0943 | -4.618 in | 0.433 | True | grazing / observed |
| `opening.top[door]` | scored | 4.074 | 3.8205 | -9.98 in | 0.433 | True | grazing / observed |
| `opening.width[door]` | scored | 2.1898 | -2.1301 | -170.075 in | 0.866 | False | grazing / observed |
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.6998 | -1.61 in | 1.061 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.622 | -1.598 in | 0.869 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.5294 | -1.409 in | 0.681 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.5214 | -1.461 in | 0.585 | True | – / – |
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
| `corner.s[rc:c1]` | phantom |  | 2.6782 |   |  |  | – / – |
| `corner.turn[rc:c1]` | phantom |  | 44.4 |   |  |  | – / – |
| `corner.s[rc:c2]` | phantom |  | 7.6244 |   |  |  | – / – |
| `corner.turn[rc:c2]` | phantom |  | 135.05 |   |  |  | – / – |
| `wall.length[rc:w2]` | phantom |  | 4.9462 |   |  |  | – / – |
| `wall.length[rc:w3]` | phantom |  | 3.2724 |   |  |  | – / – |
| `opening.gap[win5~door]` | phantom |  | 7.9257 |   |  |  | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 6%/44%/90% | 17%/57%/93% | 0.3579 | 0.9995 | 0.0003 | observed, 0.9992, 0.0007 | 0.9974 | 0.0007 |
| wall_face | 58856 | 6%/41%/83% | 15%/51%/85% | 0.3583 | 0.902 | 0.0202 | grazing, 0.8953, 0.0664 | 0.8932 | 0.0028 |
| ground_strip | 206971 | 0%/1%/3% | 0%/1%/3% | 0.1109 | 0.0327 | 0.1109 | partial, 0.1921, 0.3229 | 0.1815 | 0.0536 |
| overhead | 1602 | 3%/14%/46% | 2%/12%/52% | 0.4802 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.9967 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.0243, in own unknown voxels 0.1124, flagged generated 0.0.
