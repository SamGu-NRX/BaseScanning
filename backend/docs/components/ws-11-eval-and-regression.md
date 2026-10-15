# WS-11 evaluation, replay, and regression gates

PR 1 (`ws-11/replay-and-gates`) vendors the store harness into `server/eval/`, fixes the late harness defects, rescores the stored scenes, and adds the replay client plus `gates.yaml`. WS-00's contracts are on `main`. Rows that need WS-09 or WS-04 SKIP with `not_merged:ws-09` or `not_merged:ws-04`.

## Harness fixes (§4 step 14 a–f)

- Viewing angles are clamped to `[0, 90]` before they are written (`clamp_incidence_deg`). 90.2° becomes 90.0.
- Spans are ordered before a scene is returned, including door rectangles and coverage bands.
- GT taps use the reconstruction camera's own intrinsics and image size. A width-ratio rescale of a DSLR pixel is rejected when the sizes differ. The regression is a centre crop that keeps focal length and moves the principal point; the click matches that crop and differs from the width ratio by more than 20 px.
- The delivery-area meter sits on a jamb past the garage opening. Meter rays use a 4 m along-track slack and, if they still miss, snap to a triangulated meter within 1.5 m. Opening rays stay at 0.5 m.
- Collinear fragments merge at 20° (still under the 30° corner cut) or when a kink's endpoints are within the merge gap. A parallel sheet 0.02–0.45 m behind the wall, overlapping the shorter sheet, is dropped and the kept wall is not refit onto the glass.
- `run-c1` prints a deprecation line and exits 2. `run-hs11` is the PR #11 solver. `score-cloud --max-points` defaults to 400000.
- `key_distances.by_wall` and `headline.pass_recall` are on the scorer output. `pass_recall` is `None` when nothing labelled `pass` was scored.

The A10 delivery COLMAP artifact on disk is a 1.4 KB status JSON whose `score_error` is `RuntimeError: meter clicks hit no wall`. It is not a point cloud, so the regression is the synthetic jamb. The dense-wall regression is a synthetic 10° bend plus a glass sheet: one `w1`, and that wall's coverage band has `views >= 1`. Decidability versus a sparse twin needs WS-09, so that comparison is not claimed here.

## Re-baseline

`score_scene(gt, recon, visibility=True)` on the store scenes, with the laser PLY present. Overall inches match the previous table. `false_observed_claims` is empty on every remeasured row. `gates.yaml` records `harness_sha` `73ad23f37116d6549dbf110a5d6a02709f9b0a48`, and the tables live at `data/fixtures/reference-scores/<site>/rebaseline-73ad23f37116d6549dbf110a5d6a02709f9b0a48.json`. A1 dense extractor v2 still declares `points.ply`, which is not in the store, so that one row was not remeasured. A fresh `--oracle` extract on `sparse/best` (openings through the recon `K`) keeps a single wall and is recorded beside the stored colmap-oracle row; the gate number stays the stored scene.

| recon | prior overall n / med / p90 / max (in) | this harness | by_wall.w1 p90 (in) |
|---|---|---|---|
| facade GT vs GT | 55 KDs at 0 (README) | scored 55, overall n 53, 0 / 0 / 0, scale 0 | 0 (n 34) |
| electro GT vs GT | — | scored 72, overall n 72, 0 / 0 / 0 | 0 (n 11) |
| delivery GT vs GT | — | scored 75, overall n 74, 0 / 0 / 0 | 0 (n 14) |
| facade colmap-oracle | 30, 0.63 / 1.94 / 2.67, scale −0.45% | n 30, 0.63 / 1.94 / 2.67, scale −0.447 | 1.94 (n 30) |
| facade colmap-nonlidar | 31, 1.23 / 5.09 / 8.72, scale −1.77% | n 31, 1.23 / 5.09 / 8.72, scale −1.773 | 5.09 (n 31) |
| A1 incremental sparse | stored geomscore n 36, 0.60 / 24.87 / 172.13 | n 35, 0.60 / 6.32 / 172.13, scale 0.197 | 2.97 (n 30) |
| A1 incremental dense | stored n 31, 1.15 / 34.08 / 741.13 | n 31, 1.15 / 34.08 / 741.13, scale 0.525 | 95.02 (n 24) |
| A1 dense extractor v2 | stored n 27, 0.50 / 3.07 / 16.65 | n 27, 0.50 / 3.07 / 16.65, scale 0.378 | 1.62 (n 25) |

