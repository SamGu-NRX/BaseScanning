# ETH3D facade: a real-geometry test scene

`scene.json` for the placement server, built from a real building: the south courtyard wing of the
ETH3D "facade" dataset (an ETH Zurich building, DSLR photos plus a metric laser scan). Doors,
windows and walls are real and measured. The electric meter is invented, because the building has
none where we need it. `case.json` says what a correct server must and must not answer, in the
format the e2e checker reads.

## License

ETH3D data is for research use only. Nothing from it is in this repo: no images, points or crops.
The script reads the dataset from outside the repo and writes its output there too.

## Build

Data: `facade_dslr_undistorted.7z` and `facade_dslr_scan_eval.7z` from eth3d.net, extracted to
`~/house-scanning-data/verify/eth3d/` (it creates `facade/`). Then, from `verification/`:

```
uv run python scenes/eth3d-facade/build_scene.py [--schema path/to/scene.schema.json] [--overlays] [--rules-ref origin/t3/server]
```

This writes `~/house-scanning-data/verify/scenes/eth3d-facade/`: `scene.json`, `scene.zip`
(scene.json plus 7 keyframes at the top level), `bundle/`, and `build_report.json` with every fit
statistic and the rules ref, commit and headroom the scene was built with. The overhead probe
height is `headroom.min_ft` from `server/rules.yaml` at `--rules-ref` (default
`origin/t3/server`, the e2e harness's default server). `case.json` pins that value in
`rules_assumed.headroom`. When the server under test applies another headroom, because its
rules changed or a private override sets one, its `headroom` check reports that threshold and
the e2e run marks the case `assumption mismatch` instead of judging it. Rebuild the scene
against that ref, then update the pin. `--overlays` also draws the wall chain and the annotations on each keyframe. The run
takes about 10 s and is deterministic (fixed RANSAC seed). The output validates against
`server/schemas/scene.schema.json` from `origin/t3/server` (44607e0).

## How each number is made

- **Frame.** The laser scan and the COLMAP poses share one metric frame. I checked this by
  projecting scan points into DSC_0419, where walls, window edges and the door line up. The ground
  is a RANSAC plane fitted to scan points in the courtyard: 1.86 M inliers, 0.97 cm RMS. Its normal
  becomes +y, so heights are above the local ground. The normal is 1.30 degrees off the scanner's
  vertical, which is about the gravel's drainage slope, so this frame is not exactly gravity.
  Scene x is scan x made horizontal, z = x cross y. All lengths are feet. The origin is the ground
  point under the invented meter.
- **Walls.** Each wall's ground line is a RANSAC line fitted in plan to laser points 0.3-8 ft
  high near a rough segment that I read off a top view (`annotations.json` `walls`).
  Neighbouring lines meet at their intersection. The free ends are the last scanned points. I used
  the laser scan rather than the COLMAP points because it is denser (millions of points on this
  wing against 85 k for the whole SfM model) and it is the dataset's ground truth. Each wall's `plus_minus_ft`
  is max(0.1, 3 x the fit's RMS).
- **Chain.** Left to right as seen from the courtyard: w1, a 52.0 ft (15.85 m) wall with five
  ground-floor windows. Then w2, the 11.6 ft (3.54 m) east side of the entrance tower, which
  projects 3.5 m into the courtyard. Then w3, the tower front with the entrance door, 16.2 ft
  (4.95 m). Past w3 the wall steps back into w1's plane, but the scan and SfM points stop there,
  so the chain ends. Both ends are `unexplored`.
- **Poses.** COLMAP gives world-to-camera `R, t` for an OpenCV camera (+y down, +z forward).
  Camera-to-world is `R^T` and `C = -R^T t`. ARKit's camera has +y up and looks along -z, so
  `R_c2w = A . R^T . diag(1, -1, -1)` and `C_scene = A (C - origin) / 0.3048`, where `A` rotates
  scan axes into scene axes. The 16 numbers are that 4x4, column-major. Keyframes are the
  undistorted images resized to 1/4, and fx, fy, cx, cy are scaled by the same factor.
- **Features.** For each image of a feature I clicked four points: one on the left jamb, one on
  the right jamb, one on the sill (or door threshold) and the arch apex. Each point's ray
  `((u-cx)/fx, -(v-cy)/fy, -1)` goes through the pose onto the wall's brick face. The brick face
  sits 0.33 ft behind the ground line on w1, because the stone plinth stands proud. The hits give
  `span_ft` (jamb positions along the chain) and `bottom_ft` / `top_ft` (heights). Every feature
  uses 2 or 3 images, and the value is their mean.
  `plus_minus_ft = max(0.1, largest disagreement between the images over the four values,
  documented floor)`. Only the door has a floor (0.7 ft). It sits in a recess, and both photos
  see it from the east, so their 0.19 ft agreement hides a shared bias that the scan exposes.
- **Coverage.** A stretch counts as observed for a band when one keyframe sees all of that band's
  probe points. A point counts as seen when it is inside the image, on the camera side of its
  wall, not behind another wall of the chain, and within 75 degrees of the wall's normal. The
  75-degree limit is my judgement, not a measured value. The probes are: wall, ground line and
  battery height (3.29 ft); ground, out to 6 ft; overhead, at the rules' headroom (6.5 ft at
  `origin/t3/server` 1d1e7e1) at 0 and 3 ft out; facing,
  3 ft up, straight out. No camera sees w2 at better than grazing, so w2 is in no band.
- **Overheads and facing** come from the scan. An overhead is anything within 3 ft of the wall,
  lower than 12 ft, whose underside is above 4 ft. With a 2 ft floor, the tops of the two bins by
  the door read as overheads, because the scanner saw their tops but not the parts below. Facing is
  the nearest point more than 1 ft out and 1-6 ft high. The 1 ft skips pilasters and sills. It
  reports 60 ft, a lower bound, where the scan saw open ground that far out and nothing in between.

## What is real and what is invented

| Item | Real? | wall | span_ft (s) | bottom-top ft | +/- ft | laser-scan check |
|---|---|---|---|---|---|---|
| electric meter | **invented** | w1 | 0 (5 ft up) | | 0.3 | placed midway between windows 4 and 5 |
| window 1 | real | w1 | -40.08 to -34.97 | 3.00-11.12 | 1.29 | opening -40.20 to -34.60 |
| window 2 | real | w1 | -29.26 to -24.04 | 2.87-11.25 | 0.36 | -29.45 to -23.90 |
| window 3 | real | w1 | -18.59 to -13.26 | 3.14-11.58 | 0.33 | -18.80 to -13.20 |
| window 4 | real | w1 | -8.13 to -2.57 | 3.32-11.73 | 0.27 | -8.15 to -2.55 |
| window 5 | real | w1 | 2.54 to 7.92 | 3.52-11.98 | 0.40 | 2.55 to 8.05 |
| entrance door | real | w3 | 23.83 to 31.01 | 0.69-13.37 | 0.70 | 24.0 to 31.65 |

The scan check column comes from gaps in the laser points on the wall face 4.5-6 ft up. Every
window edge from the photos lies within its stated +/- of the scan's edge. Scan sill heights come
out 0.1-0.5 ft lower than `bottom_ft`, because I clicked the top edge of the stone sill and the
scan measures the brick below it. There is no gas meter; any stretch it could constrain already
fails on a window.

Present but not annotated:

- A narrow window on w2, visible only edge-on in DSC_0419. w2 is unobserved, which is the point.
- A downspout in the w1/w2 corner. It shows up only as the 1.03 ft facing depth there and a 6.1 ft
  overhead.
- Two bins beside the door. They show up as the 2.3-2.5 ft facing depths; `objects` has no type
  for them.

## Expected placement outcomes

Rule values come from `server/rules.yaml` at 44607e0: `battery.width_ft` 2.5833 (31 in),
`battery.depth_ft` 1.8333, `clearances.opening_ft` 3.0 for `openings.types` [door, window] (no
height exemption: `exempt_bottom_above_ft` is null), `route.max_ft` 20.0 and
`route.corner_allowance_ft` 0.5 per corner. The solver measures opening clearance as the plan
distance from the footprint to the opening's stretch of wall line, so sill height does not enter.
`case.json` lists ranges of the battery's start position (its left edge in s, as the server's
`sweep` reports it), where start = centre - 1.2917.

