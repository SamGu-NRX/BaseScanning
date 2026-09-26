# Capture packet spec v0.1 (draft for the iOS team)

**What this is.** The contract between the iOS capture app and the server pipeline: one capture session becomes one *packet*, a folder (or zip) holding the photos plus everything ARKit, Core Motion and Core Location reported while taking them. The server builds the 3D asset and evaluates the eligibility criteria from it. This is v0.1: field names can still change before 1.0, and every open question at the end has a default we will build against until you answer.

**Built on what already exists, not beside it.**
- **PR #7 `measure-lab-session` v2** is the base. Keyframe, tap and tracking fields keep its names, units and conventions exactly (meters, gravity-aligned y-up world, column-major camera-to-world pose, unrotated landscape JPEG, `[fx, fy, cx, cy]`, `k00001.depth.f32` / `.confidence.u8`). A packet converts losslessly into a v2 `session.json`, so PR #10's replay, PR #9's converter and PR #12's field scoring read it unchanged.
- **PR #10 `scene.json` (contract C1)** stays what it is: the small placement request in feet. The packet is the raw evidence behind it and travels separately. `stills` purposes and `gps` / `heading` come from there.
- **PR #18 capture admission** adds coordinate *epochs* (a new ARKit world after a reset), a SHA-256 and size for every file, and an optional per-frame admission record.

Machine-readable schema (JSON Schema 2020-12): Appendix A, also at `internal/packet-generator/schema/capture-packet.schema.json` in the project store. Examples: Appendix B.

## 1. Packet at a glance

Three capture tiers share one format:

| Tier | When | What it must carry |
| --- | --- | --- |
| `arkit_lidar` | LiDAR iPhone (12 Pro and later Pro models) | ARKit keyframes with pose and intrinsics, **plus** LiDAR depth and confidence per keyframe |
| `arkit` | Any ARKit iPhone without LiDAR (the common case) | ARKit keyframes with pose and intrinsics |
| `photo_only` | No ARKit (fallback camera, uploaded photos) | Photos with EXIF; gravity and location per photo if the app took them |

```
<packet>/
  packet.json                    this spec (write it last)
  keyframes/
    k00001.jpg                   ARFrame.capturedImage, unrotated landscape, JPEG quality 0.9
    k00001.features.bin          ARFrame.rawFeaturePoints (recommended)
    k00001.depth.f32             LiDAR only: Float32 meters, row-major
    k00001.confidence.u8         LiDAR only: UInt8 0 low, 1 medium, 2 high
  stills/                        checklist photos not taken from ARKit (optional)
  streams/
    motion.csv                   CMDeviceMotion, 100 Hz (recommended)
    heading.csv                  CLHeading (recommended)
    location.csv                 CLLocation (recommended)
  geometry/
    mesh.ply                     LiDAR scene mesh (optional)
```

## 2. Coordinate conventions and units

These are PR #7's conventions, restated with the Core Motion and Core Location parts it did not need. The app writes the same text into `units` and `conventions` in every packet.

**Units.** Lengths in meters, angles in degrees, times in seconds of device uptime (assumed shared by ARKit and Core Motion; see Clocks). Exceptions, kept in the native Apple units: rotation rate in rad/s, acceleration and gravity in g, magnetic field in µT, plane `rotationOnYAxis` in radians.

**World (ARKit).** `worldAlignment = .gravity`: right-handed, +y up (away from gravity). The origin is the device's position when the session started. Apple: "ARKit chooses a basis vector (0,0,-1) pointing in the direction the device camera faces and perpendicular to the gravity axis" ([`.gravity`](https://developer.apple.com/documentation/arkit/arconfiguration/worldalignment-swift.enum/gravity)). Consequences the server relies on:
- **y = 0 is the phone's height at session start, not the ground.** The ground is typically about 1.2–1.6 m below.
- Heading is arbitrary. Do not use `.gravityAndHeading` (its axes are east, up, south and it is unstable near houses). Absolute heading comes from the compass streams instead.

**Epochs.** One ARKit world per epoch. A tracking reset, a failed relocalization or a restart starts a new epoch with a new origin and heading. Poses from different epochs are never comparable. Every keyframe, tap and plane names its epoch.

