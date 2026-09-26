| Method | abs scale err median / p90 / p95 | 15 ft err median / p90 (in) | 26 ft err median / p90 (in) | 15 ft within 6 in | 26 ft within 6 in | 15 ft within 2 in | 95 % bound median / p90 | coverage of 95 % bound |
|---|---|---|---|---|---|---|---|---|
| ARKit modern only | 2.4 / 5.7 / 6.9 % | 4.4 / 10.8 | 7.6 / 18.2 | 63 % | 40 % | 23 % | 8.5 / 8.8 % | 96 % |
| ARKit 2018 only | 6.8 / 16.7 / 18.0 % | 12.5 / 30.4 | 21.4 / 52.3 | 16 % | 5 % | 3 % | 23.0 / 24.1 % | 100 % |
| Meter close-up, generic cover prior (+ARKit modern) | 1.7 / 4.9 / 6.9 % | 3.1 / 9.2 | 5.3 / 15.5 | 78 % | 55 % | 34 % | 4.3 / 5.2 % | 89 % |
| Meter close-up, exact model (+ARKit modern) | 0.8 / 2.6 / 4.0 % | 1.7 / 5.2 | 2.7 / 8.5 | 93 % | 82 % | 56 % | 1.9 / 4.8 % | 93 % |
| Meter close-up, exact model (+ARKit 2018) | 0.8 / 2.8 / 4.5 % | 1.8 / 5.5 | 2.8 / 8.9 | 91 % | 81 % | 55 % | 2.0 / 6.5 % | 93 % |
| Meter close-up, exact model, no ARKit | 0.8 / 2.8 / 4.7 % | 1.8 / 5.4 | 2.8 / 8.9 | 92 % | 81 % | 55 % | 2.0 / 6.9 % | 93 % |
| Meter close-up, exact model, public datasheets only (table 35 %) (+ARKit modern) | 1.2 / 3.7 / 4.8 % | 2.4 / 7.0 | 3.9 / 11.7 | 86 % | 68 % | 44 % | 4.2 / 5.0 % | 94 % |
| Meter close-up, exact model, every close-up registers (+ARKit modern) | 0.7 / 2.2 / 3.3 % | 1.6 / 4.6 | 2.5 / 7.1 | 94 % | 86 % | 59 % | 1.9 / 4.2 % | 93 % |
| Two meter close-ups (head-on + ~30 deg), exact model (+ARKit modern) | 0.6 / 2.0 / 3.1 % | 1.4 / 4.2 | 2.1 / 6.6 | 95 % | 88 % | 64 % | 1.4 / 4.5 % | 93 % |
| Two meter close-ups + TrueDepth scan (+ARKit modern) | 0.5 / 1.5 / 2.0 % | 1.3 / 3.4 | 1.9 / 5.0 | 98 % | 93 % | 69 % | 1.3 / 2.6 % | 94 % |
| Typed objects + ARKit 2018 | 1.0 / 10.7 / 15.6 % | 2.2 / 19.3 | 3.3 / 33.4 | 78 % | 69 % | 47 % | 2.6 / 19.5 % | 93 % |
| Typed objects only (door, garage, brick, siding) + ARKit modern | 0.8 / 3.2 / 5.7 % | 1.8 / 6.2 | 2.8 / 10.4 | 90 % | 79 % | 54 % | 2.1 / 8.5 % | 93 % |
| Footprint + ARKit modern | 2.2 / 5.5 / 6.9 % | 4.1 / 10.4 | 7.0 / 17.7 | 67 % | 44 % | 26 % | 7.9 / 9.0 % | 96 % |
| Passive priors (camera height, stride, depth) + ARKit modern | 2.1 / 5.2 / 6.3 % | 3.9 / 9.7 | 6.6 / 16.5 | 70 % | 46 % | 28 % | 6.8 / 8.5 % | 97 % |
| Meter exact + typed objects + ARKit modern | 0.6 / 1.6 / 2.1 % | 1.4 / 3.5 | 2.0 / 5.3 | 98 % | 93 % | 67 % | 1.5 / 2.6 % | 93 % |
| All zero-effort (meter exact, objects, footprint, priors, ARKit modern) | 0.5 / 1.5 / 1.9 % | 1.3 / 3.5 | 1.9 / 5.2 | 98 % | 94 % | 68 % | 1.4 / 2.2 % | 93 % |
| Stress: all zero-effort with meter registration 70 %, rim depth +-0.6 in, 8 % contour confusion, 20 % door frame/leaf errors | 0.7 / 2.0 / 2.8 % | 1.6 / 4.2 | 2.4 / 6.5 | 96 % | 88 % | 60 % | 1.8 / 3.3 % | 92 % |
| All zero-effort, ARKit 2018 | 0.6 / 1.6 / 2.1 % | 1.4 / 3.6 | 2.0 / 5.4 | 97 % | 92 % | 67 % | 1.5 / 2.8 % | 93 % |
| All zero-effort + TrueDepth meter scan (near-zero effort) | 0.5 / 1.3 / 1.6 % | 1.3 / 3.2 | 1.8 / 4.6 | 99 % | 96 % | 71 % | 1.4 / 1.9 % | 94 % |
| All zero-effort + one targeted extra photo when bound > 2 % | 0.5 / 1.4 / 1.8 % | 1.3 / 3.2 | 1.9 / 4.9 | 99 % | 95 % | 70 % | 1.4 / 2.0 % | 93 % |
| Recommended: meter anchor (one close-up) + ARKit, objects/footprint as a check, re-shoot meter on conflict | 0.8 / 2.5 / 3.4 % | 1.7 / 4.9 | 2.6 / 7.9 | 94 % | 83 % | 57 % | 1.9 / 4.9 % | 95 % |
| Recommended: meter anchor (two close-ups) + ARKit, objects/footprint as a check, re-shoot meter on conflict | 0.6 / 2.0 / 3.1 % | 1.4 / 4.2 | 2.1 / 6.5 | 95 % | 89 % | 64 % | 1.4 / 4.6 % | 94 % |
| Reference: tape 10 ft + meter exact + ARKit modern (A8-style, needs a tape) | 0.4 / 1.0 / 1.2 % | 1.1 / 2.8 | 1.5 / 3.7 | 98 % | 97 % | 77 % | 1.0 / 1.2 % | 95 % |