- **A window or door blocks every start within 3 ft of it.** For a span [a, b] the blocked starts
  are (a - 2.58 - 3, b + 3). Window 4 gives (-8.13 - 5.58, -2.57 + 3) = (-13.71, 0.43). Window 5
  gives (-3.04, 10.92), clipped at the w1/w2 corner (9.9). The door gives (18.25, 34.01), of which
  w3 holds [21.52, 34.01].
- **Nothing on w1 can pass.** The gaps between windows are 5.71, 5.45, 5.13 and 5.11 ft. A battery
  needs 2.58 + 3 + 3 = 8.58 ft, so each gap falls short by at least 2.87 ft. That is more than
  twice the largest window error (1.29 ft), so the error bars can't rescue it. The window bands
  overlap end to end and cover every start on w1, [-42.1, 9.9].
- **Beyond reach.** Left of the meter the route runs to the battery's right edge, so it is
  -(start + 2.58) > 20 when start < -22.58. On w2 the route is start + 0.5 (one corner), over 20
  past start 19.5. Every start on w3 has route >= 21.52 + 2 x 0.5 = 22.52. Measured in a straight
  line, w3's start is only 15.1 ft from the meter, but the door blocks starts up to 34.01, and a
  battery starting there is 25.0 ft away in a straight line. So w3 fails under either reading of
  "within 20 ft".
- **w2 must never pass**, because no band observes it.
- **The decision must be `manual_review`.** It can't be `pass`, because no start passes. It can't
  be `reject` either. w2 is in reach and unseen, and a battery on it starting at 12.93 or later
  clears window 5 on the nominal numbers. Window 5's edge is 1.98 ft from the corner and the
  battery stands 1.83 ft proud, so the gap is sqrt(d^2 + 0.15^2), where d = start - 9.9; that
  reaches 3 at d = 3.0. An area that could hold a valid spot was not seen. Both chain ends lie
  beyond reach, so only w2 blocks a reject.
- The server at 44607e0 answered `manual_review` (unsure_checks, unobserved_area) with its spot on
  w2 starting at 12.405. There the gap to window 5 is 2.47 ft. The check allows window 5's ± plus
  w2's ± (0.40 + 0.13 = 0.53), and 2.47 + 0.53 reaches 3.0, so the check is UNSURE, not FAIL. That
  agrees with the numbers above.
