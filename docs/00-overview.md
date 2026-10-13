# House scanning: plan, decisions and evidence

A homeowner scans the outside wall around their electric meter with an iPhone. The server works out whether a Base Power battery fits there and where, and the app shows the spot in AR. The goal is one capture session that gives Base enough to decide, with no follow-up photos. A person can still review any result.

Base's survey starts today from customer photos. A photo has no scale, can't show whether two things share a wall, and misses whatever sits just outside the frame. Borderline cases go to a reviewer, and homeowners get asked for more photos. An AR session gives every photo a position and a real scale, and the app knows what it hasn't seen while the homeowner is still standing there.

Full-house scans, roofs and Android are out of scope. Each component's README is the source of truth for that component and wins where it differs from this file.

## How the pieces fit

The work is three problems. The phone guides the homeowner until it has captured everything the rules need. The photos and their metadata become a 3D model. Plain code checks that model against the placement rules, and the answer comes back to the phone.

**Client team: Sam with AI agents, and Aiden on video and sample datasets.** The app in `ios/` builds a live 3D map on the phone. A haze, the "fog of war", covers what it hasn't seen and lifts as the homeowner scans. The app guides them until everything the rules need is covered, which is a zone around the meter rather than the whole house. Guided capture is in PR #10 and the live map in PR #21. The app's output is the capture packet: photos, camera poses, intrinsics, motion-sensor readings, LiDAR depth and mesh when the phone has LiDAR, and the homeowner's marks. `packet/README.md` (PR #22) specifies it and lists what the app records today. On `main` the app only shows AR tracking. With PR #10 it uploads only its measurements, `scene.json`, and exports the full packet on request.

