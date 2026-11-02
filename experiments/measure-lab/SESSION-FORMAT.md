# Measure Lab session format

Every Measure Lab session records its frames and taps in this format, so other pipelines can replay the same input. `score import-measure-lab` in `experiments/scoring` (PR #4) reads it. `MeasureLab/Session/SessionManifest.swift` writes it.

## Files

Each session is a folder, `Documents/Sessions/<id>/` on the phone. The Files app shows it under On My iPhone › Measure Lab. **Share session** in the Session sheet sends the folder as one zip, only after the current manifest saves successfully. A failed save stops the export rather than sharing an older manifest. Late keyframes from closed sessions remain pending until their manifest saves; failed writes retry on later saves. Those pending updates exist only in memory until saved.

```
<id>/
  session.json
  keyframes/
    k00001.jpg              unrotated landscape sensor image
    k00001.depth.f32        only with LiDAR depth on: Float32 meters, row-major
    k00001.confidence.u8    only with LiDAR depth on: UInt8 0 low, 1 medium, 2 high
```

## Units and frames

Every session.json also writes these conventions into `units` and `conventions`.

- Lengths are meters, and angles are degrees. Times are seconds of device uptime, the clock of `ARFrame.timestamp`. `session.startedAt` (ISO 8601) and `session.startedAtUptime` line that clock up with wall time.
- World: ARKit's frame with `.gravity` alignment. It is right-handed, with y up, and its origin and heading are wherever the session started. Each session has its own world, so points from two sessions can't be compared.
- Camera: +x right and +y up in the unrotated sensor image, looking along −z.
- `pose`: camera-to-world 4×4, 16 numbers column by column (the `simd_float4x4` layout).
- `intrinsics`: `[fx, fy, cx, cy]` in pixels of that keyframe's JPEG.
- Pixels: `[u, v]`, with (0, 0) at the top-left corner of the JPEG and v growing down. The ray through a pixel has camera-space direction `((u − cx)/fx, −(v − cy)/fy, −1)`, as in `docs/00-overview.md` under "Conventions the code relies on".
- Vectors are `[x, y, z]` arrays.

## session.json fields

`format` is `"measure-lab-session"` and `formatVersion` is 2. The top-level fields are:

| Field | Contents |
| --- | --- |
| `session` | `id`, `startedAt`, `startedAtUptime`, `appVersion`, `deviceModel` (for example `iPhone15,4`), `systemVersion`, `lidarAvailable`, `meshReconstructionSupported`, `sceneDepthEnabled` |
| `gates` | Every threshold the session ran with, so a replay can apply the same ones |
| `keyframes` | `id`, `img` (path), `w`, `h`, `intrinsics`, `pose`, `timestamp`, `tracking` (`normal`, `limited.excessiveMotion`, ...), `reason` (`motion`, `tap` or `freeze`), and `depth` (`file`, `confidenceFile`, `w`, `h`) or null. The field names match the `keyframes` entries of `scene.json` in the [feature map](https://github.com/SamGu-NRX/house-scanning-master/blob/b5869de1f1bddc4c5809494a5565b335795016d1/docs/01-feature-map.md). |
| `taps` | `id`, `time`, `tool`, `step` (`firstContact`, `secondView`, ...), `keyframe`, `frozen`, `pixel`, `rayOrigin`, `rayDirection`, `displayMappingCheck`, and the `point`, the `refusal` or both that it produced |
| `points` | `id`, `kind` (`ground`, `wall`, `twoView`), `position` and `taps`. Then, by kind, `ground` (`surface`, `planeAnchor`, `lookDown`), `onWall` (`wall`, `range`, `angleFromNormal`) or `twoView` (`firstTap`, `secondTap`, `rayAngle`, `gap`, `baseline`, `t1`, `t2`). `wallCoordinates` are against the newest wall at the time (`along`, `heightAboveGround`, `offset`, `withinContacts`). `flags` are the point's own warnings (`estimatedPlane`, `extendedPlane`, `shallowLookDown`, `outsideWallContacts`). `wallWarnings`, for a point on a wall, are that wall's warnings when the point was made. |
| `walls` | `id`, `contacts` (point ids), `start`, `end`, `direction`, `normal`, `length`, `cameraPosition`, `validations` (`point`, `residual`, `tolerance`, `passes`), and `warnings` as of the last check (`wallContactWarning`, `wallNotValidated`, `wallValidationFailed`). `passes` is the residual test alone. A passing validation whose point has its own `flags` leaves `wallNotValidated` in place, and any failing one sets `wallValidationFailed`. |
| `measurements` | `id`, `time`, `from`, `to` (point or wall id), `referenceWall`, `values` (every quantity that applies, keyed `straight`, `horizontal`, `vertical`, `alongWall`, `gapToWall`, `heightAboveGround`), `compared`, `tape` (`feet`, `inches`, `meters`) or null, `errorMeters` and `errorInches` (app minus tape), `warnings` (inherited as above, plus `outsideWallContacts` when a height, facing gap or along-wall distance reads its wall beyond the wall's contacts, and `belowGround`), and `accepted`, which is true only with no warnings. A wall check made after the measurement can add warnings and set `accepted` to false. It never removes a warning. `heightAboveGround` is signed. The other values are non-negative. |
| `refusals` | `id`, `time`, `tool`, `tap` (null when no frame was resolved), `reason` (for example `grazingRay`, `rayAngleTooSmall`, `noGround`, `trackingNotReady`), `message` as shown, and the `values` behind it |
| `tracking` | `time` and `state`, one entry per tracking change |

## Keyframes and replay

The app saves a keyframe when the camera moves 0.5 m or turns 15° since the last saved one, and at every live tap and freeze. `keyframes` lists them in arrival order. Sort by `id` or `timestamp` if order matters. A JPEG that was still being written when the zip was made can be in the folder without an entry. Use only listed keyframes.

`displayMappingCheck` on a live tap is the pixel distance between ARKit's display transform and the app's own portrait mapping for the same screen point. It should stay under a pixel or two. A larger value means frozen-frame taps land on the wrong pixel.

To replay a tap, build the ray through `pixel` from its keyframe's `pose` and `intrinsics`. The ray should match `rayOrigin` and `rayDirection`. `CameraFrame.ray(throughPixel:)` in `Geometry/` does this.
