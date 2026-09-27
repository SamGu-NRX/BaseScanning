# WS-10 result composer

## Purpose

Build the device result, AR anchors, customer copy, meter block, events, and the read-only reviewer page from a verdict and a scene.

## Owner

WS-10. Branch `ws-10/result-and-ar`.

## Inputs / outputs

Inputs: `CaptureRecord`, `RunRecord`, `scene/scene.json`, `verdict.<profile>.json`, optional `reads.json`, `scale.json`, `placement.<profile>.json`, and the prompt catalogue.

Outputs: `result.json` (`ResultResponse`), `result.internal.json`, `preview.ply`, `review.html`, plus `verdict_ready` and, when needed, `retake_request`.

## How to run

`python -m server.result.compose` with `--capture`, `--run`, `--scene`, `--verdicts`, and `--out`. See `server/result/README.md`.

## How to test

`uv run pytest server/result/tests -q`

`make result-golden` composes the committed synthetic, photo, and reject fixtures.

## Rebuild

No model weights. `uv lock` after a dependency change, then `uv sync --frozen --extra result`.

## Last gate table

| # | Check | Expected | Observed |
|---|---|---|---|
| 1 | Synthetic `result.json` validates; `viewsNeeded` matches; no snake_case keys | PASS | covered by `test_synthetic_eligible_has_arkit_box_and_route` |
| 2 | Eligible box and route; photo flow crops; shown and hidden reject; degraded; hard stop | PASS for the committed fixtures | ETH3D facade, delivery, and electro are not on this branch |
| 3 | Round trip < 1e-9; box corners ≤ 0.01 m; size matches scale within 1e-9 | PASS | unit tests |
| 4 | Wall and ground camera bands on the synthetic wall | PASS | facade forged-frame row waits on fixtures |
| 5 | Copy selections, limits, banned words, headlines | PASS | unit tests |
| 6 | Five meter samples; agreement never auto-fills; log omits the value | PASS | unit tests |
| 7 | One `verdict_ready`; retake when required; APNs literal; HMAC; 24 h schedule | PASS | unit tests |
| 8 | Reviewer page: four profiles, criteria rows, offline notice | PASS | unit tests |
| 9 | `preview.ply` binary little-endian with `generated` | PASS | unit tests |
| 10 | At least 25 tests, including the error and leakage cases | PASS | `server/result/tests` |
| 11 | No file over 5 MB; no `SSR`; no Anthropic import | PASS | hygiene script and `test_package_has_no_ssr_token` |

## Known limits

- ETH3D golden rows need `make fixtures` and `make criteria-golden`, which are not on this branch.
- WS-09 PR 39 writes the chosen spot's criteria with `candidate_id: "best"` and `site.chosen_candidate` as `runN` (run ids are assigned after the rows). The composer treats `best` as that spot when no row uses the run id. `placement.<profile>.json` `start_s_m` is a single number and `checks_at_spot[].margin_over_uncertainty` is null, so confidence comes from the verdict rows.
- A hidden reject (`SHOW_REJECT_TO_HOMEOWNER=false`, SD-69) empties `site.reasons`, sets `verdictUrl` null, and shows each `fail` row as `unsure` with id `review` and no measurements. The true decision stays in `result.internal.json`, the reviewer page, and the webhook.
- Without `placement.<profile>.json`, `startSM` is the run endpoint closer to the meter, and the box centre is always `startSM + width/2` (WS-09). The rebuilt cable route drops a point that repeats the previous one, including when that start is 0.
- A shown reject whose fail rows do not match the spot ids still lists every candidate-scope `fail` row in `outcome.reasons`.
- A scene asset path that is missing becomes `preview_skipped`. Compose does not raise `FileNotFoundError`.
- ETH3D facade, delivery, and electro rows stay deferred (SD-70) until PR 39 merges. PR 41 is on main (`ca84005`); this branch has merged that main. PR 39 still lands later, and this branch will merge main once more (SD-71).
- The prompt catalogue file from WS-14 is not in the repo. A missing file uses `copy.FALLBACK` and records `catalogue_missing`. The tests commit a copy of that fallback.
- `render` matches `ios_kit.prompts`: numeric `s_ft` and `out_ft` are metres, a centred view drops `, 0 ft along`, and a missing placeholder is left out instead of raising.
- The stage posts the webhook once. `retry_schedule` is the 24 hour plan for a worker.
- Webhook ids are `evt_<run_id>` so a repeat of the same run is idempotent.
- `server/tests/test_skeleton.py` no longer expects `result` to raise `NotImplementedError`.
- `jinja2` and `httpx` are in the main dependency list so `uv sync --frozen` (CI, no extras) can import this package. The `result` extra lists them as well.
