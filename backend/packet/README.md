# packetgen: synthetic capture packets

Makes realistic capture packets in the v0.1 format of [the capture packet spec](../../docs/capture-packet-spec.md) from test image sets, so server-side paths can run before the iOS team sends real packets. **Every packet it writes has `source.kind = "synthetic"` and is never evidence.**

## What it does

1. **Clean poses.** For ETH3D it reads the laser-aligned ground-truth COLMAP model. For any frame sequence it runs pycolmap SIFT, matching and incremental mapping. Single photos skip this step (step 6).
2. **ARKit conventions** (`conventions.py`, `align.py`, `images.py`):
   - Gravity is solved from scene planes (walls must contain "up", ground must be normal to it), not from the camera average, which the photographer's pitch biases by 12° on ETH3D facade.
   - The world gets ARKit's `.gravity` frame: origin at the first frame, −z its horizontal facing.
   - Portrait images are turned into the landscape sensor layout, center-cropped to an ARKit video aspect and resized to 1920 × 1440 (or 1920 × 1080). Intrinsics follow the pixels exactly.
   - Metric scale comes from the reference when it has one (ETH3D). Otherwise the camera height above the fitted ground is assumed to be 1.40 m (±12%; ±20% when the ground comes from the lowest points instead of a plane), or, as a last resort, a 6 m viewing distance (±40%). The truth file records which.
3. **ARKit-like error** (`noise.py`, `profiles.json`), always applied because the clean poses come from the same images:
   - a per-epoch scale;
   - a yaw random walk and a position random walk per metre walked;
   - per-frame jitter and a per-session severity multiplier;
   - an intrinsics error;
   - tracking states (`limited.*`) and occasional tracking resets, which start a new epoch with a new origin and heading.
4. **Other sensors** (`sensors.py`, `depth.py`), all synthetic:
   - Core Motion at 100 Hz (`xTrueNorthZVertical`, with gait and sensor noise), derived from a PCHIP and rotation-spline trajectory through the photos;
   - compass heading with a magnetic bias near the house, and 1 Hz GPS with warm-up and correlated multipath;
   - feature points (SfM points this photo observed, re-expressed in the drifted world);
   - plane anchors (RANSAC within 8 m of the walk; vertical planes are missed more often without LiDAR);
   - LiDAR depth and confidence (laser scan rendered at 256 × 192 from the clean pose, plus range noise, smoothing, and confidence by range and edges);
   - EXIF and exposure.
5. **Variants per house.** They share one set of keyframe JPEGs; each variant has its own manifest.

   | Variant | Tier | Noise profile | Device |
   | --- | --- | --- | --- |
   | `arkit-lidar` | `arkit_lidar` | `lidar_modern` | iPhone 15 Pro (`iPhone16,1`) |
   | `arkit-nonlidar` | `arkit` | `nonlidar_modern` (**assumed**) | iPhone 15 (`iPhone15,4`) |
   | `arkit-nonlidar-2018` | `arkit` | `nonlidar_2018` (worst case) | iPhone 6s (`iPhone8,1`), iOS 15, so no `exif` |
   | `pose-free` | `photo_only` | none | nothing derived from the reconstruction: no pose, no SfM intrinsics (EXIF 35 mm focal only), no features, planes, depth, motion or heading; a coarse per-photo location |
6. **Single photos** (`photos.py`) get a `photo_only` packet with EXIF only. Missing EXIF is synthesized and listed in `source.synthesizedFields`; an absent focal length is assumed to be 26 mm.

## Noise calibration (PR #12)

`python -m packetgen.calibrate` fits the drift terms by simulating house walks and scoring them with PR #12's own metric: the displacement over D ft of walked path, turned by the heading offset at the window start. Full table: [calibration.md](calibration.md).