**Camera (ARKit).** +x right and +y up in the unrotated landscape sensor image; the camera looks along −z. Apple: "the x-axis always points along the long axis of the device, from the front-facing camera toward the Home button … the z-axis points away from the device on the screen side" ([`ARCamera.transform`](https://developer.apple.com/documentation/arkit/arcamera/transform)).

**Device (Core Motion).** +x right and +y toward the top with the phone upright in portrait, +z out of the screen. In device coordinates the camera axes are:

```
camera x = device -y      camera y = device +x      camera z = device +z
world_from_device = pose_rotation · [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]ᵀ
```

**Pose.** `ARCamera.transform`, camera-to-world, 16 numbers column by column (`simd_float4x4` memory order), translation in meters. OpenCV / COLMAP camera-to-world is `pose · diag(1, −1, −1, 1)`.

**Images.** Keyframes store the pixels exactly as `capturedImage` delivers them: landscape, never rotated. A phone held upright in portrait gives a sensor image that must be rotated 90° clockwise to display (EXIF orientation 6). Portrait pixel `(u_p, v_p) = (h − v, u)` for landscape pixel `(u, v)`.

**Intrinsics.** `[fx, fy, cx, cy]` from [`ARCamera.intrinsics`](https://developer.apple.com/documentation/arkit/arcamera/intrinsics), in pixels of the saved image. If the saved image is not `ARCamera.imageResolution`, scale all four numbers. High-resolution frames carry their own intrinsics.

**Pixels and rays.** `[u, v]` continuous coordinates: (0, 0) is the top-left corner of the top-left pixel, v grows down. The ray through a pixel has camera-space direction `((u − cx)/fx, −(v − cy)/fy, −1)`, the same as PR #7 and `pixel_ray` in docs/02.

**Clocks.** We assume `ARFrame.timestamp` and Core Motion's `timestamp` are both device uptime. Apple documents Core Motion's clock as "time in seconds since the device booted" but documents no clock for `ARFrame.timestamp`, so open question 14 asks for a device check. `CLLocation.timestamp` and `CLHeading.timestamp` are wall-clock `Date`s. The CSV streams carry both: `t = startedAtUptime + (date − startedAt)`, plus the original ISO time.

**Geodetic and compass.** WGS84 degrees. `altitude` is above mean sea level and `ellipsoidalAltitude` above the WGS84 ellipsoid, both in meters. Headings are degrees clockwise from north. Core Motion `xTrueNorthZVertical` is x north, y west, z up. Given the true azimuth θ of ARKit world −z, a world point maps to local east and north as `east = x cos θ − z sin θ`, `north = −x sin θ − z cos θ`, and up = y.

## 3. Fields: required, recommended, optional

- **R, required:** the server rejects the packet without it (for that tier).
- **Rec, recommended:** send it whenever the phone has it; the pipeline degrades without it.
- **O, optional:** use it if it's cheap.

"ARKit tiers" means `arkit` and `arkit_lidar`.

### 3.1 Packet and session

| Field | Level | Source | Notes |
| --- | --- | --- | --- |
| `format`, `formatVersion`, `packetId`, `createdAt` | R | app | `"house-capture-packet"`, `"0.1"`, UUID, ISO 8601 UTC |
| `units`, `conventions` | R | app | fixed text (Appendix B) |
| `source.kind` | R | app | `device` for real sessions; `synthetic` packets come from our generator and are never evidence |
| `session.id`, `startedAt`, `startedAtUptime` | R | [`ProcessInfo.systemUptime`](https://developer.apple.com/documentation/foundation/processinfo/systemuptime) | uptime at `startedAt` ties wall clock to uptime |
| `session.appVersion`, `deviceModel`, `systemVersion` | R | [`uname`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/uname.3.html) `machine` (e.g. `iPhone16,1`), [`UIDevice.systemVersion`](https://developer.apple.com/documentation/uikit/uidevice/systemversion) | the model identifier decides the camera and LiDAR |
| `session.tier` | R | app | `arkit_lidar` / `arkit` / `photo_only` |
| `session.lidarAvailable` | R | [`supportsFrameSemantics(.sceneDepth)`](https://developer.apple.com/documentation/arkit/arconfiguration/supportsframesemantics%28_:%29) | |
| `session.sceneDepthEnabled` | R | configuration | true when `.sceneDepth` was requested |
| `session.worldAlignment` | R (ARKit tiers) | [`ARConfiguration.worldAlignment`](https://developer.apple.com/documentation/arkit/arconfiguration/worldalignment-swift.property) | must be `gravity` |
| `session.videoFormat.imageResolution`, `framesPerSecond` | R (ARKit tiers) | [`ARConfiguration.VideoFormat`](https://developer.apple.com/documentation/arkit/arconfiguration/videoformat-swift.class) | usually 1920 × 1440 |
| `session.meshReconstructionSupported`, `sceneReconstruction`, `planeDetection` | Rec | [`supportsSceneReconstruction(_:)`](https://developer.apple.com/documentation/arkit/arworldtrackingconfiguration/supportsscenereconstruction%28_:%29) | |
| `session.timeZone`, `locationAuthorization`, `motionReferenceFrame` | Rec | [`accuracyAuthorization`](https://developer.apple.com/documentation/corelocation/cllocationmanager/accuracyauthorization) | reduced accuracy makes GPS useless |
| `session.endedAtUptime`, `thermalStateMax`, `gates` | O | [`ProcessInfo.thermalState`](https://developer.apple.com/documentation/foundation/processinfo/thermalstate-swift.property) | `gates`: PR #7's capture thresholds |
| `epochs[]`: `id`, `startTime`, `reason` | R (ARKit tiers) | app | `sessionStart`, `trackingReset`, `relocalizationFailed`, `userRestart` |
| `tracking[]`: `time`, `state`, `epoch` | R (ARKit tiers) | [`ARCamera.trackingState`](https://developer.apple.com/documentation/arkit/arcamera/trackingstate-swift.enum) | one entry per change; PR #7 spellings: `normal`, `notAvailable`, `limited.initializing`, `limited.excessiveMotion`, `limited.insufficientFeatures`, `limited.relocalizing` |
| `files[]`: `path`, `bytes`, `sha256`, `role` | R | app | every file except `packet.json` |

### 3.2 Keyframes (ARKit tiers)

A keyframe is an `ARFrame` the app saved, with that frame's own pose and calibration. Save all keyframes at their own timestamps, not "the latest pose".

| Field | Level | Source | Notes |
| --- | --- | --- | --- |
| `id`, `img`, `w`, `h` | R | [`ARFrame.capturedImage`](https://developer.apple.com/documentation/arkit/arframe/capturedimage) | JPEG quality 0.9, sRGB, **unrotated** |
| `intrinsics` | R | [`ARCamera.intrinsics`](https://developer.apple.com/documentation/arkit/arcamera/intrinsics), [`imageResolution`](https://developer.apple.com/documentation/arkit/arcamera/imageresolution) | per frame; do not reuse one value for the session |
| `pose` | R | [`ARCamera.transform`](https://developer.apple.com/documentation/arkit/arcamera/transform) | |
| `timestamp` | R | [`ARFrame.timestamp`](https://developer.apple.com/documentation/arkit/arframe/timestamp) | uptime seconds |
| `tracking` | R | [`ARCamera.trackingState`](https://developer.apple.com/documentation/arkit/arcamera/trackingstate-swift.enum) | state at this frame |
| `epoch`, `reason` | R | app | `reason`: `motion`, `tap`, `freeze`, `still`, `request`, `auto` |
| `exposure.duration`, `exposure.offset` | Rec | [`ARCamera.exposureDuration`](https://developer.apple.com/documentation/arkit/arcamera/exposureduration), [`exposureOffset`](https://developer.apple.com/documentation/arkit/arcamera/exposureoffset) (iOS 13+) | seconds, EV; motion blur and glare checks |
| `exif` | Rec | [`ARFrame.exifData`](https://developer.apple.com/documentation/arkit/arframe/exifdata) (**iOS 16+**) | copy `ExposureTime`, `ISOSpeedRatings`, `FNumber`, `FocalLength`, `FocalLenIn35mmFilm`, `BrightnessValue`, `LensModel` |
| `features` | Rec | [`ARFrame.rawFeaturePoints`](https://developer.apple.com/documentation/arkit/arframe/rawfeaturepoints) | binary layout in section 4; the tracker's own 3D points, which help seed reconstruction |
| `depth` | **R when `sceneDepthEnabled`** | [`ARFrame.sceneDepth`](https://developer.apple.com/documentation/arkit/arframe/scenedepth) → [`depthMap`](https://developer.apple.com/documentation/arkit/ardepthdata/depthmap), [`confidenceMap`](https://developer.apple.com/documentation/arkit/ardepthdata/confidencemap) | same files as PR #7; `smoothed: true` if taken from [`smoothedSceneDepth`](https://developer.apple.com/documentation/arkit/arframe/smoothedscenedepth) |
| `highResolution` | O | [`ARSession.captureHighResolutionFrame`](https://developer.apple.com/documentation/arkit/arsession/capturehighresolutionframe%28completion:%29) (iOS 16+) | the frame keeps its pose; intrinsics are for the large image |
| `purpose` | O | app | when a keyframe doubles as a checklist photo |
| `lightEstimate`, `cameraGrainIntensity`, `worldMappingStatus` | O | [`ARFrame.lightEstimate`](https://developer.apple.com/documentation/arkit/arframe/lightestimate), [`cameraGrainIntensity`](https://developer.apple.com/documentation/arkit/arframe/cameragrainintensity), [`worldMappingStatus`](https://developer.apple.com/documentation/arkit/arframe/worldmappingstatus-swift.property) | |
| `admission` | O | PR #18 ledger | `usable`, `supports` (requirement and revision) |

**Gravity** in the ARKit tiers is world −y by construction (`.gravity` alignment), so keyframes need no separate gravity vector. Photo-only stills do (3.4).

### 3.3 Taps, planes, mesh

| Field | Level | Source | Notes |
| --- | --- | --- | --- |
| `taps[]`: `id`, `time`, `label`, `keyframe`, `pixel`, `rayOrigin`, `rayDirection` | **R for the `meter` tap** when the flow reached it; Rec for other labels | app (PR #7 `TapRecord`) | labels: `meter`, `wall_end_left`, `wall_end_right`, `corner`, `wall_contact`, `gas_meter`, `ac_unit`, `door_corner`, `window_corner`, `fence`, `driveway_edge`, `reference_point`, `other`. The tap's pixel must be in the listed keyframe (save one for the tap if needed). |
| `taps[].hit`: `position`, `target`, `alignment`, `planeId`, `distance` | Rec | [`ARRaycastQuery`](https://developer.apple.com/documentation/arkit/arraycastquery) / [`ARRaycastResult`](https://developer.apple.com/documentation/arkit/arraycastresult) | `target`: `existingPlaneGeometry`, `existingPlaneInfinite`, `estimatedPlane`, `sceneDepth`, `mesh`, `none` |
| `taps[].frozen`, `tool`, `step`, `displayMappingCheck`, `confirmedByUser`, `refusal` | O | PR #7 | |
| `planes[]`: `id`, `epoch`, `alignment`, `transform`, `planeExtent` | Rec | [`ARPlaneAnchor`](https://developer.apple.com/documentation/arkit/arplaneanchor), [`planeExtent`](https://developer.apple.com/documentation/arkit/arplaneanchor/planeextent) | snapshot at session end; the plane lies in the anchor's x-z plane, anchor +y is the normal |
| `planes[].classification`, `center`, `boundary`, `firstSeen`, `lastUpdated` | O | [`classification`](https://developer.apple.com/documentation/arkit/arplaneanchor/classification-swift.enum), [`ARPlaneGeometry.boundaryVertices`](https://developer.apple.com/documentation/arkit/arplanegeometry) | |
| `mesh`: `file`, `epoch`, `faceCount`, `classified` | O (LiDAR) | [`ARMeshAnchor`](https://developer.apple.com/documentation/arkit/armeshanchor) | one PLY in world meters |

### 3.4 Stills (checklist photos)

Base's checklist asks for the meter close-up, meter surroundings from 10+ steps back, left and right of the meter, the adjacent wall, behind the fence, the main panel (closed and open), the main disconnect with amperage, and the panel surroundings. A still can be an AR keyframe (`keyframe` set, ideally a high-resolution frame) or a plain camera photo.

| Field | Level | Source | Notes |
| --- | --- | --- | --- |
| `id`, `purpose`, `img`, `w`, `h`, `timestamp`, `orientation` | R (tier `photo_only` needs ≥ 1) | app | `purpose`: `meter_close`, `meter_surround`, `meter_left`, `meter_right`, `adjacent_wall`, `behind_fence`, `panel_close`, `panel_open`, `main_disconnect`, `panel_surround`, `house_overview`, `other` |
| `exif` | R for `photo_only`, Rec otherwise | [`AVCapturePhoto.metadata`](https://developer.apple.com/documentation/avfoundation/avcapturephoto/metadata) or `ARFrame.exifData` | `FocalLenIn35mmFilm` gives nominal intrinsics without ARKit |
| `keyframe` | Rec when taken in AR | app | pose and intrinsics then come from that keyframe |
| `gravity` | Rec | [`CMDeviceMotion.gravity`](https://developer.apple.com/documentation/coremotion/cmdevicemotion/gravity) | device frame, g; camera tilt and roll for photos without a pose |
| `intrinsics`, `intrinsicsSource`, `location` | O | | `intrinsicsSource`: `arkit`, `calibration`, `exif_focal35` |

### 3.5 Streams (CSV, header row, one row per sample)

| Stream | Level | Source | Rate | Columns |
| --- | --- | --- | --- | --- |
| `motion` | Rec | [`CMDeviceMotion`](https://developer.apple.com/documentation/coremotion/cmdevicemotion) with reference frame [`xTrueNorthZVertical`](https://developer.apple.com/documentation/coremotion/cmattitudereferenceframe/xtruenorthzvertical) (falls back to `xArbitraryCorrectedZVertical` without location) | 100 Hz (≥ 50) | `t` uptime; `qx,qy,qz,qw` [attitude](https://developer.apple.com/documentation/coremotion/cmdevicemotion/attitude) quaternion; `rotX..Z` [rotationRate](https://developer.apple.com/documentation/coremotion/cmdevicemotion/rotationrate) rad/s; `gravX..Z` [gravity](https://developer.apple.com/documentation/coremotion/cmdevicemotion/gravity) g; `accX..Z` [userAcceleration](https://developer.apple.com/documentation/coremotion/cmdevicemotion/useracceleration) g; `magX..Z` [magneticField](https://developer.apple.com/documentation/coremotion/cmdevicemotion/magneticfield) µT; `magAccuracy` (−1 uncalibrated, 0 low, 1 medium, 2 high); `heading` [deg](https://developer.apple.com/documentation/coremotion/cmdevicemotion/heading) (north reference frames only) |
| `heading` | Rec | [`CLHeading`](https://developer.apple.com/documentation/corelocation/clheading) | on change (`headingFilter` 1°) | `t`; `timestamp` ISO; `magneticHeading`; [`trueHeading`](https://developer.apple.com/documentation/corelocation/clheading/trueheading) (negative = invalid); [`headingAccuracy`](https://developer.apple.com/documentation/corelocation/clheading/headingaccuracy) (negative = invalid); `x,y,z` µT |
| `location` | Rec | [`CLLocation`](https://developer.apple.com/documentation/corelocation/cllocation), `desiredAccuracy` [`kCLLocationAccuracyBest`](https://developer.apple.com/documentation/corelocation/kcllocationaccuracybest) | every update (~1 Hz) | `t`; `timestamp` ISO; `latitude`, `longitude`; [`altitude`](https://developer.apple.com/documentation/corelocation/cllocation/altitude); [`ellipsoidalAltitude`](https://developer.apple.com/documentation/corelocation/cllocation/ellipsoidalaltitude); [`horizontalAccuracy`](https://developer.apple.com/documentation/corelocation/cllocation/horizontalaccuracy); `verticalAccuracy`; `course`, `courseAccuracy`, `speed`, `speedAccuracy` (−1 = invalid) |
| `altimeter` | O | [`CMAltimeter`](https://developer.apple.com/documentation/coremotion/cmaltimeter) | on update | `t`, `relativeAltitude` m, `pressure` kPa |

Record `headingOrientation` in the packet ([`CLLocationManager.headingOrientation`](https://developer.apple.com/documentation/corelocation/cllocationmanager/headingorientation)). The property is deprecated in iOS 27 with no documented replacement; on iOS 27+ record the orientation you assume (`portrait` for this app) until Apple names a successor. Empty CSV cells mean "not available".

### 3.6 What the server does with each group

| Group | Used for |
| --- | --- |
| Keyframe pose and intrinsics | Metric scale and pose priors for reconstruction (COLMAP / MapAnything with poses), projecting detections into 3D, coverage (what was seen) |
| Epochs and tracking | Dropping or down-weighting `limited` frames; never mixing worlds |
| Depth and confidence | Metric surfaces and occlusion-aware coverage on LiDAR phones (PR #12 found coverage overclaims without a depth test) |
| Feature points and planes | Seeding and checking reconstruction; ground and wall planes |
| Taps (meter, wall ends) | The anchor of every placement distance (PR #10's s = 0) |
| Exposure and EXIF | Blur, glare and legibility checks; nominal intrinsics for photo-only |
| Motion | Gravity for photo-only; time sync; detecting fast motion |
| Heading and location | Map alignment (aerial imagery, parcel), which side of the house; never for distances |

## 4. Binary layouts

| File | Layout |
| --- | --- |
| `*.depth.f32` | `ARDepthData.depthMap`: Float32 little-endian meters, row-major, `w × h` (256 × 192 observed on current phones; Apple documents only "low-resolution", so write the actual `CVPixelBufferGetWidth/Height` and check `CVPixelBufferGetPixelFormatType` is `DepthFloat32`), no row padding (copy row by row, as PR #7 `KeyframeRecorder.rows` does) |
| `*.confidence.u8` | `ARDepthData.confidenceMap`: UInt8 [`ARConfidenceLevel`](https://developer.apple.com/documentation/arkit/arconfidencelevel) values written explicitly as 0 low, 1 medium, 2 high; same layout. Apple does not document the raw values (0/1/2 comes from PR #7 and header mirrors), so map the enum cases in code rather than casting `rawValue` |
| `*.features.bin` | `ARFrame.rawFeaturePoints`: `count` packed records `{float32 x, float32 y, float32 z, uint64 identifier}`, 20 bytes each, little-endian; world meters of the keyframe's epoch ([`ARPointCloud`](https://developer.apple.com/documentation/arkit/arpointcloud) `points` and `identifiers`) |
| `geometry/mesh.ply` | binary little-endian PLY; vertices in world meters; optional per-face `uchar classification` ([`ARMeshClassification`](https://developer.apple.com/documentation/arkit/armeshclassification)) |

## 5. Capture and transport rules

1. **Never rotate or re-encode keyframes** after saving. Rotation belongs in `orientation` (stills) or is implied (keyframes are always sensor layout).
2. **One pose per image.** Take pose, intrinsics, timestamp and tracking from the same `ARFrame` whose pixels you saved (PR #7, PR #18). Don't pair an image with a later frame's pose.
3. **Keyframe cadence.** Keep PR #10's rule (every 0.5 m or 15°, plus every tap and freeze). Add a frame at least every 1 s while walking, up to about 150 frames.
4. **Keep old epochs.** After a reset, keep the earlier keyframes with their epoch id rather than discarding them.
5. **Upload.** Upload files individually and resumably (background `URLSession`; PR #18 suggests tus). Send `packet.json` last. The server checks every `bytes` and `sha256` and acknowledges per file. Don't delete local files until the packet is acknowledged; note that PR #10 currently deletes the previous scan when a new one starts. `scene.json` stays a separate small request (the placement endpoint is capped at 4.5 MB).
6. **Size budget.** About 0.5–0.9 MB per 1920 × 1440 keyframe, 245 KB of depth and confidence per LiDAR keyframe, and about 3 MB of motion CSV per 4 minutes (gzip it). 60 keyframes come to roughly 45 MB.
7. **Privacy.** A packet holds house photos and precise location. Ask for consent before capturing and treat the packet as personal data.

## 6. What the server validates on arrival

- Schema (Appendix A), including the tier rules: ARKit tiers need keyframes, epochs, tracking, `worldAlignment` and `videoFormat`; `photo_only` needs stills.
- Every listed file exists with the stated size and SHA-256. Unlisted files are ignored.
- Poses are finite, rotations orthonormal with determinant +1, and intrinsics positive and plausible for `w × h` (principal point inside the image).
- Timestamps increase within an epoch, and every keyframe's epoch exists.
- **Tap replay** (PR #9): rebuilding each tap's ray from `pixel`, `pose` and `intrinsics` must reproduce `rayOrigin` and `rayDirection` to within 1e-3. This is the one check that the conventions were followed; please keep taps in every packet you test with.
- Depth files are exactly `w × h × 4` bytes and confidence files `w × h` bytes.

## 7. Open questions for the iOS team

Each has the default we build against until you answer.

1. **Do original images leave the phone?** PR #10 uploads only `scene.json` and keeps JPEGs local. Default: after the scan, keyframes and stills are uploaded resumably and kept until acknowledged.
2. **Minimum iOS version.** `ARFrame.exifData` and `captureHighResolutionFrame` need iOS 16. Default: iOS 16 or later; below that, `exif` and `highResolution` are omitted.
3. **High-resolution stills.** Can checklist items (meter close-up, panel, disconnect) use `captureHighResolutionFrame` so they keep a pose? Default: yes for `meter_close` and `panel_*`.
4. **Location and compass permission.** PR #10 avoids GPS and compass. Can we ask for full-accuracy location and run Core Motion in `xTrueNorthZVertical`? Default: yes. Heading only orients the house on a map and never feeds a distance.
5. **What does `CLHeading.trueHeading` measure with the phone upright?** With `headingOrientation = .portrait`, is it the top edge or the camera direction? A 2-minute test (point the camera at a known azimuth) settles it. Default: we trust `CMDeviceMotion.heading` and attitude and treat `CLHeading` as a cross-check.
6. **Half-pixel convention.** PR #9 and PR #12 disagree by 0.5 px on ARKit's `cx, cy`. Default: continuous coordinates with (0, 0) at the pixel corner. Tap replay on a device will confirm (the effect is about 0.02°).
7. **Lens distortion.** ARKit exposes pinhole intrinsics only. Is `capturedImage` distortion-corrected, and can the app also log `AVCameraCalibrationData.lensDistortionLookupTable`? Default: pinhole, no distortion.
8. **LiDAR.** Raw `sceneDepth` or `smoothedSceneDepth`? Every keyframe? Export the mesh at the end? Default: raw depth and confidence on every keyframe, mesh optional.
9. **Resets.** After a failed relocalization, PR #10 discards earlier keyframes. Can they be kept with an epoch id? Default: keep.
10. **Taps in the current flow.** Which labels exist today (meter, wall ends, corners, gas, AC, door and window corners, fence, driveway), and which raycast target does each use? PR #7 has an open issue: frozen-frame taps raycast against the *current* planes. Default: record `frozen` and `hit.target` for every tap.
11. **Motion rate.** Is 100 Hz `CMDeviceMotion` alongside ARKit fine for battery and heat? Default: 100 Hz, 50 Hz acceptable.
12. **Feature points.** Is exporting `rawFeaturePoints` per keyframe acceptable (about 4 KB per frame)? Default: yes.
13. **Admission records.** Will the app keep PR #18's per-frame `usable` / `supports` ledger? Default: the field is optional and reserved.
14. **Clocks.** Please confirm on a device that `ARFrame.timestamp` and `CMDeviceMotion.timestamp` share one uptime base (log both for a few seconds). Default: they do.
15. **Photo-only fallback.** When ARKit is unavailable or the user picks photos, does the app still attach `CMDeviceMotion.gravity` and location to each photo? Default: yes, as `stills[].gravity` and `stills[].location`.

## Appendix A: JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/SamGu-NRX/house-scanning/schemas/capture-packet-0.1.schema.json",
  "title": "House capture packet v0.1 (packet.json)",
  "description": "Everything one capture session produced on the phone: photos plus ARKit, Core Motion and Core Location metadata. Raw sensor units (meters, seconds of device uptime, ARKit world). Field names and conventions of keyframes, taps and tracking follow measure-lab-session v2 (PR #7). Unknown fields are rejected so typos fail loudly; put experiments under `ext`.",
  "type": "object",
  "additionalProperties": false,
  "required": ["format", "formatVersion", "packetId", "createdAt", "units", "conventions", "source", "session", "keyframes", "files"],
  "properties": {
    "format": {"const": "house-capture-packet"},
    "formatVersion": {"type": "string", "pattern": "^0\\.[0-9]+$", "description": "0.x: breaking changes allowed between minor versions until 1.0."},
    "packetId": {"type": "string", "minLength": 8, "description": "UUID, unique per upload attempt of this session."},
    "createdAt": {"$ref": "#/$defs/isoTime"},
    "units": {
      "type": "object",
      "additionalProperties": false,
      "required": ["length", "angle", "time", "image"],
      "properties": {
        "length": {"const": "meters"},
        "angle": {"const": "degrees"},
        "time": {"type": "string"},
        "image": {"type": "string"},
        "rotationRate": {"const": "radians per second"},
        "acceleration": {"const": "g (9.80665 m/s^2)"},
        "magneticField": {"const": "microtesla"}
      }
    },
    "conventions": {
      "type": "object",
      "description": "Human-readable restatement of the conventions section of the spec; written verbatim by the app.",
      "additionalProperties": {"type": "string"},
      "required": ["world", "camera", "pose", "intrinsics", "pixel"]
    },
    "source": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind"],
      "properties": {
        "kind": {"enum": ["device", "replay", "synthetic"], "description": "device: a real phone session. replay: re-exported from a recording. synthetic: made by the packet generator; never evidence."},
        "appBuild": {"type": "string"},
        "generator": {"type": "string"},
        "variant": {"type": "string"},
        "noiseProfile": {"type": "string"},
        "sourceDataset": {"type": "string"},
        "synthesizedFields": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"}
      }
    },
    "session": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "startedAt", "startedAtUptime", "appVersion", "deviceModel", "systemVersion", "tier", "lidarAvailable", "sceneDepthEnabled"],
      "properties": {
        "id": {"type": "string", "minLength": 1},
        "startedAt": {"$ref": "#/$defs/isoTime", "description": "Wall clock at session start (UTC, ISO 8601)."},
        "startedAtUptime": {"type": "number", "description": "ProcessInfo.systemUptime at startedAt; lines wall-clock times (CLLocation, CLHeading) up with uptime."},
        "endedAtUptime": {"type": "number"},
        "timeZone": {"type": "string", "description": "IANA name, e.g. America/Chicago."},
        "appVersion": {"type": "string"},
        "deviceModel": {"type": "string", "description": "Hardware identifier from utsname.machine, e.g. iPhone16,1."},
        "systemVersion": {"type": "string"},
        "tier": {"enum": ["arkit_lidar", "arkit", "photo_only"], "description": "arkit_lidar: ARKit world tracking with LiDAR scene depth. arkit: ARKit world tracking, no depth. photo_only: no ARKit poses (fallback camera, uploaded photos)."},
        "lidarAvailable": {"type": "boolean", "description": "ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)."},
        "meshReconstructionSupported": {"type": "boolean", "description": "supportsSceneReconstruction(.mesh)."},
        "sceneDepthEnabled": {"type": "boolean", "description": "The session requested .sceneDepth (or .smoothedSceneDepth) frame semantics."},
        "sceneReconstruction": {"enum": ["none", "mesh", "meshWithClassification"]},
        "worldAlignment": {"enum": ["gravity", "gravityAndHeading", "camera"], "description": "Required for ARKit tiers. `gravity` is expected."},
        "planeDetection": {"type": "array", "items": {"enum": ["horizontal", "vertical"]}, "uniqueItems": true},
        "videoFormat": {
          "type": "object",
          "additionalProperties": false,
          "required": ["imageResolution"],
          "properties": {
            "imageResolution": {"$ref": "#/$defs/size"},
            "framesPerSecond": {"type": "number", "exclusiveMinimum": 0},
            "captureDeviceType": {"type": "string"}
          }
        },
        "locationAuthorization": {"enum": ["fullAccuracy", "reducedAccuracy", "denied", "notDetermined"]},
        "motionReferenceFrame": {"enum": ["xArbitraryZVertical", "xArbitraryCorrectedZVertical", "xMagneticNorthZVertical", "xTrueNorthZVertical"]},
        "thermalStateMax": {"enum": ["nominal", "fair", "serious", "critical"]},
        "gates": {"type": "object", "additionalProperties": {"type": "number"}, "description": "Capture thresholds the app ran with (PR #7 `gates`)."}
      }
    },
    "epochs": {
      "type": "array",
      "description": "Coordinate epochs: one ARKit world each. A tracking reset or failed relocalization starts a new epoch; poses from different epochs are not comparable (PR #18).",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "startTime", "reason"],
        "properties": {
          "id": {"type": "string", "minLength": 1},
          "startTime": {"type": "number"},
          "reason": {"enum": ["sessionStart", "trackingReset", "relocalizationFailed", "userRestart"]}
        }
      }
    },
    "keyframes": {
      "type": "array",
      "maxItems": 5000,
      "description": "Frames saved from the ARKit session, each with its own pose and calibration. Empty only for tier photo_only.",
      "items": {"$ref": "#/$defs/keyframe"}
    },
    "stills": {
      "type": "array",
      "maxItems": 500,
      "description": "Checklist photos (meter close-up, panel, ...). May point at an AR keyframe, or be a plain camera photo with EXIF.",
      "items": {"$ref": "#/$defs/still"}
    },
    "taps": {"type": "array", "maxItems": 2000, "items": {"$ref": "#/$defs/tap"}},
    "tracking": {
      "type": "array",
      "description": "One entry per ARCamera.trackingState change.",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["time", "state"],
        "properties": {
          "time": {"type": "number"},
          "state": {"$ref": "#/$defs/trackingState"},
          "epoch": {"type": "string"}
        }
      }
    },
    "planes": {"type": "array", "maxItems": 500, "items": {"$ref": "#/$defs/plane"}},
    "mesh": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "epoch"],
      "properties": {
        "file": {"type": "string", "description": "PLY of all ARMeshAnchor geometry, vertices in world meters of `epoch`; per-face `classification` property when meshWithClassification."},
        "epoch": {"type": "string"},
        "faceCount": {"type": "integer", "minimum": 0},
        "classified": {"type": "boolean"}
      }
    },
    "streams": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "motion": {"$ref": "#/$defs/motionStream"},
        "heading": {"$ref": "#/$defs/headingStream"},
        "location": {"$ref": "#/$defs/locationStream"},
        "altimeter": {"$ref": "#/$defs/csvStream"}
      }
    },
    "files": {
      "type": "array",
      "minItems": 1,
      "description": "Every file of the packet except packet.json, with size and SHA-256, so uploads can resume and the server can prove it has the exact bytes (PR #18).",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["path", "bytes", "sha256", "role"],
        "properties": {
          "path": {"$ref": "#/$defs/relPath"},
          "bytes": {"type": "integer", "minimum": 0},
          "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
          "role": {"enum": ["keyframe", "depth", "confidence", "features", "still", "stream", "mesh", "compat", "other"]}
        }
      }
    },
    "ext": {"type": "object", "description": "Experimental fields; ignored by the server."}
  },
  "allOf": [
    {
      "if": {"properties": {"session": {"properties": {"tier": {"enum": ["arkit", "arkit_lidar"]}}}}},
      "then": {
        "required": ["epochs", "tracking"],
        "properties": {
          "keyframes": {"minItems": 1},
          "epochs": {"minItems": 1},
          "session": {"required": ["worldAlignment", "videoFormat"]}
        }
      }
    },
    {
      "if": {"properties": {"session": {"properties": {"tier": {"const": "photo_only"}}}}},
      "then": {"required": ["stills"], "properties": {"stills": {"minItems": 1}, "keyframes": {"maxItems": 0}}}
    },
    {
      "if": {"properties": {"session": {"properties": {"tier": {"const": "arkit_lidar"}}}}},
      "then": {"properties": {"session": {"properties": {"lidarAvailable": {"const": true}}}}}
    }
  ],
  "$defs": {
    "isoTime": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"},
    "relPath": {"type": "string", "minLength": 1, "pattern": "^(?!/)(?!.*\\.\\./).+$"},
    "size": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer", "minimum": 1}, "description": "[width, height] in pixels."},
    "vec3": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
    "mat4": {"type": "array", "minItems": 16, "maxItems": 16, "items": {"type": "number"}, "description": "4x4, 16 numbers column by column (simd_float4x4 layout)."},
    "intrinsics": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number"}, "description": "[fx, fy, cx, cy] in pixels of the saved image."},
    "trackingState": {"enum": ["normal", "notAvailable", "limited.initializing", "limited.excessiveMotion", "limited.insufficientFeatures", "limited.relocalizing"]},
    "exif": {
      "type": "object",
      "description": "Subset of the image's EXIF dictionary (kCGImagePropertyExif* keys without the prefix), from ARFrame.exifData (iOS 16+) or AVCapturePhoto.metadata.",
      "properties": {
        "Make": {"type": "string"},
        "Model": {"type": "string"},
        "LensModel": {"type": "string"},
        "ExposureTime": {"type": "number", "exclusiveMinimum": 0},
        "ISOSpeedRatings": {"type": "array", "items": {"type": "number"}},
        "FNumber": {"type": "number", "exclusiveMinimum": 0},
        "FocalLength": {"type": "number", "exclusiveMinimum": 0},
        "FocalLenIn35mmFilm": {"type": "number", "exclusiveMinimum": 0},
        "BrightnessValue": {"type": "number"},
        "ExposureBiasValue": {"type": "number"},
        "DateTimeOriginal": {"type": "string"},
        "SubsecTimeOriginal": {"type": "string"},
        "OffsetTimeOriginal": {"type": "string"},
        "Orientation": {"type": "integer", "minimum": 1, "maximum": 8},
        "PixelXDimension": {"type": "integer"},
        "PixelYDimension": {"type": "integer"}
      }
    },
    "keyframe": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "img", "w", "h", "intrinsics", "pose", "timestamp", "tracking", "epoch", "reason"],
      "properties": {
        "id": {"type": "string", "minLength": 1},
        "img": {"$ref": "#/$defs/relPath", "description": "JPEG of ARFrame.capturedImage, unrotated landscape sensor layout, quality 0.9."},
        "w": {"type": "integer", "minimum": 1},
        "h": {"type": "integer", "minimum": 1},
        "intrinsics": {"$ref": "#/$defs/intrinsics", "description": "ARCamera.intrinsics for this image (rescale if the saved image differs from ARCamera.imageResolution)."},
        "pose": {"$ref": "#/$defs/mat4", "description": "ARCamera.transform: camera-to-world, meters."},
        "timestamp": {"type": "number", "description": "ARFrame.timestamp (device uptime seconds)."},
        "tracking": {"$ref": "#/$defs/trackingState"},
        "epoch": {"type": "string"},
        "reason": {"enum": ["motion", "tap", "freeze", "still", "request", "auto"], "description": "Why it was saved: motion spacing, a tap, a frozen frame, a checklist still, a server/guidance request, or an automatic quality trigger."},
        "highResolution": {"type": "boolean", "description": "From ARSession.captureHighResolutionFrame (iOS 16+)."},
        "exposure": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "duration": {"type": "number", "exclusiveMinimum": 0, "description": "ARCamera.exposureDuration, seconds (iOS 13+)."},
            "offset": {"type": "number", "description": "ARCamera.exposureOffset, EV (iOS 13+)."},
            "iso": {"type": "number", "exclusiveMinimum": 0},
            "fNumber": {"type": "number", "exclusiveMinimum": 0}
          }
        },
        "exif": {"$ref": "#/$defs/exif"},
        "lightEstimate": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "ambientIntensity": {"type": "number", "description": "ARLightEstimate.ambientIntensity, lumens (1000 is neutral)."},
            "ambientColorTemperature": {"type": "number", "description": "Kelvin."}
          }
        },
        "cameraGrainIntensity": {"type": "number", "minimum": 0, "maximum": 1},
        "worldMappingStatus": {"enum": ["notAvailable", "limited", "extending", "mapped"]},
        "depth": {
          "oneOf": [
            {"type": "null"},
            {
              "type": "object",
              "additionalProperties": false,
              "required": ["file", "w", "h"],
              "properties": {
                "file": {"$ref": "#/$defs/relPath", "description": "ARDepthData.depthMap: Float32 little-endian meters, row-major w x h, no row padding."},
                "confidenceFile": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/relPath"}], "description": "ARDepthData.confidenceMap: UInt8 ARConfidenceLevel (0 low, 1 medium, 2 high), same layout."},
                "w": {"type": "integer", "minimum": 1},
                "h": {"type": "integer", "minimum": 1},
                "smoothed": {"type": "boolean", "description": "true when taken from ARFrame.smoothedSceneDepth."}
              }
            }
          ]
        },
        "features": {
          "oneOf": [
            {"type": "null"},
            {
              "type": "object",
              "additionalProperties": false,
              "required": ["file", "count"],
              "properties": {
                "file": {"$ref": "#/$defs/relPath", "description": "ARFrame.rawFeaturePoints: packed little-endian records {float32 x, y, z (world meters); uint64 identifier}, 20 bytes each."},
                "count": {"type": "integer", "minimum": 0}
              }
            }
          ]
        },
        "purpose": {"type": "string", "description": "Set when this frame also serves as a checklist still (see stills.purpose)."},
        "admission": {
          "type": "object",
          "description": "PR #18 capture-admission record for this frame, when the app runs one.",
          "additionalProperties": false,
          "properties": {
            "requestId": {"type": "string"},
            "usable": {"type": "boolean"},
            "supports": {
              "type": "array",
              "items": {"type": "object", "additionalProperties": false, "required": ["requirement", "revision"], "properties": {"requirement": {"type": "string"}, "revision": {"type": "integer", "minimum": 1}}}
            }
          }
        },
        "ext": {"type": "object"}
      }
    },
    "still": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "purpose", "img", "w", "h", "timestamp", "orientation"],
      "properties": {
        "id": {"type": "string", "minLength": 1},
        "purpose": {"type": "string", "description": "meter_close, meter_surround, meter_left, meter_right, adjacent_wall, behind_fence, panel_close, panel_open, main_disconnect, panel_surround, house_overview, other. Unknown values are treated as other."},
        "img": {"$ref": "#/$defs/relPath"},
        "w": {"type": "integer", "minimum": 1},
        "h": {"type": "integer", "minimum": 1},
        "timestamp": {"type": "number", "description": "Device uptime seconds of the exposure."},
        "orientation": {"type": "integer", "minimum": 1, "maximum": 8, "description": "EXIF orientation of the stored pixels (1 = stored upright; 6 = rotate 90 degrees clockwise to display, the usual portrait case for unrotated sensor images)."},
        "keyframe": {"type": "string", "description": "Id of the AR keyframe these pixels are, if any; its pose and intrinsics apply."},
        "intrinsics": {"$ref": "#/$defs/intrinsics"},
        "intrinsicsSource": {"enum": ["arkit", "calibration", "exif_focal35"]},
        "gravity": {"$ref": "#/$defs/vec3", "description": "CMDeviceMotion.gravity at the exposure, device frame, g."},
        "location": {
          "type": "object",
          "additionalProperties": false,
          "required": ["latitude", "longitude"],
          "properties": {
            "latitude": {"type": "number", "minimum": -90, "maximum": 90},
            "longitude": {"type": "number", "minimum": -180, "maximum": 180},
            "horizontalAccuracy": {"type": "number"}
          }
        },
        "exif": {"$ref": "#/$defs/exif"},
        "ext": {"type": "object"}
      }
    },
    "tap": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "time", "keyframe", "pixel", "rayOrigin", "rayDirection", "label"],
      "properties": {
        "id": {"type": "string", "minLength": 1},
        "time": {"type": "number"},
        "label": {"type": "string", "description": "meter, wall_end_left, wall_end_right, corner, wall_contact, gas_meter, ac_unit, door_corner, window_corner, fence, driveway_edge, reference_point, other."},
        "keyframe": {"type": "string", "description": "The keyframe the pixel is in (saved for the tap if needed)."},
        "pixel": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}, "description": "[u, v] continuous pixels of that keyframe's JPEG."},
        "rayOrigin": {"$ref": "#/$defs/vec3"},
        "rayDirection": {"$ref": "#/$defs/vec3"},
        "hit": {
          "type": "object",
          "additionalProperties": false,
          "required": ["position", "target"],
          "properties": {
            "position": {"$ref": "#/$defs/vec3"},
            "target": {"enum": ["existingPlaneGeometry", "existingPlaneInfinite", "estimatedPlane", "sceneDepth", "mesh", "none"]},
            "alignment": {"enum": ["horizontal", "vertical", "any"]},
            "planeId": {"type": "string"},
            "distance": {"type": "number", "minimum": 0}
          }
        },
        "tool": {"type": "string"},
        "step": {"type": "string"},
        "frozen": {"type": "boolean"},
        "displayMappingCheck": {"type": ["number", "null"]},
        "confirmedByUser": {"type": "boolean"},
        "refusal": {"type": ["string", "null"]},
        "epoch": {"type": "string"},
        "ext": {"type": "object"}
      }
    },
    "plane": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "epoch", "alignment", "transform", "planeExtent"],
      "properties": {
        "id": {"type": "string", "minLength": 1, "description": "ARAnchor.identifier."},
        "epoch": {"type": "string"},
        "alignment": {"enum": ["horizontal", "vertical"]},
        "classification": {"enum": ["none", "wall", "floor", "ceiling", "table", "seat", "window", "door", "notAvailable", "undetermined", "unknown"]},
        "transform": {"$ref": "#/$defs/mat4", "description": "ARAnchor.transform (anchor-to-world). The plane lies in the anchor's x-z plane; anchor +y is the normal."},
        "center": {"$ref": "#/$defs/vec3", "description": "ARPlaneAnchor.center in anchor coordinates."},
        "planeExtent": {
          "type": "object",
          "additionalProperties": false,
          "required": ["width", "height"],
          "properties": {
            "width": {"type": "number", "minimum": 0},
            "height": {"type": "number", "minimum": 0},
            "rotationOnYAxis": {"type": "number", "description": "Radians, as ARPlaneExtent reports it."}
          }
        },
        "boundary": {"type": "array", "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}, "description": "ARPlaneGeometry.boundaryVertices as [x, z] in anchor coordinates."},
        "firstSeen": {"type": "number"},
        "lastUpdated": {"type": "number"},
        "sourceInliers": {"type": "integer", "description": "Synthetic packets only."}
      }
    },
    "csvStream": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "columns"],
      "properties": {
        "file": {"$ref": "#/$defs/relPath"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "rateHz": {"type": "number"}
      }
    },
    "motionStream": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "columns", "rateHz", "referenceFrame"],
      "properties": {
        "file": {"$ref": "#/$defs/relPath"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "rateHz": {"type": "number", "exclusiveMinimum": 0},
        "referenceFrame": {"enum": ["xArbitraryZVertical", "xArbitraryCorrectedZVertical", "xMagneticNorthZVertical", "xTrueNorthZVertical"]}
      }
    },
    "headingStream": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "columns"],
      "properties": {
        "file": {"$ref": "#/$defs/relPath"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "headingOrientation": {"enum": ["portrait", "portraitUpsideDown", "landscapeLeft", "landscapeRight", "faceUp", "faceDown", "unknown"]},
        "headingFilter": {"type": "number"}
      }
    },
    "locationStream": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "columns"],
      "properties": {
        "file": {"$ref": "#/$defs/relPath"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "desiredAccuracy": {"enum": ["bestForNavigation", "best", "nearestTenMeters", "hundredMeters", "kilometer", "threeKilometers", "reduced"]}
      }
    }
  }
}
```

## Appendix B: example packets

**ARKit tier, non-LiDAR iPhone 15** (three keyframes, one high-resolution meter close-up, one meter tap, one wall plane). The hashes are illustrative.

```json
{
  "format": "house-capture-packet",
  "formatVersion": "0.1",
  "packetId": "5b0c1e9e-7a8e-4f0e-9d8c-2f1b7a3c9e41",
  "createdAt": "2026-09-27T16:05:12Z",
  "units": {
    "length": "meters",
    "angle": "degrees",
    "time": "seconds of device uptime, the clock of ARFrame.timestamp and CMDeviceMotion.timestamp",
    "image": "pixels of the saved JPEG",
    "rotationRate": "radians per second",
    "acceleration": "g (9.80665 m/s^2)",
    "magneticField": "microtesla"
  },
  "conventions": {
    "world": "ARKit world frame with worldAlignment .gravity: right-handed, y up (away from gravity), origin at the device's position when the session (epoch) started, -z the camera's initial facing projected onto the horizontal",
    "camera": "ARKit camera frame: +x right and +y up in the unrotated landscape sensor image, the camera looks along -z",
    "device": "Core Motion device frame: +x right and +y toward the top with the phone upright in portrait, +z out of the screen",
    "pose": "camera-to-world 4x4 matrix, 16 numbers column by column (simd_float4x4 layout), meters",
    "intrinsics": "[fx, fy, cx, cy] in pixels of the saved, unrotated landscape image",
    "pixel": "[u, v] continuous image coordinates: (0, 0) is the top-left corner of the top-left pixel, v grows down",
    "ray": "origin is the camera position; direction is a unit vector; camera-space direction ((u - cx)/fx, -(v - cy)/fy, -1)",
    "geodetic": "WGS84 degrees; altitude above mean sea level, ellipsoidalAltitude above the WGS84 ellipsoid, meters",
    "heading": "degrees clockwise from north, 0 to 360"
  },
  "source": {
    "kind": "device",
    "appBuild": "HouseScan 0.4 (57)"
  },
  "session": {
    "id": "3F2C9A10-5D7B-4E0A-9B61-0C8E2D4F7A13",
    "startedAt": "2026-09-27T15:58:40.120Z",
    "startedAtUptime": 84205.13200000001,
    "endedAtUptime": 84453.33200000001,
    "timeZone": "America/Chicago",
    "appVersion": "0.4 (57)",
    "deviceModel": "iPhone15,4",
    "systemVersion": "26.0",
    "tier": "arkit",
    "lidarAvailable": false,
    "meshReconstructionSupported": false,
    "sceneDepthEnabled": false,
    "sceneReconstruction": "none",
    "worldAlignment": "gravity",
    "planeDetection": [
      "horizontal",
      "vertical"
    ],
    "videoFormat": {
      "imageResolution": [
        1920,
        1440
      ],
      "framesPerSecond": 60
    },
    "locationAuthorization": "fullAccuracy",
    "motionReferenceFrame": "xTrueNorthZVertical",
    "thermalStateMax": "fair",
    "gates": {
      "keyframeSpacingMeters": 0.5,
      "keyframeSpacingDegrees": 15.0,
      "trackingStableSeconds": 1.0
    }
  },
  "epochs": [
    {
      "id": "e1",
      "startTime": 84205.13200000001,
      "reason": "sessionStart"
    }
  ],
  "keyframes": [
    {
      "id": "k00001",
      "img": "keyframes/k00001.jpg",
      "w": 1920,
      "h": 1440,
      "intrinsics": [
        1442.1,
        1442.1,
        959.6,
        719.4
      ],
      "pose": [
        0,
        -1,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        0.02,
        -0.01,
        -0.05,
        1
      ],
      "timestamp": 84211.532,
      "tracking": "normal",
      "epoch": "e1",
      "reason": "motion",
      "exposure": {
        "duration": 0.00098,
        "offset": 0.0,
        "iso": 50,
        "fNumber": 1.6
      },
      "exif": {
        "Make": "Apple",
        "Model": "iPhone 15",
        "LensModel": "iPhone 15 back dual wide camera 6.24mm f/1.6",
        "ExposureTime": 0.00098,
        "ISOSpeedRatings": [
          50
        ],
        "FNumber": 1.6,
        "FocalLength": 6.24,
        "FocalLenIn35mmFilm": 26,
        "BrightnessValue": 9.21
      },
      "lightEstimate": {
        "ambientIntensity": 1380.0,
        "ambientColorTemperature": 5900.0
      },
      "depth": null,
      "features": {
        "file": "keyframes/k00001.features.bin",
        "count": 212
      }
    },
    {
      "id": "k00002",
      "img": "keyframes/k00002.jpg",
      "w": 1920,
      "h": 1440,
      "intrinsics": [
        1442.1,
        1442.1,
        959.6,
        719.4
      ],
      "pose": [
        0,
        -1,
        0,
        0,
        0.9659,
        0,
        0.2588,
        0,
        -0.2588,
        0,
        0.9659,
        0,
        0.35,
        -0.08,
        -1.25,
        1
      ],
      "timestamp": 84213.402,
      "tracking": "normal",
      "epoch": "e1",
      "reason": "tap",
      "exposure": {
        "duration": 0.00112,
        "offset": 0.0,
        "iso": 50,
        "fNumber": 1.6
      },
      "depth": null,
      "features": {
        "file": "keyframes/k00002.features.bin",
        "count": 187
      }
    },
    {
      "id": "k00003",
      "img": "keyframes/k00003.jpg",
      "w": 4032,
      "h": 3024,
      "intrinsics": [
        3028.4,
        3028.4,
        2015.2,
        1510.7
      ],
      "pose": [
        0,
        -1,
        0,
        0,
        0.9659,
        0,
        0.2588,
        0,
        -0.2588,
        0,
        0.9659,
        0,
        0.85,
        -0.15,
        -2.75,
        1
      ],
      "timestamp": 84220.952,
      "tracking": "normal",
      "epoch": "e1",
      "reason": "still",
      "highResolution": true,
      "purpose": "meter_close",
      "exposure": {
        "duration": 0.0021,
        "offset": 0.0,
        "iso": 64,
        "fNumber": 1.6
      },
      "depth": null,
      "features": null
    }
  ],
  "stills": [
    {
      "id": "s1",
      "purpose": "meter_close",
      "img": "keyframes/k00003.jpg",
      "w": 4032,
      "h": 3024,
      "timestamp": 84220.952,
      "orientation": 6,
      "keyframe": "k00003",
      "intrinsicsSource": "arkit",
      "gravity": [
        0.012,
        -0.989,
        -0.146
      ]
    }
  ],
  "taps": [
    {
      "id": "t1",
      "time": 84213.402,
      "label": "meter",
      "tool": "meter",
      "step": "anchor",
      "keyframe": "k00002",
      "frozen": false,
      "pixel": [
        1010.0,
        655.0
      ],
      "rayOrigin": [
        0.35,
        -0.08,
        -1.25
      ],
      "rayDirection": [
        0.30147,
        -0.0349,
        -0.95284
      ],
      "hit": {
        "position": [
          0.9831,
          -0.1533,
          -3.251
        ],
        "target": "existingPlaneGeometry",
        "alignment": "vertical",
        "planeId": "8D3E6B1C-2F4A-4C7E-A1B9-6E0D5C2F8A77",
        "distance": 2.1
      },
      "displayMappingCheck": 0.6,
      "confirmedByUser": true,
      "epoch": "e1"
    }
  ],
  "tracking": [
    {
      "time": 84205.13200000001,
      "state": "limited.initializing",
      "epoch": "e1"
    },
    {
      "time": 84206.432,
      "state": "normal",
      "epoch": "e1"
    }
  ],
  "planes": [
    {
      "id": "8D3E6B1C-2F4A-4C7E-A1B9-6E0D5C2F8A77",
      "epoch": "e1",
      "alignment": "vertical",
      "classification": "wall",
      "transform": [
        1,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        -1,
        0,
        0,
        0.8,
        -0.6,
        -3.25,
        1
      ],
      "center": [
        0,
        0,
        0
      ],
      "planeExtent": {
        "width": 4.2,
        "height": 2.6,
        "rotationOnYAxis": 0.0
      },
      "boundary": [
        [
          -2.1,
          -1.3
        ],
        [
          2.1,
          -1.3
        ],
        [
          2.1,
          1.3
        ],
        [
          -2.1,
          1.3
        ]
      ],
      "firstSeen": 84212.432,
      "lastUpdated": 84449.732
    }
  ],
  "streams": {
    "motion": {
      "file": "streams/motion.csv",
      "rateHz": 100,
      "referenceFrame": "xTrueNorthZVertical",
      "columns": [
        "t",
        "qx",
        "qy",
        "qz",
        "qw",
        "rotX",
        "rotY",
        "rotZ",
        "gravX",
        "gravY",
        "gravZ",
        "accX",
        "accY",
        "accZ",
        "magX",
        "magY",
        "magZ",
        "magAccuracy",
        "heading"
      ]
    },
    "heading": {
      "file": "streams/heading.csv",
      "headingOrientation": "portrait",
      "headingFilter": 1.0,
      "columns": [
        "t",
        "timestamp",
        "magneticHeading",
        "trueHeading",
        "headingAccuracy",
        "x",
        "y",
        "z"
      ]
    },
    "location": {
      "file": "streams/location.csv",
      "desiredAccuracy": "best",
      "columns": [
        "t",
        "timestamp",
        "latitude",
        "longitude",
        "altitude",
        "ellipsoidalAltitude",
        "horizontalAccuracy",
        "verticalAccuracy",
        "course",
        "courseAccuracy",
        "speed",
        "speedAccuracy"
      ]
    }
  },
  "files": [
    {
      "path": "keyframes/k00001.jpg",
      "bytes": 612344,
      "sha256": "1fe257e3be8488b30225faff5a8a7a05b6cf6efd2f4bf8990b34e610add6569d",
      "role": "keyframe"
    },
    {
      "path": "keyframes/k00002.jpg",
      "bytes": 598102,
      "sha256": "16af222466a6c7661fd56041bc58e6a180a977d231b95f7e6db327ba1c0858e5",
      "role": "keyframe"
    },
    {
      "path": "keyframes/k00003.jpg",
      "bytes": 2811904,
      "sha256": "b41e1827243f6c809b59fa6abb8ad208bfc096726e3f1db0c5eca38090e0ec75",
      "role": "keyframe"
    },
    {
      "path": "keyframes/k00001.features.bin",
      "bytes": 4240,
      "sha256": "52820c619e1706e71184d3f855560fbdbd46b3056cb01e5891e7889c01d09afd",
      "role": "features"
    },
    {
      "path": "keyframes/k00002.features.bin",
      "bytes": 3740,
      "sha256": "bdd244fdccdcbae63af33176a6e26c02955d288085afec04888bc840424c15bf",
      "role": "features"
    },
    {
      "path": "streams/motion.csv",
      "bytes": 3187220,
      "sha256": "15be3d3211669e6ce8b3e482f790211301270d65f36128b3fa0b386cbf6aea5c",
      "role": "stream"
    },
    {
      "path": "streams/heading.csv",
      "bytes": 61240,
      "sha256": "54de152405d41d6be249f915d91aed4adc10701a18bf18033ddbf542ddc1ec21",
      "role": "stream"
    },
    {
      "path": "streams/location.csv",
      "bytes": 29880,
      "sha256": "6aaa7b6e1e979716a28efbe06ed602ef78640cf41984cf419df34925a8fff07f",
      "role": "stream"
    }
  ]
}
```

The first keyframe shows the upright-portrait case: camera x points down the world (−y), camera y points right (+x), and the camera looks along world −z.

**Photo-only tier** (one panel photo from a phone without an AR session):

```json
{
  "format": "house-capture-packet",
  "formatVersion": "0.1",
  "packetId": "a41f0c7e-3b2d-4f1a-8e6c-9d0b2a7c5e18",
  "createdAt": "2026-09-27T16:20:03Z",
  "units": {
    "length": "meters",
    "angle": "degrees",
    "time": "seconds of device uptime, the clock of ARFrame.timestamp and CMDeviceMotion.timestamp",
    "image": "pixels of the saved JPEG"
  },
  "conventions": {
    "world": "ARKit world frame with worldAlignment .gravity: right-handed, y up (away from gravity), origin at the device's position when the session (epoch) started, -z the camera's initial facing projected onto the horizontal",
    "camera": "ARKit camera frame: +x right and +y up in the unrotated landscape sensor image, the camera looks along -z",
    "pose": "camera-to-world 4x4 matrix, 16 numbers column by column (simd_float4x4 layout), meters",
    "intrinsics": "[fx, fy, cx, cy] in pixels of the saved, unrotated landscape image",
    "pixel": "[u, v] continuous image coordinates: (0, 0) is the top-left corner of the top-left pixel, v grows down"
  },
  "source": {
    "kind": "device"
  },
  "session": {
    "id": "9E1B7C44-0A2D-4B6F-8C3E-5F7A1D2B9C60",
    "startedAt": "2026-09-27T16:18:51Z",
    "startedAtUptime": 90112.4,
    "appVersion": "0.4 (57)",
    "deviceModel": "iPhone14,5",
    "systemVersion": "26.0",
    "tier": "photo_only",
    "lidarAvailable": false,
    "sceneDepthEnabled": false
  },
  "keyframes": [],
  "stills": [
    {
      "id": "s1",
      "purpose": "panel_open",
      "img": "stills/s1.jpg",
      "w": 4032,
      "h": 3024,
      "timestamp": 90131.07,
      "orientation": 6,
      "intrinsics": [
        2970.6,
        2970.6,
        2016.0,
        1512.0
      ],
      "intrinsicsSource": "exif_focal35",
      "gravity": [
        0.03,
        -0.97,
        -0.24
      ],
      "location": {
        "latitude": 30.26705,
        "longitude": -97.74322,
        "horizontalAccuracy": 9.4
      },
      "exif": {
        "Make": "Apple",
        "Model": "iPhone 13",
        "LensModel": "iPhone 13 back dual wide camera 5.1mm f/1.6",
        "ExposureTime": 0.0083,
        "ISOSpeedRatings": [
          320
        ],
        "FNumber": 1.6,
        "FocalLength": 5.1,
        "FocalLenIn35mmFilm": 26,
        "DateTimeOriginal": "2026:09:27 11:19:10",
        "OffsetTimeOriginal": "-05:00",
        "Orientation": 6
      }
    }
  ],
  "files": [
    {
      "path": "stills/s1.jpg",
      "bytes": 3120448,
      "sha256": "fbffed3a377eb14f906eaf3469f178cedca03eb6ff1e98e9c02fab6f8f5c13d7",
      "role": "still"
    }
  ]
}
```

**Stream rows** (first rows of each CSV):

```
# streams/motion.csv
t,qx,qy,qz,qw,rotX,rotY,rotZ,gravX,gravY,gravZ,accX,accY,accZ,magX,magY,magZ,magAccuracy,heading
84205.132,0.4821,-0.5163,-0.4917,0.5082,0.0132,-0.0071,0.0045,0.0021,-0.9994,0.0339,0.0104,-0.0088,0.0017,-11.82,-35.41,-22.07,2,196.4
# streams/heading.csv
t,timestamp,magneticHeading,trueHeading,headingAccuracy,x,y,z
84205.140,2026-09-27T15:58:39.140Z,194.1,196.5,17.0,-11.8,-35.4,-22.1
# streams/location.csv
t,timestamp,latitude,longitude,altitude,ellipsoidalAltitude,horizontalAccuracy,verticalAccuracy,course,courseAccuracy,speed,speedAccuracy
84206.020,2026-09-27T15:58:40.020Z,30.2671832,-97.7430615,151.2,124.8,9.43,6.1,-1,-1,0.12,0.9
```

## Appendix C: Apple references

ARKit: [ARFrame](https://developer.apple.com/documentation/arkit/arframe), [ARCamera](https://developer.apple.com/documentation/arkit/arcamera), [ARDepthData](https://developer.apple.com/documentation/arkit/ardepthdata), [ARWorldTrackingConfiguration](https://developer.apple.com/documentation/arkit/arworldtrackingconfiguration), [ARConfiguration.WorldAlignment](https://developer.apple.com/documentation/arkit/arconfiguration/worldalignment-swift.enum), [ARPlaneAnchor](https://developer.apple.com/documentation/arkit/arplaneanchor), [ARMeshAnchor](https://developer.apple.com/documentation/arkit/armeshanchor), [ARPointCloud](https://developer.apple.com/documentation/arkit/arpointcloud), [ARRaycastQuery](https://developer.apple.com/documentation/arkit/arraycastquery). Core Motion: [CMDeviceMotion](https://developer.apple.com/documentation/coremotion/cmdevicemotion), [CMAttitudeReferenceFrame](https://developer.apple.com/documentation/coremotion/cmattitudereferenceframe), [CMAltimeter](https://developer.apple.com/documentation/coremotion/cmaltimeter). Core Location: [CLLocation](https://developer.apple.com/documentation/corelocation/cllocation), [CLHeading](https://developer.apple.com/documentation/corelocation/clheading), [CLLocationManager](https://developer.apple.com/documentation/corelocation/cllocationmanager). Image I/O: [EXIF dictionary keys](https://developer.apple.com/documentation/imageio/exif-dictionary-keys). AVFoundation: [AVCapturePhoto](https://developer.apple.com/documentation/avfoundation/avcapturephoto), [AVCameraCalibrationData](https://developer.apple.com/documentation/avfoundation/avcameracalibrationdata).
