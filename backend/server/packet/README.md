# Packet validation

Stage `validate`. Turns a finalized capture packet (spec v0.4) into a `ValidationReport`, a `NormalizedPacket`, and a `capture_check` checklist. Placement stays plain code. This package makes no model calls.

Owner: WS-02. Brief: `docs/build/ws-02-packet-validation.md`.

## Run

```bash
uv sync --frozen --extra packet
uv run python -m server.packet.cli validate path/to/packet.json
uv run python -m server.packet.cli validate path/to/packet.json --manifest-only
uv run python -m server.packet.cli normalize path/to/packet.json --flow guided --out /tmp/ws02/out
uv run python -m server.packet.cli export-schemas --check
uv run pytest server/packet/tests -q --cov=server.packet --cov-fail-under=90
```

Exit codes: 0 when every report is ok, 1 when any report has an error, 2 on usage or IO.

`server.packet.run(ctx)` is the stage entry (`STAGE = "validate"`). It reads `ctx.inputs["packet_dir"]` or materializes `captures/{captureId}/**` into `ctx.work_dir/packet`. Artifacts:

- `captures/{captureId}/runs/{runId}/packet/validation.json`
- `captures/{captureId}/runs/{runId}/packet/normalized.json`
- `captures/{captureId}/runs/{runId}/packet/capture_check.json`
- transcoded stills at `captures/{captureId}/normalized/stills/<id>.jpg`

A failed report raises `PacketError("invalid")`.

## Thresholds

Numbers live in `PacketParams`. The final-pass brief sets frame flags, not packet warnings:

- `motion_blur` when `exposure.duration × |gyro rad/s| × fx` is above 4 px
- `high_iso` when ISO is above 200
- `low_light` when `exif.BrightnessValue` is below 3
- `keyframe_cadence` weak when median spacing is above 0.6 m or keyframes/s is below 1.5
- `photo_count` weak below 40 stills; the count is a proxy, and shot-list purposes are indexed on `derived.purposes`

`stats.blurredShare` and `stats.highIsoShare` are for WS-03. This stage does not emit `frames_blurry` or `frames_dark`.
