# House Scan for iOS

Native iPhone app for the AR capture walk. The homeowner marks the electric meter, the app takes a close-up of it, then keyframes are captured automatically while they walk the wall. The app asks for any missing stretch, uploads the scan and shows the placement result.

## How it is split

- `HouseScanKit/` is the capture logic as a plain Swift package (Foundation and simd only): camera and wall geometry, the coverage map, the auto-capture and close-up gates, walk guidance, the gap planner, scene.json export, the zip writer, result decoding and replay reading. Its thresholds are hypotheses, documented where they are defined.
- `HouseScan/Runtime/` is the engine: ARKit and replay frame sources, the state machine behind `ScanActions`, keyframe storage, upload and the autopilot.
- `HouseScan/UI/` holds the screens. They read `ScanViewState` and call `ScanActions`; `HouseScan/Contract/ScanContract.swift` is the boundary between the two.

## Launch arguments

| Argument | Effect |
| --- | --- |
| `-replay <folder>` | Plays a measure-lab-session v2 folder instead of the camera. A replay without wall taps gets a wall assumed from its trajectory, logged as an assumption. Keyframes with a `depth` entry (`{file, confidenceFile, w, h}`, Float32 meters) play their LiDAR depth into coverage as a LiDAR phone would. |
| `-autopilot` | Drives every step on a replay, answering the ground question with mulch. It holds some walk frames back to close one gap request before the upload. After each upload it also drives the requests the server's answer raises, closing each with the replay's frames, until the result shows. A request the frames don't close gets "I can't get there", as a homeowner would answer. Before the result it answers the spot check "It's clear". |
| `-serverURL <url>` | Uploads the scan to this server: `POST <url>/v1/placements` with scene.json as `application/json`. Without it the app uses `HOUSESCAN_SERVER_URL` from `Config/Shared.xcconfig` (https://house-scanning-server.vercel.app), carried in Info.plist as `HouseScanServerURL`. Photos stay on the phone unless someone uses Share scan, which shares the scan as a capture packet (see "Scan bundle"). |
| `-sampleResult` | Answers with the bundled sample result, which the result screen must label as a sample, even when a server is configured. The UI tests pass it so they run offline. It is also the fallback when `HOUSESCAN_SERVER_URL` is empty. The spot check asks about the sample spot and labels it a sample too. |
| `-autopilotHold <s>` | How long the autopilot leaves each screen up (default 1.2 s). |
| `-autopilotCantGetThere` | With `-autopilot`, ends the walk the way device run 1 did. It answers "Can't get there" to the walk's first request, the ground in front of the meter, before the replay has shown it. Then, instead of marking the ends, it plays again the frame where the walk went farthest on each side and answers "Can't get there" when the walk asks to go on that way, so the end lands where the phone stands. `FullFlowUITests.testCantGetThereEndsWhereThePhoneIs` runs it. |
| `-autopilotSomethingThere` | With `-autopilot`, answers the first spot check "Something's there" instead of "It's clear". The scan stops claiming that area, is uploaded again, and later checks get "It's clear". The stretch taken out goes to the `-autopilotGate` folder as `spot-refusal.json` (`withdrawn_span_ft`, feet along the wall as in scene.json). `FullFlowUITests.testSomethingThereChecksTheWallAgain` runs it. |
| `-autopilotCannotCheck` | With `-autopilot`, answers the first spot check "I can't check this area" instead. As with `-autopilotSomethingThere`, the scan stops claiming that area, is uploaded again, and the withdrawn stretch goes to `spot-refusal.json`; the result says the area wasn't checked. `SpotCannotCheckUITests` runs it. The two flags can't be combined. |
| `-sampleResultAfterSpotAnswer <path>` | Debug builds, with the bundled sample. The upload sent after "Something's there" or "I can't check this area", and every later upload of that scan, is answered with the server answer in this JSON file, such as one without a spot. Start over or a new wall goes back to the bundled sample. `SpotCannotCheckUITests` uses it. |
| `-autopilotGate <folder>` | Used by the UI tests. Before leaving a screen, the app waits for a file named after that phase in this folder, and while it waits it leaves `<phase>.held` there, so a test can audit the screen once the autopilot has finished with it. Once the result shows, the autopilot writes the scan's scene.json there. |
| `-simulateAppStore` | Runs as an App Store install would: no developer options, and practice meter stays off whatever the stored switch says. `PracticeMeterUITests` uses it. |
| `-practiceMeter YES` or `NO` | Overrides the stored practice meter switch for one run (UserDefaults' argument domain). The full-flow UI tests pass `NO`, since the switch outlives a run in the Simulator. |
| `-savedScansRoot <folder>` | Debug builds only. Saved scans lists the scan folders in this folder instead of the app's Caches/Scans. It only changes what is listed; the scan store still writes and cleans up Caches/Scans. `SavedScansUITests` passes it with synthetic scans. |
| `-processingBackend <legacy\|photoProcessing>` | Debug builds only. The processing choice in Developer options starts at this value and stays in memory for the run, so a UI test's choice never reaches the stored setting. |
| `-photoProcessingFixture <answer>` | Debug builds only, with `-replay`. Photo processing sends to a capture API answered inside the app (HouseScanKit's `FixtureCaptureHTTP`), which ends each capture with this answer: `candidate`, `needsViews`, `manualReview`, `notEligible`, `failed`, `expired`, `notReady`, `unreadable`, `refuseCreate`, `hold` or `wrongRun`. Nothing leaves the phone. With `-autopilotGate`, the routes it answered are listed in `photo-transport.log` there. |
| `-photoProcessingSyntheticCapture` | Debug builds only, with `-photoProcessingFixture`. Sends HouseScanKit's `SyntheticCapture` (packet `source.kind: synthetic`) in place of the replay's photos, so the fixture's answer comes back through the real upload. |
| `-photoProcessingFixtureReadOnlyCapture` | Debug builds only. Makes the capture's folder read-only just before "Stop sending photos" takes effect, so the phone can't save the withdrawal and says so. |
| `-uiDemo` | Runs the screens on a scripted fake engine instead of the capture engine, for design work and for auditing states a replay can't reach. The arguments it takes are listed in `HouseScan/UI/Preview/UIDemo.swift`; for example `-uiDemoGroundQuestion` opens the feature review with the ground question showing, `-uiDemoPhase meterCloseUp -uiDemoMeterChoose` shows the meter number picker, and `-uiDemoPhase result -uiDemoCorner` shows the result model of a wall that turns a corner. |

**Spot check.** Before the result, the app shows the kept photo that best sees the answer's spot, with the spot and its clearance area outlined, and asks whether anything stands in front of the wall or on the ground there (`STATE=spotConfirm`). When no kept photo shows the area, it shows no photo and gives the area in words: where it runs beside the meter, how far out from the wall, and how high up the wall. Photos without depth and the walked path claim wall, ground and clear space they never saw; "It's clear" backs those claims for that spot, and "Something's there" takes them back over the area, so scene.json reports it unseen, and sends the scan again. "I can't check this area", for a homeowner who can't see or reach it, does the same without saying anything stands there, and the result says the area wasn't checked and needs a person to check it. When the scan sent again names the same spot, the earlier answer settles it and the homeowner isn't asked again. When it names no spot, or another one, the result still says an area along the wall went unchecked, until Start over or a new wall clears the scan's spot checks. The rules are in `Runtime/ScanEngine+Confirm.swift` and HouseScanKit's `Confirm/SpotConfirmation.swift`.

**Processing (beta).** Developer options offer two backends, both remote: **Legacy (remote checker)** sends scene.json to the placement server, and **Photo processing (beta)** sends the scan's photos and camera path to the capture API. Legacy is the default. A scan reads the choice once, when it starts, and keeps it with its endpoint in its `ScanContext` (HouseScanKit); a change applies to the next scan, and an App Store install always uses Legacy. A photo-processing scan asks before sending (`PhotoConsentCard`), sends photos as they are kept, and after the review goes to `STATE=processing` instead of the upload: its progress, then the service's answer quoted as written (`PhotoProcessingScreen`). It never falls back to the Legacy checker. An answer is shown only when the run it names, and the phone's own record of what it sent (the recording session and world epoch, the coordinator session, packet and capture), match this scan; that is a local association, since the service returns no digest of its input and no attestation. AR is withheld for every answer. Sending off the phone is off in every build for now, so choosing photo processing ends the scan saying it isn't set up.

Two debug-only switches run the path on a replay against `FixtureCaptureHTTP`, a stand-in for the capture API inside the app (the type is in every build; only its activation is debug-only): `-photoProcessingFixture` alone sends the replay's photos, whose packet always fails the phone's 0.4 checks because a replay has no motion data and too few poses, so the scan ends "couldn't be prepared"; adding `-photoProcessingSyntheticCapture` sends HouseScanKit's `SyntheticCapture` instead, labelled `source.kind: synthetic`, so the real coordinator and uploader carry it to the fixture's answer. Either way the answer is the fixture's, never a service's or a GPU reconstruction's, and the screens say so. Live captures now record each frame's intrinsics with its pose for the 0.4 pose stream, and an accepted meter mark adds the packet's meter tap from that mark's own ARKit frame (`Runtime/LiveCapture+MeterTap.swift`); a mark that can't give one ends the capture as not prepared. Neither is checked on a phone yet. Live use still requires a scoped server-issued credential, the clock and privacy contract, and an input digest or attestation from the service. The code is in `Runtime/ScanEngine+Processing.swift` and HouseScanKit's `Processing/`.

**Practice meter.** For trying the whole scan where there is no electric meter, such as indoors. Debug and TestFlight builds show a wrench button at the top of the first screen. Tap it, turn on **Practice meter**, tap Done, then start the scan. At "find the meter", tap any spot on a wall: the usual raycast and refusals apply, and a drawn sample meter appears there, fixed to the meter's anchor, on every camera screen after that. The close-up keeps the live camera's pose and depth, but stores a photo of the sample (made-up maker SAMPLEWORKS, number 12345678), and the real reader reads it. The walk, fog, marks, requests, spot check, upload, result and AR all run for real. Every screen shows a "Practice meter" badge, and `scan-stamp.json` has `"practice": true`; the packet and scene.json are unchanged. The switch is kept until turned off, and the button reads "Practice meter on" while it is. App Store installs never show the button and ignore a stored switch: the app asks StoreKit's `AppTransaction` which server signed the install (sandbox means TestFlight) and treats no answer as the App Store. It is not in the iOS Settings app, because a Settings.bundle ships in the same binary that TestFlight later promotes to the App Store. The code is in `Runtime/Practice/`, `UI/Practice/` and HouseScanKit's `Practice/PracticeMeter.swift`.

**Live fog.** During the walk and a gap request, a soft fog covers what the coverage map has not counted and lifts only where it counts a cell, the same rule the scan's decisions use (`FogValue.target` in `UI/Camera/LiveFog/LiveFogScene.swift`). On a LiDAR phone, dots settle on the surfaces the kept keyframes' depth measured (HouseScanKit's `Overlay/SurfaceDots.swift`, run by `Runtime/LiveDotsFeed.swift`); they are guidance and feed nothing. The look is the prototype's (experiments/live-dots on t3/experience). Phones without LiDAR get the same fog without dots. The shaders compile at launch, and the old frosted strip (`FogOverlay`) draws until they are ready, and for good if Metal fails.

Every screen change is logged as `STATE=<phase>` under subsystem `dev.housescanning.housescan`, category `state`.

## Layout

| Path | Contents |
| --- | --- |
| `project.yml` | XcodeGen spec, the source of truth for the project |
| `HouseScan.xcodeproj` | Generated from `project.yml` and committed |
| `HouseScan/` | Swift sources, a synchronized folder |
| `Config/Shared.xcconfig` | Settings for all configurations; includes `Local.xcconfig` if present |
| `Config/Local.xcconfig.example` | Template for per-person signing |
| `Config/Info.plist` | Camera prompt, `arkit` capability, portrait only, HTTP to local-network servers |
| `HouseScanKit/` | Capture logic package with its tests |
| `HouseScanUITests/` | Full-flow UI tests on a replay, and an accessibility audit of every screen state in demo mode. `Fixtures/` holds two synthetic replays, one with LiDAR depth; the tests read them from the source tree, and `project.yml` keeps them out of the test bundle. |
| `Tools/make-synthetic-replay.swift` | Renders the synthetic fixture |
| `Tools/check-app-scene.sh` | Checks a scan bundle's scene.json against the server schema |

## Requirements

- Xcode 26 or newer. The deployment target is iOS 26.0 and the code uses only iOS 26 SDK APIs.
- An iPhone that supports ARKit to run it. LiDAR is not required; with it the scan also records depth and a mesh.
- XcodeGen 2.46.0, only if you change `project.yml`.

## Commands

Build without signing, as CI does, from the repository root:

```sh
xcodebuild -project ios/HouseScan.xcodeproj -scheme HouseScan -configuration Debug \
  -destination "generic/platform=iOS" CODE_SIGNING_ALLOWED=NO build
```

`make ios` runs the same build.

Test the capture logic, then run the whole flow in the Simulator on the synthetic replay:

```sh
swift test --package-path ios/HouseScanKit
xcodebuild -project ios/HouseScan.xcodeproj -scheme HouseScan \
  -destination "platform=iOS Simulator,name=iPhone 17" -only-testing:HouseScanUITests test
```

To run the flow on a local recording, set `TEST_RUNNER_HOUSESCAN_REPLAY=<session folder>` for that `xcodebuild` command. Keep such recordings and their screenshots out of git.

After editing `project.yml`, regenerate the project from the repository root and commit it:

```sh
make ios-project
```

CI regenerates the project with XcodeGen 2.46.0 and fails if the result differs from the committed project. Other XcodeGen versions can write a different file, so `make ios-project` refuses to run with any other version.

## Run on your iPhone

1. From the repository root, `cp ios/Config/Local.xcconfig.example ios/Config/Local.xcconfig`.
2. In `Local.xcconfig`, set `DEVELOPMENT_TEAM` to your 10-character Team ID (developer.apple.com > Account > Membership details) and `BUNDLE_ID_PREFIX` to a reverse-DNS prefix of your own, such as `com.yourname`. The app id becomes `<prefix>.housescan`.
3. Open `ios/HouseScan.xcodeproj`, plug in the iPhone, pick it as the run destination and press Run. Signing is automatic, so Xcode creates the profile for your team.
4. The first time, the iPhone asks you to turn on Developer Mode (Settings > Privacy & Security > Developer Mode, then restart). With a free Apple account you also trust yourself in Settings > General > VPN & Device Management.

Git ignores `Local.xcconfig`. Leave the team field in Xcode's Signing & Capabilities tab empty. Setting it there writes your team into `project.pbxproj`, and the CI drift check then fails.

**Server.** By default the app sends scans to the hosted API, https://house-scanning-server.vercel.app (`HOUSESCAN_SERVER_URL` in `Config/Shared.xcconfig`). To use a server on your laptop instead, start it on the same Wi-Fi as the phone (`uv run uvicorn api:app --host 0.0.0.0 --port 8000`, see `server/README.md`), find the laptop's address with `ipconfig getifaddr en0`, and add `-serverURL http://<address>:8000` under Product > Scheme > Edit Scheme > Run > Arguments. Info.plist allows plain HTTP to local-network addresses; if iOS asks for Local Network access, allow it. `-sampleResult` answers with the bundled sample instead, with no network at all. Xcode saves these arguments into the shared scheme, so don't commit that change.

**Logs.** Open Console.app on the Mac, select the iPhone, press Start and search for `subsystem:dev.housescanning.housescan`. Every screen change logs as `STATE=<phase>`; the `engine` category records the capture's decisions, the scan bundle and upload failures. The same lines show in Xcode's console while it runs the app.

**Getting a scan off the phone.** The result screen, and the screens for a failed or refused upload, have a Share scan button. It shares the scan bundle (below); AirDrop it to your Mac. It holds photos of a real home, so keep it out of git. After the app is closed, **Saved scans** in the first screen's top bar lists the completed scans the phone still keeps (the two most recent whose `scan.zip` was written to the end, as `ScanFolderCleanup` keeps them) with when each bundle was saved, its size and a Practice tag when its `scan-stamp.json` says so. A bundle cut short by a quit during its write is not listed, holds no kept place and is not deleted. Start over doesn't wait for a bundle still being written, but the next scan's cleanup does before it deletes anything. If that write finishes the zip, the scan reaches Saved scans; if it fails before zipping, its folder is deleted, and if it leaves a partial zip, the scan isn't listed. Share there sends the same bundle. It copies the bundle to the app's tmp folder first, so a new scan's cleanup can't delete it while the share sheet reads it, and deletes the copy when the sheet closes; a launch deletes copies earlier runs left. A saved scan can only be shared, never reopened or continued. The code is in `UI/SavedScans/` and HouseScanKit's `SavedScans/`.

## Scan bundle

`scan.zip` in the scan's folder is what Share scan sends: a capture packet, version 1.1, with `manifest.json` at the zip's root. The packet's specification is `packet/README.md` on the `t3/packet` branch, with `packet/manifest.schema.json` and a validator (`uv run python -m packet validate <scan.zip>`). The upload sends only scene.json; nothing uploads the packet.

Everything in the packet is in meters, seconds of device uptime (`ARFrame.timestamp`) and the meter frame (origin at the meter, +y up, +z out of the wall, +x along the wall to the right), except scene.json.

| File | Contents |
| --- | --- |
| `manifest.json` | The session (device model, iOS version, LiDAR and what the session ran with, including whether the mesh was classified, capture times, distance walked, the meter frame in ARKit's world), every photo's time, pose, intrinsics, tracking, exposure, lens and sharpness, the depth frames, the streams, plane anchors, marks and the guidance log. Every other file is named in it with its size and sha256. |
| `photos/pNNNNN.jpg` | Every kept keyframe and the meter close-up, in time order, landscape and unrotated as the sensor produced them. |
| `depth/pNNNNN.f32`, `depth/pNNNNN.conf.u8` | Live LiDAR phones only. ARKit's scene depth for the photo, float32 meters along the camera's -z (0 = no reading), and its confidence (0 low, 1 medium, 2 high). ARKit depth without its confidence is left out. |
| `depth_frames/dNNNNN.f32`, `.conf.u8` | LiDAR depth between photos, with no image: 2 a second at most, frames with normal tracking only, at most 300 in a scan (`DepthFrameBudget`). Each has the pose and time of an ARFrame on the trajectory and intrinsics for the depth map's own 256 × 192 grid. The recorder writes each to disk as it arrives. On a replay that recorded depth, the same budget picks from the recording's frames, and the frames carry no `source`. |
| `streams/trajectory.csv` | The camera at every ARFrame (60 Hz), with its tracking state. On a replay, one row per recorded frame. |
| `streams/accelerometer.csv`, `gyroscope.csv`, `magnetometer.csv`, `device_motion.csv`, `barometer.csv` | Core Motion on the phone, 100 Hz except the barometer, from the meter search to the upload. Not recorded on a replay or in the Simulator. |
| `lidar/mesh.ply` | LiDAR phones only. ARKit's mesh in the meter frame with each face's classification, all 0 when `session.device.mesh_classification_enabled` is false. |
| `scene.json` | The scene the server checks (contract C1), unchanged: feet, ground at y = 0. Its `img` names are the scan folder's `k<NNNNN>.jpg`, not the packet's photo names. |

`planes` in the manifest lists ARKit's plane anchors, which every ARKit phone detects. Each plane's pose sits at the centre of its extent, turned by `planeExtent.rotationOnYAxis`, and `boundary_m` is the anchor's boundary polygon in that frame. A plane whose boundary leaves its extent keeps its extent and loses the boundary. A replay has no plane anchors.

`DepthPacket.estimated(meters:sigma:width:height:)` is the entry point for depth a model infers from the image on a phone without LiDAR. It carries a per-pixel standard deviation and no confidence, and goes on a photo or a depth frame. Nothing in the app produces it yet.

The app does not record location or heading, and leaves `session.consent` out. A replay's photos keep the recording's times; their depth is left out because it is not ARKit's own, and its capture has no wall-clock start. On a replay the guidance times follow the latest frame played, which stands still while the gap loop replays earlier frames.

`HouseScan/Runtime/ScanEngine+Packet.swift` assembles the packet, `Runtime/CaptureRecorder.swift` records the streams and depth frames, and `Runtime/GuidanceLog.swift` the requests.

## Conventions

- Add Swift files under `HouseScan/`. Xcode includes them without a project change. Edit `project.yml` only for settings, targets or files outside that folder.
- Swift 6 language mode with complete concurrency checking. UI and `ScanEngine` run on the main actor. ARKit delegate callbacks run on a private serial queue and send Sendable frame snapshots to the main actor (see `Runtime/LiveCapture.swift`).
- Never commit captures, photos or measurements of a real home. See the repository CONTRIBUTING.md.
