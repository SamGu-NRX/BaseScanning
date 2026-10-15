# Server pipeline: breadth-then-depth evaluation plan

## Scope

- **Input:** a capture packet from the iOS team: photos plus everything the iPhone and ARKit can provide (camera pose per photo, lens intrinsics, gravity, gyro and accelerometer, compass, GPS, optional LiDAR depth, user taps such as the meter anchor). Spec: [Capture packet spec](/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/docs/capture-packet-spec.md) (in progress).
- **Output:** a metric 3D asset of the exterior, a labeled scene (walls, corners, ground, equipment, obstacles, what was and wasn't seen), a verdict per criterion (pass / fail / unsure with reason), and the views still needed.

## Principles

- Real data first. Synthetic data is only a dev loop, and every result is labeled real or synthetic.
- Every path gets the same inputs and the same scorer, so results are comparable.
- Unseen means unsure. Generated or hallucinated geometry never counts as evidence.
- Nobody waits. Agents start on public laser-scanned datasets immediately and swap in the curated set and real iOS packets as they arrive.
- Breadth work is timeboxed and each path returns the same one-page result: metrics, runtime, cost, failure modes, licence, and a go / no-go.

## Phase 0: foundations (parallel, starts now)

1. **Capture packet spec and synthetic metadata generator.** v0 packet schema modeled on real ARKit output, shared with the iOS team. A generator adds realistic metadata to test images: poses recovered from frame sequences, then corrupted with ARKit-like scale error and drift (LiDAR and non-LiDAR profiles, calibrated on PR #12's measurements). Also a pose-free variant, so no path gets an unfair advantage from poses derived from the same images.
2. **Test dataset.** 15 to 25 real house exteriors where the meter wall is visible, preferring walkaround videos (overlapping frames), plus Mapillary sequences, laser-scanned public datasets (ETH3D, Tanks and Temples, KITTI-360) for accuracy, meter and panel close-ups, and a small hand-picked listing-photo sample. Each house is tagged with what's visible and scale references (doors, brick, meter socket).
3. **Scoring harness and scene format.** One output format every reconstruction path writes, so every criteria path can read any of them. Scorers for geometry (error on criteria-relevant distances, completeness of the regions the criteria need, scale error) and for verdicts (unsafe passes, agreement with hand labels, manual-review rate).

## Phase 1: breadth (parallel, one lead per track)

Each lead runs one worker per path and returns a single comparison.

### Track A: building the 3D asset

- **A1 Classical:** COLMAP or GLOMAP plus OpenMVS, using ARKit poses as priors and for metric scale.
- **A2 Feed-forward multi-view:** MapAnything (can take poses and intrinsics as input), VGGT, MASt3R, π³, Depth Anything 3.
- **A3 Metric depth plus fusion:** MoGe-2, Depth Pro, UniDepth, Metric3D fused along ARKit poses, and LiDAR depth fusion when present.
- **A4 Gaussian splatting / neural fields:** 3DGS and 2DGS with mesh extraction, judged on metric geometry, not looks.
- **A5 World models:** World Labs, Runway GWM-1 and any open equivalents. Scored on geometric faithfulness, scale, and how much they invent in unseen areas. Possible roles even if not metric: visualization, or filling gaps that are explicitly flagged as unobserved.

### Track B: understanding the scene

- **B1 Find the criteria objects:** meter, meter can, panel, gas meter, AC unit, windows, doors, downspouts, fences, pool, driveway, ground type. Compare open-vocabulary detectors and segmenters (Grounding DINO, SAM 2, Florence-2) with vision-language models, then lift detections into 3D across views.
- **B2 Close-up equipment reading:** meter number, CL320 label, panel brand, amperage and condition via on-device-style OCR and vision-language models, building on PR #16.

### Track C: evaluating the criteria

Every path runs first on ground-truth geometry (to test the logic alone), then on each Track A output (to see how reconstruction error flips verdicts).

- **C1 Structured rules:** extract walls, corners and ground from the asset and extend PR #11's rules engine to multiple walls in 3D.
- **C2 Volumetric fit:** voxelize the labeled scene and sweep the battery footprint plus clearance envelopes along every wall, including corners, checking collisions and whether each region was actually observed.
- **C3 Vision-language judge:** a model with measurement and render tools evaluates the criteria. Compared with C1 and C2 on agreement and unsafe passes.

## Gate: choosing what goes deep

| Track | Main measures |
| --- | --- |
| A | Error on criteria distances (laser datasets), completeness of meter wall / ground out to 14 ft / overhead, success rate on online houses, hallucination in evidence regions, runtime and cost per house, commercial licence |
| B | Detection recall and precision on criteria objects, 3D placement error, read accuracy |
| C | Unsafe passes (headline), agreement with hand-worked verdicts, manual-review rate |

Pick the top one or two paths per track.

## Phase 2: depth

- Tune and combine the winners, for example MapAnything with ARKit poses, LiDAR depth and tap anchors, or classical geometry for measurement plus splats for display.
- Build the end-to-end server: packet in, asset, scene, verdicts, and needed views out.
- Feed the needed views back to the iOS team as the fog-of-war contract.

## Phase 3: integration with the iOS team

- Swap synthetic packets for real ones and recalibrate the metadata noise model.
- Final test on at least one real house with tape-measured distances.

## Open questions

- World Labs and Runway API access.
- GPU access for Track A (cloud agent VMs have been CPU-only so far).
- Deadline, which sets the breadth timebox.
- Which of the three conflicting placement rule versions to test against (default: strictest).
- Where the server code lives in the repo, and whether commercial-use licences matter for the hackathon.
