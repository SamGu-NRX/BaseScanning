# Geometry score: eth3d-facade/A8-plus1p5 vs eth3d-facade/gt-laser

Producer `A8-plus1p5`. Status counts: {'scored': 31, 'missing': 25, 'extent_limited': 1, 'phantom': 0, 'scale_reference': 0}.

**Key distances (31 scored):** median 1.12 in, p90 5.86 in, max 33.9 in; ≤2 in 68%, ≤4 in 84%, ≤6 in 90%; within the recon's own bound 97%.

**Scale:** KD ratio median 1.01662 (n=6, error 1.662 %), landmark Umeyama 1.01647. Alignment: {'metric_declared': True, 'mode': 'centres', 'requested': 'auto', 'centre_collinearity': 0.266, 'similarity_scale': 0.985222, 'method': 'rigid (metric cloud): camera centres (Umeyama) -> GT cameras', 'cloud_scale_error_pct': 1.5, 'rotation_residual_deg_median': 0.039, 'camera_rms_m': 0.1508, 'cameras': 76}.

**Anchor:** ICP translation 0.1074 m [-0.063, 0.0535, -0.0686], yaw -0.044°, tilt 0.032°.

**On truly observed elements:** 26 KDs, median 1.27 in, p90 5.3 in. Elements the recon would claim observed but the truth grades grazing/unobserved: none.

| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |
|---|---|---|---|---|---|---|---|
| `meter.height` | scored | 1.524 | 1.449 | -2.953 in | 0.262 | True | observed / observed |
| `corner.s[c1]` | missing | 3.0179 |  |   |  |  | – / – |
| `corner.turn[c1]` | missing | 91.11 |  |   |  |  | – / – |
| `corner.s[c2]` | missing | 6.5604 |  |   |  |  | – / – |
| `corner.turn[c2]` | missing | -91.08 |  |   |  |  | – / – |
| `wall.length[w1]` | extent_limited | 15.85 | 15.3915 | -18.051 in | 1.112 | True | – / – |
| `wall.length[w2]` | missing | 3.5426 |  |   |  |  | – / – |
| `wall.length[w3]` | missing | 4.9485 |  |   |  |  | – / – |
| `opening.edge_s[win1:left]` | scored | -12.2164 | -12.4187 | -7.965 in | 0.586 | True | observed / observed |
| `opening.edge_s[win1:right]` | scored | -10.6588 | -10.8331 | -6.862 in | 0.586 | True | observed / observed |
| `opening.bottom[win1]` | scored | 0.9144 | 0.9399 | 1.004 in | 0.586 | True | observed / observed |
| `opening.top[win1]` | scored | 3.3882 | 3.435 | 1.843 in | 0.586 | True | observed / observed |
| `opening.width[win1]` | scored | 1.5576 | 1.5856 | 1.102 in | 1.172 | True | observed / observed |
| `opening.edge_s[win2:left]` | scored | -8.9181 | -9.0669 | -5.858 in | 0.486 | True | observed / observed |
| `opening.edge_s[win2:right]` | scored | -7.3281 | -7.4487 | -4.748 in | 0.486 | True | observed / observed |
| `opening.bottom[win2]` | scored | 0.8755 | 0.8743 | -0.047 in | 0.486 | True | observed / observed |
| `opening.top[win2]` | scored | 3.4292 | 3.4503 | 0.831 in | 0.486 | True | observed / observed |
| `opening.width[win2]` | scored | 1.59 | 1.6182 | 1.11 in | 0.972 | True | observed / observed |
| `opening.edge_s[win3:left]` | scored | -5.6655 | -5.7627 | -3.827 in | 0.387 | True | observed / observed |
| `opening.edge_s[win3:right]` | scored | -4.043 | -4.1117 | -2.705 in | 0.387 | True | observed / observed |
| `opening.bottom[win3]` | scored | 0.958 | 0.9322 | -1.016 in | 0.387 | True | observed / observed |
| `opening.top[win3]` | scored | 3.5282 | 3.5249 | -0.13 in | 0.387 | True | observed / observed |
| `opening.width[win3]` | scored | 1.6225 | 1.651 | 1.122 in | 0.774 | True | observed / observed |
| `opening.edge_s[win4:left]` | scored | -2.4778 | -2.5212 | -1.709 in | 0.29 | True | observed / observed |
| `opening.edge_s[win4:right]` | scored | -0.7831 | -0.7978 | -0.579 in | 0.29 | True | observed / observed |
| `opening.bottom[win4]` | scored | 1.0116 | 0.9618 | -1.961 in | 0.29 | True | observed / observed |
| `opening.top[win4]` | scored | 3.5756 | 3.5472 | -1.118 in | 0.29 | True | observed / observed |
| `opening.width[win4]` | scored | 1.6947 | 1.7234 | 1.13 in | 0.58 | True | observed / observed |
| `opening.edge_s[win5:left]` | scored | 0.7754 | 0.785 | 0.378 in | 0.287 | True | observed / observed |
| `opening.edge_s[win5:right]` | scored | 2.4127 | 2.4487 | 1.417 in | 0.287 | True | observed / observed |
| `opening.bottom[win5]` | scored | 1.0732 | 0.9986 | -2.937 in | 0.287 | True | observed / observed |
| `opening.top[win5]` | scored | 3.6508 | 3.5968 | -2.126 in | 0.287 | True | observed / observed |
| `opening.width[win5]` | scored | 1.6373 | 1.6637 | 1.039 in | 0.574 | True | observed / observed |
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
| `opening.gap[win1~win2]` | scored | 1.7407 | 1.7662 | 1.004 in | 1.072 | True | – / – |
| `opening.gap[win2~win3]` | scored | 1.6626 | 1.686 | 0.921 in | 0.873 | True | – / – |
| `opening.gap[win3~win4]` | scored | 1.5652 | 1.5905 | 0.996 in | 0.677 | True | – / – |
| `opening.gap[win4~win5]` | scored | 1.5585 | 1.5828 | 0.957 in | 0.577 | True | – / – |
| `opening.gap[win5~win_w2_arch]` | missing | 0.91 |  |   |  |  | – / – |
| `opening.gap[win_w2_arch~door]` | missing | 2.2633 |  |   |  |  | – / – |
| `facing.depth[w1@2.79]` | missing | 0.309 |  |   |  |  | – / – |
| `facing.depth[w2@3.14]` | missing | 0.315 |  |   |  |  | – / – |
| `facing.depth[w3@6.68]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@6.93]` | missing | 0.749 |  |   |  |  | – / – |
| `facing.depth[w3@9.94]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.19]` | missing | 0.708 |  |   |  |  | – / – |
| `facing.depth[w3@10.44]` | missing | 0.708 |  |   |  |  | – / – |
| `overhead.clearance[w1@2.79]` | scored | 2.136 | 1.275 | -33.898 in | 0.05 | False | – / – |

| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) | truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |
|---|---|---|---|---|---|---|---|---|---|
| meter_wall | 49689 | 6%/44%/90% | 16%/55%/91% | 0.3574 | 0.9995 | 0.0003 | observed, 0.9992, 0.0007 | 0.9993 | 0.0008 |
| wall_face | 58856 | 6%/41%/82% | 16%/51%/83% | 0.3694 | 0.902 | 0.0347 | grazing, 0.8953, 0.0664 | 0.8937 | 0.003 |
| ground_strip | 206971 | 0%/0%/3% | 0%/1%/3% | 0.1306 | 0.0327 | 0.1306 | partial, 0.1921, 0.3229 | 0.1747 | 0.0276 |
| overhead | 1602 | 8%/31%/71% | 8%/25%/64% | 0.4755 | 1.0 | 0.0 | observed, 1.0, 0.0 | 0.996 | 0.0 |

**Unobserved geometry** (recon points within 10.0 m): unseen by any input camera 0.028, in own unknown voxels 0.1468, flagged generated 0.0.
