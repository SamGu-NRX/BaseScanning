| 95 % scale bound | runs | decided sites kept (pass + reject) | GT passes kept | GT rejects kept | unsafe passes (spot fails in truth) | passes on truth-unsure spots | rejects where truth is not reject |
|---|---|---|---|---|---|---|---|
| 0.0 % | 30 | 8/9 (89 %) | 6/7 | 2/2 | 0 | 2 | 0 |
| 0.5 % | 120 | 32/36 (89 %) | 24/28 | 8/8 | 0 | 0 | 0 |
| 1.0 % | 120 | 31/36 (86 %) | 23/28 | 8/8 | 0 | 0 | 0 |
| 1.5 % | 120 | 27/36 (75 %) | 19/28 | 8/8 | 2 | 0 | 0 |
| 2.0 % | 120 | 22/36 (61 %) | 16/28 | 6/8 | 0 | 0 | 0 |
| 3.0 % | 120 | 16/36 (44 %) | 16/28 | 0/8 | 0 | 0 | 0 |
| 4.0 % | 120 | 16/36 (44 %) | 16/28 | 0/8 | 0 | 0 | 0 |
| 6.0 % | 120 | 16/36 (44 %) | 16/28 | 0/8 | 0 | 0 | 0 |
| 8.0 % | 120 | 10/36 (28 %) | 10/28 | 0/8 | 0 | 0 | 0 |
| 12.0 % | 120 | 0/36 (0 %) | 0/28 | 0/8 | 0 | 0 | 0 |
| 18.0 % | 120 | 0/36 (0 %) | 0/28 | 0/8 | 0 | 0 | 0 |

Per check: share of check instances decided (pass or fail) in truth that stay decided and right; wrong = flipped pass <-> fail.

| check | 0.0 % | 1.0 % | 1.5 % | 2.0 % | 3.0 % | 4.0 % | 6.0 % | 12.0 % | 18.0 % | wrong at ≤ 2 % / ≤ 6 % |
|---|---|---|---|---|---|---|---|---|---|---|
| meter_working_space | 100 | 99 | 98 | 97 | 95 | 94 | 91 | 85 | 79 | 0.0 / 0.0 % |
| route_length | 99 | 98 | 97 | 96 | 95 | 93 | 90 | 71 | 58 | 0.0 / 0.0 % |
| drive_clearance | 100 | 99 | 98 | 98 | 97 | 96 | 92 | 79 | 65 | 0.0 / 0.0 % |
| footprint_clear | 89 | 63 | 55 | 48 | 43 | 42 | 35 | 23 | 18 | 0.1 / 0.1 % |
| facing_gap | 99 | 98 | 99 | 98 | 93 | 90 | 86 | 78 | 72 | 0.8 / 0.7 % |
| headroom | 99 | 97 | 97 | 94 | 93 | 92 | 88 | 83 | 75 | 0.5 / 0.4 % |
| opening_clearance | 99 | 97 | 95 | 92 | 86 | 78 | 62 | 28 | 15 | 0.0 / 0.0 % |
| ac_clearance | 100 | 99 | 98 | 98 | 96 | 95 | 93 | 70 | 55 | 0.0 / 0.0 % |
| route_path | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.1 / 0.1 % |
| ground_surface | 99 | 98 | 97 | 98 | 96 | 94 | 94 | 88 | 85 | 0.9 / 1.1 % |
| wall_equipment_above | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 99 | 99 | 0.0 / 0.0 % |
| wall_backing | 99 | 96 | 95 | 94 | 92 | 90 | 85 | 73 | 61 | 0.0 / 0.0 % |
| pool_clearance | 100 | 97 | 96 | 94 | 92 | 89 | 83 | 69 | 54 | 0.0 / 0.0 % |
| gas_clearance | 100 | 95 | 93 | 91 | 85 | 82 | 72 | 43 | 22 | 0.0 / 0.0 % |
| under_window | 98 | 89 | 84 | 80 | 73 | 66 | 58 | 46 | 44 | 0.0 / 0.0 % |

Monte Carlo houses mapped onto the curves (bound interpolated; each method's bound inflated by the factor k that makes |err| <= k x bound for 95 % of houses):

| Method | k | decided sites kept | GT passes kept | houses with bound ≤ 1.5 % / 2 % / 3 % |
|---|---|---|---|---|
| ARKit modern only | 1.00 | 24 % | 31 % | 0 / 0 / 0 % |
| ARKit 2018 only | 1.00 | 0 % | 0 % | 0 / 0 / 0 % |
| Meter close-up, generic cover prior (+ARKit modern) | 1.38 | 41 % | 52 % | 0 / 0 / 0 % |
| Meter close-up, exact model (+ARKit modern) | 1.14 | 53 % | 56 % | 0 / 0 / 71 % |
| Meter close-up, exact model (+ARKit 2018) | 1.15 | 50 % | 53 % | 0 / 0 / 71 % |
| Meter close-up, exact model, no ARKit | 1.19 | 48 % | 53 % | 0 / 0 / 73 % |
| Meter close-up, exact model, public datasheets only (table 35 %) (+ARKit modern) | 1.12 | 47 % | 56 % | 0 / 0 / 27 % |
| Meter close-up, exact model, every close-up registers (+ARKit modern) | 1.18 | 54 % | 56 % | 0 / 0 / 83 % |
| Two meter close-ups (head-on + ~30 deg), exact model (+ARKit modern) | 1.13 | 65 % | 63 % | 0 / 79 / 79 % |
| Two meter close-ups + TrueDepth scan (+ARKit modern) | 1.07 | 71 % | 67 % | 60 / 79 / 92 % |
| Typed objects + ARKit 2018 | 1.47 | 32 % | 37 % | 0 / 0 / 40 % |
| Typed objects only (door, garage, brick, siding) + ARKit modern | 1.25 | 46 % | 49 % | 0 / 18 / 61 % |
| Footprint + ARKit modern | 1.00 | 31 % | 40 % | 0 / 0 / 0 % |
| Passive priors (camera height, stride, depth) + ARKit modern | 1.00 | 35 % | 45 % | 0 / 0 / 0 % |
| Meter exact + typed objects + ARKit modern | 1.12 | 67 % | 63 % | 29 / 72 / 90 % |
| All zero-effort (meter exact, objects, footprint, priors, ARKit modern) | 1.10 | 68 % | 65 % | 35 / 76 / 92 % |
| Stress: all zero-effort with meter registration 70 %, rim depth +-0.6 in, 8 % contour confusion, 20 % door frame/leaf errors | 1.17 | 58 % | 59 % | 3 / 44 / 78 % |
| All zero-effort, ARKit 2018 | 1.12 | 66 % | 63 % | 27 / 71 / 90 % |
| All zero-effort + TrueDepth meter scan (near-zero effort) | 1.06 | 74 % | 69 % | 58 / 89 / 97 % |
| All zero-effort + one targeted extra photo when bound > 2 % | 1.09 | 70 % | 65 % | 38 / 82 / 97 % |
| Recommended: meter anchor (one close-up) + ARKit, objects/footprint as a check, re-shoot meter on conflict | 1.02 | 56 % | 57 % | 0 / 57 / 69 % |
| Recommended: meter anchor (two close-ups) + ARKit, objects/footprint as a check, re-shoot meter on conflict | 1.06 | 67 % | 64 % | 25 / 77 / 77 % |
| Reference: tape 10 ft + meter exact + ARKit modern (A8-style, needs a tape) | 1.08 | 81 % | 77 % | 93 / 94 / 94 % |
