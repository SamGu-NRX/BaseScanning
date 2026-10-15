# A7 degradation sweep: generated tables (python summarize.py)

    python summarize.py > results/summary.md

Fixed KD set per path x scene: the key distances (KDs) the path's `dslr_ref` run scored on truly observed
elements; a degraded run that loses one counts it as lost, not as a smaller sample. Two views of it:
- ground-free geometry: opening edge positions, widths, gaps, corner / wall-length KDs, and opening heights
  (top error minus bottom error), none of which depend on where the extractor put the ground;
- ground datum: the median error of the ground-referenced KDs (meter height, sills, heads). The reference
  extractor fits the ground as the best-supported low plane, which flips to a ~0.6 m ledge on the facade
  when the wall base is thinly covered (section 5 of the report).
Bar (2-6 in, as A1 applied it), on the raw fixed set: `in` = all kept and within 6 in with p90 <= 4 in;
`edge` = >= 90 % kept and p90 <= 6 in; `out` otherwise; `broken` = registration < 90 % of the `dslr_ref` count (A1), scorer
failure (meter wall missing), or < 50 % of the fixed KDs kept.


### facade: A1 COLMAP + non-LiDAR ARKit priors (metric, rigid)

Fixed set: 21 KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).

| Condition | Registered | Keypts (med) | Verified pairs | Centre RMS | Scale err | KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dslr_ref | 76/76 | 11398 | 2042 | 14.3 cm | -1.42 % | 21/21 | 3.2 / 5.5 / 5.9 | 1.6 / 2.9 / 4.0 | -4.3 | 1.7 / 9.0, 19 % | edge |
| base | 73/76 | 13024 | 1852 | 14.3 cm | -1.45 % | 21/21 | 4.0 / 24.9 / 25.8 | 1.4 / 3.1 / 4.7 | -24.6 | 2.2 / 9.9, 15 % | out |
| noise_iso400 | 73/76 | 11808 | 1817 | 13.3 cm | -1.34 % | 21/21 | 3.6 / 37.7 / 93.7 | 1.5 / 15.9 / 59.3 | -35.7 | 1.9 / 8.7, 15 % | out |
| noise_iso1600 | 73/76 | 10963 | 1725 | 6.2 cm | -0.61 % | 16/21 | 0.5 / 19.9 / 20.5 | 0.3 / 0.9 / 1.2 | -19.3 | 2.0 / 7.7, 7 % | out |
| noise_iso6400 | 72/76 | 9982 | 1588 | 10.4 cm | -1.02 % | 0/21 | — | — | — | — | broken (no meter wall) |
| blur_8 | 73/76 | 11774 | 1524 | 14.4 cm | -1.45 % | 21/21 | 4.1 / 26.3 / 27.5 | 1.4 / 3.2 / 4.9 | -25.8 | 2.8 / 9.0, 4 % | out |
| blur_16 | 65/76 | 9700 | 1165 | 15.4 cm | -1.69 % | 0/21 | — | — | — | — | broken (registration) |
| hdr_sharp | 73/76 | 12588 | 1812 | 17.4 cm | -1.77 % | 21/21 | 3.6 / 37.7 / 81.4 | 1.9 / 9.1 / 46.9 | -20.2 | 1.8 / 9.7, 14 % | out |
| combo_day | 73/76 | 13540 | 1810 | 15.5 cm | -1.25 % | 16/21 | 6.4 / 20.2 / 21.4 | 1.6 / 4.3 / 8.4 | -19.7 | 2.8 / 9.6, 15 % | out |
| combo_dusk | 66/76 | 9288 | 1220 | 13.6 cm | -1.20 % | 0/21 | — | — | — | — | broken (registration) |

### electro: A1 COLMAP + non-LiDAR ARKit priors (metric, rigid)

Fixed set: 21 KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).

