# Capture packet spec v0.4 (for the iOS team to build against)

**What this is.** The contract between the iOS capture app and the server pipeline: one capture session becomes one *packet*, a folder (or zip) holding the photos plus everything ARKit, Core Motion and Core Location reported while taking them. The server builds the 3D asset and evaluates the eligibility criteria from it. This is v0.4 (changes are listed right below), doc revision 0.4.2 of 2026-09-27 (`formatVersion` stays `"0.4"`; 0.4.1 was editorial, 0.4.2 adds the server id and path patterns): field names can still change before 1.0, and every open question at the end has a default we will build against until you answer.

**Built on what already exists, not beside it.**
- **PR #7 `measure-lab-session` v2** is the base. Keyframe, tap and tracking fields keep its names, units and conventions exactly (meters, gravity-aligned y-up world, column-major camera-to-world pose, unrotated landscape JPEG, `[fx, fy, cx, cy]`, `k00001.depth.f32` / `.confidence.u8`). A packet converts losslessly into a v2 `session.json`, so PR #10's replay, PR #9's converter and PR #12's field scoring read it unchanged.
- **PR #10 `scene.json` (contract C1)** stays what it is: the small placement request in feet. The packet is the raw evidence behind it and travels separately. `stills` purposes and `gps` / `heading` come from there.
- **PR #18 capture admission** adds coordinate *epochs* (a new ARKit world after a reset), a SHA-256 and size for every file, and an optional per-frame admission record.

Machine-readable schema (JSON Schema 2020-12): Appendix A, also at `internal/packet-generator/schema/capture-packet.schema.json` in the project store. Examples: Appendix B.

**Changes in v0.4 (2026-09-26).** Scale on phones without LiDAR now follows the finished no-tool study (A9, `internal/breadth/A9-no-tape-scale.md`). The primary scale source is the meter itself, read as an exact make and model from two close-ups; ARKit is the gate; doors and the building footprint are checks only. The homeowner still never measures anything or uses a tool.
- **Two meter close-ups inside the ARKit session** (sections 3.4, 3.7, 5.2): head-on from about 3 ft, plus one from about 30° to the side at the same distance, both with a legible nameplate. New still purpose `meter_oblique`.
- **Passive logging** (section 3.5): two new streams, required in ARKit tiers. `arkitPoses` has every ARFrame's pose, intrinsics and tracking at 30 Hz or more. `imuRaw` is raw accelerometer and gyro at 100 Hz, for offline visual-inertial bundle adjustment.
- **One head-on view of any door on the meter side** (entry or garage), recorded in `scaleReference.ext.doorViews`.
- **Service address** from the account, for the server's building-footprint lookup (`scaleReference.ext.serviceAddress`).
- **Optional front TrueDepth scan of the meter** (about 2 s at 30–45 cm), prompted only in shade or when scale comes back unverified (`scaleReference.ext.trueDepthScan`, binary layout in section 4).
- `scaleReference` is now **required in ARKit tiers**, and so is its `ext` with `method`, `obliqueCloseUp` and `doorViews`. Every item may be reported as not captured, so nothing forces the homeowner. It stays optional for `photo_only`.
- `formatVersion` is `"0.4"`. v0.3 ARKit packets fail v0.4 validation (no `ext`, no new streams).

- **Upload API (Appendix D; design in `docs/hosted-architecture.md`)** is part of v0.4 and reconciled with the packet format:
  - streams travel gzip-compressed as `streams/<name>.csv.gz` and are listed under that path in `files[]` and `streams.*.file`;
  - the Upload API's per-file `role` takes exactly the `files[].role` values (so `keyframe`, not `keyframe_image`), with a new role `truedepth` for TrueDepth frames;
  - `meta` for a TrueDepth file is its `trueDepthFrame` record, as it is the keyframe or still record for images;
  - retakes are amendments: an ordinary v0.4 packet uploaded under `amendments/{aid}/`, with the new optional `parentPacketId`. A9's `retake_meter_closeups` and `truedepth_meter_scan` actions arrive as retake requests and are answered this way;
  - section 5 rule 5 now defers to Appendix D (finalize may come before the last commits).