A7 §5.5 reported A1 facade non-LiDAR at 0.84 / 2.90 / 5.20 published versus 1.73 / 4.11 / 4.76 on that day's harness. No A7 scene or cloud is in the fixture store, so that triple was not re-extracted. The stored A7 summary's facade/A1 rows are different numbers (`dslr_ref` geom 1.59 / 2.91 / 3.96, `base` 1.43 / 3.06 / 4.67) and do not contain 1.73 / 4.11. The stored `colmap-nonlidar` scene still scores 1.23 / 5.09 / 8.72, and `by_wall.w1.p90_in` is 5.09. The brief's bars (3.0 LiDAR, 5.5 non-LiDAR, 6.0 electro observed) are confirmed and left unchanged. Electro has no stored non-LiDAR scene here.

The committed repo snapshot `eval/results/eth3d-facade/gt-laser/scene.json` is an older scene: 49 scored KDs, not 55. The 55-KD scene lives in the store. This PR does not overwrite `eval/`.

## What else this PR ships

- `server/eval/replay.py`: upload order, PUT (including multipart), commit batches, one mismatch re-upload, retries, early finalize, event polling, and result download. `packet.json` is loaded with `server.contracts.packet.Packet`. An invalid document exits 4. An unreachable API exits 2. A fixture tar nested under `<site>/` resolves.
- `server/eval/gates.py` + `gates.yaml`: missing inputs are SKIP. `--strict` fails on SKIP. Exit 1 on any FAIL. An evidence-backed pass is not an unsafe pass.
- `server/eval/adapters/criteria.py`: calls `evaluate_scene` through `pipeline_adapters` or raises `not_merged:ws-09`.
- `make gates-offline` runs the fixture table. `make e2e` exits 2 when it replays nothing. `make lamar` prints `not_merged:ws-04` when `run_poses` is absent.
- `scripts/safety_scan.py` refuses secrets, the prompt-dump names, tars, `.7z`, `.p8`, `.env`, and files over 5 MB on files this branch adds or changes versus `origin/main`.

## Defaults chosen here

- `SCHEMA_DIR` is `server/contracts/schemas`. The duplicate copies under `server/eval/schemas` are gone; those three files matched the contract schemas byte for byte.
- `merge_angle_deg` 20, `meter_along_slack_m` 4.0, `meter_snap_m` 1.5, `duplicate_offset_m` 0.45.
- Positive-pass overlap uses a 1 mm tolerance so the rounded window `[-3.891, -3.383]` contains the re-derived endpoints `-3.891369…` and `-3.383268…`.
- `run-c1` exits 2. `pass_recall is None` is SKIP.
- A declared laser, recon, or occluders PLY that is missing raises `MissingOccluderError`, even when another occluders file is on disk (SD-61). `--allow-missing-occluders` marks the result `provisional: true`, and the gates reject that.
- Commit batches follow this brief (≤ 50 every 2 s). WS-01's interim client text says every 10 files or 2 s.
- Shared still/keyframe image paths are registered once, as `keyframe`.
- The e2e workflow skips when `CAPTURE_API_URL` or `CAPTURE_API_TOKEN` is unset. `make e2e` exits 2 when it replays nothing, including when the URL is set.
- `start_agreement_min` is set for the facade demo only. `site_agreement_min` is 1.0; criteria rows SKIP with `not_merged:ws-09` until that engine imports.
- Stage adapters import `evaluate_scene` and `run_poses`. There are no stub modules in `server/criteria` or `server/poses`.
- `server/eval/**` stays out of `ruff format`, matching WS-00. Files WS-00 vendored are kept byte-for-byte except the scoring fixes (taps, extract, coverage, geomscore, sceneio, verdictscore, cli).

## Inconsistencies

- The final-pass report names the same harness items the brief's §4 step 14 (c)–(f) already states. This branch follows that brief text: the tap cameras use the reconstruction `K` and size, the re-baseline is a dedicated commit, and the delivery and dense-wall cases are regression-tested.
- WS-00 contracts (`66ee387`) and the vendored harness (`5525a2d`) are on `main`. WS-01 and WS-09 are not, so gravel, decidability, coverage, veto, packet geometry, and live replay stay pending. The 55-KD self-check used the store scene, not a repo fixture.
- `eval/` in the repo is an older partial harness. Scoring uses `server/eval/`, WS-00's vendored copy plus the step 14 fixes.
- The crop regression is a `score_cloud` GT-vs-GT self-check: a centre crop with the recon `K` matches the full frame, and the GT `K` on that crop misses the wall.
- `ci.yml` is not owned here, so `gates-offline` is a Make target. Pull-request CI still runs `pytest`, including `server/eval/tests`.
- The unpatched C reference verdict has no `ground_rule` field. It is used as the `gt-laser` / `footprint3in` / `manual_review` row because that is the decision the brief assigns to `eth3d-facade__gt-laser__C.json`.
