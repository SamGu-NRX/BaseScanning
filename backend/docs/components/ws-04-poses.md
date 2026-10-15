# WS-04 poses

## Why not housepipe

The Modal scaffold's SfM path is reference only. On the facade photo packets it does not meet the opening tolerance that the A1/A6 tight-prior recipe does.

Pinned image `im-IJBZcHC7t19iFxc44I4MDA` (app `ap-wTcMNueC1TE7AloYArKQaE`, from `internal/modal-pipeline/benchmark.md`). That image is Python 3.11.5, COLMAP 3.11.1 commit `682ea9a`, pycolmap 3.11.1. The facade runs below used it.

| Run | Key-distance median | p90 | Camera RMS |
|---|---:|---:|---:|
| L4 facade 60 | 26.2 in | 38.7 in | 0.83 m |
| L4 facade 70 | 25.2 in | 47.1 in | 0.62 m |
| A10G facade 70 | 23.9 in | 45.7 in | 0.19 m |
| L4 electro LiDAR | 1.38 in | 20.4 in | 0.048 m |

A1/A6 with σ 0.03 m and no robust loss measures about 0.9–1.9 in on openings (A6 real-phone openings p90 2.8 / 4.1 / 3.9 / 4.2 / 3.9 in). The scaffold's 24–26 in median and 39–47 in p90 are a different recipe. Four suspects, all present in that scaffold and none in this stage:

1. Matching. Housepipe pairs 20 neighbours inside 12 m with sequential overlap 2. A6 uses 40 neighbours, a 20 m radius, and sequential overlap 10.
2. Largest epoch only. `housepipe/pipeline.py` drops every epoch except the biggest. This stage maps each epoch that has enough frames.
3. The 3.11.1 prior-flag hack. That wheel has no `PosePrior.corr_data_id` and no `IncrementalPipelineOptions.use_prior_position`. The benchmark turned the flag on by writing through a raw C++ pointer (`_prior_lib` / `prior_setter.cpp`). This stage does not.
4. Frame subsampling inside housepipe. The SD-10 budget here is explicit (48 mapper frames, then `image_registrator`), and the A6 reproduction turns the budget off so all 99 frames enter the mapper.

Mapper times from the same table, for the budget: L4 96 s at 40 photos, 186 s at 60, 237 s at 70. Electro LiDAR method time 77 s.

## Toolchain (SD-34)

- This worktree pins `pycolmap==4.2.0`. `PosePrior.corr_data_id` exists. `IncrementalPipelineOptions.use_prior_position` exists and defaults to false. The A6 writer (`prior.corr_data_id = image.data_id`, `db.write_pose_prior(prior)`) runs unchanged.
- A separate probe of the pycolmap 3.11.1 macOS arm64 wheel (Python 3.12, under `/tmp/hss-pycolmap311`, not this lockfile) has neither `corr_data_id` nor `use_prior_position`. `write_pose_prior` there takes `(image_id, prior)`. The A6 recipe cannot be expressed on 3.11.1 without the pointer hack, which is banned. Parity on 3.11.1 is not proven. The pin stays 4.2.0. WS-12 and WS-00 should keep that pin and ship a COLMAP build that matches the 4.2 wheel. Do not mix a 4.x pycolmap with a 3.11 binary.
- No `colmap` binary is on this Mac's PATH. Reports record `pycolmap 4.2.0 (no colmap binary)` until `HSS_POSES_COLMAP_BIN` or a matching binary exists.
- Modal: `modal` is a dev dependency. A token is configured on this machine (`modal token info` exits 0; the value is not recorded here). The L4 smoke (app `ap-HBQKJYiOwB8QOzg5wBIExM`) pip-installs `pycolmap==4.2.0` on Python 3.12. It does not use benchmark image `im-IJBZcHC7t19iFxc44I4MDA` (pycolmap 3.11.1). Remote result: `4.2.0`, `corr_data_id` true, `use_prior_position` true, devices `auto`, `cpu`, `cuda`. Spend for this workstream stops at $30 of the $40 cap. After the smoke the workspace billing summary showed metered cost 1.16 and billed cost $0.00.
- Numeric host for WS-11. Mapping, post-registration, and fixed triangulation stay on one thread. Extraction and matching stay at `PoseParams.num_threads` (4), and matching stays on FAISS. Both the builder VM and the validator VM are AVX-512; unpinned, pycolmap's OpenBLAS selects Cooperlake on each, and the facade models still differed (66,770 points here, 66,152 on the validator). FAISS in this wheel has no dispatch env var, so the gate host is the `ws-11-ci` instance type. `server.poses.host_pin` sets `OPENBLAS_NUM_THREADS=1`, `GOTO_NUM_THREADS=1`, and `OPENBLAS_CORETYPE=HASWELL` only while it imports pycolmap, then restores the previous environment. Poses shares `run_stage_impl` and the CPU and GPU images with the other stages, so those variables are not set on the image: a warm container's numpy for dense and coverage stays multi-threaded. `scripts/score_poses.py` imports `server.poses.host_pin` before numpy and pycolmap so the GT-pose floor is computed on that pin. If pycolmap is already imported, the pin cannot apply: the module logs that, and the registration report records `blas_coretype` `unpinned` instead of `HASWELL`.

