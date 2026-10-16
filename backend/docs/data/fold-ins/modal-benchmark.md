# Fold-in: Modal GPU benchmark

Source: `internal/modal-pipeline/benchmark.md`, landed 2026-09-26 22:28 UTC.

## Old to new

| Item | Old | New |
|---|---|---|
| L4 method time | 3 min p50 assumed across the shot list | 148 / 259 / 324 s at 40 / 60 / 70 photos (mapper 96 / 186 / 237 s). Electro LiDAR packet 77 s. A10G facade-70 230 s. The 3 min p50 holds only near 40 frames. The 8 min p95 holds everywhere (max client time 397 s) |
| Mapper budget | open | 48 frames, `HSS_POSES_SFM_MAX_FRAMES` (SD-10) |
| Housepipe key distances | a candidate recipe | facade median 24–26 in, p90 39–47 in, camera RMS 0.19–0.83 m, against A1/A6 tight-prior 0.9–1.9 in. Port the A1/A6 recipe. Housepipe is reference only (SD-35) |
| pycolmap | 3.11.1 was in play | 4.2.0 with a matching COLMAP build unless parity is shown (SD-34) |
| Warm container | `scaledown_window=900` expected to hold one | it did not (30–76 s boot). Weight load 15–22 s. L4 uses about 7 GiB of 23 GiB |
| GPU cost | open | geometry-only $0.05–0.15 per house. Benchmark spend $0.85 |

## Owners

WS-03 (timing and prewarm), WS-04 (48-frame budget and the A1/A6 port), WS-12 (image line, `HSS_PIPELINE_MIN_CONTAINERS`, cost).

## Issues not opened

- `fold-in modal: timing and prewarm` (WS-03)
- `fold-in modal: 48-frame budget and A1/A6 pose port` (WS-04)
- `fold-in modal: pycolmap 4.2.0 and warm-container cost` (WS-12)