| Condition | Registered | Keypts (med) | Verified pairs | Centre RMS | Scale err | KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dslr_ref | 41/45 | 6865 | 346 | 5.7 cm | +2.20 % | 21/21 | 2.6 / 5.1 / 7.0 | 3.3 / 5.2 / 7.0 | +2.4 | 0.8 / 3.9, 9 % | edge |
| base | 39/45 | 8022 | 272 | 7.0 cm | +2.69 % | 21/21 | 2.4 / 6.7 / 7.8 | 2.9 / 6.7 / 7.8 | +1.2 | 0.8 / 4.6, 8 % | out |
| noise_iso400 | 33/45 | 4538 | 256 | 7.2 cm | +2.73 % | 21/21 | 3.4 / 29.0 / 182.7 | 3.6 / 108.0 / 182.7 | +0.9 | 0.7 / 2.5, 4 % | broken (registration) |
| noise_iso1600 | 32/45 | 4569 | 249 | 10294.6 cm | +6911.06 % | 0/21 | — | — | — | — | broken (registration) |
| noise_iso6400 | 23/45 | 3427 | 204 | 56.4 cm | +5.89 % | 0/21 | — | — | — | — | broken (registration) |
| blur_8 | 32/45 | 3439 | 202 | 1090.7 cm | +626.89 % | 0/21 | — | — | — | — | broken (registration) |
| blur_16 | 14/45 | 1752 | 131 | 17.8 cm | +6.70 % | 0/21 | — | — | — | — | broken (registration) |
| hdr_sharp | 40/45 | 13808 | 272 | 6.0 cm | +2.30 % | 21/21 | 2.0 / 5.6 / 7.9 | 3.2 / 5.9 / 7.9 | +2.0 | 0.8 / 4.2, 12 % | edge |
| combo_day | 39/45 | 10121 | 264 | 8.9 cm | +2.64 % | 21/21 | 3.4 / 6.3 / 10.4 | 3.6 / 6.7 / 10.4 | +2.2 | 1.0 / 5.6, 6 % | out |
| combo_dusk | 15/45 | 3697 | 152 | 20.3 cm | +7.11 % | 0/21 | — | — | — | — | broken (registration) |

### facade: pi3 on its own poses, scaled by exact camera centres

Fixed set: 26 KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).

| Condition | Pose centre RMS (Sim3) | Rot err (med) | KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |
|---|---|---|---|---|---|---|---|---|
| dslr_ref | 28.6 cm | 1.18° | 26/26 | 1.3 / 4.7 / 6.0 | 1.0 / 3.1 / 3.6 | +2.6 | 2.3 / 7.8, 72 % | edge |
| base | 50.8 cm | 2.39° | 26/26 | 2.5 / 8.7 / 11.0 | 1.8 / 5.7 / 9.3 | +4.4 | 2.9 / 8.4, 64 % | out |
| noise_iso400 | 51.7 cm | 3.12° | 0/26 | — | — | — | 3.1 / 8.4, 76 % | surface only |
| noise_iso1600 | 60.0 cm | 1.40° | 26/26 | 2.4 / 11.0 / 13.2 | 1.7 / 5.6 / 11.7 | +4.1 | 2.8 / 8.0, 61 % | out |
| noise_iso6400 | 53.7 cm | 2.18° | 0/26 | — | — | — | 3.0 / 9.5, 71 % | surface only |
| blur_8 | 46.4 cm | 1.48° | 0/26 | — | — | — | 3.1 / 8.3, 74 % | surface only |
| blur_16 | 47.1 cm | 1.28° | 26/26 | 15.0 / 1402.4 / 1725.7 | 131.3 / 1481.0 / 1725.7 | +6.2 | 3.6 / 8.6, 52 % | out |
| hdr_sharp | 62.0 cm | 1.10° | 0/26 | — | — | — | 2.8 / 8.5, 77 % | surface only |
| combo_day | 65.4 cm | 1.94° | 0/26 | — | — | — | 3.6 / 9.7, 83 % | surface only |
| combo_dusk | 69.9 cm | 1.63° | 26/26 | 1.5 / 6.2 / 9.5 | 1.1 / 2.6 / 4.3 | +2.8 | 2.3 / 7.2, 75 % | out |

### electro: pi3 on its own poses, scaled by exact camera centres