**Doc revision 0.4.1 (2026-09-26, 22:5x UTC; coordinator, per `internal/build/reconciliation-queue.md` and `internal/build/seam-decisions.md` §9 item 4). No field, unit or convention changes; `formatVersion` stays `"0.4"`.**
- Appendix A: the `scaleReference.ext.obliqueCloseUp.still` description said "purpose meter_close"; it is the `meter_oblique` still (sections 3.4, 3.7, 5.2 and the validator always said so). `$id` now names the server repo (`huntertcarver/house-scanning-server`) instead of `SamGu-NRX/house-scanning`. The JSON copies (`internal/packet-generator/schema/capture-packet.schema.json`, `server/contracts/schemas/capture-packet.v0.4.schema.json`) must be updated to match by the WS-00 / WS-14 spec PR; until then the kit's `check-spec` parity check will flag these two descriptions.
- Section 3.2: `depth` on a `highResolution: true` keyframe may be `null` even when `sceneDepthEnabled` (`captureHighResolutionFrame` output need not carry `sceneDepth`; the v0.4 example's k00003 already shows it); the server warns (`depth_missing_highres`) instead of rejecting.
- Section 3.5: `arkitPoses` is requested at 30 Hz or more; the schema floor is `rateHz ≥ 10`.
- Section 6: non-increasing timestamps within an epoch are a warning (`keyframe_order`); only two keyframes of one epoch with the same timestamp and different poses are an error (`keyframe_timestamp_conflict`).
- Appendix D: the role `compat` is defined (server-derived transcodes, never sent by the app); finalize headers (`Idempotency-Key`, `X-Packet-Sha256`, `Content-Encoding: gzip`), the `422 format_version_unsupported` and `role_unknown` codes, the `POST /v1/captures/{id}/confirmations` one-tap answer, TrueDepth frames in the upload order, and `parentPacketId` on amendment packets.

**Doc revision 0.4.2 (2026-09-27). The server models reject ids and paths outside the patterns in "Id and path characters". `formatVersion` stays `"0.4"`. Appendix A's JSON Schema is unchanged (`minLength`, and the older `relPath`); a packet can satisfy Appendix A and still be rejected by the server.**

History: v0.2 briefly added a homeowner tape step; v0.3 removed it because no customer tools are allowed.

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
| `format`, `formatVersion`, `packetId`, `createdAt` | R | app | `"house-capture-packet"`, `"0.4"`, `StorageId`, ISO 8601 UTC; `packetId` is the Upload API's `Idempotency-Key` (Appendix D) |
| `parentPacketId` | R on amendments | app | `StorageId`: `packetId` of the packet a retake amendment answers (Appendix D step 5) |
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
| `files[]`: `path`, `bytes`, `sha256`, `role` | R | app | every file except `packet.json`; `path` is a `RelPath` |

#### Id and path characters

The server stores these strings in object keys and state keys, so it enforces a tighter pattern than Appendix A. Generate ids inside these patterns. A UUIDv4 written with hyphens is 36 characters and fits `StorageId`.

- **`StorageId`** `^[A-Za-z0-9_-]{8,64}$` — `packetId`, `parentPacketId`, and `amendmentId`. UUIDs, ULIDs, and `mini-arkit` fit. `/`, `.` (so `..`), and control characters do not.
- **`SegmentId`** `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` — `Keyframe.id` and `Still.id`, which become one path segment (`dense/depth/<id>.npz`, `normalized/stills/<id>.jpg`). No slash, no dot, no leading `-`, no control character. `k00001` and `s1` fit.
- **`RelPath`** `^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*(/[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*)*$` — every relative object key, including `files[].path` and `Mesh.file`. Each segment starts with an alphanumeric, so `.` / `..` segments, `foo..bar`, a leading `-`, `\`, and control characters are rejected.

### 3.2 Keyframes (ARKit tiers)

A keyframe is an `ARFrame` the app saved, with that frame's own pose and calibration. Save all keyframes at their own timestamps, not "the latest pose".

| Field | Level | Source | Notes |
| --- | --- | --- | --- |
| `id`, `img`, `w`, `h` | R | [`ARFrame.capturedImage`](https://developer.apple.com/documentation/arkit/arframe/capturedimage) | `id` is a `SegmentId`; JPEG quality 0.9, sRGB, **unrotated** |
| `intrinsics` | R | [`ARCamera.intrinsics`](https://developer.apple.com/documentation/arkit/arcamera/intrinsics), [`imageResolution`](https://developer.apple.com/documentation/arkit/arcamera/imageresolution) | per frame; do not reuse one value for the session |
| `pose` | R | [`ARCamera.transform`](https://developer.apple.com/documentation/arkit/arcamera/transform) | |
| `timestamp` | R | [`ARFrame.timestamp`](https://developer.apple.com/documentation/arkit/arframe/timestamp) | uptime seconds |
| `tracking` | R | [`ARCamera.trackingState`](https://developer.apple.com/documentation/arkit/arcamera/trackingstate-swift.enum) | state at this frame |
| `epoch`, `reason` | R | app | `reason`: `motion`, `tap`, `freeze`, `still`, `request`, `auto` |
| `exposure.duration`, `exposure.offset` | Rec | [`ARCamera.exposureDuration`](https://developer.apple.com/documentation/arkit/arcamera/exposureduration), [`exposureOffset`](https://developer.apple.com/documentation/arkit/arcamera/exposureoffset) (iOS 13+) | seconds, EV; motion blur and glare checks |
| `exif` | Rec | [`ARFrame.exifData`](https://developer.apple.com/documentation/arkit/arframe/exifdata) (**iOS 16+**) | copy `ExposureTime`, `ISOSpeedRatings`, `FNumber`, `FocalLength`, `FocalLenIn35mmFilm`, `BrightnessValue`, `LensModel` |
| `features` | Rec | [`ARFrame.rawFeaturePoints`](https://developer.apple.com/documentation/arkit/arframe/rawfeaturepoints) | binary layout in section 4; the tracker's own 3D points, which help seed reconstruction |
| `depth` | **R when `sceneDepthEnabled`** (may be `null` on a `highResolution: true` frame, which the server only warns about) | [`ARFrame.sceneDepth`](https://developer.apple.com/documentation/arkit/arframe/scenedepth) → [`depthMap`](https://developer.apple.com/documentation/arkit/ardepthdata/depthmap), [`confidenceMap`](https://developer.apple.com/documentation/arkit/ardepthdata/confidencemap) | same files as PR #7; `smoothed: true` if taken from [`smoothedSceneDepth`](https://developer.apple.com/documentation/arkit/arframe/smoothedscenedepth); `captureHighResolutionFrame` output need not carry `sceneDepth` (Appendix B's k00003 has `depth: null`) |
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
| `mesh`: `file`, `epoch`, `faceCount`, `classified` | O (LiDAR) | [`ARMeshAnchor`](https://developer.apple.com/documentation/arkit/armeshanchor) | `file` is a `RelPath`; one PLY in world meters |

### 3.4 Stills (checklist photos)

Base's checklist asks for the meter close-up, meter surroundings from 10+ steps back, left and right of the meter, the adjacent wall, behind the fence, the main panel (closed and open), the main disconnect with amperage, and the panel surroundings. A still can be an AR keyframe (`keyframe` set, ideally a high-resolution frame) or a plain camera photo.

| Field | Level | Source | Notes |
| --- | --- | --- | --- |
| `id`, `purpose`, `img`, `w`, `h`, `timestamp`, `orientation` | R (tier `photo_only` needs ≥ 1) | app | `id` is a `SegmentId`. `purpose`: `meter_close`, `meter_oblique`, `meter_surround`, `meter_left`, `meter_right`, `adjacent_wall`, `behind_fence`, `panel_close`, `panel_open`, `main_disconnect`, `panel_surround`, `house_overview`, `other` |
| `exif` | R for `photo_only`, Rec otherwise | [`AVCapturePhoto.metadata`](https://developer.apple.com/documentation/avfoundation/avcapturephoto/metadata) or `ARFrame.exifData` | `FocalLenIn35mmFilm` gives nominal intrinsics without ARKit |
| `keyframe` | Rec when taken in AR | app | pose and intrinsics then come from that keyframe |
| `gravity` | Rec | [`CMDeviceMotion.gravity`](https://developer.apple.com/documentation/coremotion/cmdevicemotion/gravity) | device frame, g; camera tilt and roll for photos without a pose |
| `intrinsics`, `intrinsicsSource`, `location` | O | | `intrinsicsSource`: `arkit`, `calibration`, `exif_focal35` |

**Two meter close-ups are required by the capture flow** whenever the meter is reachable (the schema can't enforce them, because a meter behind a locked gate is a valid outcome): `meter_close` head-on from about 3 ft (1 m), and `meter_oblique` from about 30° to the side at the same distance. In both, the whole cover, ring and socket face fill about half the frame and the nameplate (make, model, form, class) is legible. Take both **inside the running ARKit session**, walking up from the wall view, and link each to a keyframe (`keyframe`) so it keeps a pose. The server reads the exact model from the plate, looks up that model's cover diameter, and triangulates the rim from the two views; this is the primary scale source on phones without LiDAR (A9). The head-on one also serves meter-number reading (B2).

### 3.5 Streams (CSV, header row, one row per sample)

| Stream | Level | Source | Rate | Columns |
| --- | --- | --- | --- | --- |
| `motion` | Rec | [`CMDeviceMotion`](https://developer.apple.com/documentation/coremotion/cmdevicemotion) with reference frame [`xTrueNorthZVertical`](https://developer.apple.com/documentation/coremotion/cmattitudereferenceframe/xtruenorthzvertical) (falls back to `xArbitraryCorrectedZVertical` without location) | 100 Hz (≥ 50) | `t` uptime; `qx,qy,qz,qw` [attitude](https://developer.apple.com/documentation/coremotion/cmdevicemotion/attitude) quaternion; `rotX..Z` [rotationRate](https://developer.apple.com/documentation/coremotion/cmdevicemotion/rotationrate) rad/s; `gravX..Z` [gravity](https://developer.apple.com/documentation/coremotion/cmdevicemotion/gravity) g; `accX..Z` [userAcceleration](https://developer.apple.com/documentation/coremotion/cmdevicemotion/useracceleration) g; `magX..Z` [magneticField](https://developer.apple.com/documentation/coremotion/cmdevicemotion/magneticfield) µT; `magAccuracy` (−1 uncalibrated, 0 low, 1 medium, 2 high); `heading` [deg](https://developer.apple.com/documentation/coremotion/cmdevicemotion/heading) (north reference frames only) |
| `heading` | Rec | [`CLHeading`](https://developer.apple.com/documentation/corelocation/clheading) | on change (`headingFilter` 1°) | `t`; `timestamp` ISO; `magneticHeading`; [`trueHeading`](https://developer.apple.com/documentation/corelocation/clheading/trueheading) (negative = invalid); [`headingAccuracy`](https://developer.apple.com/documentation/corelocation/clheading/headingaccuracy) (negative = invalid); `x,y,z` µT |
| `location` | Rec | [`CLLocation`](https://developer.apple.com/documentation/corelocation/cllocation), `desiredAccuracy` [`kCLLocationAccuracyBest`](https://developer.apple.com/documentation/corelocation/kcllocationaccuracybest) | every update (~1 Hz) | `t`; `timestamp` ISO; `latitude`, `longitude`; [`altitude`](https://developer.apple.com/documentation/corelocation/cllocation/altitude); [`ellipsoidalAltitude`](https://developer.apple.com/documentation/corelocation/cllocation/ellipsoidalaltitude); [`horizontalAccuracy`](https://developer.apple.com/documentation/corelocation/cllocation/horizontalaccuracy); `verticalAccuracy`; `course`, `courseAccuracy`, `speed`, `speedAccuracy` (−1 = invalid) |
| `arkitPoses` | **R in ARKit tiers** | every [`ARFrame`](https://developer.apple.com/documentation/arkit/arframe) from [`session(_:didUpdate:)`](https://developer.apple.com/documentation/arkit/arsessiondelegate/session(_:didupdate:)-9v2kw): [`camera.transform`](https://developer.apple.com/documentation/arkit/arcamera/transform), [`camera.intrinsics`](https://developer.apple.com/documentation/arkit/arcamera/intrinsics), tracking state | 30 Hz or more requested (every frame or every other); the schema floor is `rateHz ≥ 10` | `t` uptime; `epoch`; `tracking`; `tx, ty, tz` m; `qx, qy, qz, qw` (camera-to-world, same frame as keyframe poses); `fx, fy, cx, cy` px at the video format's resolution |
| `imuRaw` | **R in ARKit tiers** | [`CMAccelerometerData`](https://developer.apple.com/documentation/coremotion/cmaccelerometerdata) and [`CMGyroData`](https://developer.apple.com/documentation/coremotion/cmgyrodata) via [`startAccelerometerUpdates`](https://developer.apple.com/documentation/coremotion/cmmotionmanager/startaccelerometerupdates(to:withhandler:)) and [`startGyroUpdates`](https://developer.apple.com/documentation/coremotion/cmmotionmanager/startgyroupdates(to:withhandler:)) | 100 Hz | `t` uptime; `ax, ay, az` g (device frame, gravity included); `gx, gy, gz` rad/s (not bias-corrected); gyro paired to the nearest accelerometer sample |
| `altimeter` | O | [`CMAltimeter`](https://developer.apple.com/documentation/coremotion/cmaltimeter) | on update | `t`, `relativeAltitude` m, `pressure` kPa |


Every stream file is gzip-compressed CSV, `streams/<name>.csv.gz`, and is listed under that path in `files[]` with `role: stream` (Appendix D).
Record `headingOrientation` in the packet ([`CLLocationManager.headingOrientation`](https://developer.apple.com/documentation/corelocation/cllocationmanager/headingorientation)). The property is deprecated in iOS 27 with no documented replacement; on iOS 27+ record the orientation you assume (`portrait` for this app) until Apple names a successor. Empty CSV cells mean "not available".

### 3.6 What the server does with each group

| Group | Used for |
| --- | --- |
| Keyframe pose and intrinsics | Metric scale and pose priors for reconstruction (COLMAP / MapAnything with poses), projecting detections into 3D, coverage (what was seen) |
| Epochs and tracking | Dropping or down-weighting `limited` frames; never mixing worlds |
| Depth and confidence | Metric surfaces and occlusion-aware coverage on LiDAR phones (PR #12 found coverage overclaims without a depth test) |
| Feature points and planes | Seeding and checking reconstruction; ground and wall planes |
| Taps (meter, wall ends) | The anchor of every placement distance (PR #10's s = 0) |
| Meter close-ups and `scaleReference` | Metric scale on non-LiDAR phones (A9): exact meter model → datasheet cover diameter, rim triangulated from the two close-ups; ARKit as the gate; doors, footprint (from the address) and TrueDepth as checks |
| Exposure and EXIF | Blur, glare and legibility checks; nominal intrinsics for photo-only |
| Motion | Gravity for photo-only; time sync; detecting fast motion |
| `arkitPoses`, `imuRaw` | ARKit scale gate; offline visual-inertial bundle adjustment on the raw IMU to tighten it (A9) |
| Heading and location | Map alignment (aerial imagery, parcel), which side of the house; never for distances |

### 3.7 Scale reference (v0.4; zero-effort only, per A9)

Nothing in this block asks the homeowner to measure or use a tool. It records the two meter close-ups, what the phone read from the nameplate, the check references and the optional TrueDepth scan. Every item can be reported as not captured.

| Field | Level | Source | Notes |
| --- | --- | --- | --- |
| `scaleReference` | **R in ARKit tiers**, O for `photo_only` | app | one object per packet |
| `.meterCloseUp.captured`, `.still` | R | app | head-on close-up (`purpose: meter_close`); `still` required if captured |
| `.meterCloseUp.arkitDistanceM` | Rec | [`ARRaycastQuery`](https://developer.apple.com/documentation/arkit/arraycastquery) at the meter's centre, or LiDAR depth | camera-to-meter distance; null without a hit |
| `.meterCloseUp.viewAngleDeg` | Rec | detected vertical [`ARPlaneAnchor`](https://developer.apple.com/documentation/arkit/arplaneanchor) | angle between viewing axis and wall normal (0 = head-on) |
| `.meterCloseUp.nameplateVisible` | O | [`VNRecognizeTextRequest`](https://developer.apple.com/documentation/vision/vnrecognizetextrequest) | legible text found on the meter face |
| `.meterIdentity`: `make`, `model`, `rawText`, `source` | O | on-device text recognition | `source`: `on_device_text` or `not_read`; the server reads the plate anyway (B2) |
| `.ext.method` | **R in ARKit tiers** | app | `"a9-meter-model"` for v0.4 captures |
| `.ext.obliqueCloseUp` | **R in ARKit tiers** | app | same fields as `meterCloseUp` plus `side` (`left` / `right`); `purpose: meter_oblique`, about 30° off head-on at the same distance |
| `.ext.doorViews[]`: `frame`, `kind`, `box`, `viewAngleDeg` | **R in ARKit tiers** (may be empty) | app | keyframe or still with a meter-side door seen head-on; `kind`: `entry_door`, `garage_door`, `other_door`; `box` `[u0, v0, u1, v1]` if the app detected it |
| `.ext.doorOnMeterSide` | Rec | app / homeowner glance | `false` makes an empty `doorViews` expected; null if unknown |
| `.ext.serviceAddress`: `line1`, `line2`, `city`, `state`, `postalCode`, `country`, `source` | Rec | the account or order, never typed at capture | the server looks up the building footprint (county GIS or Microsoft footprints) as a check |
| `.ext.trueDepthScan.captured` | O | app | front TrueDepth scan of the meter (below) |
| `.ext.trueDepthScan`: `prompt`, `failure`, `depthDataAccuracy`, `depthDataQuality`, `frames[]` | R if captured | [`builtInTrueDepthCamera`](https://developer.apple.com/documentation/avfoundation/avcapturedevice/devicetype-swift.struct/builtintruedepthcamera), [`AVCaptureDepthDataOutput`](https://developer.apple.com/documentation/avfoundation/avcapturedepthdataoutput), [`AVDepthData`](https://developer.apple.com/documentation/avfoundation/avdepthdata) ([`depthDataAccuracy`](https://developer.apple.com/documentation/avfoundation/avdepthdata/depthdataaccuracy), [`depthDataQuality`](https://developer.apple.com/documentation/avfoundation/avdepthdata/depthdataquality)) | `prompt`: `shade`, `scale_unverified`, `always`; `failure`: `too_bright`, `no_valid_depth`, `declined`, `unsupported_device`; relative accuracy can't give scale |
| `.ext.trueDepthScan.frames[]`: `depth`, `image`, `w`, `h`, `intrinsics`, `timestamp` | R if captured | [`AVCameraCalibrationData.intrinsicMatrix`](https://developer.apple.com/documentation/avfoundation/avcameracalibrationdata/intrinsicmatrix) scaled from [`intrinsicMatrixReferenceDimensions`](https://developer.apple.com/documentation/avfoundation/avcameracalibrationdata/intrinsicmatrixreferencedimensions) to `w × h` | up to 120 frames; `image` optional |
| `.ext` (other keys) | O | app | allowed and ignored, for future scale inputs |

The TrueDepth scan runs outside the ARKit world-tracking session (an `AVCaptureSession` on the front camera), so its frames have no world pose; it measures the meter directly. Pause ARKit, scan, then resume; a new epoch after resuming is fine. Upload its files with `role: truedepth` and the `trueDepthFrame` record as `meta`, with the meter stills (Appendix D upload order). When the scan answers a `truedepth_meter_scan` retake request, send it as an amendment packet (`parentPacketId` set) that also carries a fresh head-on `meter_close` still.

## 4. Binary layouts

| File | Layout |
| --- | --- |
| `*.depth.f32` | `ARDepthData.depthMap`: Float32 little-endian meters, row-major, `w × h` (256 × 192 observed on current phones; Apple documents only "low-resolution", so write the actual `CVPixelBufferGetWidth/Height` and check `CVPixelBufferGetPixelFormatType` is `DepthFloat32`), no row padding (copy row by row, as PR #7 `KeyframeRecorder.rows` does) |
| `*.confidence.u8` | `ARDepthData.confidenceMap`: UInt8 [`ARConfidenceLevel`](https://developer.apple.com/documentation/arkit/arconfidencelevel) values written explicitly as 0 low, 1 medium, 2 high; same layout. Apple does not document the raw values (0/1/2 comes from PR #7 and header mirrors), so map the enum cases in code rather than casting `rawValue` |
| `*.features.bin` | `ARFrame.rawFeaturePoints`: `count` packed records `{float32 x, float32 y, float32 z, uint64 identifier}`, 20 bytes each, little-endian; world meters of the keyframe's epoch ([`ARPointCloud`](https://developer.apple.com/documentation/arkit/arpointcloud) `points` and `identifiers`) |
| `truedepth/t00001.depth.f32` (optional `.jpg`) | TrueDepth `AVDepthData` converted to `kCVPixelFormatType_DepthFloat32`: Float32 little-endian meters, row-major `w × h`, no row padding |
| `geometry/mesh.ply` | binary little-endian PLY; vertices in world meters; optional per-face `uchar classification` ([`ARMeshClassification`](https://developer.apple.com/documentation/arkit/armeshclassification)) |

## 5. Capture and transport rules

1. **Never rotate or re-encode keyframes** after saving. Rotation belongs in `orientation` (stills) or is implied (keyframes are always sensor layout).
2. **One pose per image.** Take pose, intrinsics, timestamp and tracking from the same `ARFrame` whose pixels you saved (PR #7, PR #18). Don't pair an image with a later frame's pose.
3. **Keyframe cadence.** Keep PR #10's rule (every 0.5 m or 15°, plus every tap and freeze). Add a frame at least every 0.5 s while walking (section 5.1), up to about 150 frames per wall segment.
4. **Keep old epochs.** After a reset, keep the earlier keyframes with their epoch id rather than discarding them.
5. **Upload.** Use the Upload API in Appendix D: register files in batches during capture, PUT each to its signed URL (background `URLSession`), commit each, and finalize with `packet.json` as the body (finalize may come before the last commits). The server checks every `bytes` and `sha256`; a commit is the per-file acknowledgement. Don't delete local files until the packet is acknowledged; note that PR #10 currently deletes the previous scan when a new one starts. `scene.json` stays a separate small request (the placement endpoint is capped at 4.5 MB).
6. **Size budget.** About 0.5–0.9 MB per 1920 × 1440 keyframe, 245 KB of depth and confidence per LiDAR keyframe, and about 3 MB of motion CSV per 4 minutes before gzip (streams are sent as `.csv.gz`). 60 keyframes come to roughly 45 MB.
7. **Privacy.** A packet holds house photos and precise location. Ask for consent before capturing and treat the packet as personal data.

### 5.1 What the capture must look like for reconstruction to work

We ran image-only reconstruction (COLMAP SfM) on the test houses. Overlap decided the outcome more than resolution:

| Footage | Frames registered |
| --- | --- |
| 1080p rig walkaround of a barn, steady | 137 / 137 (geometry within 1.2 cm of laser truth) |
| 480p phone walk along one wall at 3 frames/s, no pans | 135 / 141 |
| 1080p steady walk at 2.5 frames/s, one sky tilt | 153 / 183 |
| 360–720p walks at 1.2–1.7 frames/s with pans to sky, trees and lawn, fast motion or blur | 47 / 151, 58 / 148, 77 / 150, each split into several disconnected pieces |
| fixed security-camera time-lapse | none (no baseline); sent as photo-only |

ARKit tracks through these gaps with its IMU, but the server's 3D asset is built from the pixels, so consecutive keyframes must overlap. What the app should enforce:

1. **Dense, overlapping keyframes:** at least 2 per second while walking, each sharing about 60% of its view with the previous one. The walks at 1.2–1.7 frames/s fragmented; the ones at 2.5–3 frames/s did not.
2. **Keep the wall in frame.** No pans to the sky, trees or ground between wall segments. Prompt the user when a frame shows mostly sky or grass. Full-resolution `capturedImage` (1920 × 1440) helps on low-texture siding and stucco, but can't make up for pans.
3. **Walk slowly and parallel to the wall**, 2–4 m away, phone upright at chest height, turning corners in two or three steps rather than one swing. This tightens rule 3 of section 5 to every 0.5 m or 15° and at least two frames per second, still capped at about 150 frames per wall segment.
4. **Reject blurry frames at capture.** Use `limited.excessiveMotion` and the exposure duration (drop frames above about 1/60 s while moving), and retake instead of saving them.
5. **Close the loop when the yard allows it:** walk past the first corner again at the end. On the full-loop walkaround, the start and end joined even though the middle did not register.
6. **Always send ARKit poses, even when tracking is `limited`.** They are the only way to join pieces that the images alone can't connect, and together with LiDAR depth or the meter close-up (section 3.4) they give metric scale. Image-only scale from an assumed 1.40 m camera height was 17% short on the barn (its camera was at 1.68 m).

### 5.2 Zero-effort scale inputs (v0.4, from A9)

No tape, no measuring, no extra tools. What the app does, at the cost of two meter photos and, rarely, a 2-second flip of the phone:

1. **Two meter close-ups, inside the running ARKit session.** Walk up from the wall view. Head-on from about 3 ft, then about 30° to one side at the same distance; whole cover, ring and socket face filling about half the frame, nameplate legible. Use the vertical plane for "face the wall" and "step to the side" hints and the raycast distance for "closer" / "farther". Fill `meterCloseUp` and `ext.obliqueCloseUp`.
2. **Nameplate (optional).** If on-device text recognition reads a make and model, put them in `meterIdentity`; never ask the homeowner to type them.
3. **Passive logging, no user effort.** ARKit poses and per-frame intrinsics (`arkitPoses`), gravity, detected planes, and raw 100 Hz accelerometer and gyro (`imuRaw`), from session start to end, including the walk-up to the meter.
4. **Doors on the meter side.** Make sure the walk includes any entry or garage door on the meter side head-on at least once (a hint if the walk misses it). Record the frame in `ext.doorViews`.
5. **Service address.** Copy it from the account or order into `ext.serviceAddress`; the server does the footprint lookup.
6. **TrueDepth scan (optional).** "Turn the phone around and hold it about a foot from the meter for 2 seconds." Prompt only in shade, or when the server's retake request asks for it (`truedepth_meter_scan`, sent when scale comes back `unverified`); answer that request with an amendment (Appendix D step 5). A `retake_meter_closeups` request is answered the same way, with both close-ups. Record failures (bright sun, no valid depth, declined) in `ext.trueDepthScan.failure`.

## 6. What the server validates on arrival

- Schema (Appendix A), including the tier rules: ARKit tiers need keyframes, epochs, tracking, `worldAlignment` and `videoFormat`; `photo_only` needs stills.
- Every listed file exists with the stated size and SHA-256. Unlisted files are ignored.
- Poses are finite, rotations orthonormal with determinant +1, and intrinsics positive and plausible for `w × h` (principal point inside the image).
- Every keyframe's epoch exists (error). Timestamps should increase within an epoch: array order out of time order is a warning (`keyframe_order`; an app that appends close-up frames after the walk is legitimate), and only two keyframes of one epoch with the same timestamp and poses more than 1 cm apart are an error (`keyframe_timestamp_conflict`).
- **Tap replay** (PR #9): rebuilding each tap's ray from `pixel`, `pose` and `intrinsics` must reproduce `rayOrigin` and `rayDirection` to within 1e-3. This is the one check that the conventions were followed; please keep taps in every packet you test with.
- Depth files are exactly `w × h × 4` bytes and confidence files `w × h` bytes.
- **Scale inputs (v0.4).** In ARKit tiers the schema requires `scaleReference` with `ext.method`, `ext.obliqueCloseUp`, `ext.doorViews`, and the `arkitPoses` and `imuRaw` streams. A captured close-up must name an existing still (error); warnings for a wrong purpose, a close-up with no keyframe link (not taken inside ARKit), a distance outside 0.3–3 m, a head-on view more than 30° off, or an oblique view outside 15–50°. Door views must reference existing frames with boxes inside the image. TrueDepth frames must be listed in `files` with `w × h × 4` bytes; relative-accuracy depth is flagged.

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
16. **TrueDepth scan and the ARKit session (v0.4).** Can the app pause world tracking, run the front TrueDepth camera for about 2 s, and resume with relocalization? Default: pause, scan, resume; a new epoch after resuming is acceptable, and the scan needs no world pose.
17. **Raw IMU alongside device motion (v0.4).** Is 100 Hz `CMAccelerometerData` + `CMGyroData` on top of 100 Hz `CMDeviceMotion` and ARKit fine for battery and heat over a 5-minute capture? Default: yes; drop `motion` to 50 Hz before dropping `imuRaw`.

## Appendix A: JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/huntertcarver/house-scanning-server/schemas/capture-packet-0.4.schema.json",
  "title": "House capture packet v0.4 (packet.json)",
  "description": "Everything one capture session produced on the phone: photos plus ARKit, Core Motion and Core Location metadata. Raw sensor units (meters, seconds of device uptime, ARKit world). Field names and conventions of keyframes, taps and tracking follow measure-lab-session v2 (PR #7). Unknown fields are rejected so typos fail loudly; put experiments under `ext`.",
  "type": "object",
  "additionalProperties": false,
  "required": ["format", "formatVersion", "packetId", "createdAt", "units", "conventions", "source", "session", "keyframes", "files"],
  "properties": {
    "format": {"const": "house-capture-packet"},
    "formatVersion": {"type": "string", "pattern": "^0\\.[0-9]+$", "description": "0.x: breaking changes allowed between minor versions until 1.0."},
    "packetId": {"type": "string", "minLength": 8, "description": "UUID, unique per upload attempt of this session."},
    "parentPacketId": {
      "type": "string",
      "minLength": 8,
      "description": "Set on an amendment (a retake uploaded under amendments/{aid}/, Appendix D): the packetId of the packet it answers. An amendment is an ordinary packet with its own epochs."
    },
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
        "kind": {
          "enum": ["device", "replay", "synthetic"],
          "description": "device: a real phone session. replay: re-exported from a recording. synthetic: made by the packet generator; never evidence."
        },
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
        "startedAtUptime": {
          "type": "number",
          "description": "ProcessInfo.systemUptime at startedAt; lines wall-clock times (CLLocation, CLHeading) up with uptime."
        },
        "endedAtUptime": {"type": "number"},
        "timeZone": {"type": "string", "description": "IANA name, e.g. America/Chicago."},
        "appVersion": {"type": "string"},
        "deviceModel": {"type": "string", "description": "Hardware identifier from utsname.machine, e.g. iPhone16,1."},
        "systemVersion": {"type": "string"},
        "tier": {
          "enum": ["arkit_lidar", "arkit", "photo_only"],
          "description": "arkit_lidar: ARKit world tracking with LiDAR scene depth. arkit: ARKit world tracking, no depth. photo_only: no ARKit poses (fallback camera, uploaded photos)."
        },
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
        "properties": {"time": {"type": "number"}, "state": {"$ref": "#/$defs/trackingState"}, "epoch": {"type": "string"}}
      }
    },
    "planes": {"type": "array", "maxItems": 500, "items": {"$ref": "#/$defs/plane"}},
    "mesh": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "epoch"],
      "properties": {
        "file": {
          "type": "string",
          "description": "PLY of all ARMeshAnchor geometry, vertices in world meters of `epoch`; per-face `classification` property when meshWithClassification."
        },
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
        "altimeter": {"$ref": "#/$defs/csvStream"},
        "imuRaw": {"$ref": "#/$defs/imuRawStream"},
        "arkitPoses": {"$ref": "#/$defs/arkitPoseStream"}
      }
    },
    "scaleReference": {"$ref": "#/$defs/scaleReference"},
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
          "role": {
            "enum": ["keyframe", "depth", "confidence", "features", "still", "stream", "truedepth", "mesh", "compat", "other"],
            "description": "The same values are the Upload API's per-file role (Appendix D)."
          }
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
        "properties": {"keyframes": {"minItems": 1}, "epochs": {"minItems": 1}, "session": {"required": ["worldAlignment", "videoFormat"]}}
      }
    },
    {
      "if": {"properties": {"session": {"properties": {"tier": {"const": "photo_only"}}}}},
      "then": {"required": ["stills"], "properties": {"stills": {"minItems": 1}, "keyframes": {"maxItems": 0}}}
    },
    {
      "if": {"properties": {"session": {"properties": {"tier": {"const": "arkit_lidar"}}}}},
      "then": {"properties": {"session": {"properties": {"lidarAvailable": {"const": true}}}}}
    },
    {
      "if": {"properties": {"session": {"properties": {"tier": {"enum": ["arkit", "arkit_lidar"]}}}}},
      "then": {
        "required": ["scaleReference", "streams"],
        "properties": {
          "streams": {"required": ["imuRaw", "arkitPoses"]},
          "scaleReference": {"required": ["meterCloseUp", "ext"], "properties": {"ext": {"required": ["method", "obliqueCloseUp", "doorViews"]}}}
        }
      }
    }
  ],
  "$defs": {
    "isoTime": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"},
    "relPath": {"type": "string", "minLength": 1, "pattern": "^(?!/)(?!.*\\.\\./).+$"},
    "size": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer", "minimum": 1}, "description": "[width, height] in pixels."},
    "vec3": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
    "mat4": {
      "type": "array",
      "minItems": 16,
      "maxItems": 16,
      "items": {"type": "number"},
      "description": "4x4, 16 numbers column by column (simd_float4x4 layout)."
    },
    "intrinsics": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number"}, "description": "[fx, fy, cx, cy] in pixels of the saved image."},
    "trackingState": {
      "enum": ["normal", "notAvailable", "limited.initializing", "limited.excessiveMotion", "limited.insufficientFeatures", "limited.relocalizing"]
    },
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
        "intrinsics": {
          "$ref": "#/$defs/intrinsics",
          "description": "ARCamera.intrinsics for this image (rescale if the saved image differs from ARCamera.imageResolution)."
        },
        "pose": {"$ref": "#/$defs/mat4", "description": "ARCamera.transform: camera-to-world, meters."},
        "timestamp": {"type": "number", "description": "ARFrame.timestamp (device uptime seconds)."},
        "tracking": {"$ref": "#/$defs/trackingState"},
        "epoch": {"type": "string"},
        "reason": {
          "enum": ["motion", "tap", "freeze", "still", "request", "auto"],
          "description": "Why it was saved: motion spacing, a tap, a frozen frame, a checklist still, a server/guidance request, or an automatic quality trigger."
        },
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
                "confidenceFile": {
                  "oneOf": [{"type": "null"}, {"$ref": "#/$defs/relPath"}],
                  "description": "ARDepthData.confidenceMap: UInt8 ARConfidenceLevel (0 low, 1 medium, 2 high), same layout."
                },
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
                "file": {
                  "$ref": "#/$defs/relPath",
                  "description": "ARFrame.rawFeaturePoints: packed little-endian records {float32 x, y, z (world meters); uint64 identifier}, 20 bytes each."
                },
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
              "items": {
                "type": "object",
                "additionalProperties": false,
                "required": ["requirement", "revision"],
                "properties": {"requirement": {"type": "string"}, "revision": {"type": "integer", "minimum": 1}}
              }
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
        "purpose": {
          "type": "string",
          "description": "meter_close, meter_oblique (second close-up about 30 degrees to the side), meter_surround, meter_left, meter_right, adjacent_wall, behind_fence, panel_close, panel_open, main_disconnect, panel_surround, house_overview, other. Unknown values are treated as other."
        },
        "img": {"$ref": "#/$defs/relPath"},
        "w": {"type": "integer", "minimum": 1},
        "h": {"type": "integer", "minimum": 1},
        "timestamp": {"type": "number", "description": "Device uptime seconds of the exposure."},
        "orientation": {
          "type": "integer",
          "minimum": 1,
          "maximum": 8,
          "description": "EXIF orientation of the stored pixels (1 = stored upright; 6 = rotate 90 degrees clockwise to display, the usual portrait case for unrotated sensor images)."
        },
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
        "label": {
          "type": "string",
          "description": "meter, wall_end_left, wall_end_right, corner, wall_contact, gas_meter, ac_unit, door_corner, window_corner, fence, driveway_edge, reference_point, other."
        },
        "keyframe": {"type": "string", "description": "The keyframe the pixel is in (saved for the tap if needed)."},
        "pixel": {
          "type": "array",
          "minItems": 2,
          "maxItems": 2,
          "items": {"type": "number"},
          "description": "[u, v] continuous pixels of that keyframe's JPEG."
        },
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
        "transform": {
          "$ref": "#/$defs/mat4",
          "description": "ARAnchor.transform (anchor-to-world). The plane lies in the anchor's x-z plane; anchor +y is the normal."
        },
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
        "boundary": {
          "type": "array",
          "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
          "description": "ARPlaneGeometry.boundaryVertices as [x, z] in anchor coordinates."
        },
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
        "file": {"$ref": "#/$defs/relPath", "description": "gzip-compressed CSV, streams/<name>.csv.gz (Appendix D)."},
        "columns": {"type": "array", "items": {"type": "string"}},
        "rateHz": {"type": "number"}
      }
    },
    "motionStream": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "columns", "rateHz", "referenceFrame"],
      "properties": {
        "file": {"$ref": "#/$defs/relPath", "description": "gzip-compressed CSV, streams/<name>.csv.gz (Appendix D)."},
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
        "file": {"$ref": "#/$defs/relPath", "description": "gzip-compressed CSV, streams/<name>.csv.gz (Appendix D)."},
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
        "file": {"$ref": "#/$defs/relPath", "description": "gzip-compressed CSV, streams/<name>.csv.gz (Appendix D)."},
        "columns": {"type": "array", "items": {"type": "string"}},
        "desiredAccuracy": {"enum": ["bestForNavigation", "best", "nearestTenMeters", "hundredMeters", "kilometer", "threeKilometers", "reduced"]}
      }
    },
    "scaleReference": {
      "type": "object",
      "additionalProperties": false,
      "description": "Zero-effort scale inputs. Nothing here asks the homeowner to measure or use a tool. v0.4 follows the A9 study: the meter read as an exact make and model is the primary scale source (two close-ups), ARKit is the gate, and doors and the building footprint are checks only. Required in ARKit tiers.",
      "properties": {
        "meterCloseUp": {
          "type": "object",
          "additionalProperties": false,
          "required": ["captured"],
          "properties": {
            "captured": {
              "type": "boolean",
              "description": "Head-on meter close-up from about 3 ft (1 m) inside the running ARKit session: whole cover, ring and socket face filling about half the frame, nameplate legible."
            },
            "still": {"type": ["string", "null"], "description": "Id of that still (purpose meter_close)."},
            "arkitDistanceM": {
              "type": ["number", "null"],
              "exclusiveMinimum": 0,
              "description": "Camera-to-meter distance at capture from an ARKit raycast (or LiDAR depth) at the meter's centre. Null without a hit."
            },
            "viewAngleDeg": {
              "type": ["number", "null"],
              "minimum": 0,
              "maximum": 90,
              "description": "Angle between the camera's viewing axis and the wall normal (0 = head-on), from a detected vertical plane. Null without one."
            },
            "nameplateVisible": {
              "type": ["boolean", "null"],
              "description": "The app found legible text on the meter face (e.g. Vision text recognition). Null if not checked."
            }
          },
          "if": {"properties": {"captured": {"const": true}}},
          "then": {"required": ["still"], "properties": {"still": {"type": "string"}}}
        },
        "meterIdentity": {
          "type": "object",
          "additionalProperties": false,
          "required": ["source"],
          "description": "Meter make and model if read on the phone, so the server can use datasheet dimensions (e.g. base flange). The server also reads them from the close-up.",
          "properties": {
            "make": {"type": ["string", "null"]},
            "model": {"type": ["string", "null"]},
            "rawText": {"type": ["string", "null"], "description": "Text recognized on the nameplate, unedited."},
            "source": {"enum": ["on_device_text", "not_read"]}
          }
        },
        "ext": {
          "type": "object",
          "description": "Scale inputs specified by the A9 no-tool study (internal/breadth/A9-no-tape-scale.md). Unknown keys are allowed and ignored.",
          "properties": {
            "method": {"type": "string", "description": "Scale method these inputs were captured for; v0.4 apps send \"a9-meter-model\"."},
            "obliqueCloseUp": {
              "type": "object",
              "additionalProperties": false,
              "required": ["captured"],
              "properties": {
                "captured": {
                  "type": "boolean",
                  "description": "Second meter close-up from about 30 degrees to the side at the same distance, inside the ARKit session."
                },
                "still": {"type": ["string", "null"], "description": "Id of that still (purpose meter_oblique)."},
                "arkitDistanceM": {
                  "type": ["number", "null"],
                  "exclusiveMinimum": 0,
                  "description": "Camera-to-meter distance at capture from an ARKit raycast (or LiDAR depth) at the meter's centre. Null without a hit."
                },
                "viewAngleDeg": {
                  "type": ["number", "null"],
                  "minimum": 0,
                  "maximum": 90,
                  "description": "Angle between the camera's viewing axis and the wall normal (0 = head-on), from a detected vertical plane. Null without one."
                },
                "nameplateVisible": {
                  "type": ["boolean", "null"],
                  "description": "The app found legible text on the meter face (e.g. Vision text recognition). Null if not checked."
                },
                "side": {"enum": ["left", "right", null], "description": "Which side of the meter the phone was on, facing the wall."}
              },
              "if": {"properties": {"captured": {"const": true}}},
              "then": {"required": ["still"], "properties": {"still": {"type": "string"}}}
            },
            "doorViews": {
              "type": "array",
              "maxItems": 10,
              "items": {"$ref": "#/$defs/doorView"},
              "description": "Head-on views of doors on the meter side (entry or garage), the check references."
            },
            "doorOnMeterSide": {
              "type": ["boolean", "null"],
              "description": "False when the meter side has no door, so an empty doorViews is expected. Null if unknown."
            },
            "serviceAddress": {
              "type": "object",
              "additionalProperties": false,
              "required": ["line1", "city", "state", "postalCode", "source"],
              "description": "For the server's building-footprint lookup (a scale check). Taken from the account, never typed at capture.",
              "properties": {
                "line1": {"type": "string"},
                "line2": {"type": ["string", "null"]},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "postalCode": {"type": "string"},
                "country": {"type": "string", "default": "US"},
                "source": {"enum": ["account", "order"]}
              }
            },
            "trueDepthScan": {
              "type": "object",
              "additionalProperties": false,
              "required": ["captured"],
              "description": "Optional front TrueDepth scan of the meter at 30-45 cm (about 2 s), prompted only in shade or when scale came back unverified.",
              "properties": {
                "captured": {"type": "boolean"},
                "prompt": {"enum": ["shade", "scale_unverified", "always"]},
                "failure": {"enum": ["too_bright", "no_valid_depth", "declined", "unsupported_device", null]},
                "depthDataAccuracy": {"enum": ["absolute", "relative"], "description": "AVDepthData.depthDataAccuracy; relative depth is not usable for scale."},
                "depthDataQuality": {"enum": ["high", "low"]},
                "frames": {"type": "array", "maxItems": 120, "items": {"$ref": "#/$defs/trueDepthFrame"}}
              },
              "if": {"properties": {"captured": {"const": true}}},
              "then": {"required": ["frames", "depthDataAccuracy"], "properties": {"frames": {"minItems": 1}}}
            }
          }
        }
      }
    },
    "imuRawStream": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "columns", "rateHz"],
      "description": "Raw CoreMotion IMU for offline visual-inertial bundle adjustment: CMAccelerometerData (g, device frame, gravity included) and CMGyroData (rad/s, not bias-corrected), paired by nearest time.",
      "properties": {
        "file": {"$ref": "#/$defs/relPath", "description": "gzip-compressed CSV, streams/<name>.csv.gz (Appendix D)."},
        "columns": {"type": "array", "items": {"type": "string"}, "description": "t, ax, ay, az, gx, gy, gz"},
        "rateHz": {"type": "number", "minimum": 50, "description": "100 requested."}
      }
    },
    "arkitPoseStream": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file", "columns", "rateHz"],
      "description": "Every ARFrame (or every other one) from session(_:didUpdate:): pose, intrinsics and tracking, not just keyframes.",
      "properties": {
        "file": {"$ref": "#/$defs/relPath", "description": "gzip-compressed CSV, streams/<name>.csv.gz (Appendix D)."},
        "columns": {"type": "array", "items": {"type": "string"}, "description": "t, epoch, tracking, tx, ty, tz, qx, qy, qz, qw, fx, fy, cx, cy"},
        "rateHz": {"type": "number", "minimum": 10, "description": "30 or 60."}
      }
    },
    "doorView": {
      "type": "object",
      "additionalProperties": false,
      "required": ["frame", "kind"],
      "properties": {
        "frame": {"type": "string", "description": "Keyframe (or still) id with the door seen head-on."},
        "kind": {"enum": ["entry_door", "garage_door", "other_door"]},
        "box": {
          "type": "array",
          "minItems": 4,
          "maxItems": 4,
          "items": {"type": "number"},
          "description": "[u0, v0, u1, v1] pixels, if the app detected the door."
        },
        "viewAngleDeg": {"type": ["number", "null"], "minimum": 0, "maximum": 90}
      }
    },
    "trueDepthFrame": {
      "type": "object",
      "additionalProperties": false,
      "required": ["depth", "w", "h", "intrinsics", "timestamp"],
      "properties": {
        "depth": {"$ref": "#/$defs/relPath", "description": "Float32 little-endian meters, row-major w x h (AVDepthData converted to DepthFloat32)."},
        "image": {"$ref": "#/$defs/relPath", "description": "Optional front-camera JPEG of the same instant."},
        "w": {"type": "integer", "minimum": 1},
        "h": {"type": "integer", "minimum": 1},
        "intrinsics": {
          "$ref": "#/$defs/intrinsics",
          "description": "AVCameraCalibrationData.intrinsicMatrix scaled from intrinsicMatrixReferenceDimensions to w x h."
        },
        "timestamp": {"type": "number"}
      }
    }
  }
}
```

## Appendix B: example packets

**ARKit tier, non-LiDAR iPhone 15** (four keyframes, one meter tap, one wall plane; the v0.4 scale inputs: head-on close-up `s1` from 0.94 m and oblique close-up `s2` from 0.97 m at 31°, both with a legible Itron CENTRON nameplate, an entry door seen head-on in `k00001`, the service address, the TrueDepth scan declined, and the `arkitPoses` and `imuRaw` streams). The hashes are illustrative.

```json
{
  "format": "house-capture-packet",
  "formatVersion": "0.4",
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
  "source": {"kind": "device", "appBuild": "HouseScan 0.4 (57)"},
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
    "planeDetection": ["horizontal", "vertical"],
    "videoFormat": {"imageResolution": [1920, 1440], "framesPerSecond": 60},
    "locationAuthorization": "fullAccuracy",
    "motionReferenceFrame": "xTrueNorthZVertical",
    "thermalStateMax": "fair",
    "gates": {"keyframeSpacingMeters": 0.5, "keyframeSpacingDegrees": 15.0, "trackingStableSeconds": 1.0}
  },
  "epochs": [{"id": "e1", "startTime": 84205.13200000001, "reason": "sessionStart"}],
  "keyframes": [
    {
      "id": "k00001",
      "img": "keyframes/k00001.jpg",
      "w": 1920,
      "h": 1440,
      "intrinsics": [1442.1, 1442.1, 959.6, 719.4],
      "pose": [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0.02, -0.01, -0.05, 1],
      "timestamp": 84211.532,
      "tracking": "normal",
      "epoch": "e1",
      "reason": "motion",
      "exposure": {"duration": 0.00098, "offset": 0.0, "iso": 50, "fNumber": 1.6},
      "exif": {
        "Make": "Apple",
        "Model": "iPhone 15",
        "LensModel": "iPhone 15 back dual wide camera 6.24mm f/1.6",
        "ExposureTime": 0.00098,
        "ISOSpeedRatings": [50],
        "FNumber": 1.6,
        "FocalLength": 6.24,
        "FocalLenIn35mmFilm": 26,
        "BrightnessValue": 9.21
      },
      "lightEstimate": {"ambientIntensity": 1380.0, "ambientColorTemperature": 5900.0},
      "depth": null,
      "features": {"file": "keyframes/k00001.features.bin", "count": 212}
    },
    {
      "id": "k00002",
      "img": "keyframes/k00002.jpg",
      "w": 1920,
      "h": 1440,
      "intrinsics": [1442.1, 1442.1, 959.6, 719.4],
      "pose": [0, -1, 0, 0, 0.9659, 0, 0.2588, 0, -0.2588, 0, 0.9659, 0, 0.35, -0.08, -1.25, 1],
      "timestamp": 84213.402,
      "tracking": "normal",
      "epoch": "e1",
      "reason": "tap",
      "exposure": {"duration": 0.00112, "offset": 0.0, "iso": 50, "fNumber": 1.6},
      "depth": null,
      "features": {"file": "keyframes/k00002.features.bin", "count": 187}
    },
    {
      "id": "k00003",
      "img": "keyframes/k00003.jpg",
      "w": 4032,
      "h": 3024,
      "intrinsics": [3028.4, 3028.4, 2015.2, 1510.7],
      "pose": [0, -1, 0, 0, 0.9659, 0, 0.2588, 0, -0.2588, 0, 0.9659, 0, 0.85, -0.15, -2.75, 1],
      "timestamp": 84220.952,
      "tracking": "normal",
      "epoch": "e1",
      "reason": "still",
      "highResolution": true,
      "purpose": "meter_close",
      "exposure": {"duration": 0.0021, "offset": 0.0, "iso": 64, "fNumber": 1.6},
      "depth": null,
      "features": null
    },
    {
      "id": "k00004",
      "img": "keyframes/k00004.jpg",
      "w": 4032,
      "h": 3024,
      "intrinsics": [3028.4, 3028.4, 2015.2, 1510.7],
      "pose": [0.0, -1.0, 0.0, 0.0, 0.707094, 0.0, 0.707077, 0.0, -0.707077, 0.0, 0.707094, 0.0, 0.617332, -0.15, -2.883671, 1.0],
      "timestamp": 84225.552,
      "tracking": "normal",
      "epoch": "e1",
      "reason": "still",
      "highResolution": true,
      "exposure": {"duration": 0.0021, "offset": 0.0, "iso": 64, "fNumber": 1.6},
      "depth": null,
      "purpose": "meter_oblique"
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
      "gravity": [0.012, -0.989, -0.146]
    },
    {
      "id": "s2",
      "purpose": "meter_oblique",
      "img": "keyframes/k00004.jpg",
      "w": 4032,
      "h": 3024,
      "timestamp": 84225.552,
      "orientation": 6,
      "keyframe": "k00004",
      "intrinsicsSource": "arkit",
      "gravity": [0.018, -0.991, -0.131]
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
      "pixel": [1010.0, 655.0],
      "rayOrigin": [0.35, -0.08, -1.25],
      "rayDirection": [0.30147, -0.0349, -0.95284],
      "hit": {
        "position": [0.9831, -0.1533, -3.251],
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
  "tracking": [{"time": 84205.13200000001, "state": "limited.initializing", "epoch": "e1"}, {"time": 84206.432, "state": "normal", "epoch": "e1"}],
  "planes": [
    {
      "id": "8D3E6B1C-2F4A-4C7E-A1B9-6E0D5C2F8A77",
      "epoch": "e1",
      "alignment": "vertical",
      "classification": "wall",
      "transform": [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0.8, -0.6, -3.25, 1],
      "center": [0, 0, 0],
      "planeExtent": {"width": 4.2, "height": 2.6, "rotationOnYAxis": 0.0},
      "boundary": [[-2.1, -1.3], [2.1, -1.3], [2.1, 1.3], [-2.1, 1.3]],
      "firstSeen": 84212.432,
      "lastUpdated": 84449.732
    }
  ],
  "streams": {
    "motion": {
      "file": "streams/motion.csv.gz",
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
      "file": "streams/heading.csv.gz",
      "headingOrientation": "portrait",
      "headingFilter": 1.0,
      "columns": ["t", "timestamp", "magneticHeading", "trueHeading", "headingAccuracy", "x", "y", "z"]
    },
    "location": {
      "file": "streams/location.csv.gz",
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
    },
    "imuRaw": {"file": "streams/imu_raw.csv.gz", "rateHz": 100, "columns": ["t", "ax", "ay", "az", "gx", "gy", "gz"]},
    "arkitPoses": {
      "file": "streams/arkit_poses.csv.gz",
      "rateHz": 30,
      "columns": ["t", "epoch", "tracking", "tx", "ty", "tz", "qx", "qy", "qz", "qw", "fx", "fy", "cx", "cy"]
    }
  },
  "scaleReference": {
    "meterCloseUp": {"captured": true, "still": "s1", "arkitDistanceM": 0.94, "viewAngleDeg": 6.5, "nameplateVisible": true},
    "meterIdentity": {"make": "Itron", "model": "CENTRON", "rawText": "Itron CENTRON CL200 240V 3W FM2S TA30", "source": "on_device_text"},
    "ext": {
      "method": "a9-meter-model",
      "obliqueCloseUp": {"captured": true, "still": "s2", "arkitDistanceM": 0.97, "viewAngleDeg": 31.0, "nameplateVisible": true, "side": "left"},
      "doorViews": [{"frame": "k00001", "kind": "entry_door", "box": [1212.0, 402.0, 1498.0, 1105.0], "viewAngleDeg": 8.0}],
      "doorOnMeterSide": true,
      "serviceAddress": {
        "line1": "1234 Example Ln",
        "line2": null,
        "city": "Austin",
        "state": "TX",
        "postalCode": "78701",
        "country": "US",
        "source": "account"
      },
      "trueDepthScan": {"captured": false, "prompt": "shade", "failure": "declined"}
    }
  },
  "files": [
    {"path": "keyframes/k00001.features.bin", "bytes": 4240, "sha256": "52820c619e1706e71184d3f855560fbdbd46b3056cb01e5891e7889c01d09afd", "role": "features"},
    {"path": "keyframes/k00001.jpg", "bytes": 612344, "sha256": "1fe257e3be8488b30225faff5a8a7a05b6cf6efd2f4bf8990b34e610add6569d", "role": "keyframe"},
    {"path": "keyframes/k00002.features.bin", "bytes": 3740, "sha256": "bdd244fdccdcbae63af33176a6e26c02955d288085afec04888bc840424c15bf", "role": "features"},
    {"path": "keyframes/k00002.jpg", "bytes": 598102, "sha256": "16af222466a6c7661fd56041bc58e6a180a977d231b95f7e6db327ba1c0858e5", "role": "keyframe"},
    {"path": "keyframes/k00003.jpg", "bytes": 2811904, "sha256": "b41e1827243f6c809b59fa6abb8ad208bfc096726e3f1db0c5eca38090e0ec75", "role": "keyframe"},
    {"path": "keyframes/k00004.jpg", "bytes": 2417731, "sha256": "3f9c2a7be41d0c85e6a1b9d04f27c3e8a5d6b1f0e9c8d7a6b5c4d3e2f1a0b9c8", "role": "keyframe"},
    {"path": "streams/arkit_poses.csv.gz", "bytes": 309828, "sha256": "7e2b9c41d05f3a86e1c7b4d9a2f0e3c6b5a8d7f4e1c0b9a8f7e6d5c4b3a29180", "role": "stream"},
    {"path": "streams/heading.csv.gz", "bytes": 17147, "sha256": "54de152405d41d6be249f915d91aed4adc10701a18bf18033ddbf542ddc1ec21", "role": "stream"},
    {"path": "streams/imu_raw.csv.gz", "bytes": 451474, "sha256": "a41d6e0c9b2f7385d1e4c6a9b0f3e2d5c8b7a6f5e4d3c2b1a09f8e7d6c5b4a39", "role": "stream"},
    {"path": "streams/location.csv.gz", "bytes": 8366, "sha256": "6aaa7b6e1e979716a28efbe06ed602ef78640cf41984cf419df34925a8fff07f", "role": "stream"},
    {"path": "streams/motion.csv.gz", "bytes": 892421, "sha256": "15be3d3211669e6ce8b3e482f790211301270d65f36128b3fa0b386cbf6aea5c", "role": "stream"}
  ]
}
```

The first keyframe shows the upright-portrait case: camera x points down the world (−y), camera y points right (+x), and the camera looks along world −z.

**Photo-only tier** (one panel photo from a phone without an AR session; no `scaleReference`, which is optional):

```json
{
  "format": "house-capture-packet",
  "formatVersion": "0.4",
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
  "source": {"kind": "device"},
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
      "intrinsics": [2970.6, 2970.6, 2016.0, 1512.0],
      "intrinsicsSource": "exif_focal35",
      "gravity": [0.03, -0.97, -0.24],
      "location": {"latitude": 30.26705, "longitude": -97.74322, "horizontalAccuracy": 9.4},
      "exif": {
        "Make": "Apple",
        "Model": "iPhone 13",
        "LensModel": "iPhone 13 back dual wide camera 5.1mm f/1.6",
        "ExposureTime": 0.0083,
        "ISOSpeedRatings": [320],
        "FNumber": 1.6,
        "FocalLength": 5.1,
        "FocalLenIn35mmFilm": 26,
        "DateTimeOriginal": "2026:09:27 11:19:10",
        "OffsetTimeOriginal": "-05:00",
        "Orientation": 6
      }
    }
  ],
  "files": [{"path": "stills/s1.jpg", "bytes": 3120448, "sha256": "fbffed3a377eb14f906eaf3469f178cedca03eb6ff1e98e9c02fab6f8f5c13d7", "role": "still"}]
}
```

**Stream rows** (first rows of each CSV after gunzip):

```
# streams/motion.csv.gz
t,qx,qy,qz,qw,rotX,rotY,rotZ,gravX,gravY,gravZ,accX,accY,accZ,magX,magY,magZ,magAccuracy,heading
84205.132,0.4821,-0.5163,-0.4917,0.5082,0.0132,-0.0071,0.0045,0.0021,-0.9994,0.0339,0.0104,-0.0088,0.0017,-11.82,-35.41,-22.07,2,196.4
# streams/heading.csv.gz
t,timestamp,magneticHeading,trueHeading,headingAccuracy,x,y,z
84205.140,2026-09-27T15:58:39.140Z,194.1,196.5,17.0,-11.8,-35.4,-22.1
# streams/arkit_poses.csv.gz
t,epoch,tracking,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy
84211.532,e1,normal,0.020000,-0.010000,-0.050000,0.000000,0.000000,0.707107,0.707107,1442.100000,1442.100000,959.600000,719.400000
# streams/imu_raw.csv.gz
t,ax,ay,az,gx,gy,gz
84205.132,0.0142,-1.0061,0.0381,0.0183,-0.0019,0.0097
# streams/location.csv.gz
t,timestamp,latitude,longitude,altitude,ellipsoidalAltitude,horizontalAccuracy,verticalAccuracy,course,courseAccuracy,speed,speedAccuracy
84206.020,2026-09-27T15:58:40.020Z,30.2671832,-97.7430615,151.2,124.8,9.43,6.1,-1,-1,0.12,0.9
```

## Appendix C: Apple references

ARKit: [ARFrame](https://developer.apple.com/documentation/arkit/arframe), [ARCamera](https://developer.apple.com/documentation/arkit/arcamera), [ARDepthData](https://developer.apple.com/documentation/arkit/ardepthdata), [ARWorldTrackingConfiguration](https://developer.apple.com/documentation/arkit/arworldtrackingconfiguration), [ARConfiguration.WorldAlignment](https://developer.apple.com/documentation/arkit/arconfiguration/worldalignment-swift.enum), [ARPlaneAnchor](https://developer.apple.com/documentation/arkit/arplaneanchor), [ARMeshAnchor](https://developer.apple.com/documentation/arkit/armeshanchor), [ARPointCloud](https://developer.apple.com/documentation/arkit/arpointcloud), [ARRaycastQuery](https://developer.apple.com/documentation/arkit/arraycastquery). Core Motion: [CMDeviceMotion](https://developer.apple.com/documentation/coremotion/cmdevicemotion), [CMAttitudeReferenceFrame](https://developer.apple.com/documentation/coremotion/cmattitudereferenceframe), [CMAltimeter](https://developer.apple.com/documentation/coremotion/cmaltimeter). Core Location: [CLLocation](https://developer.apple.com/documentation/corelocation/cllocation), [CLHeading](https://developer.apple.com/documentation/corelocation/clheading), [CLLocationManager](https://developer.apple.com/documentation/corelocation/cllocationmanager). Image I/O: [EXIF dictionary keys](https://developer.apple.com/documentation/imageio/exif-dictionary-keys). AVFoundation: [AVCapturePhoto](https://developer.apple.com/documentation/avfoundation/avcapturephoto), [AVCameraCalibrationData](https://developer.apple.com/documentation/avfoundation/avcameracalibrationdata).

## Appendix D: Upload API (addendum; full design in docs/hosted-architecture.md)

This realises section 5 rule 5 without tus. Full request and response shapes, retries and the result flow are in [hosted architecture §2](/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/docs/hosted-architecture.md).

1. `POST /v1/captures` with `Idempotency-Key: <packetId>` (the `packetId` of `packet.json`) and body `{packetId, formatVersion: "0.4", tier, flow: "guided" | "photo", device, orderRef?, expected?}` returns a `captureId`. `flow` exists only here (the packet has no such field); when omitted the server assumes `guided` for ARKit tiers and `photo` for `photo_only`. A `formatVersion` other than `"0.4"` → `422 format_version_unsupported`.
2. During capture, in batches every ~2 s: `POST /v1/captures/{id}/files` with `{path, bytes, sha256, md5, role, contentType, meta}` per file (1–50 per call). `path` is the packet path (`keyframes/k00012.jpg`). `role` is exactly the `files[].role` value (`keyframe`, `depth`, `confidence`, `features`, `still`, `stream`, `truedepth`, `mesh`, `other`; `keyframe_image` → `422 role_unknown`). `meta` is the keyframe, still or `trueDepthFrame` record the file belongs to (images and TrueDepth frames only). The call returns a signed PUT URL per file. Files over 16 MiB get multipart part URLs instead.
3. PUT each file's bytes to its URL with the returned `Content-Type` and `Content-MD5` headers, then `POST /v1/captures/{id}/files:commit` with `{path, sha256}`. A commit is the per-file acknowledgement in section 5 rule 5.
4. `POST /v1/captures/{id}/finalize` with headers `Idempotency-Key: <packetId>`, `X-Packet-Sha256: <sha256 of the uncompressed body>` and `Content-Encoding: gzip`, body = `packet.json`. It may be sent before every file is committed (`202 awaiting_files` with the missing list; the run starts on the last commit); the manifest checks of section 6 answer `422` with JSON pointers.
5. Results, live hints and retake requests arrive via `GET /v1/captures/{id}/events` (long-poll), APNs push, and `GET /v1/captures/{id}/result`. Retakes are amendments: `POST /v1/captures/{id}/amendments` with `{amendmentId, answers: [view or member-action ids]}`, then the same file calls under `amendments/{aid}/`, then `POST /v1/captures/{id}/amendments/{aid}/finalize` with an ordinary v0.4 packet whose `parentPacketId` is the parent's `packetId` (fresh `packetId`, same `formatVersion`). `amendmentId` is a `StorageId`.
6. One-tap answers to a pre-filled read (the meter number, later panel brand or main amps): `POST /v1/captures/{id}/confirmations` with `{fieldId: "meter_number", kind: "confirmed" | "corrected" | "none", value?, imageId, clientAt}` → `201 {confirmationId, fieldId, imageId, kind, receivedAt, seq}`; a repeat with the same answer → `200`, a different answer → `409 confirmation_conflict`.

Format notes:
- Keyframes stay JPEG q0.9, unrotated. There is no HEIC; `photo_only` imports from Photos are the one exception, and the server transcodes those.
- The role `compat` is reserved for server-derived files (today: the JPEG the server writes when it transcodes a `photo_only` HEIC still, listed at `normalized/stills/<id>.jpg`); the app never sends `compat`.
- Depth and confidence stay raw, per section 4.
- Streams are uploaded gzipped as `streams/<name>.csv.gz` and listed under that path in `files[]`.
- Upload order: `meter_close`, `meter_oblique` and `panel_*` stills first (with any TrueDepth frames), then keyframes in capture order, each with its depth, confidence and features files, then other stills, then streams and mesh, then finalize.
