---
cursor:
  subagentId: "bc-6d4a6a52-874c-5686-978c-870a59f489c6"
---

# Layout decisions: criteria and result briefs (WS-09, WS-10)

Every `[default chosen]` in `docs/build/ws-09-criteria-engine.md` and `docs/build/ws-10-result-and-ar.md`, plus the inconsistencies found between the plan, the docs, the code and the sibling briefs. For the coordinator to reconcile with `layout-decisions-geometry.md` (WS-02..06), `layout-decisions-contracts.md` (WS-00), `layout-decisions-ws-03.md`, `layout-decisions-ws-14.md` and the WS-07/WS-08 owners. Conventions adopted from the sibling briefs after they landed: fixtures at `data/fixtures/gt/<site>/` and `data/fixtures/packets/<house>/` (WS-00 `make fixtures gt`); env prefix `HSS_<WS>_*` for per-WS knobs, WS-00 `Settings` names for shared ones (`RULES_PROFILE_DEFAULT`, `GROUND_RULE_DEFAULT`, `SHOW_REJECT_TO_HOMEOWNER`, `WEBHOOK_HMAC_SECRET`, `OPENAI_API_KEY`, `XAI_API_KEY`), `VISION_EXTERNAL_CALLS` as the shared vision kill-switch (WS-07/08); run artifacts under `captures/{captureId}/runs/{runId}/<stage>/attempt-<n>/` (WS-03).

## 1. Module paths

| Path | Owner | Note |
|---|---|---|
| `server/criteria/{run,config,compose,views_needed,candidates,lattice}.py` | WS-09 | `evaluate_scene(scene_dir, out_dir, *, reads=, config=)`, the signature WS-03's `criteria` stage and WS-11's `run-criteria` already call; CLI `python -m server.criteria.run` |
| `server/criteria/profiles.yaml` | WS-09 | port of `internal/breadth/c1-rules-3d/profiles.yaml`; `strictest` → `S`; `public` kept as an optional profile for WS-11 comparisons only; numbers reconciled to `criteria.yaml` v0.2 |
| `server/criteria/c1/{engine3d,from_scene_v0,to_verdict}.py` | WS-09 | the 3D engine `internal/breadth/c1-rules-3d/engine3d.py`; `evaluator.path = "C1-rules-3d"`; never the PR #11 solver behind `server.eval.cli run-c1` |
| `server/criteria/c2/{grid_adapter,sweep,veto}.py` | WS-09 | `c2vol` port; grid from the scene's `coverage.grid` asset (y-up → z-up transpose) |
| `server/criteria/c3/{judge,facts,client,select_photos,overlay}.py` | WS-09 | GPT-5.5 / Grok 4.7 only |
| `server/criteria/tests/data/synthetic_flatwall/make.py`, `tests/data/facade_gravel.py` | WS-09 | the only guaranteed positive-pass fixture; the facade gravel patch is derived at test time (ETH3D, never committed) |
| `server/result/{compose,ar,copy,meter,preview,notify,config}.py`, `server/result/review/{render.py,template.html,style.css}` | WS-10 | uses WS-00's `server/contracts/result.py` models; no schema of its own; additive fields only via a contracts PR |
| `docs/components/ws-09-criteria.md`, `docs/components/ws-10-result.md` | WS-09 / WS-10 | per the plan's component-doc rule |

## 2. Artifact names (per capture run)

