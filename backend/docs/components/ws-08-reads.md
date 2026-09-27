# WS-08 reads

## Purpose

Read the meter nameplate and the panel photos during the walk, confirm a meter number only when both readers agree and a barcode matches, and emit verdict facts for `meter_number`, `cl320`, and `panel_recall`.

## Owner WS and brief

WS-08. Brief: `docs/build/ws-08-meter-panel-reads.md` in the project store. Branch `ws-08/meter-panel-reads`.

## Inputs / outputs

Inputs: committed stills with purpose `meter_close`, `meter_oblique`, `panel_open`, `panel_close`, or `main_disconnect`. Bytes come from `CommittedFile.key`.

Outputs: `captures/{id}/early/reads/<stillId>.json`, `captures/{id}/early/meter_identity.json` (written by `read_still` on `meter_close`, SD-46), `captures/{id}/runs/{runId}/reads/reads.json`, `ReadsStatus`, and `Hint` objects. The one-tap card is `HintData.confirm` on `meter_number_confirm` (SD-51). The answer is posted to `POST /captures/{id}/confirmations`; `by` arrives from the principal (SD-52).

The shared synthetic packet is named explicitly: `data/fixtures/packets/mini-arkit/packet.json` (SD-43). That file is WS-00's and is not on this branch.

## How to run on a fixture

```sh
PYTHONPATH=. python -m server.reads.worker --image path/to/plate.jpg --purpose meter_close --out /tmp/r
```

With `VISION_EXTERNAL_CALLS=off` the call returns `disabled` and opens no socket. Cached replay:

```sh
python scripts/reads_seed_cache.py --raw "$STORE/internal/breadth/b2/raw" --images "$B2_DATA" --cache /tmp/reads-cache
```

Photos are optional. Without them the seeder writes nothing, because the cache key is the file sha256.

## How to test

```sh
PYTHONPATH=. uv run pytest server/reads/tests -q
make reads-gate
```

`make reads-gate` scores the stored jsonl. It does not print meter numbers and it does not call a provider. The live path is `scripts/reads_gate.py --live --max-usd 12` (default cap 12, `--workers` default 12). It calls providers only when `OPENAI_API_KEY` and `XAI_API_KEY` are both set. `--live` writes real meter numbers into `--out` (default `/tmp/reads-gate`). Never commit or upload that folder. A `reads-gate/` directory inside the repo is gitignored.

## Rebuild from scratch

The package is plain Python under `server/reads/`. Prompts `meter/v0` and `panel/v0` are the B2 strings. The offline gate needs the store raw jsonl and `HSS_READS_LABELS_DIR` pointing at the store labels. Panel CSVs in `data/fixtures/reads/` have no meter numbers. No VM-only state is required for the unit tests.

## Last gate table

Offline replay, `panel/v0`, cached answers. The `--live` provider path is implemented with `--max-usd` default 12 and was not executed in this change.

| check | observed | threshold |
|---|---|---|
| GPT-5.5 top-1 | 69/73 | 69/73 |
| Grok 4.7 top-1 | 66/73 | 66/73 |
| top-3 union | 72/73 | >=71/73 |
| pair agree | 68/73 | 68/73 |
| agree correct | 65/68 | 65/68 |
| wilson low | 0.878 | 0.878 |
| auto_fill | 11 | >=9 (expect 10) |
| auto_fill wrong | 0 | 0 |
| choose ids | m30 m33 m57 m64 m77 | those five |
| US CL | 22/22 | 22/22 |
| false CL320 | 0/83 | 0/83 |
| panel hazard (B3 bit) | 1/1 and 4/4 | 5/5 |
| false alarms | 0 and 0 | 0 |
| panel brand | 16/19 wrong 2 | >=16/19, wrong <=2 |
| extra brand | 11/13 wrong 0 | >=11/13, wrong 0 |
| main amps | 7/9 and 3/3 | >=7/9 and 3/3 |
| panel rating | 5/5 and 1/1 | 5/5 and 1/1 |
| usd/photo | 0.0137 and 0.0067 | <=0.016 and <=0.008 |
| confirmed BASE_RECALL | 4/5 | product field; x0013 is pushmatic review |
| verdict schema | 0 errors | `server/contracts/schemas/verdict.v0.schema.json` |
| contract errors | empty | pass needs observed and min_views 1 |

Unit tests: 60 passed in `server/reads/tests`. `VISION_EXTERNAL_CALLS` defaults off and opens no socket. `--live` still refuses when that switch is explicitly off.

## Known limits

- `server.reads.run` returns `server.contracts.jobs.StageResult`. Coverage uses `server.contracts.verdict.verdict_contract_errors`. Hints are `HintData`.
- `panel/v1` is implemented and not shipped. Cached answers are v0, and v1 was not measured live.
- `HSS_READS_PANEL_AUTOPASS` is off. A clean printed brand stays `unsure`.
- No `METER_HMAC_KEY`, so the HMAC label fixture is not generated. The gate reads store labels at runtime and does not commit them.
- Barcode cores for the offline gate come from the stored paddle jsonl (20 of 83 photos), not a fresh zxing decode.
- `confirmed` hazard is Base's recall list only. The §8 5/5 figure is the B3 hazard bit, which also counts agreed pushmatic. x0013 is that case: review hint, not `confirmed`.
- The `reads` extra is `openai`, `pillow`, `zxing-cpp==3.1.1`, `numpy`, `pydantic`, and `jsonschema`. `make reads-extra` installs it. A missing `zxingcpp` raises outside pytest.
- `reads.json` `confirm` matches `HintData.confirm` (`pick|type|none`, string `preselected`). The brief example that used `prefill|choose` and an index is the pre-merge shape.
