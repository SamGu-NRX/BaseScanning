# `server.reads`

Meter and panel reads for one capture. Vision models only transcribe; the placement
decision stays in `policy.py` and `panel_policy.py`. Clearance numbers are not here.

## What it writes

- `captures/{id}/early/reads/<stillId>.json` from `read_still`
- `captures/{id}/early/meter_identity.json` from the `meter_close` still (SD-46), refreshed by the `reads` stage
- `captures/{id}/runs/{runId}/reads/reads.json` (`base-reads/v0`) from `server.reads.run`

`reads.json` `confirm` uses the same names as WS-00 `HintData.confirm` (SD-51): `imageId`, `fieldId`, `mode` of `pick`, `type`, or `none`, string `candidates` (at most 3), string `preselected`, and `allowNone`. The brief's example (`prefill|choose` and an integer index) is the older shape; the merged contract replaces it. `answered_by`, `answer`, and `locks` stay on the artifact as the stored reply. WS-01 derives `by` from the principal; this package only receives it (SD-52).

## Knobs

`HSS_READS_*` in `config.py`. The shared switch is `VISION_EXTERNAL_CALLS`. Keys, when a live call is actually wanted, are `OPENAI_API_KEY` or `OpenAIChatGraphPooledProdApiKey`, and `XAI_API_KEY` or `XAI_CHATGRAPH_PROD_API_KEY`. This tree does not read those values into logs.

`HSS_READS_PANEL_PROMPT` defaults to `panel/v0`. Cached answers that pass the gate were collected with v0. `panel/v1` adds enclosure and mains counts and has not been measured live.

`HSS_READS_PANEL_AUTOPASS` defaults off. When on, a panel may `pass` only after GPT-5.5 has read the image and agrees with mini on a printed non-recall brand (SD-50, SD-58).

Install the vision extra before a live call or a fresh barcode decode: `uv sync --frozen --extra reads`. That group is `openai`, `pillow`, `zxing-cpp` 3.1.1, `numpy`, `pydantic`, and `jsonschema`. If `zxingcpp` is missing outside pytest, `read_barcodes` logs an error and raises. Under pytest it logs the same error and returns an empty list so the default CI install can still run.

## Hazard recall (SD-63)

`confirmed` uses Base's recall list only. Pushmatic is a review hint: it is never `confirmed` and it never `pass`es. The offline gate reports hazard recall as 5/5 routed-or-confirmed (the four Base-list confirms plus the Pushmatic review) and `confirmed` as 4/5.

## Live gate (SD-62)

The live gate and the live timing row are not merge blockers while this Mac has no provider keys. `--live` stays in `scripts/reads_gate.py` and does not call a provider. The post-merge smoke run is on Modal `dev` with the `hss-dev-vlm` secret, after WS-12 deploys.

## Tests

```sh
PYTHONPATH=. uv run pytest server/reads/tests -q
PYTHONPATH=. uv run python scripts/reads_gate.py --set meter --set panel --set extra --out /tmp/reads-gate
make reads-extra
```

The gate reads labels from `HSS_READS_LABELS_DIR` (or the store). It never writes a meter number. `make reads-extra` installs the extra and imports `openai` and `zxingcpp`.
