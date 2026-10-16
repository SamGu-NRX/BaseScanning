# Reuse from house-scanning PRs (server side)

What our server pipeline (packet in → 3D / point cloud → scene understanding → Base criteria) can take from [SamGu-NRX/house-scanning](https://github.com/SamGu-NRX/house-scanning), organized by the tracks in [server-pipeline-plan.md](server-pipeline-plan.md). Every item was checked against the PR branch code and, where cheap, run on a CPU-only Linux VM (4 cores, 15 GB RAM, no GPU) on 2026-09-26.

Evidence files: `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/handoffs/harvest-evidence/` (called `evidence/` below).

## Licences (apply to everything below)

- **Code: no licence.** No LICENSE file on `main` or any PR branch, so treat it as team-internal reuse only unless the authors add one.
- **Datasets are non-commercial, so use them for evaluation only.** ETH3D is CC BY-NC-SA 4.0, ADVIO is CC BY-NC 4.0, and MARViN states no licence.
- **Model checkpoints are fine to use.** MoGe-2 is MIT; DA3METRIC-LARGE and `facebook/map-anything-apache` are Apache-2.0.
- **Meter photos (#16):** Wikimedia Commons, with a licence per photo (CC BY-SA / CC0 / PD / CC BY) listed in `manifest.csv`.

## Branch tips checked

| PR | Branch | Head |
| --- | --- | --- |
| #4 | `t3/scoring-harness` | `77ce830` |
| #7 | `t3/measure-lab` | `45770a3` |
| #9 | `codex/capture-contract-probe` | `4b6c430` |
| #11 | `t3/server` | `cd0da8b` |
| #12 | `t3/evals` | `24793c3` |
| #13 | `t3/verification` | `52aa87f` |
| #16 | `t3/meter-closeup` | `b199613` |
| #17 | `t3/panel-label` | `cf6e6e1` |
| #18 | `codex/research-findings` | `bd4a7d3` |

Note on #11 at `cd0da8b`: `rules.yaml` now defaults to a `demo` policy with `auto_approve: true`. Setting `HOUSESCAN_POLICY=strict` restores the old behaviour. Private rules can be supplied through `HOUSESCAN_PRIVATE_RULES_B64`.

Tooling: Python projects use uv `>=0.11.15,<0.12` (`pip install --user 'uv>=0.11.15,<0.12'`).

---

## Top items to reuse

1. **#12 ETH3D reconstruction harness and dataset loaders.** Scores any depth output against laser scans. Reproduced on CPU.
2. **#12 model runners (MoGe-2, DA3METRIC-LARGE, MapAnything).** Pinned, verified checkpoints with a uniform `.npz` output; runs on CPU or CUDA.
3. **#11 rules engine plus `scene.schema.json`.** The criteria-level scene format and a solver for 13 checks. Correct decision on real geometry.
4. **#13 ETH3D facade scene builder plus `resultcheck.py`.** The only real-geometry criteria test scene, plus result invariants.
5. **#12 coverage truth.** Laser-scan visibility per photo, plus the finding that the app's coverage map ignores occlusion.
6. **Measure Lab session v2 as the packet prototype.** Converter and checkers: #12 `replay.py`, #13 `replaycheck.py`, #9 `capture_contract.py`.
7. **#16 meter set with `locate.py` / `quality.py`, run with a server-side OCR.** RapidOCR on CPU matches Apple Vision on class labels.
8. **#4 scoring method.** Headline metric is unsafe passes. Its rules format needs an adapter before it can read #11 output.

---

## Phase 0: foundations (datasets, packet format, scoring, scene format)

### 0.1 Dataset loaders and downloaders

- **Where:** PR #12, `t3/evals`, `experiments/evals/evals/{datasets,eth3d,advio,modern_arkit}.py`
- **What it does:** Downloads with sha256 verification into `~/house-scanning-data/evals/`. It reads:
  - ETH3D: COLMAP poses, laser scans, occlusion splats, masks.
  - ADVIO: ARKit, ARCore, ground truth, GPS, calibration.
  - MARViN: ARKit poses, GPS.
- **Deps:** numpy, opencv-headless, scipy, py7zr, gdown. CPU only.
- **Verified: ran.**
  - ETH3D facade and electro (about 2.4 GB) downloaded in about 3 min.
  - ADVIO 20–23 and MARViN downloaded too.
  - Commands: `uv run python -m evals.datasets eth3d`, `make drift`, `make modern-arkit`.
- **Maps to:** Phase 0 datasets, and ground truth for Tracks A and C.
- **Gap:** no TUM loader exists in any PR. #18 acquired TUM but committed no code.

### 0.2 Capture/recording format: Measure Lab session v2 (packet prototype)

- **Where:** PR #7, `t3/measure-lab`
  - `experiments/measure-lab/README.md`, section "Session format"
  - `MeasureLab/Session/SessionManifest.swift`
- **What it is:** a zip of `session.json` plus unrotated landscape JPEG keyframes. It optionally includes LiDAR `*.depth.f32` / `*.confidence.u8`.
  - Per keyframe: `pose` (ARKit camera-to-world, column-major), `intrinsics [fx,fy,cx,cy]`, `timestamp`, `tracking`.
  - Taps with rays and points, plus walls, measurements, refusals and gates.
- **Status in the team:** it is the team's replay contract ("C3"), read by #10, #12 and #13. It is the closest existing prototype of our packet.
- **Gap:** it has no GPS, compass or IMU streams. Feed this into [capture-packet-spec.md](capture-packet-spec.md).
- **Python producers and consumers:**
  - **#12 `evals/replay.py` + `evals/camera.py`** turn real ADVIO iPhone video plus ARKit poses into a session v2 zip.
    - Ran: `make replay` produced 79 keyframes (16.9 MB) in 11 s.
    - Epipolar check: median 3.0 px.
    - Evidence: `evidence/advio-replay-build.log`.
  - **#13 `verification/hsverify/replaycheck.py`** validates the format and scores poses against `ground_truth.json`.
    - Ran: format OK.
    - ARKit/truth path ratio is 0.638 on this walk. ADVIO walk 20's reference scale is itself wrong (see #12 README §1).
    - Error anchored at the first keyframe: 1.05 m after 3 ft walked, 4.27 m after 30 ft.
    - After removing scale, best-fit error median is 0.32 m.
    - Evidence: `evidence/advio-replaycheck.txt`.
  - **#9 `experiments/capture-contract/capture_contract.py`** (branch `codex/capture-contract-probe`, stdlib only) validates poses and taps. It converts ARKit camera-to-world to OpenCV convention, which is the MapAnything input shape.
    - Ran: 14/14 tests; converted the synthetic fixture.
    - No real data.
- **Deps:** none beyond Python, plus numpy/opencv for #12. CPU only.
- **Maps to:** Phase 0 packet format and replay; Track A pose input.

### 0.3 Scene format (criteria level)

- **Where:** PR #11, `t3/server`, `server/schemas/scene.schema.json` (12 KB) and `server/schemas/result.schema.json`
- **What it holds** (units are feet):
  - A meter.
  - A multi-wall chain, left to right, with corners.
  - Objects: window, door, garage_door, ac, gas_meter, elec_box, vent, downspout, pool. Each has a source (vlm/tap/tape), an optional plan footprint, and `plus_minus_ft`.
  - Ground polygons by surface type, overheads, and facing gaps.
  - `coverage`: bands (wall/ground/overhead/facing) with spans and `out_ft`, plus end states `limit`/`unexplored`.
  - Keyframes (pose, intrinsics, img), stills, GPS and heading.
- **Assessment:** a workable **output format for every Track A path** at the criteria level. It is a 2.5D unrolled wall chain, not a 3D volume.
- **Gaps against the plan:**
  - No fence, meter can, panel or deck-obstacle types.
  - Facing gaps and overheads are single values per span.
  - No voxels, so approach C2 would need its own format.
- **Deps:** jsonschema. CPU only.
- **Verified:** #13's builder produced a valid real-geometry scene against this schema (see 0.4 and C.2).
- **Maps to:** the hand-off between Track A/B and Track C (see [criteria-spec.md](criteria-spec.md)).

### 0.4 Scoring harness

- **Where:** PR #4, `t3/scoring-harness`, `experiments/scoring/` (`score` CLI)
- **What it does:** scores each pipeline's distances and verdicts against a tape survey. Metrics:
  - **Unsafe passes** (the headline metric).
  - Missed reviews and over-caution.
  - Distance error, with the scale reference excluded.
  - Provenance hashes.
- **Deps:** Python, CPU only.
- **Verified: ran.**
  - 223 tests pass.
  - The CLI scores the synthetic fixtures: `uv run score --rules fixtures/rules.json --truth fixtures/truth/synthetic-01.json --results fixtures/results/*.json --out DIR`.
  - Only synthetic data has ever gone through it.
- **Gap:** its rules format is flat and does not match #11's nested `rules.yaml`. No adapter exists.
- **Maps to:** the plan's verdict scorer (Phase 0 and Track C evaluation). Reuse the method and metrics; expect to write the adapter.

### 0.5 Field kit (tape-measured house)

- **Where:** PR #12, `evals/field.py`, `field/FIELD_SHEET.md`, plus the templates.
- **What it does:** reruns Measure Lab's wall and measurement formulas in Python and scores AR taps against a tape survey.
- **Deps:** CPU only.
- **Verified: not rerun here.** Only a synthetic dry run exists.
- **Maps to:** Phase 3 real-house ground truth.

---

## Track A: reconstruction

### A.1 ETH3D wall-reconstruction accuracy harness

- **Where:** PR #12, `evals/recon.py`, `evals/pairs.py`, `evals/eth3d.py`
- **What it does:** measures point-pair length error against the laser scan, which needs no alignment step. It includes:
  - Cohorts: surface interior, vertical walls, depth edges.
  - Fixed evaluation sets per seed view.
  - A tape-calibrated variant.
  - The laser scan rendered as depth, which gives the evaluation floor.
- **Deps:** CPU only for scoring.
- **Verified: reproduced on CPU.**
  - `recon prepare`: 4 min 19 s.
  - MoGe-2 on the 45 electro photos: 8 min (about 9 s per photo).
  - `recon score`: 1 min 39 s.
  - **Electro rows match the committed `results/eth3d_recon.md` within ±0.1 in and ±0.1%. Floor rows are identical.**
  - Evidence: `evidence/eth3d-recon-electro-moge2-cpu-rerun.md`.
- **Maps to:** the ready-made Track A geometry scorer. Any path that writes per-view depth `.npz`, or a reconstruction in model frame, can be scored.

### A.2 Model runners

- **Where:** PR #12, `experiments/evals/models/`: `moge2.py`, `da3metric.py`, `map_anything.py`, `run.py`, `run_groups.py`
- **What it does:**
  - Pinned model commits and checkpoints with sha256 checks.
  - A uniform `.npz` output (depth, valid, intrinsics, cam_to_world).
  - `--device cpu|cuda|mps`.
  - MapAnything accepts poses and intrinsics as priors (Apache checkpoint, about 4.9 GB, 1.2 B params).
- **Deps:** torch (2.14.0+cu130 resolved). A GPU is needed for practical speed on real volumes, but not for correctness.
- **Verified:**
  - `uv sync --project models` OK.
  - 9/9 runner tests pass.
  - MoGe-2 ran on CPU.
  - **MapAnything and DA3METRIC-LARGE were not run here** (too heavy for this CPU VM). Their committed numbers stand.
- **Maps to:** Track A candidate engines, for monocular metric depth (A1-style) and multi-view with pose priors.

### A.3 Pose-prior triangulation rescale

- **Where:** PR #12, `evals/triangulate.py`, `evals/ar_poses.py`, `evals/pose_priors.py`
- **What it does:** rescales per-photo MoGe-2 depth to SIFT points triangulated with AR-like poses, then fuses the results.
  - `ar_poses.py` degrades true poses using the ARKit scale and noise measured on ADVIO. This is the same noise idea the packet generator uses.
- **Deps:** CPU (opencv SIFT).
- **Verified: not rerun.** The inputs it depends on (the ETH3D loaders, `recon prepare`, MoGe-2 on CPU) were reproduced in A.1. The full rerun is about 35 min of CPU and was skipped as not needed yet (`make pose-priors` after `make recon`). Committed numbers are in the baselines table.
- **Maps to:** Track A, using phone poses to fix monocular scale.

### A.4 Coverage truth (laser visibility)

- **Where:** PR #12, `evals/coverage.py` (`photo_truth`, `band_samples`, `evaluate_band`) and `evals/coverage_options.py`
- **What it does:**
  - Computes what each photo really saw, from a laser-scan z-buffer with occlusion splats and masks.
  - Includes a Python replica of the app's `CoverageMap` view gates (`app_rows`), which reproduces all 337 app sightings.
  - Includes a depth-test occlusion option.
- **Deps:** CPU only for the truth side.
  - The comparison against the unmodified app uses `coverage_driver/` (Swift plus macOS 26), so it **cannot run on Linux**.
- **Verified: not rerun.**
- **Maps to:** the plan's "completeness of the regions the criteria need" and hallucination scoring (Track A/B evaluation), and the `coverage` block in 0.3.

### A.5 Photo-selection result

- **Where:** PR #12, `evals/frames.py`
- **Finding:** distance to the wall matters; viewing angle barely does (numbers in the baselines table).
- **Verified: not rerun.**
- **Maps to:** Track A frame selection, and guidance for the iOS capture team.

---

## Track B: scene understanding

### B.1 Meter OCR evaluation set and tooling

- **Where:** PR #16, `t3/meter-closeup`, `experiments/meter-closeup/`
- **What it contains:**
  - `manifest.csv`: 83 Commons photos (80 usable) with URLs and per-photo licences.
    - Meter numbers are stored as **HMAC digests**. Scoring number reads needs Sam's key (`METER_HMAC_KEY` or `~/house-scanning-data/meter-closeup/hmac.key`).
    - Class labels and current ratings are plaintext.
  - `locate.py`: number ranking. Works with any OCR engine; takes `{lines:[{text, box}], barcodes:[...]}`.
  - `quality.py`: retake checks (Laplacian focus ≤ 6.63, text height ≤ 33.9 px).
  - `match.py`: `class_read`, `number_read`.
  - The Swift `meterocr/` (Apple Vision) is macOS only.
- **Deps:** Python, CPU only. A server OCR is needed; RapidOCR (PP-OCR ONNX) works.
- **Verified: ran.**
  - 44 tests pass.
  - `make images` downloaded all 83 photos.
  - **New check:** a server-side OCR (RapidOCR, CPU, about 1.1 s per photo) through #16's own `class_read`:
    - US class labels: 20/22 (Apple Vision accurate: 20/22).
    - Current ratings: 54/57 (Apple Vision: 57/57).
  - Evidence: `evidence/rapid_meter.py`, `evidence/rapid-meter-reads.jsonl`.
- **Maps to:** Track B meter identification and reading.

### B.2 Equipment association (low priority)

- **Where:** PR #18, `codex/research-findings`, `experiments/research-handoff/equipment-association/association.py`
- **What it does:** an event reducer for candidates, labels, tracks and review tickets.
- **Deps:** Python, CPU only.
- **Verified:** 11/11 tests pass; synthetic data only.
- **Maps to:** marginal for B1 multi-view object identity.

### B.3 Panel labels (spec only)

- **Where:** PR #17, `t3/panel-label`
- **What's there:** `sources.py` (a Commons/Openverse photo finder) and a field-photo spec.
- **Assessment:** no data (only 7 usable US panels). Pass the photo-capture spec to the iOS team; nothing else is reusable.

---

## Track C: criteria

### C.1 Rules engine

- **Where:** PR #11, `t3/server`, `server/`: `solver.py` (1.4k lines), `scene.py`, `rules.py`, `rules.yaml`, `siteplan.py`, `api.py`
- **What it does:**
  - Computes 13 checks with a three-valued margin rule (pass / fail / UNSURE).
  - Treats anything unseen as UNSURE.
  - Returns `missing_evidence`, the views still needed.
  - Merges private rules over the public ones.
- **Deps:** fastapi, shapely, jsonschema, pyyaml. CPU only.
- **Verified: ran and reproduced.**
  - 273 tests pass.
  - **Solved the real-geometry ETH3D facade scene** (from C.2):
    - Decision: `manual_review`, in 0.38 s.
    - It flags the spot on unseen wall w2.
    - Window-5 gap: 2.54 ± 0.53 ft against a 3 ft requirement, so UNSURE.
    - This matches the hand-derived expectation under both the `demo` and `strict` policies.
  - To call the solver directly without HTTP: `evidence/solve_scene.py` (`PYTHONPATH=server uv run python solve_scene.py scene.json`).
  - HTTP: `uv run uvicorn api:app --port 8765`.
- **Caveats:**
  - The three unresolved Base placement profiles are only public placeholders: drive 5 ft, pool 10 ft, reach 15 ft, headroom 6.5 ft.
  - The `demo` policy auto-decides on these placeholders.
  - `drift_per_ft` 0.16 comes from ADVIO ARKit-vs-ARCore disagreement, not error against truth. Prefer `internal/packet-generator/calibration.md` as the noise source.
- **Maps to:** Track C evaluator (see [criteria-spec.md](criteria-spec.md)).

### C.2 Independent verification and the real-geometry test scene

- **Where:** PR #13, `t3/verification`, `verification/`:
  - `hsverify/resultcheck.py`: checks the margin rule, coverage and bookkeeping invariants on any result.
  - `hsverify/e2e.py`
  - `scenes/eth3d-facade/build_scene.py`, `annotations.json`, `case.json`
- **What the builder produces:** the only real-geometry criteria test scene.
  - Walls fitted to the laser scan with RANSAC; wall fits 0.02–0.04 ft RMS.
  - Ground plane with 1.86 M inliers at 0.97 cm RMS.
  - 6 hand-clicked openings, ray-cast through the COLMAP poses.
  - An invented meter.
- **Deps:** Python, CPU only. Building the scene takes 26 s.
- **Verified: ran.**
  - Fit stats: `evidence/eth3d-facade-build-report.json`.
  - e2e against local #11 `cd0da8b`:
    - **`strict` policy: 44/44 pass.**
    - **`demo` policy: 41/44 pass.** The 3 failures (`d-reach-drift-left/right`, `g06-regulator-footprint`) are cases that assume nothing is auto-passed; their per-position sweep checks still pass.
  - Evidence: `evidence/e2e-strict-policy-cd0da8b.md`, `evidence/e2e-demo-policy-cd0da8b.md`.
  - The 43 synthetic cases are a regression gate only, not evidence.
- **Maps to:** Track C evaluation. `resultcheck.py` works on any criteria engine's output; the facade scene is the first real-geometry regression case.

---

## Established baselines

Each number is labelled with its data source and whether we reproduced it.

| Number | Source | Status | Caveats |
| --- | --- | --- | --- |
| MoGe-2 single photo at its own scale, electro, wall ≤ 6 m, 1–3 m spans: median 6.8 in, p90 19.4 in; scale +3.5% | #12, ETH3D laser | **Reproduced on CPU** | Two institutional buildings shot with a 24 MP DSLR, not houses or phones |
| One photo plus one taped distance: surfaces 1.6 / 9.6 in, walls 1.8 / 15.1 in, edges 3.7 / 26.9 in (median / p90) | #12, ETH3D | **Reproduced** | The tape length is simulated exactly from the scan |
| Evaluation floor (scan rendered as depth): 0.5 / 1.6 in; walls 0.4 / 1.5 in | #12, ETH3D | **Reproduced** | — |
| Metric scale bias (≤ 6 m): DA3METRIC-LARGE −7 to −11%; MoGe-2 +4 to +12% | #12, committed | DA3 not rerun | — |
| Triangulation rescale, 8 photos, wall p90: 2.8 in with exact poses, 5.0 in at 2% pose scale error, 10.8 in with 2018-phone error. Edges ≥ 8 in everywhere; 6.3 in with realistic matching even with exact poses | #12, committed (hybrid: real photos, simulated pose noise) | Not rerun (skipped, ~35 min CPU) | Pose noise is simulated; `modern_assumed` (2% short) is a guess with no data |
| MapAnything with poses at 392 px: electro scale +17% → +3%; facade still −12 to −16%; 10–20% of pairs unpredicted | #12, committed | Not rerun (needs a GPU) | A 4.05 GB memory cap forced 392 px |
| Photos with the wall within 6 m: single-photo p90 11.4 → 6.9 in (1–3 m spans), 39.0 → 14.8 in (3–10 m spans) | #12, committed | Not rerun | Head-on views (< 26°) untested |
| ARKit on a 2018 iPhone 6s, position error p90: 18.6 / 55.7 / 94.6 / 132.6 in after 3 / 10 / 20 / 30 ft walked; scale 5–17% short; 1 of 4 walks lost tracking | #12, ADVIO | **Reproduced exactly** | Noise in the reference inflates these |
| ARKit on an iPhone 14 Pro Max (LiDAR): scale within 2% on 30/35 walks; position p90 8.6 / 13.4 / 18.5 in at 10 / 20 / 30 ft (trusted walks); within-site scale spread 0.3–0.8% | #12, MARViN | **Reproduced exactly** | COLMAP reference with unverifiable scale; the phone has LiDAR |
| Coverage map false "observed": 1.1 ft of a 19.3 ft wall (6%), all from occlusion. A depth test with 0.4 m relief cuts it to 0.1 ft; the spacing/angle rules lose 6–8.5 ft | #12, ETH3D electro | Not rerun (needs macOS) | One wall; ground band untested |
| Hand-clicked photo edges on COLMAP poses vs laser: windows within 0.01–0.37 ft (median ≈ 0.13 ft); door 0.64 ft (shared recess bias) | #13, ETH3D facade | Built and checked | Perfect poses; manual clicks |
| Solver on real geometry: correct `manual_review` in 0.38 s | #11 + #13 | **Reproduced** | Invented meter; only the opening, route and coverage checks are exercised |
| Meter number read, Apple Vision accurate: 71/73 (97%); Vision fast: 75%; top-3 candidates contain the number 28/33 (85%); barcode confirms 20/24 | #16, committed | Not rerun (needs the HMAC key and macOS) | AI-made labels; Commons close-ups are optimistic |
| US class label: Vision 20/22, **RapidOCR (server, CPU) 20/22**. Current rating: Vision 57/57, RapidOCR 54/57 | #16 committed + **new server-side run** | **New** | Only 22 US photos |
| ARKit drift on a real ADVIO replay (walk 20): 1.05 m after 3 ft, 4.27 m after 30 ft (anchored at first keyframe); median 0.32 m after removing scale | #12 replay + #13 `replaycheck` | **Ran** | Walk 20's reference scale is itself wrong (path ratio 0.638) |
| DA3-Small: adding a 4th view revises earlier depth by 3–6.5% (raw) | #18, rerun by the #18 reviewer | Rerun by reviewer | Measures self-consistency, not accuracy |
| COLMAP on 7 casual exterior phone photos: 5/7 registered, 339 points, 0.25 px reprojection | #18 reviewer's check | Ran by reviewer | Sparse and unscaled |

---

## Open issues

- **Base placement profile is unresolved (A/B/C).** #11's public rules are placeholders, and the `demo` policy auto-decides on them. Any pass or reject is therefore under placeholder values.
- **Scoring meter-number reads needs Sam's HMAC key.** Without it, only class labels and ratings can be scored on the #16 set.
- **The coverage comparison against the app is macOS-only.** The Python truth side runs on Linux.
- **There is no TUM loader** in any PR.
- **#4's rules format does not match #11's `rules.yaml`.** An adapter is needed before #4 can score solver output.
- **`drift_per_ft` 0.16 in #11 is ARKit-vs-ARCore disagreement,** not error against truth.
- **There is no repo licence, and the datasets are non-commercial.**

## Discarded

- #10 iOS app UI, overlays, `ScanEngine`, accessibility tests: iOS team's scope (keep only the fact that `CoverageMap` lacks occlusion).
- #7 Measure Lab Swift app and geometry tests: iOS side; its session format is kept (0.2).
- #8 TestFlight workflow; #2 toolchains; #5 landing submodule; #1/#3/#6/#14/#19 docs: no server code or real results.
- #15 web review page: parked; synthetic samples only.
- #18 capture-admission ledger and Swift patches (phone side; stale vs #10); its DA3/COLMAP/MoGe runs (superseded by #12, no ground truth); TUM acquisition (no code committed).
- #17 panel-label harness: no data; keep only the field-photo spec (B.3).
- #11/#13/#10/#4 synthetic golden, e2e and unit suites: regression gates, not evidence.
