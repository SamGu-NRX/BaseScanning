# How to check the retake thresholds on an iPhone

The thresholds in [PORTING.md](PORTING.md) come from processed Commons JPEGs, not from the app's camera. Sharpening and noise reduction change sharpness values, so the focus threshold may move. Test both thresholds on one real meter before relying on them.

1. Take the photos below with the app's capture path, or the iPhone camera if the app is not ready.
2. Copy them into one folder. JPEG and HEIC both work. The command measures each photo's own pixels and never re-encodes them, because a JPEG round trip can lift sharpness across the threshold.
3. Run the command from `experiments/meter-closeup` in a checkout of this repository. Point PHOTO_DIR at the folder from step 2:

	```sh
	uv run python -m meter_eval.fieldtest PHOTO_DIR --number "<number as printed>"
	```

The command prints each photo's read result, the candidate's rank and the check values. It then counts retakes asked for photos that read, and photos accepted that did not. Like the app, its checks use the ranking's top candidate; `--number` only scores the outcome and is never written to disk.

| Threshold | Photos to take | The threshold holds if |
|---|---|---|
| Out of focus | three with focus locked on a distant background, one from 5 cm, three sharp | blurred photos score 6.68 or less and fail to read, and sharp photos score far above |
| Number too small | square to the meter from 15, 30, 50, 80 and 120 cm | photos whose top candidate is taller than 33.9 px read, with the number in the top three |
| Glare (no check) | a second phone's flashlight beside the camera at 0°, 20° and 45°, or direct sun on the number | it records how often glare alone breaks reading |
| Cut off (no check) | the frame edge cutting a quarter digit, half a digit and a whole digit | it records whether a cut number ever becomes a candidate |
| Hand shake (no check) | three in shade while sweeping the phone sideways | it records how often shake alone breaks reading; if it does, gate capture on gyroscope motion |
