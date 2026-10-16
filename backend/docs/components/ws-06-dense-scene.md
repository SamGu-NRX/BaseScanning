# WS-06 dense scene

## Purpose

Build a metric scene from registered frames. The dense stage writes a point cloud (LiDAR TSDF when depth is present, otherwise π³, with an optional DA3 second opinion that never enters the cloud). Coverage turns that cloud into a ray grid, wall and ground bands, and retake requests. Scene validates the document, casts nothing from 2D detections yet, and copies the assets the scene names.

## Owner WS and brief

WS-06. Brief: `docs/build/ws-06-dense-scene.md`. The contract this package writes landed in PR 31 (`6dbd7f9`).

## Inputs / outputs (artifact names)

Inputs: `packet.json`, `poses/cameras.json`, `poses/sparse.ply`, `poses/tracks.npz` when present, `scale/scale.json`, and optionally `objects/objects.json` (`hss-objects/v0`). Weights come from `HSS_DENSE_WEIGHTS` (default `/vol/hf`), as `<org>--<repo>` snapshots. A missing directory falls back to the hub repo id. The prove path does not download weights.

Dense writes `dense/cloud.npz`, `dense/tracks.npz`, `dense/cameras_w.json`, `dense/dense.ply`, `dense/flagged.ply`, `dense/dense_report.json`, and `dense/depth/<keyframe>.npz`.

Coverage writes `coverage/coverage.npz`, `coverage/bands.json`, `coverage/points.ply` (float32), `coverage/scene_ready.json`, `coverage/scene_draft.json` (the ready document without `assets`), `coverage/coverage_report.json`, and `coverage/state.npz`.

Scene reads `coverage/scene_ready.json`. A missing file raises `SceneError("input_missing:scene_ready")`. It writes `scene/scene.json`, `scene/build_report.json`, `scene/scale.json`, `scene/coverage_topdown.png`, and copies of the ply files the scene references. `run()` returns `scene.json` in `StageResult`.

## How to run on a fixture

Name the committed packet directly. Dense inference needs the weight snapshots; the command below is the stage entry, and it will not finish a photo window without them.

```bash
uv run python -m server.scene.pipeline \
  --packet data/fixtures/packets/mini-arkit/packet.json \
  --poses runs/mini/poses \
  --scale runs/mini/scale/scale.json \
  --out runs/mini/ws06
```

`mini-arkit` is the guided synthetic packet (`data/fixtures/packets/mini-arkit/packet.json`). The meter anchor is the packet's `meter` tap. `--max-frames` caps the windows. `--cpu` is accepted and ignored.

## How to test

```bash
uv run --frozen --extra api --extra dense pytest server/tests/test_ws06_fixes.py server/tests/test_box_house.py -q
uv run --frozen ruff check server/dense server/coverage server/scene
```

`make ci` is the full gate (lint, the unit suite, schemas, hygiene, fixture validate). The two Open3D TSDF tests call `pytest.importorskip("open3d")`. They run when the `dense` extra is installed and skip in a plain `uv sync`. That extra is not on this commit's lockfile yet; the CI test job still uses a plain sync, so those two tests skip there until the extra lands.

The suite does not run π³, DA3, or MoGe. It does not score ETH3D and it does not time the L4 caps.

## Rebuild from scratch

`server/dense/`, `server/coverage/`, and `server/scene/` are the stages. `server/contracts/scene.py` is WS-00's scene document. `server/eval/` is the vendored harness (extractor, grid, ply, visibility). Install with `uv sync --frozen`. Fetch weights only when a snapshot directory is missing:

```bash
HSS_DENSE_WEIGHTS=/vol/hf uv run python -m server.dense.models --fetch
```

Pins: π³ `yyfz/Pi3@9fa3ddb3f8d53041f8b2738df404f62223bbaa7b`, MoGe-2 `microsoft/MoGe@925b8ed835a7a9cdb7578ba15c658a0afc969030`, DA3-Large `ByteDance-Seed/Depth-Anything-3@3d835ec1a5802d64a8b8b15f817a1ab54809bfe4`. Do not point this package at `HOUSE_WEIGHTS`.

## Last gate table

| Check | Expected | Observed |
|---|---|---|
| yt dense, `yt-LCPBf86oloE`, 153 frames, second opinion none | ≤ 90 s | 60.128 s. Counts: 502480 points, 0 flagged, 153 frames, 100 MoGe frames. Warnings: `moge_depth`, `tracks_missing`. |
| Facade pose-free dense with DA3, 76 frames | ≤ 150 s | Not measured. The L4 prove reached the yt checkpoint and then hit `FunctionTimeoutError` at 1500 s. DA3 did not start. |
| Coverage | ≤ 60 s | Not measured on L4. A local replay of the post-dense cloud spent about 49 s in `rays_from_tracks` and about 51 s in `build_metric_scene`. |
| Scene | ≤ 30 s | Not measured on L4. The scene write itself was not the local cost. |
| `points.ply` float32 and sha256 | dtype float32, sha matches the asset | Local box-house chain. 3368 rows, sha256 `4b85043e5f60ce38604e273cbef236bbbcf8919d19dcb9409dcf4ae0f1a273c3`. `dense.ply` and `flagged.ply` float32. Flagged rows 0. |
| SD-78 fixture scores | within 0.02 of the reference; `false_observed_by_rule` at most reference + 0.005 | Deltas ≤ 0.006. `false_observed_claims` empty. Houses with no WS-06 input scene are N/A. |
| Open3D one-view TSDF | a plane at 2 m is kept | Passes where Open3D imports. Skipped in CI until the `dense` extra is installed. |
| I-18 artifact names | dense keeps `cloud.npz`, `tracks.npz`, `cameras_w.json`; scene reads `scene_ready.json` | Covered by the dense → coverage → scene chain test. |

## Known limits

- The facade, coverage, and scene L4 caps are open. The yt dense cap is the one that was met.
- `objects/objects.json` is counted (`objects_2d:<n>` on the provenance notes) and not cast onto walls. Ground regions stay empty. That is brief step 19–20.
- The Modal prove walks the camera 0.3 m per frame at 1.5 m height. Those are not COLMAP poses, so the window pose gate is opened (`window_rms_max_m` and `window_scale_dev_max` are `1e6`). `tracks_missing` is expected. A synthetic src-0 wall is written so the image cloud can still fit a wall.
- Image points do not measure a wall (`src != 1` is the measurement mask). A cloud of only π³ points raises `no_wall`.
- `earlyAccessModels` is not this package. Every plan can use the pinned models.
- Do not download weights on the prove path. `fetch_weights` exists for a cold volume and is not part of the gate run.
