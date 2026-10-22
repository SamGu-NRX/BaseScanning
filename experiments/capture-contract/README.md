# Capture metadata contract probe

## Question and pass criteria

Can a recorded camera frame preserve the same pixel ray when converted from ARKit to the OpenCV camera convention used by reconstruction models?

Before running the probe, require:
- Translated and rotated synthetic cameras produce hand-computed rays and projections.
- Crop/resize transforms preserve the original ray when intrinsics change with the image.
- Incorrect camera axes, inverted extrinsics, matrix storage, focal lengths and source units fail explicit controls.
- Keyframes retain source IDs, image references and tracking state; arrival order may differ from timestamp order.
- A conversion does not pretend that session coordinates are meter-anchor coordinates or that successful arithmetic establishes physical accuracy.

## Scope

This isolated experiment consumes the **measure-lab-session version 2** camera/tap subset documented in the team's [measurement-rig proposal](https://github.com/SamGu-NRX/house-scanning/pull/7), inspected at commit `ca89849224814732b389e4b6ea9e26f3d41d09a5`. It complements that rig; it does not implement or modify capture. It converts validated camera metadata into an inspectable JSON file for a future reconstruction adapter. It does not run MapAnything, load photos, validate the full session schema, inspect image dimensions, or determine installation eligibility.

The synthetic manifest is a focused contract fixture, not a complete app export. Its image references are placeholders; there are no real photos, addresses, or customer identifiers.

## Run

Python 3.10+ standard library only. From this directory:

```sh
python3 -m unittest -v
python3 capture_contract.py fixtures/synthetic-session.json --out data/converted.json
```

For a real permitted session, point the first argument at its session.json and keep outputs in ignored data/. Never commit real manifests or images.

## Coordinate contract

- Input: ARKit camera-to-world, 16 values **column by column**; right-handed camera axes with X right, Y up, looking along negative Z.
- Output: OpenCV camera-to-world, a 4×4 matrix serialized as **nested rows**; X right, Y down, Z forward.
- Conversion changes the camera basis with diag(1,-1,-1,1) on the right. It does not rotate or re-anchor the world.
- World remains the source ARKit session world, Y up. No meter anchor is available in this source contract; consumers must supply and validate one before anchor-relative placement.
- A COLMAP exporter would additionally invert the camera-to-world transform; COLMAP stores world-to-camera. This probe does not emit COLMAP files.
- Intrinsics apply to each saved sensor-native image. The crop/resize helper is tested separately; original images are not automatically resized or reoriented.
- The output's image references remain relative to the **source manifest directory**, not the converted JSON's directory. The CLI records that local directory as `image_reference_base`; keep the generated output private with the source capture.

The source format/version defines the expected conventions. A rigid matrix alone cannot prove the producer used those conventions. Tap records provide an additional consistency check when available; absent taps mean that check was not performed.

## Validation and evidence limits

The rigid-rotation tolerance 1e-4 and ray arithmetic tolerance 1e-6 detect serialization and coordinate mistakes. They are numerical test tolerances, **not measured accuracy bounds**. A geometrically self-consistent but wrong pose or wrongly calibrated camera can pass these tests.

The output preserves limited tracking; it never silently upgrades it to normal. Even normal tracking does not establish metric accuracy. Original source images, field measurements, calibration and tracking-epoch validation remain necessary.

Keyframe `w` and `h` must be integers of at least 1, and `w` must exceed `h`: replay intrinsics are defined in the pixels of the unrotated landscape JPEG, so a portrait or square size means a rotated image or rotated intrinsics. Duplicate frame IDs, missing tap references, incompatible versions/units, nonfinite numeric inputs, reflections and scaled rotations are rejected. The CLI rejects duplicate JSON keys and emits no new output on validation failure. It does not access the referenced image paths or execute anything from the manifest.

## Result

All 14 known-answer and malformed-input controls passed on macOS using Python 3.13.7. The CLI converted two synthetic cameras and checked both saved tap rays. An independent review also checked 100 generated proper rotations against a separate construction and verified the extreme-intrinsics, nonunit-ray and off-image-tap controls. This is arithmetic/metadata evidence only; real-device projection and geometric accuracy remain untested. The fixture includes a limited-tracking frame, which remains explicitly limited in the output.

## Primary references

- [Apple ARCamera transform](https://developer.apple.com/documentation/arkit/arcamera/transform)
- [Apple camera intrinsics](https://developer.apple.com/documentation/arkit/arcamera/intrinsics)
- [COLMAP coordinate definitions](https://colmap.github.io/format.html)
- [MapAnything inputs and conventions](https://github.com/facebookresearch/map-anything)
- [OpenCV calibration and image scaling](https://docs.opencv.org/4.13.0/dc/dbb/tutorial_py_calibration.html)