| Profile | Basis | Position error after 3 / 10 / 20 / 30 ft, median / p90 (in) |
| --- | --- | --- |
| `lidar_modern` | MARViN iPhone 14 Pro Max, 35 walks (PR #12 `modern_arkit.md`). Yaw drift fixed by MARViN's 0.6° heading steadiness | target 1.4/4.0, 3.1/8.6, 5.1/13.4, 6.9/18.5; achieved 1.5/4.4, 3.1/8.7, 4.9/13.5, 6.6/17.3 |
| `nonlidar_2018` | ADVIO iPhone 6s (PR #12 `advio_drift.md`): walk scales 0.838 / 0.943 / 0.951, beyond-scale drift from the three-cornered-hat split, 1 of 4 walks lost tracking | beyond scale: target 3.5/6.4, 7.1/12.9, 15.3/27.9, 21.2/38.7; achieved 3.5/6.8, 7.1/14.6, 12.7/28.3, 18.9/45.0 |
| `nonlidar_modern` | **No measurement exists.** Drift is the geometric mean of the two; scale is PR #12's `modern_assumed` (2% short) ± 2% | 2.4/5.4, 5.1/11.5, 8.6/19.2, 11.7/26.6 |

Replace `nonlidar_modern` with the first real non-LiDAR phone session (PR #7's tape protocol scored by PR #12's `make field`).

## Output layout

`internal/data/packets/<house-id>/`:

```
house.json                   what was generated, from what, scale source
packet.<variant>.json        one manifest per variant; paths are relative to this folder
keyframes/k00001.jpg         shared pixels (unrotated landscape sensor layout)
keyframes/k00001.depth.f32   arkit-lidar only (with .confidence.u8)
features/<variant>/k00001.bin
streams/<variant>/{motion,heading,location}.csv
_truth/truth.json            clean poses, alignment, every noise draw: NEVER give this to a path under test
_truth/checks.json           physical consistency checks (below)
_truth/validation.json       schema and semantic validation of every manifest
```

`internal/data/packets/index.json` lists every house.

To hand one variant to a tool as a standalone packet (folder or `.zip` with `packet.json`, plus a measure-lab v2 `session.json` for PR #10 replay, PR #9 and PR #12):

```sh
python -m packetgen export --house internal/data/packets/eth3d-facade --variant arkit-nonlidar --out /tmp/facade-nonlidar.zip
```

## Commands

Run from this folder with Python 3.12 (`pip install -r requirements.txt`):

```sh
python -m packetgen eth3d --scene-dir <eth3d>/facade                  # ETH3D GT poses + laser-scan depth
python -m packetgen sequence --images <frames> --house-id <id> [--anchor '{"latitude": ..., "longitude": ...}']
python -m packetgen photos --images <photos> --house-id <id>          # EXIF-only photo_only packet
python -m packetgen manifest                                           # every new house in internal/data/manifest.json
python -m packetgen validate internal/data/packets/<id>/packet.*.json
python -m packetgen.checks internal/data/packets/<id> [--colmap-model <model> --images-root <dir>]
python -m packetgen.calibrate                                          # refit profiles.json, calibration.md
python -m pytest                                                       # conventions, noise, gravity, planes, schema
```

ETH3D data: `curl -O https://www.eth3d.net/data/facade_dslr_undistorted.7z` and `facade_dslr_scan_eval.7z` (7z x), CC BY-NC-SA 4.0 (evaluation only).

## How it was verified (ETH3D facade)

- **Conventions.** SfM points reproject through the clean ARKit poses and prepared intrinsics at 0.26 px median and 0.78 px p90 at 1920 px. This covers cropping, resizing, axes and the pose conversion.
- **Gravity.** The estimate is within 0.4° of the leveled laser scan's z axis. The mean camera "up" is 12.8° off.
- **Depth.** Rendered LiDAR depth agrees with the independent SfM points to about 1% median, the injected noise level. Visual check: `/opt/cursor/artifacts/eth3d-facade-lidar-depth-check.jpg` (VM artifact).
- **Sensors.** IMU gravity is within 0.12° median of the pose. Compass bias is ±5–7° with a 4–5° spread. GPS error is 5–6 m median. 94% of feature points reproject inside their frame.
- **Interop.** Every manifest passes the schema, file SHA-256s and tap-ray replay (error below 1e-6). The exported `session.json` passes PR #9's `capture_contract.py`.

## Limitations

- The sensor streams are synthetic and derived from the clean trajectory. Between photos the trajectory is interpolated, so IMU detail between sparse photos is invented (gait is added, not measured).
- The LiDAR depth needs a dense reference (the ETH3D laser scan). Houses without one get the LiDAR tracking profile but `sceneDepthEnabled: false`.
- Houses built by SfM are only pseudo-metric (see `house.json` `scale_source`, `scale_rel_sd`). Score their distances only as relative, or against a tape measure.
- Plane anchors only come from surfaces within 8 m of the walk. On ETH3D facade the walls are about 14 m away, so only ground planes appear, which is realistic for ARKit.
- ETH3D facade is an institutional building photographed with a DSLR, not a house or a phone. Its photos are about 1 m apart with three jumps (10, 25 and 30 m).
- No scene mesh is generated yet (`mesh` is optional in the spec).
