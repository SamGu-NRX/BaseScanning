# Object understanding

Owner: WS-07. Brief: `docs/build/ws-07-object-understanding.md`. Stage name: `objects`.

Two stages. `server.objects.run` writes 2D detections after the frame gate and the vision cascade. `server.objects.run_3d` places those detections once a scene exists. It does not call a vision model.

Cached scores and answers under `data/fixtures/objects/` reproduce the published gate and cascade numbers with no API key. Vendor calls read only `OPENAI_API_KEY` and `XAI_API_KEY` (SD-67: no ChatGraph pooled or production key names) and stay off unless `VISION_EXTERNAL_CALLS` is explicitly on. They stay inside `HSS_OBJECTS_VLM_BUDGET_USD_PER_HOUSE` (the lower of that cap and `StageContext.vlm_budget_usd`). The live tests skip unless that switch is on and both keys are set. They call GPT-5.5 and Grok 4.7, and they stop the next vendor call once planned spend would meet `HSS_OBJECTS_LIVE_MAX_USD` (default 3).

```bash
uv run python -m server.objects.cli gate-eval \
  --images data/fixtures/objects/b3/images.json \
  --gt data/fixtures/objects/b3/gt.json \
  --scores data/fixtures/objects/b3/gate_scores_all.json \
  --out /tmp/ws07/gate
uv run python -m server.objects.cli cascade-eval \
  --presence-cache data/fixtures/objects/b3/raw/presence \
  --scores data/fixtures/objects/b3/gate_scores_all.json \
  --images data/fixtures/objects/b3/images.json \
  --gt data/fixtures/objects/b3/gt.json \
  --out /tmp/ws07/cascade
uv run pytest server/objects/tests -q -m "not live"
```

The stills packet is the file `data/fixtures/packets/mini-arkit/packet.json`. Commands name that path. They do not glob `packet.*.json`.

Inputs: packet/normalized.json, poses/cameras.json; objects_3d also reads scene/scene.json.

Outputs: objects/objects.json (hss-objects/v0, optional ground_type), objects/objects_3d.json.