| Artifact | Producer → consumer | Shape |
|---|---|---|
| `verdicts/verdict.{A,B,C,S}.json` | WS-09 → WS-10, WS-11, WS-03 | verdict v0, `evaluator.path = "C1-rules-3d"`, `rules_profile ∈ {A,B,C,S}` (+ `public` on request) |
| `verdicts/criteria_report.json` | WS-09 → WS-10, WS-03 | `{profiles, timings_s, degraded[], c2: {status, agreement_with_c1, vetoes}, c3: {facts_calls, judge_calls, cost_usd, status}, downgrades, ground_rule, scale}` |
| `verdicts/placement.<profile>.json` | WS-09 → WS-10 | `{profile, chosen_candidate, start_s_m, box_scene_m {center_m, size_m, yaw_deg}, route_polyline_scene_m, route_length_ft, checks_at_spot}`; WS-10 falls back to chain reconstruction when absent |
| `verdicts/c1.<profile>.raw.json`, `c2.sweep.json`, `c3.facts.json`, `c3.judge.json` | WS-09 → reviewer | debugging sidecars |
| `scale/scale.json` (+ copy next to `scene.json`, mirror at `provenance.producer.params.scale`) | WS-05/06 → WS-09, WS-10 | WS-00 `ScaleBlock`; WS-06 already adds `rel_bound_95 × distance` to element bounds (confirmed in the WS-05/06 briefs) |
| `captures/{id}/reads/reads.json` | WS-08 → WS-09, WS-10 | WS-08 `Reads` (`facts[]` for `meter_number`, `cl320`, `panel_recall`; `views_needed[]` with `rd…` ids; `meter: MeterRead`; `panel_count`); confirmed against the WS-08 brief |
| `frame_scores.json` (optional) | WS-07 → WS-09 C3 photo selection | per-frame CLIP ∪ SigLIP class scores (B3 §3.2); **still to confirm with the WS-07 owner** |
| `result.json`, `result.internal.json`, `preview.ply`, `review.html` at the `result` stage root | WS-10 → WS-01 (`/result`, review route), WS-03 | `result.json` = WS-00 `ResultResponse`; `result.internal.json` = true decision, `failure_code`, `degraded`, judge flags, C2 vetoes (never served to the device) |

## 3. Environment variables introduced

