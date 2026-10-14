# Evals on real data

Saved photo-geometry and pose-prior summaries predate the edge-cohort, fixed-camera-center, and shared-pose-noise fixes. They need dataset and model regeneration before use. The old Map3D report's CoverageMap comparison lacks a validated matching baseline and is no longer generated.

**Question.** Can a phone's photos and AR tracking measure a house wall well enough for placement: about 4 in at p90 on 1 to 3 m spans? And does the app's coverage map claim only wall it saw (false-observed at most 0.5 ft)? Each bar was fixed before its run.

**Run.** `cd experiments/evals && uv sync && make test`; each result has its own target (`make drift`, `make recon`, `make coverage`, …). Model runs need an Apple-silicon Mac, and the app-code checks need macOS 26 with Swift 6.2. [METHODS.md](METHODS.md) lists every target, prerequisite and dataset sha256.

**Results.**
- **Tracking:** a 2018 iPhone's position error, p90 18.6 to 132.6 in over 3 to 30 ft, is 2 to 3 times the server's allowance. An iPhone 14 Pro Max stays inside it: 8.6, 13.4 and 18.5 in at 10, 20 and 30 ft.
- **Depth:** learned depth is 4% to 12% off scale. Rescaled with AR poses, walls reach p90 5.0 in [3.7, 6.9] at a simulated 2% pose error, which misses 4 in.
- **Coverage:** `CoverageMap` claimed 1.1 ft that no photo saw, and fails. Map3D's LiDAR path claimed none on ideal depth, and passes.

**Changed.** The server kept its drift allowance. Map3D replaces `CoverageMap`. Occlusion needs a depth test. The field kit scores the phone against a tape.