Fixed set: 26 KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).

| Condition | Pose centre RMS (Sim3) | Rot err (med) | KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |
|---|---|---|---|---|---|---|---|---|
| dslr_ref | 3.4 cm | 0.61° | 26/26 | 2.7 / 4.7 / 5.7 | 3.4 / 5.7 / 5.9 | +1.1 | 2.4 / 6.8, 44 % | edge |
| base | 4.0 cm | 0.32° | 26/26 | 3.1 / 6.2 / 7.6 | 4.5 / 6.7 / 7.6 | +0.1 | 1.0 / 5.0, 87 % | out |
| noise_iso400 | 5.8 cm | 0.32° | 0/26 | — | — | — | 1.1 / 3.0, 95 % | surface only |
| noise_iso1600 | 4.1 cm | 0.31° | 26/26 | 2.7 / 4.9 / 6.1 | 4.1 / 5.7 / 6.1 | -0.3 | 1.2 / 3.3, 92 % | edge |
| noise_iso6400 | 5.3 cm | 0.72° | 0/26 | — | — | — | 1.5 / 4.3, 88 % | surface only |
| blur_8 | 4.5 cm | 0.28° | 0/26 | — | — | — | 1.2 / 4.6, 85 % | surface only |
| blur_16 | 4.6 cm | 0.33° | 26/26 | 2.4 / 4.8 / 6.5 | 3.6 / 5.3 / 6.5 | +0.4 | 1.4 / 4.6, 84 % | edge |
| hdr_sharp | 4.7 cm | 0.46° | 0/26 | — | — | — | 1.1 / 5.3, 88 % | surface only |
| combo_day | 6.3 cm | 0.70° | 0/26 | — | — | — | 1.1 / 3.1, 95 % | surface only |
| combo_dusk | 4.6 cm | 0.45° | 6/26 | 3.9 / 15.8 / 17.8 | 8.9 / 16.6 / 17.8 | +0.0 | 1.2 / 3.4, 90 % | broken (KDs lost) |

### facade: pi3 depth placed at exact poses, one global scale

Fixed set: 16 KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).

| Condition | Pose centre RMS (Sim3) | Rot err (med) | KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |
|---|---|---|---|---|---|---|---|---|
| dslr_ref | 28.6 cm | 1.18° | 16/16 | 1.7 / 5.2 / 5.5 | 1.7 / 5.1 / 5.9 | +3.2 | 2.7 / 7.9, 68 % | edge |
| base | 50.8 cm | 2.39° | 16/16 | 1.8 / 4.8 / 6.3 | 1.5 / 4.4 / 6.3 | +2.1 | 3.6 / 8.9, 59 % | edge |
| noise_iso400 | 51.7 cm | 3.12° | 6/16 | 3.5 / 6.0 / 7.0 | 5.4 / 7.5 / 7.7 | +1.5 | 3.5 / 9.0, 58 % | broken (KDs lost) |
| noise_iso1600 | 60.0 cm | 1.40° | 16/16 | 6.4 / 1016.2 / 1213.6 | 69.5 / 1065.7 / 1213.6 | +3.2 | 3.2 / 8.6, 54 % | out |
| noise_iso6400 | 53.7 cm | 2.18° | 11/16 | 1.8 / 3.4 / 3.5 | 1.8 / 3.6 / 3.7 | +1.6 | 3.4 / 9.4, 59 % | out |
| blur_8 | 46.4 cm | 1.48° | 16/16 | 3.5 / 8.5 / 11.9 | 3.6 / 8.0 / 11.9 | +4.5 | 3.7 / 9.2, 52 % | out |
| blur_16 | 47.1 cm | 1.28° | 16/16 | 3.0 / 7.5 / 10.6 | 3.3 / 7.4 / 10.6 | +3.6 | 3.9 / 9.5, 50 % | out |
| hdr_sharp | 62.0 cm | 1.10° | 16/16 | 2.6 / 6.7 / 8.9 | 2.5 / 6.0 / 8.9 | +3.0 | 3.2 / 9.2, 63 % | out |
| combo_day | 65.4 cm | 1.94° | 6/16 | 3.2 / 7.8 / 9.1 | 4.9 / 9.5 / 10.8 | +0.1 | 3.2 / 9.5, 65 % | broken (KDs lost) |
| combo_dusk | 69.9 cm | 1.63° | 6/16 | 2.8 / 6.3 / 7.0 | 5.0 / 7.1 / 7.7 | -0.3 | 2.7 / 7.4, 71 % | broken (KDs lost) |