WS-09 (`HSS_CRITERIA_*`): `PROFILES=A,B,C,S`, `C2=on`, `C2_BUDGET_S=40`, `C2_PROFILES=default`, `C3=on` (master), `C3_FACTS=on`, `C3_JUDGE=on`, `C3_AUTOPASS=off`, `JUDGE_MAY_REJECT=off`, `JUDGE_REQUIRED=off`, `C3_BUDGET_USD_PER_HOUSE=0.60`, `ABSENT_FACTS=review` (prod) / `ignore` (non-prod, keyed on `SMP_ENVIRONMENT`), `METER_NUMBER_REQUIRED=on`, `MAX_VIEWS=8`. Shared names reused, not introduced: `RULES_PROFILE_DEFAULT=C`, `GROUND_RULE_DEFAULT=angle80` (WS-00 `Settings`), `VISION_EXTERNAL_CALLS` (WS-07/08; WS-07 makes it required in prod), `OPENAI_API_KEY`, `XAI_API_KEY` (the breadth code's `OpenAIChatGraphPooledProdApiKey` / `XAI_CHATGRAPH_PROD_API_KEY` accepted as aliases).

WS-10 (`HSS_RESULT_*`): `MAX_VIEWS=6`, `SIGNED_URL_TTL_S=900`, `PREVIEW_MAX_POINTS=200000`, `METER_AUTOFILL=barcode|never`, `REVIEW_THREEJS_URL` (pinned CDN), `PROMPT_CATALOGUE` (WS-14 file path). Shared: `RULES_PROFILE_DEFAULT`, `SHOW_REJECT_TO_HOMEOWNER=false`, `BASE_WEBHOOK_URL`, `WEBHOOK_HMAC_SECRET`, `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_KEY_P8` (names only).

## 4. Behavioural defaults chosen

1. C1 = `engine3d.py` (3D rules engine); the harness `run-c1` (PR #11 single-chain solver via `hs11.py`) stays a comparison baseline only (C1 dump §1 trap). `from_scene_v0` swaps the ground bands to the selected ground rule (`coverage.sensitivity[rule_id]`), as `hs11._bands` does.
2. Physical battery box height 39.5 in (Base Core) in every profile; profile C's "3 ft" kept as `source` text (C2 dump §4; criteria.yaml says 36 in for C).
3. S may reject with C's thresholds (spec §7.4); C1's `strictest` had `allow_reject: false`. AC distance rule removed from A/B/C/S (spec conflict #23; C1 inherited 3 ft).
4. Element `plus_minus_m` already includes the scale term (contract §3; WS-06 adds it); WS-09 never re-adds `rel_bound_95 × s`; `scale.json` drives routing: `> 0.12` all placement unsure, `> 0.06` no reject on reach rules, `> 0.02` `opening_clearance`/`under_window` never pass (A9 §9 via the WS-05 brief).
5. C2 join: `pass + {fail, unsure} → unsure`; C2 never fails on its own; default profile only, 40 s budget, timeout downgrades passes to `unsure/rule_requires_review`; without a grid C2 emits `not_evaluated`, never pass. Positive-pass tests added because C2's 0-unsafe result came from never passing (C2 dump §3).
6. Judge (C3): one GPT-5.5 call per profile whose placement passes, photos with the chosen box and route drawn in; `min()` join on `reject < manual_review < pass`; judge `reject` clamps to `manual_review` (`JUDGE_MAY_REJECT=off`); judge failure leaves the verdict alone with `judge_unavailable` (`JUDGE_REQUIRED=off`); judge numbers never used (C3 dump §8).
7. C3 facts never pass (`C3_AUTOPASS=off`); single GPT-5.5 reader while off, Grok 4.7 agreement required for benign facts when on; P12 `yes` and `main_panel_type reject` from photos become `unsure/rule_requires_review`, never `fail`.
8. Site composition: decision facts = placement, `main_panel_type`, `panel_recall`, three P12 flags (`not_same_wall` never fails); feature facts informational except `meter_number` (required); absent decision facts → review in prod, ignored in dev; WS-08's `element_ids` placeholders remapped to scene ids; WS-08's `rd…` views re-numbered.
9. `views_needed` cap 8 in the verdict, 6 on the device; merge rule (kind, band, wall_id, spans within 0.25 m); technical `target` in the verdict, customer `prompt_id` + `prompt {title, body}` only in the device payload (verdict schema has no `prompt`).
10. Result payload = WS-00 `ResultResponse` (envelope camelCase, `outcome` snake_case); envelope `viewsNeeded[].prompt` = rendered body string, `outcome.views_needed[].prompt` = `{title, body}`; `outcome.views_needed[i].id == viewsNeeded[i].id`.
11. Hidden reject: device sees `manual_review`, `outcome.reasons = []`; true decision only in `result.internal.json`, the reviewer page and the webhook.
12. Run-level overrides from WS-03's run record: `degraded` or `failure_code` → never `eligible`; no verdict → `manual_review` payload with the failure code as reason.
13. AR anchors only when `scene.frame.source_frame` is an ARKit epoch (`arkit_world:<epoch>` per WS-06; `arkit_world`, `arkit_world/<epoch>` accepted); otherwise text + `reference_crop`; camera 3.0 m out at 1.3 m for walls, 1.0 m out at 1.3 m for ground (WS-14 §3.3).
14. Meter block from WS-08's `MeterRead`: `auto_fill → auto_filled` (barcode or confirmation only, B3 §3.1), `prefill|choose|single_reader → prefill_confirm`, `unreadable`, `disabled|absent → not_captured`; values never logged.
15. Copy wording lives in WS-14's catalogue; WS-10 owns only the `prompt_id` mapping (`vn.<band>.<cause>`, `vn.past_end.<side>`, `vn.close_up.meter`, `vn.confirm_attribute.<class>`, `vn.region.frontier`, `outcome.<kind>`, `reason.<criterion>`, `hint.<code>`, `capture_check.<code>`) and variable filling.
16. Placement confidence = `clamp(min (margin_over_uncertainty − 1) / 2, 0, 1)`, heuristic. Webhook retries exponential 1 s → 3600 s cap for 24 h; APNs payload carries no decision.
17. WS-10 reuses WS-06's `scene/preview.ply` when present instead of rebuilding.
18. WS-09 fixtures need `eth3d-delivery` GT (only site with start-range labels); WS-00/WS-06's `make gt` lists facade and electro only.

## 5. Inconsistencies found (plan, docs, code, sibling briefs)

1. **`run-c1` ≠ the 3D engine.** Plan WS-09 names `engine3d.py` as C1 but its "Done when" says "`run-c1` reproduces 0 unsafe passes"; `run-c1` is the PR #11 single-chain solver (`hs11.py`). WS-03's `criteria` stub also runs `hs11.run` until WS-09 lands; fine as a stub, wrong as the engine.
2. **WS-11's positive-pass row uses the wrong coordinate.** `docs/build/ws-11-eval-and-regression.md` §4.7 expects the facade C/`footprint3in` chosen candidate at `start_s_m` overlapping `[9.45, 10.24]` m; that is C1's `u` (metres from w1's left end, 31.00–33.58 ft), not chain `s` from the meter. In chain `s` the passing spots are at −3.891…−3.383 m (`internal/breadth/c1-rules-3d/outputs/verdicts/facade-M1__C-c1rule.json`; w1 `s_range_m` starts at −12.832). Also, on the unpatched harness GT the same engine gave `manual_review` (`eth3d-facade__gt-laser__C.json`: ground unobserved, no ground material), so the row needs the gravel-patched scene or must expect `manual_review` there.
3. **`profiles.yaml` vs `criteria.yaml` v0.2**: `strictest.allow_reject: false` (spec: S-reject = C-reject); AC 3 ft inherited from PR #11 (Base has no AC distance rule, spec #23); B headroom inherited 6.5 (spec: 6–7 band); B ground lacks `dirt`; slope 10 % (C1) vs 5° (C2).
4. **Battery height**: criteria.yaml C `h: 36` in vs Base Core 39.5 in used by PR #11, C2 and S (spec conflict #5).
5. **Scale block home**: the plan (§4) treats the A9 block as a contract, but `scene.v0.schema.json` has no `scale` field; WS-00's brief both says "lives in `provenance.producer.params.scale`, not as a new field" and "apply the two additive scene edits (`scale`, `meter_model`)". One statement must go.
6. **Plan WS-09 wording "plus_minus = shape + rel_bound_95 × s"** vs contract §3 and the WS-05/06 briefs (the producer adds it). WS-09 does not re-add.
7. **`views_needed.prompt`**: plan WS-09 lists `prompt` among the verdict's `views_needed` fields; `verdict.v0.schema.json` forbids it. Resolved by WS-00's `ViewNeededDevice`; but WS-00 leaves `prompt` as both a string (envelope) and `{title, body}` (outcome), and WS-14 uses `reference_image {image_id, crop_px}` where WS-00 uses `reference_crop {image, box_px}`.
8. **Event data casing**: WS-00 `VerdictReadyData {runId}`, WS-14 `verdict_ready {runId, kind}`, WS-03 `verdict_ready {run_id, status, outcome_kind}` and `retake_request {run_id, views_needed, member_actions}` vs WS-00 `RetakeRequestData {viewsNeeded, memberActions}`. One spelling is needed; WS-10 follows WS-00 plus `kind`.
9. **Golden fixtures lack the point clouds**: `internal/eval-harness/results/<site>/gt-laser/` has `scene.json` + `coverage.npz` but not `laser.ply`/`occluders.ply`, which `CoverageQuery.from_scene` requires; electro/delivery copies lack `rule_id` in `coverage.sensitivity[]` (no `footprint3in`). Rebuild (`build_gt.py`, `build_gt_c2.py`); ETH3D-derived, fetch-only. `make gt` (WS-00/WS-06) omits `eth3d-delivery`.
10. **No `eth3d-delivery` packet**: the plan says "ETH3D facade/electro/delivery packets in `internal/data/packets/archives/`"; the archives hold facade, electro, tt-barn and six `yt-*` tars only.
11. **housepipe writes `format: "house-scene/v0-modal"`**, not `base-scene/v0`, and a scale block with `status: lidar_metric` (A9 enumerates `verified | single_reference | unverified`); WS-06 converts.
12. **Object classes**: plan WS-07 adds `water_heater`, `shrub/tree`, `deck/stairs`, `eave/porch_roof`, `wall_box (comms)`; `scene.v0.schema.json` has no `water_heater` (→ `other` + `label`), no `driveway` object (ground material `drive`), no `fence` object (wall `role: fence`); `from_base_scene.CLASS` and `hs11.TYPE_MAP` disagree (`meter_can`/`disconnect` → `elec_box` only in hs11; `basement_well → door`, `hot_tub → pool` only in C1). Union used.
13. **`house-labels.md` says 28 label files**; `labels-v0/` holds 32 (delivery added in v0.4). `c1-rules-3d.md` and `C1-rules-3d.md` are byte-identical duplicates. Verdict schema text says `rules_profile` "public, A, B, C or strictest" while labels and exports use `S`.
14. **electro GT ground region is `concrete`** while the C1/C2 notes treat the yard as asphalt drive or untyped (labels: every door-wall spot fails the drive rule under C); no start-range labels there, so scoring is unaffected.
15. **C3 judge may reject** per the C3 dump §8 (`min()` including reject) vs plan/gate-decision "downgrade toward review only". Clamped to `manual_review` by default.
16. **Photo facts "never pass"** (plan WS-09) makes `eligible` unreachable whenever panel photos exist unless Base allows auto-pass; exposed as `C3_AUTOPASS` and `ABSENT_FACTS`.
17. **Env names**: WS-08 §12 still cites `CRITERIA_*` for WS-09 (now `HSS_CRITERIA_*`); WS-04 uses `HSS_POSES_*`, WS-08 `READS_*`, WS-07 `OBJECTS_*`; WS-00 may sweep. The breadth code's pooled key names differ from `OPENAI_API_KEY` / `XAI_API_KEY`.
18. **B3 report** is final (`internal/breadth/B3-cheap-gate.md`) but plan §1 still lists it pending; its meter policy (pre-fill unless barcode) is handled in WS-08 and WS-10. **E2E report** still absent; WS-09 §12 assumes the golden thresholds.
19. **WS-11 requests `--profiles … public`** from `evaluate_scene`; WS-09 accepts `public` (PR #11 demo numbers through engine3d, `rules_profile: public`), but the harness `demo` labels still match only the `run-c1` baseline's `rules_profile: demo`.
20. **`capture_check`/`hint` copy**: WS-03 ships neutral messages and expects WS-10/WS-14 copy; WS-14 adds `prompts[]` to `capture_check` (additive to hosted-architecture's `missing[]`). WS-10 maps `hint.<code>`/`capture_check.<code>` ids; the wording is WS-14's.

## 6. Notes for the WS-07 / WS-08 owners

- WS-09 consumes `objects[].attrs` (`operable`, `ground_level`, `well`, `movable`) and `source.views[]` as WS-07 wrote them into the scene; it adds no objects. `frame_scores.json` (per-frame gate class scores) is optional and only speeds C3 photo selection.
- WS-09 copies WS-08's `facts[]` into every verdict and remaps `region.element_ids`; WS-10 fills `MeterBlock` from `reads.meter` (`MeterRead`) and `confirm.still_id`. Both briefs were checked against `docs/build/ws-08-meter-panel-reads.md` §3.
- `VISION_EXTERNAL_CALLS` is the shared kill-switch in WS-07, WS-08 and WS-09.
