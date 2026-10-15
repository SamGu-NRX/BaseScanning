# server/poses

Owner: WS-04. Brief: `docs/build/ws-04-pose-backbone.md`.

Stage name: `poses`.

Inputs: `packet.json`, then `normalized.json`, and the packet image files.

Outputs: `poses/cameras.json`, `poses/sparse.ply`, `poses/tracks.npz`, `poses/registration_report.json`, `poses/sparse/0/`, optional `poses/fragments/<k>/`, `poses/database.db`.

COLMAP incremental SfM with ARKit position priors (guided) or image-only SfM (photo).

```bash
uv run python -m server.poses.run --packet PACKET.json --out OUT --cpu \
  --sfm-max-frames 99 --max-image-size 1920
uv run python -m server.poses.report --check OUT/poses/registration_report.json
```

`server.poses.run(ctx)` is the stage entry. It reads the packet under `ctx.inputs["packet_dir"]` and writes `work_dir/poses/`. Pose maths come from `server/common/geometry.py`. The numbers live in `params.py` and match the A6 `prior_s03` recipe. Recipe and the housepipe discrepancy: `docs/components/ws-04-poses.md`.
