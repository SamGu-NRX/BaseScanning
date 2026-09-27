# Scale

Turns a registered model into metres and writes `scale/scale.json`.

LiDAR depth is a similarity alignment when the session tier is `arkit_lidar` and depth files are present. Otherwise the meter cover diameter from the dimension table is measured in the two close-ups. ARKit session scale is a gate. Doors, garage doors and footprints are checks and are never used as the scale. An unresolved scale widens the bound. ARKit alone never verifies a placement.

This package does not call a vision model and does not expose an identity entry point. Make and model come from `reads/reads.json`, or from `early/meter_identity.json` when the reads file is absent (SD-46). A packet directory resolves to `packet.json` only (SD-43).

Customers bring no measuring tool. The app supplies two close-ups and an optional TrueDepth scan.

```bash
uv run python -m server.scale.run --packet path/to/packet.json --poses path/to/poses --out runs/scale
uv run python -m server.scale.validate runs/scale/scale.json
uv run pytest server/scale/tests -q
```

The photo-flow error budget at 26 ft is in `docs/components/ws-05-scale.md`. The simulation CLI is documented there too. `meter_verified_enabled` stays false until the real-photo experiment in the brief's done-when item 8.