## What this stage does

Guided flow: SIFT (`max_num_features` 8192, `max_image_size` 2048 by default, 1920 for the A6 reproduction), `PINHOLE` per image, packet intrinsics locked, position prior σ 0.03 m, `CARTESIAN`, no robust loss, `random_seed` 0 written to `registration_report.params.random_seed`. Pairing is a per-epoch pair list (40 neighbours inside 20 m, sequential overlap 10) matched with `match_image_pairs`. Each epoch is its own `incremental_mapping` call. Pairs do not cross epochs until the join step, which adds boundary pairs (and exhaustive pairs when the product is at most 20,000) and registers the other epoch into the primary. A rejected join is rolled back and the epoch stays a fragment. Non-primary fragments with at least 3 frames are written under `poses/fragments/<id>/`. `run_poses(..., base_model=path)` registers new frames into that model and does not remap. `random_seed` stays 0; a caller does not pass another seed (SD-45).

Photo flow: images-only COLMAP is the pose engine (`SIMPLE_RADIAL`, focal and extra params refined, warning `image_only_unchecked`). π³ and DA3 are not pose engines. Stills with `intrinsicsSource == arkit` stay locked `PINHOLE` with priors. `photo_spacing` records `median_step_m`, `p90_step_m` and `sides_registered`. `photo_spacing_sparse` when the registered share is under 0.60 or more than one model comes back, and on the §8.1 case of 40 frames thinned to 1 m steps (path over 20 m and median gap over 0.9 m). `sparse_photo_set` is not used.

Fallback (guided): `sfm_split` below 90 % of the mapper set, `sfm_bent` above 0.25 m rigid RMS against ARKit, `sfm_scale_off` above 5 % scale. Warn `scale_vs_arkit_off` above 3 %. Fixed-ARKit triangulation replaces the prior model only when a trigger fired and it registers more frames. Pose source on that path is `arkit`.

Quality filter (A7): keyframes flagged `motion_blur` or `high_iso`, and frames whose exposure × gyro rate × focal exceeds 4 px, stay out of extraction and matching. Meter frames and close-ups stay in and are warned `meter_frame_degraded`. The count is `frame_budget.frames_dropped_quality`. Median keypoints under 3000 adds `low_texture_or_blur`.

## Measured

Scores below are on the current harness. `main` is `66ee387` (WS-00 contracts). There is still no `server/eval` package, so this page cannot name a `server/eval` commit. WS-11 re-baselines items 3, 6 and 7 before they gate. A phone field-of-view score counts only after WS-11 lands the harness patch. A1's stored non-LiDAR model now scores 1.73 / 4.11 / 4.76 in on that harness against the published 0.84 / 2.90 / 5.20.

CPU reproductions (`HSS_LAMAR_ROOT=~/hss-data/lamar`, `max_image_size` 1920, `random_seed` 0). The GT fit uses every `gt_ios.json` key inside the segment span. `arkit_fit` is the Sim(3) to the ARKit centres, the health check, not the GT comparison. Openings p90 is not scored: the NavVis depth maps are not on disk.

| Segment | Registered | Mode | GT scale | Rigid RMS | GT frames |
|---|---|---|---|---|---|
| `2022-06-17_10.43.29_s0` | 99/99 | `arkit_prior` | −0.412 % (target −0.40 ± 0.1 pp) | 0.087 m (target 0.088 ± 0.01 m) | 38 |
| `2022-06-17_10.43.29_s1` | 100/100 | `arkit_prior` | −0.424 % | 0.058 m | 42 |
| `2022-02-10_14.47.24_s0` | 99/99 | `arkit_fixed` (`sfm_split`) | +1.81 % | 0.180 m | 39 |
| `2022-02-10_14.47.24_s1` | 87/87 | `arkit_prior` | +2.52 % | 0.219 m | 39 |

`06-17 s0`'s `arkit_fit` was RMS 0.062 m, scale −0.17 %. `06-17 s1`'s was RMS 0.047 m, scale +0.02 %. `02-10 s0` took the fixed-ARKit fallback after `sfm_split`, which is the A6 outcome for that segment (fixed 99/99). `02-10 s1` stayed on the prior (RMS 0.219 m, under the 0.25 m bend trigger). `2021-06-02 s0` is not run yet.

WS-00 PR 38 put `server/common/geometry.py` and `scripts/check_repo_hygiene.py` on `main` (`5525a2d`). This branch uses those files. `server/eval` is now in the tree; fixture packets are still not fetched, so items 2–5 and 7 stay pending.
