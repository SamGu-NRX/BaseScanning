| Method | median / p90 / p95 abs scale err | 15 ft err median / p90 (in) | 26 ft err median / p90 (in) | 26 ft within 6 in | 15 ft within 2 in | declared SD (median), coverage at 2 SD |
|---|---|---|---|---|---|---|
| ARKit non-LiDAR, modern (sim.) | 2.4 / 5.7 / 6.9 % | 4.5 / 10.8 | 7.6 / 18.2 | 40 % | 23 % | – |
| ARKit non-LiDAR, 2018 (sim. from real ADVIO) | 7.1 / 17.8 / 19.4 % | 12.9 / 32.5 | 22.1 / 55.8 | 5 % | 3 % | – |
| Learned metric depth (MoGe-2 / Metric3D, scene median) | 11.0 / 20.1 / 21.4 % | 19.7 / 36.2 | 34.2 / 62.6 | 4 % | 2 % | – |
| Tape 10 ft (with 5 % gross reading errors) | 0.4 / 1.2 / 2.1 % | 1.2 / 3.2 | 1.6 / 4.4 | 94 % | 74 % | – |
| Tape 15 ft (with 5 % gross reading errors) | 0.4 / 1.0 / 1.8 % | 1.1 / 3.0 | 1.5 / 4.0 | 95 % | 75 % | – |
| Tape 10 ft, no gross errors | 0.4 / 1.0 / 1.2 % | 1.1 / 2.7 | 1.5 / 3.7 | 99 % | 77 % | – |
| Tape 6 ft (with 5 % gross reading errors) | 0.5 / 1.5 / 7.1 % | 1.3 / 3.6 | 1.9 / 5.2 | 93 % | 69 % | – |
| Meter base 6.95 in, close-up photo at 1 m (4032 px) | 0.6 / 7.6 / 9.4 % | 1.4 / 12.4 | 2.0 / 22.8 | 88 % | 66 % | – |
| Meter base 6.95 in, from a house photo at 4.5 m | 1.8 / 5.9 / 9.3 % | 3.4 / 10.9 | 5.7 / 18.7 | 52 % | 31 % | – |
| Door leaf 80 in | 2.1 / 8.5 / 13.0 % | 4.1 / 15.5 | 6.6 / 26.6 | 47 % | 30 % | – |
| Brick course, modular assumed, counted | 1.0 / 15.4 / 16.7 % | 2.4 / 26.5 | 3.6 / 47.2 | 61 % | 45 % | – |
| Siding reveal, 6 in assumed | 19.6 / 49.7 / 50.3 % | 34.9 / 89.3 | 60.9 / 154.9 | 14 % | 11 % | – |
| ARKit(2018) + depth | 5.2 / 13.1 / 14.9 % | 9.4 / 23.5 | 16.3 / 40.9 | 18 % | 10 % | 7.7 %, 96 %; re-measure prompted 0 % |
| ARKit(modern) + depth | 2.2 / 5.6 / 6.9 % | 4.1 / 10.3 | 6.9 / 17.6 | 45 % | 27 % | 2.9 %, 91 %; re-measure prompted 0 % |
| Weak refs, no homeowner action: ARKit(2018) + meter(house photo) + door + depth | 1.7 / 4.7 / 6.0 % | 3.2 / 8.7 | 5.4 / 14.9 | 54 % | 33 % | 2.2 %, 88 %; re-measure prompted 0 % |
| Weak refs + meter close-up: ARKit(2018) + meter close-up + door + depth | 0.6 / 6.8 / 8.7 % | 1.4 / 11.2 | 2.1 / 20.6 | 87 % | 65 % | 1.0 %, 89 %; re-measure prompted 0 % |
| Tape 10 ft + ARKit(2018) gate | 0.4 / 1.2 / 2.1 % | 1.2 / 3.2 | 1.6 / 4.4 | 94 % | 74 % | 0.6 %, 90 %; re-measure prompted 0 % |
| Tape 10 ft + meter close-up + ARKit(2018) | 0.4 / 0.9 / 1.1 % | 1.1 / 2.7 | 1.5 / 3.6 | 99 % | 78 % | 0.5 %, 93 %; re-measure prompted 14 % |
| Tape 10 ft + meter close-up + ARKit(modern) | 0.4 / 0.9 / 1.1 % | 1.1 / 2.7 | 1.5 / 3.6 | 99 % | 78 % | 0.5 %, 93 %; re-measure prompted 14 % |