**Server team: Hunter with his agents.** The packet goes in, and a 3D model comes out at real scale with a record of which parts were observed. The rules engine in `server/` (PR #11) then checks every candidate spot. The team is comparing reconstruction methods broadly first, then the best in depth. Hunter's research handoff (PR #18) plans the server side:
- Original photos travel to object storage, separate from the compact placement request, because a Vercel function accepts at most 4.5 MB.
- Recognition and reconstruction run asynchronously while the phone keeps guiding, and capture, upload, analysis and assessment finish at different times.
- A new reconstruction can revise earlier geometry. Coverage and measurements that depend on it get recomputed, never carried over.
- OCR confidence doesn't settle an exact identifier, so unreadable or conflicting values stay unresolved.

The reconstruction worker is in `recon/` (PR #20), the accuracy evals in `experiments/evals/` (PR #12), and Hunter's live-survey design in [05-live-guided-survey-hld.md](05-live-guided-survey-hld.md).

## Decisions

- **Guided AR capture, not a video processed later.** Photogrammetry alone has no scale, is slow, and struggles on blank siding and stucco. COLMAP's own tutorial says to avoid texture-less walls. The packet carries ARKit's poses and any LiDAR depth, so the server's model starts at real scale.
- **The server builds a 3D model at real scale.** Learned depth alone is 4 to 12% off in scale and 8 in or worse at edges, so LiDAR and the phone's poses supply the scale.
- **Models build geometry and recognize things. Plain code decides placement.** The engine unrolls the walls into one line, `s`, in feet from the meter, negative to the left. It slides the battery's footprint along that line in 2 in steps and checks each rule at each position. Every answer then traces to a rule and a measurement.
- **Every check answers PASS, FAIL or UNSURE.** A check passes only when its margin beats the measurement's error, and fails only when it misses by more than the error. Everything else is UNSURE. That includes any check on an area nobody observed, and the answer names the view that would settle it.
- **Rule values live in rules files, each with its source.** `server/rules.yaml` (PR #11) holds the public values and marks each value with no public source `placeholder: true`, such as pool 10 ft and driveway 5 ft. Base's values load from a git-ignored private file. `rules.yaml` also holds the battery's size, because models differ. Base Core is 39.5 in tall on a 30.68 × 22 in footprint, and older units are 3 × 3 ft.
- **The coverage map shows evidence, not approval.** Gray means nobody saw it, yellow means seen but the evidence isn't enough yet, and green means enough evidence for the current requirement, which is not installation approval. Coverage follows the surfaces the camera saw, not the path the homeowner walked.
- **Native iOS only.** Capture needs mesh export, per-frame pose and lens data, and mesh raycasts, and Expo has no first-class ARKit support.
- **AR results hang off the meter's anchor, with `.gravity` alignment.** ARKit corrects anchors as tracking improves, and drift runs about 2 cm a second, so a result tied to the meter moves with the correction. `.gravityAndHeading` depends on the compass, which developers report off by up to about 176° near a house.

## Conventions the code relies on

These are the facts behind numbers in the code that the code alone doesn't explain.

- `capturedImage` is landscape, as the sensor reads it, and the intrinsics match it. Any rotation must also rotate the intrinsics and every image-space annotation. Gemini is the planned box detector, because Claude's docs call Claude's coordinates approximate. Gemini returns `[ymin, xmin, ymax, xmax]` on a 0 to 1000 scale, so apply EXIF rotation before inference and before casting a box.
- ARKit's depth maps are 256 × 192, smaller than the camera image. Scale the intrinsics down to match.
- ARKit camera space is x right, y up, looking down −z, with image v growing downward. The ray through pixel (u, v) points along ((u − cx)/fx, −(v − cy)/fy, −1) before the camera-to-world rotation.
- Keyframes are kept every 0.5 m or 15° of movement, a day-1 choice that Measure Lab kept.
- Mesh vertices are local to their `ARMeshAnchor`. Copy each one out of ARKit's buffer, then multiply the copy by the anchor's transform, leaving the buffer untouched. Classified mesh needs `sceneReconstruction = .meshWithClassification` on the world-tracking configuration, on LiDAR phones only. RealityKit also turns classification off unless `automaticallyConfigureSession` is `false`.
- On the mesh, the gap to a facing fence is a ray straight out from the wall at 1.5 ft high, and headroom is a ray straight up from 1 ft out. The 1.5 ft height and the 1 ft offset are day-1 choices, not measured. The mesh has holes, so each measurement is the smallest distance over a small fan of rays.
- LiDAR reads from about 0.5 m to 5 m, so capture stands 1 to 3 m from the wall. A ray that meets nothing within 5 m is unknown, not open.
- The default error bars are day-1 estimates, untested: 0.3 ft for an AR tap, 0.5 ft for the LiDAR mesh and 1.5 ft for a position from photo detection. The server adds 0.16 ft per foot along the wall from the meter, from measured ARKit drift.

## Evidence so far

These results come from public datasets with laser-scanned or surveyed ground truth. Only the first phone run below was measured on our phone. The sources are `experiments/evals/README.md` (PR #12), `recon/HANDOFF.md` (PR #20), `experiments/meter-closeup/README.md` (PR #16) and `experiments/device-field-test/README.md` (PR #23).

- **Tracking.** A current iPhone stayed inside the server's error allowance. On the MARViN dataset, an iPhone 14 Pro Max had p90 position error of 8.6, 13.4 and 18.5 in after 10, 20 and 30 ft, against an allowance of 19.2, 38.4 and 57.6 in. That phone has LiDAR, and MARViN's reference may be scaled to its tracking. A 2018 iPhone 6s on ADVIO ran two to three times over, partly from noise in that dataset's reference.
- **Learned depth.** A model's own scale is 4 to 12% off. Rescaled with the phone's poses, walls come within about 5 in at p90 at a realistic 2% pose error, and within 2.8 in with exact poses. Edges stay at 8 in or worse, and clearances are measured from edges. MapAnything's scale shifted with input resolution.
- **Reconstruction worker.** On the ETH3D building, wall pair-error p90 is 1.8 in with a laser scan standing in for LiDAR, and 1.5 in from photos only. Both fit stretches of the same wall, with different lengths. The photos-only change includes a different fitted stretch; see `recon/HANDOFF.md`.
- **Coverage.** The app's first coverage map claimed 1.1 ft of a 19.3 ft wall that no photo saw. Requiring several view angles removed the false claim but also discarded 6 to 8.5 ft of good wall. A depth test on LiDAR phones removed it cleanly. Checked against depth, the reconstruction worker claims at most about 0.46 ft of unseen wall.
- **Meter reading.** Apple's on-device text recognition read the full meter number on 71 of 73 real photos. It picked the right line on only 21 of 75, because nameplates carry several numbers. A barcode held the number on 20 of the 24 photos that had one. A list of three candidates held it on about 4 in 5 held-out photos (27 of 34). So the app offers three candidates to tap, barcode match first, and asks for a retake when the photo is blurry or the number too small.
- **Panel labels.** Not tested. A search found 7 usable open photos of US panels, and the test needs 40.
- **World models.** Not evaluated yet.
- **First phone run.** The TestFlight build's first run on a real wall (PR #23) set both wall ends at the meter, so the server placed no spot. Two of five measured features came within 4 in of the tape.

## Open questions

- **Clearance values.** [04-prior-art-and-codes.md](04-prior-art-and-codes.md) has the public values with their citations. Base's own values go in the private rules file, along with which number on the meter plate Base needs.
- **Disconnect box.** Does the install add a battery disconnect beside the meter? Base's public pages mention a wall-mounted transfer switch and a battery disconnect.
- **The 3D path.** The candidates are traditional reconstruction fused with LiDAR, learned depth rescaled with the phone's poses, and world models, which produce a whole 3D scene from photos or video. The winner needs real scale, edges within the error allowance, and a record of which parts it observed and which it filled in.
- **Accuracy on our phone.** The field test uses a current iPhone without LiDAR, the Measure Lab build (PR #7), a 30 ft tape and a real meter wall. It follows `experiments/evals/field/FIELD_SHEET.md` (PR #12), and a dry run recovered a planted 1.5% scale error. It also checks the default error bars. LiDAR on siding in midday sun is untested too.
- **Pool and driveway.** Their checks need up to 13 ft of photographed ground. The alternative is one yes-or-no question, recorded as the homeowner's answer.
- **Hidden wall.** Something standing in front of the wall can hide it, and nobody owns that check yet. One proposal is for the app to show the homeowner the photo of the chosen spot and ask, and to run the depth test on LiDAR phones.
