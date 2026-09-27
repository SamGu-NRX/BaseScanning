# WS-02 packet validation

## Purpose

Check a v0.4 capture packet, normalize poses and stills, and write the capture checklist. The decision is plain code. Thresholds live in `server/packet/params.py`.

## Owner

WS-02. Brief: `docs/build/ws-02-packet-validation.md`. Package: `server/packet`.

## Inputs and outputs

Input is `packet.json` plus the files it lists, or `ctx.inputs["packet_dir"]`.

Outputs:

- `validation.json` (`ValidationReport`, RFC 6901 pointers)
- `normalized.json` (a v0.4 packet; derived fields under `ext.hss`)
- `capture_check.json`
- JPEG stills transcoded from HEIC at `normalized/stills/<id>.jpg`

Frame flags from the final pass: `motion_blur` above 4 px, `high_iso` when ISO is above 200, `low_light` when BrightnessValue is below 3. `photo_count` is a 40-still proxy. Shot-list purposes, including an unknown purpose such as `eave`, are indexed on `derived.purposes` (`eave` lands in `other`). Cadence stays 0.6 m and 1.5 keyframes/s. WS-04 owns the 0.5 m `photo_spacing` check.

## How to run on a fixture

```bash
uv sync --frozen --extra packet
uv run python -m server.packet.cli validate .fixtures/packets/yt-0gotY_syWWU/packet.arkit-nonlidar.json
uv run python -m server.packet.cli normalize .fixtures/packets/yt-0gotY_syWWU/packet.arkit-nonlidar.json --flow guided --out /tmp/ws02/out
```

`make fixtures` is WS-00 and is not on `main`. This package does not extract store tars.

## How to test

```bash
uv run ruff check server/packet
uv run pytest server/packet/tests -q --cov=server.packet --cov-fail-under=90
uv run python -m server.packet.cli export-schemas --check
```

`server/__init__.py` is on main. Pytest uses importlib mode so coverage traces `server.packet`. Coverage omits `cli.py` and `server/packet/tests`.

## Rebuild from scratch

Port `packetgen` validation into `server/packet`, keep every number in `PacketParams`, and export schemas from the pydantic models. `ValidationReport` and `StageResult` come from `server.contracts`. `server.common.geometry` is not on main yet, so `conventions.geometry()` uses the local copies until that module lands.

## Last gate table

Observed 2026-09-26 on this worktree, Python 3.12.13, ws-02 venv, after merging WS-00 contracts (`66ee387`).

| # | Check | Observed |
|---|---|---|
| 1 | 33 store packets, 0 errors, warnings from the allowed set, ARKit replay < 1e-6 | Pending the fixtures PR. 5 packets on disk (`yt-0gotY_syWWU` × 4, `yt-12Ks1idWj40` photo-only) are all `ok`, warnings inside the allowed set, replay 3.26e-7–5.32e-7. Manifest-stage `ok` matches `check_packet`. |
| 2 | Both examples, manifest-only, 0 errors; example meter frames and four checklist items present | PASS. Committed examples. `server.contracts.cli check` prints ok for both. `meterFrames = {close: s1, oblique: s2, tapKeyframe: k00002}`. The four required checklist items are present. |
| 3 | Each corrupt case is exactly one error | PASS on the unit packets. Each listed case reports that code and no other error. |
| 4 | HEIC Orientation 6 → upright JPEG, `compat` entry, notes | PASS in the unit test. |
| 5 | `normalized.json` vs both schemas, byte round-trip, branch and flow | Packet schema export `--check` passed. Round-trip is a unit test. Full fixture normalize waits on the fixtures PR. |
| 6 | ≥ 30 tests, ≥ 90 % line coverage, thresholds, `check_packet` agreement | PASS. 64 packet tests, 90.24% (2223 statements, 217 missed). Agreement holds on the examples and the five extracted packets. Geometry-only corruptions stay WS-02 errors; `check_packet` does not implement them. |
| 7 | `yt-LCPBf86oloE` timing | Pending the fixtures PR. Not extracted. |
| 8 | `make schemas` | PASS (`export_schemas --check`, 38 contract tests). `make validate`, `make hygiene`, and `lint-imports` are not in the Makefile yet. |

## Known limits

- `ValidationReport`, `StageResult`, and `CaptureCheckCode` are the WS-00 types. `server.common.geometry` was not in the contracts PR, so pose math stays in `conventions.py` until that module lands.
- `frames_blurry` and `frames_dark` are WS-03 warnings, derived later from `blurredShare` and `highIsoShare`.
- Framing (wall base and a metre of ground) is a WS-06 check. This stage only flags `pitched_up` and `pitched_down`.
- `check_packet` does not cover pose, stream, image-format, or tap-replay errors, so `ok` matches on clean packets and on the checks it owns, and WS-02-only corruptions fail only this stage.
- Full-packet validate on the 47-keyframe house was 3.2–4.3 s on this machine, over the 3 s budget.