### electro: pi3 depth placed at exact poses, one global scale

Fixed set: 26 KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).

| Condition | Pose centre RMS (Sim3) | Rot err (med) | KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |
|---|---|---|---|---|---|---|---|---|
| dslr_ref | 3.4 cm | 0.61° | 26/26 | 1.9 / 3.7 / 4.0 | 2.0 / 4.0 / 4.3 | +1.2 | 2.5 / 6.8, 47 % | in |
| base | 4.0 cm | 0.32° | 26/26 | 1.6 / 2.9 / 4.0 | 2.3 / 3.6 / 4.0 | +0.6 | 1.2 / 4.4, 86 % | in |
| noise_iso400 | 5.8 cm | 0.32° | 6/26 | 3.6 / 15.9 / 18.6 | 9.3 / 17.0 / 18.6 | +0.2 | 1.3 / 4.1, 93 % | broken (KDs lost) |
| noise_iso1600 | 4.1 cm | 0.31° | 26/26 | 2.5 / 5.0 / 6.0 | 3.4 / 5.4 / 6.0 | -0.1 | 1.2 / 3.7, 91 % | edge |
| noise_iso6400 | 5.3 cm | 0.72° | 6/26 | 5.0 / 14.3 / 17.5 | 8.7 / 15.6 / 17.5 | +0.0 | 1.3 / 4.8, 85 % | broken (KDs lost) |
| blur_8 | 4.5 cm | 0.28° | 26/26 | 2.2 / 4.3 / 5.9 | 3.4 / 4.9 / 5.9 | +0.5 | 1.3 / 5.2, 85 % | edge |
| blur_16 | 4.6 cm | 0.33° | 6/26 | 1.4 / 4.7 / 5.5 | 2.8 / 5.0 / 5.5 | +0.3 | 1.5 / 4.8, 84 % | broken (KDs lost) |
| hdr_sharp | 4.7 cm | 0.46° | 26/26 | 2.3 / 4.3 / 5.1 | 2.8 / 4.7 / 5.1 | +0.8 | 1.1 / 3.8, 88 % | edge |
| combo_day | 6.3 cm | 0.70° | 26/26 | 1.9 / 3.7 / 4.5 | 2.5 / 4.2 / 4.5 | +1.4 | 1.2 / 4.0, 92 % | in |
| combo_dusk | 4.6 cm | 0.45° | 21/26 | 2.9 / 5.9 / 6.5 | 4.3 / 6.0 / 6.5 | +0.2 | 1.3 / 3.7, 91 % | out |

### facade: DA3-Large fed exact poses + frame intrinsics

Fixed set: 26 KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).

