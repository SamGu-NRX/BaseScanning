# WS-14 iOS contract kit

Shippable tree at `ios-kit/` for spec v0.4 and the demo capture API. `VERSION` is `0.4.0`. The iOS team installs `requirements.txt` (`jsonschema`, `numpy`) and runs `python -m packetgen validate`. Replay, the contract fake, and the checks run from a repo checkout:

```bash
cd ios-kit
uv run --project .. python -m ios_kit replay --packet fixtures/mini-arkit --api http://127.0.0.1:8765/v1 --out runs/mini-arkit
uv run --project .. python -m ios_kit.fake_api --port 8765 --root /tmp/fake-captures --result-kind needs_more_photos
```

The token is the env var `CAPTURE_API_TOKEN`. Commands do not print it. `make ios-kit` refreshes the schema copies and writes `dist/ios-kit-0.4.0.zip` (schemas, examples, prompts, vendored `packetgen`, `schema/`, the three minis, the spec). The zip omits `ios_kit/` and `tests/`.

Run kit commands from `ios-kit` so the vendored `packetgen` is imported. The repo `pythonpath` lists `packet/` first, and that copy does not open `.csv.gz` streams.

## §8 gate (this branch, after merging `ca84005`)

Schemas were copied again from `server/contracts/schemas/` on this main (`make ios-kit`). The three kit files are byte-identical (`cmp` clean). sha256: packet `56eb4397a1ce…`, result `43db39e139e6…`, api `8ec616d008dd…`.

| Item | Expected | Observed |
|---|---|---|
| 1 | `pytest ios-kit/tests -q` green | 10 passed, 1 skipped, from the repo root. Skip is `server.result.copy` (not importable on this main). |
| 2 | `check-spec` prints `parity ok` | Exit 1, `parity failed`. Appendix A `$id` is `huntertcarver/house-scanning-server`; the exported schema (and the kit copy) is `SamGu-NRX/house-scanning`. `obliqueCloseUp.still` says `meter_oblique` in Appendix A and `meter_close` in the schema. Those two fields are PR 45. `examples/packets/example-packet-meter-stills.json` is a byte copy of the SD-29 packet. |
| 3 | packetgen `ok: true` ×3, `check-packets --files` ok, `make validate` green | packetgen: three `ok: true`, zero warnings; tap replay `9.42e-7` and `9.75e-7`. `check-packets --files` printed `ok` for all three. `make validate` printed `validate ok scenes=5 verdicts=11 labels=61 packets=36`. The manifest keeps main's `hss-fixtures/v0` items and appends `ios-kit-mini-arkit-lidar`, `ios-kit-mini-arkit`, and `ios-kit-mini-photo-only`. |
| 4 | `check-ar` on the two results | `ar ok eligible.json` and `ar ok needs_more_photos.json`. |
| 5 | `check-prompts` exits 0 | `prompts ok (90 entries)`. |
| 6 | Both §7 fake replays exit 0 | Last-finalize and `--finalize-at early` both exited 0 against `ios_kit.fake_api` with the brief's default delays. `result.json` validates and has no snake_case key. `events.jsonl` has `files_committed`, `capture_check`, one `hint` `meter_number_confirm`, eleven stages in SD-42 order, `confirmation` `by: device`, and `verdict_ready`. The early log shows `awaiting_files` on the first committed file, then `processing`. `timings.last_commit_to_capture_check_s` was 8.633 s and 8.637 s. A copy of an earlier last-finalize log is `examples/events/replay-mini-arkit.jsonl`. The early run needs a fresh fake process: the API treats the same `Idempotency-Key` as the same capture. |
| 7 | `minify` byte-identical twice | Two regenerations from `eth3d-electro.tar` matched each other and the committed `fixtures/mini-*` trees (`diff -rq` empty). Packet ids `0be1b513-…`, `aeea5d1d-…`, `18fbca54-…`. |
| 8 | Real API electro replay | Deferred. WS-01 is not on this main. |
| 9 | Size, hygiene, secret scan, zip contents | `check-size`: `zip 12085441 bytes`, `size ok`. No file under `ios-kit/` over 5 MB. `rg keyframe_image ios-kit/` is empty. The §8 secret pattern is empty under `ios-kit/` and this doc. No `_truth/`. The zip has no `ios_kit/` or `tests/`. |
| 10 | `make ci` and `make gates-offline` stay green; no second uploader | `make ci` on the pre-fixture head exited 0 (pytest 117 passed / 3 skipped). This head's `ci` target includes `validate`, which is item 3. `hygiene ok` (2462 tracked files). `make test` does not collect `ios-kit/tests` (`testpaths` is unchanged); the kit workflow runs that suite (10 passed, 1 skipped). `make gates-offline` has no rule on this main. `ios_kit/replay.py` uploads itself until `server.eval.replay._execute` contains a PUT. There is no `server/eval/replay.py` on this main. |

## Inconsistencies

- Spec Appendix A is not the exported schema (the two diffs in item 2). The kit copies the export byte-for-byte. `check-spec` stays strict. This PR does not edit the spec or `server/`.
- `examples/packets/example-packet-meter-stills.json` is a byte copy of `data/fixtures/packets/mini-arkit/packet.json` (SD-29, SD-53).
- `packet/packetgen/validate.py` opens streams as text and crashes on the mini `.csv.gz` files. The kit vendors a copy that gunzips. `ios_kit.vendored` puts `ios-kit` first on `sys.path` before importing it, including when pytest has prepended `packet/`.
- `Outcome.reasons` is required. `examples/results/manual_review.json` keeps `"reasons": []`. The hand-inspection note says the file has no reasons.
- WS-11's engine, when it lands, takes a host root and appends `/v1`. The kit CLI examples pass a base that already ends in `/v1`. The client strips one trailing `/v1`.
- `make gates-offline` is named by the brief and has no rule on this main. `make validate` and `scripts/fetch_fixtures.py` arrived with the fixture merge.
- A view centred on the meter renders as "at your meter". The catalogue's ", 0 ft along" suffix is dropped when the offset rounds to 0 ft.
