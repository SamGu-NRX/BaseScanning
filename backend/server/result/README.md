# server/result

Owner: WS-10. Stage name: `result`.

Turns a verdict, a scene, and optional reads and scale into the device result.

Inputs: `verdict.<profile>.json`, `scene.json`, optional `reads.json`, `scale.json`, and the prompt catalogue.

Outputs, under the directory you pass as `--out`: `result.json`, `result.internal.json`, `review.html`, and `preview.ply` when a cloud is available.

```bash
uv run python -m server.result.compose \
  --capture server/result/tests/data/capture.sample.json \
  --run server/result/tests/data/run.ok.json \
  --scene server/result/tests/data/synthetic \
  --verdicts server/result/tests/data/synthetic/verdicts \
  --out /tmp/res/synthetic \
  --reads server/result/tests/data/reads.auto_fill.json \
  --catalogue server/result/tests/data/prompts.en-US.json
```

`server.result.run(ctx)` is the stage entry. Frame math comes from `server.eval`. Customer copy is loaded from the catalogue file and never from `ios_kit`.
