# iOS contract kit 0.4.0

Build the capture app against this folder. The placement decision stays on the server. The app records a packet, uploads it, and draws the result.

Ask for consent before capture. Delete local files only after finalize returns `processing` and every listed file is committed.

## What you render

- `viewsNeeded[]` and `outcome.viewsNeeded[]` are the same objects. Show `prompt.title` and `prompt.body` verbatim.
- `capture_check.missing[]` and hint `code` look up the same catalogue: `prompts/views-needed-prompts.en-US.json`.
- A hint with `confirm` is one tap: preselect `preselected`, offer `candidates`, and send `kind` `confirmed`, `corrected`, or `none`.
- Use `frameToArkit`, `boxArkitWorld`, polygons, and camera poses only while the current ARKit world is `outcome.arkitEpoch` (same session, or an `ARWorldMap` saved at that epoch's end). Otherwise show the prompt text.
- Save an `ARWorldMap` at the end of the session.
- `memberActions` are only `retake_meter_closeups` and `truedepth_meter_scan`.

## Live guidance codes

`capture_check.missing[]` and hint `code` are closed lists. Look the code up in `prompts/views-needed-prompts.en-US.json` and show that entry's title and body. `{side}` for a `_left` or `_right` code is "to the left of your meter" or "to the right of your meter".

Capture-check codes, which are also hint codes:

- `meter_close`, `meter_oblique`, `meter_closeups_in_session`, `meter_tap`
- `door_view_meter_side`
- `panel_close`, `panel_open`, `main_disconnect`
- `keyframe_count`, `keyframe_cadence`, `tracking_quality`, `photo_count`
- `wall_left_of_meter`, `wall_right_of_meter`
- `ground_downward_left`, `ground_downward_right`

Hint-only codes:

- `frames_blurry`, `frames_dark`, `frames_overexposed`
- `wall_gap_left`, `wall_gap_right`
- `ground_not_seen_left`, `ground_not_seen_right`
- `meter_plate_unreadable`, `meter_number_confirm`, `panel_label_unreadable`

## Defaults

The app assumes these. The server behaviour when the packet differs is in the spec.

1. Originals upload during the scan. Keep local copies until the server has them.
2. iOS 16 or newer. Older phones omit `exif` and `highResolution`.
3. High-resolution stills for meter and panel close-ups, linked to a keyframe.
4. Full-accuracy location and Core Motion `xTrueNorthZVertical`.
5. Trust `CMDeviceMotion.heading`. `CLHeading` is only a cross-check.
6. Continuous pixel coordinates. `(0, 0)` is the corner of the top-left pixel.
7. Pinhole camera, no distortion.
8. Raw scene depth and confidence on every keyframe when LiDAR exists. Mesh is optional.
9. Keep earlier keyframes with their epoch id across a tracking reset.
10. Record `frozen` and `hit.target` for every tap.
11. Motion at 100 Hz. 50 Hz is acceptable.
12. Export `rawFeaturePoints` per keyframe.
13. Admission records are optional and ignored.
14. `ARFrame.timestamp` and Core Motion share device uptime.
15. Photo-only fallback attaches `stills[].gravity` and `stills[].location` when it can.
16. TrueDepth: pause, scan about 2 seconds, resume. A new epoch is fine.
17. Keep 100 Hz `imuRaw`. Drop `motion` to 50 Hz before dropping `imuRaw`.

Also: ground in the examples is 1.40 m below the session start; `{side}` flips at 0.3 m; prompt limits are 40 and 140 characters after rendering; the server accepts this minor and the previous one for 30 days.

## Capture rules

Walk slowly. At 1/60 s, stay under about 7–10 degrees per second: no pan while moving.

1. Drop a frame when `exposure.duration × |gyro rate| × f` exceeds about 4 px of blur.
2. Capture at about 1,000 lux on the wall. Warn when ISO is above about 200 or brightness is under about 3.
3. Keep the wall base and at least 1 m of ground in several views. Hold the phone level.
4. JPEG quality 0.9. HDR and sharpening need no extra rule.

Photo flow, about 40 photos on the meter side, more if the side is long or dim:

- One head-on meter close-up.
- A slow walk along the meter wall at 2–4 m, one photo per step, out to both ends and past them.
- Each adjacent wall head-on from 2–4 m, plus the corners.
- Two or three photos looking down at the ground from chest height.
- One looking up at the eave above the meter.

## Commands

From this directory, against the repo checkout:

```bash
uv run --project .. python -m ios_kit validate --schema-only examples/packets/example-packet.json
uv run --project .. python -m ios_kit check-prompts
uv run --project .. python -m ios_kit.fake_api --port 8765 --root /tmp/fake-captures --result-kind needs_more_photos
```

Set `CAPTURE_API_TOKEN` in the environment. Commands never print it. The fake accepts any non-empty bearer unless `--token` is set.

Standalone packet check, without the server:

```bash
python3.12 -m venv .venv-standalone
.venv-standalone/bin/pip install -r requirements.txt
.venv-standalone/bin/python -m packetgen validate fixtures/mini-arkit/packet.json
```

## When the contract changes

1. A spec PR edits the store spec, then `server/contracts`, then `make ios-kit` refreshes this folder.
2. `formatVersion` stays `0.x`. Wording, examples, and new open-vocabulary values do not bump it.
3. A new required field, a renamed field, a new closed-enum value, or a changed unit bumps the minor (`0.4` to `0.5`).
4. A deprecated field keeps one minor, then leaves. The schema description starts `DEPRECATED (0.5):`.
5. The server accepts the current minor and the previous one for 30 days after the kit that introduced the new one.
6. Release tag `ios-kit-v<VERSION>`. `make ios-kit` writes `dist/ios-kit-<VERSION>.zip`.