| Condition | Pose centre RMS (Sim3) | Rot err (med) | KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |
|---|---|---|---|---|---|---|---|---|
| dslr_ref | 0.0 cm | 0.00° | 26/26 | 2.4 / 6.5 / 8.5 | 3.6 / 7.2 / 8.5 | +0.7 | 3.1 / 9.6, 50 % | out |
| base | 0.0 cm | 0.00° | 11/26 | 1.2 / 2.8 / 3.0 | 1.5 / 3.2 / 3.7 | +0.8 | 3.6 / 9.7, 45 % | broken (KDs lost) |
| noise_iso400 | 0.0 cm | 0.00° | 26/26 | 4.9 / 11.1 / 20.7 | 4.7 / 12.2 / 20.7 | -6.0 | 3.2 / 8.1, 51 % | out |
| noise_iso1600 | 0.0 cm | 0.00° | 11/26 | 5.1 / 13.1 / 13.9 | 7.2 / 15.0 / 19.0 | +1.9 | 4.0 / 8.6, 38 % | broken (KDs lost) |
| noise_iso6400 | 0.0 cm | 0.00° | 11/26 | 7.1 / 19.2 / 22.0 | 6.1 / 8.3 / 8.9 | -14.2 | 2.9 / 8.1, 52 % | broken (KDs lost) |
| blur_8 | 0.0 cm | 0.00° | 21/26 | 6.0 / 16.4 / 25.4 | 8.8 / 18.6 / 25.4 | +1.5 | 4.4 / 9.2, 32 % | out |
| blur_16 | 0.0 cm | 0.00° | 11/26 | 8.8 / 13.4 / 16.9 | 12.0 / 17.5 / 19.0 | +0.5 | 2.8 / 9.7, 55 % | broken (KDs lost) |
| hdr_sharp | 0.0 cm | 0.00° | 16/26 | 1.1 / 3.0 / 4.3 | 1.0 / 1.9 / 2.0 | +1.3 | 3.6 / 8.6, 45 % | out |
| combo_day | 0.0 cm | 0.00° | 11/26 | 1.3 / 2.7 / 3.1 | 1.3 / 2.7 / 2.8 | +1.7 | 4.1 / 8.8, 37 % | broken (KDs lost) |
| combo_dusk | 0.0 cm | 0.00° | 21/26 | 10.4 / 31.5 / 70.2 | 12.3 / 45.0 / 70.2 | -10.3 | 3.3 / 9.8, 45 % | out |

### electro: DA3-Large fed exact poses + frame intrinsics

Fixed set: 26 KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).

| Condition | Pose centre RMS (Sim3) | Rot err (med) | KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |
|---|---|---|---|---|---|---|---|---|
| dslr_ref | 0.0 cm | 0.01° | 26/26 | 2.2 / 4.0 / 7.8 | 2.5 / 5.2 / 7.8 | +0.7 | 0.9 / 4.3, 94 % | edge |
| base | 0.0 cm | 0.01° | 26/26 | 2.4 / 5.4 / 10.9 | 3.3 / 5.8 / 10.9 | +0.5 | 1.1 / 4.4, 89 % | edge |
| noise_iso400 | 0.0 cm | 0.01° | 26/26 | 2.5 / 7.6 / 15.1 | 4.0 / 8.0 / 15.1 | +0.2 | 1.1 / 4.5, 87 % | out |
| noise_iso1600 | 0.0 cm | 0.01° | 6/26 | 1.2 / 2.2 / 2.5 | 1.8 / 2.4 / 2.5 | +0.4 | 1.1 / 4.4, 85 % | broken (KDs lost) |
| noise_iso6400 | 0.0 cm | 0.01° | 26/26 | 2.1 / 5.1 / 10.9 | 3.0 / 6.8 / 10.9 | +0.3 | 1.2 / 4.5, 81 % | edge |
| blur_8 | 0.0 cm | 0.01° | 6/26 | 3.5 / 38.6 / 39.1 | 22.0 / 38.8 / 39.1 | +0.0 | 1.0 / 4.8, 91 % | broken (KDs lost) |
| blur_16 | 0.0 cm | 0.01° | 26/26 | 2.0 / 6.3 / 12.6 | 3.1 / 6.6 / 12.6 | +0.3 | 1.0 / 4.2, 89 % | out |
| hdr_sharp | 0.0 cm | 0.01° | 6/26 | 1.6 / 2.7 / 3.1 | 1.8 / 2.7 / 3.1 | +0.8 | 1.3 / 5.4, 81 % | broken (KDs lost) |
| combo_day | 0.0 cm | 0.01° | 26/26 | 2.4 / 4.5 / 9.5 | 2.7 / 5.3 / 9.5 | +1.0 | 1.7 / 5.3, 84 % | edge |
| combo_dusk | 0.0 cm | 0.01° | 26/26 | 2.4 / 5.8 / 11.7 | 3.0 / 6.8 / 11.7 | +1.3 | 1.7 / 4.8, 77 % | edge |
