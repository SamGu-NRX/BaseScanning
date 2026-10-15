# Reconciliation queue for the builder briefs

Inconsistencies reported by the brief writers, to be resolved in one pass after all 15 briefs land. Source of truth order when briefs disagree: measured breadth report > handoff notes > contract docs > plan text. The plan (`docs/server-pipeline-build-plan.md`) is updated to match the resolution; briefs are edited in place.

## Cross-cutting

- Env-var prefixes differ across briefs (`HSS_POSES_*`, `CRITERIA_*`, `READS_*`, `SCALE_*`); pick one scheme (`HSS_<WS>_*` or per-area prefixes) and one shared switch `VISION_EXTERNAL_CALLS`. Reported by WS-08.
- Verdict schema criterion `id` example says `meter_identity`; the criteria catalogue id is `meter_number`. Fix the schema example. Reported by WS-08.
- Golden packets all have `stills: null`; every brief whose acceptance test needs stills (WS-05, WS-07, WS-08) must use a fixture with stills; WS-00 should add one golden packet with stills. Reported by WS-08.
- Breadth code paths: the B2 code lives at `internal/breadth/b2/` (B3 meter raw answers under `b2/b3-raw/`), not `internal/breadth/b2-equipment-reading/`. Check every brief's Reuse section against the actual folder names. Reported by WS-08.

## WS-08 meter and panel reads (13 items reported)

1. Plan WS-08 says "any hazard flag from either reader means review" and puts Grok on panels; the measured B3 cascade is GPT-5.4-mini then GPT-5.5 with hazard `confirmed` only when both flag (5/5 hazards, 0 false alarms vs 4). Brief follows B3; update the plan line and the WS-09 brief, which still carries the "either reader" wording.
2. Plan WS-08 "at least 96% when agreeing" is a rounding of 65/68 = 95.6%; use the exact figure.
3. Plan WS-05 runs `meter_model.py` itself on `meter_close`; the brief has WS-08 make the call and hand `meter_identity` to WS-05 (steered to the WS-05 writer 22:02 UTC).
4. hosted-architecture 90 s Grok timeout vs measured p90 78 s; brief uses 120 s. Update hosted-architecture.
5. Contract text "median views < 2 breaks the contract" vs `sceneio.verdict_contract_errors` applying `rule.min_views`; brief uses `min_views: 1` for close-up facts. Clarify in `docs/scene-and-verdict-format.md`.
6. B2 handoff maps `cl320` "other CL means fail"; the contract maps a clean feature fact to `pass` with its value. Brief follows the contract; note in WS-09.
7. No route for the device to answer the one-tap confirm: brief proposes `POST /captures/{id}/confirmations` (steered to the WS-01 writer 22:02 UTC; WS-14 brief must add it too). About 80% of houses carry an unclean `meter_number` until confirmation; criteria spec §1 is ambiguous on whether that forces review. Decision for Hunter/Base.
8. `inputs/meter_manifest.csv` is missing from the B2 exclusion list; its `title`/`page_url` columns carry Commons file names with digit runs. Add to the sync exclusion list.
9. Plan's "PR #16 `locate.candidates` ranking as fallback" needs OCR lines with boxes; OCR is a no-go, so only box-free features are ported. Update the plan line.

## From the E2E report (`internal/breadth/E2E-criteria-on-recon.md`, landed 22:03 UTC)

- WS-09: on photo models the rules-3d engine decides only 1–7% of criterion cells while the PR #11 single-chain solver decides 54–56% at true or meter-model scale (82% on the laser scan). Causes: missing edge-on walls, photo coverage graded grazing or unobserved where the laser is observed. The WS-09 brief must set a decidability target, explain the coverage evidence rule's effect on photo models, and specify the hybrid: the chain solver decides the meter-wall chain when the 3D engine returns unsure and the chain's inputs are observed; the 3D engine remains the only path to a pass on placement candidates; C2 vetoes as before.
- WS-06: edge-on walls (facade w2, w3) are not reconstructed from photos; the scene builder needs wall completion from ARKit plane anchors and the capture chain, with `coverage: unobserved` kept honest, and the coverage grid must emit `views_needed` for head-on views of each wall in the meter chain.
- WS-10 and WS-14: `views_needed` must carry the wall id and the required viewing angle band, because coverage grading (not geometry error) is the main cause of undecided outcomes.
- WS-11: two harness bugs to fix and regression-test: a viewing angle rounding to 90.2° fails the schema maximum of 90 (C2 verdicts on π³-fused facade models were dropped); the reference extractor produced a reversed door span on facade w3 that crashed the PR #11 adapter (sort spans before the rules).
- WS-04: tight priors lock onto simulated ARKit drift on ETH3D (ARKit scale error −2.95% facade, +2.48% electro); A6 says real phones drift less. The WS-04 acceptance on ETH3D must use the A6 real-phone segments as the gate, not the simulated drift alone.
- WS-13: no labelled passing site exists anywhere, so the pass path is untested (steered to the WS-13 writer 22:05 UTC).
- Plan: the "0 unsafe passes" headline must be qualified until a labelled pass site exists; decidability (share of criterion cells decided) becomes a tracked metric next to unsafe passes, with the E2E table as the baseline (facade 12–13%, electro 24–25% on photos vs 38% / 75% on laser).

## Per-writer inconsistency tables (read these directly, they are not duplicated here)

- `internal/build/layout-decisions-contracts.md` (WS-00 and WS-11): §2a lists 9 clashes with sibling briefs and 3 spec PRs to file; §3 lists 20 plan/doc/code inconsistencies. Resolve the sibling clashes first, they gate everything else: result `prompt` shape and `target_region_arkit` key (WS-10 vs WS-14), WS-10's second result schema, `normalized.json` vs `normalized_packet.json` and `validation.json` vs `validation_report.json` (WS-02 vs WS-03), `poses/cameras.json` + `registration_report.json` (WS-04) vs `poses/poses.json` + `registration.json` (WS-00 §3.9), env-var naming (`HSS_*` vs bare names), fixture paths `data/fixtures/gt/...` vs `data/fixtures/scenes/.../gt-laser/`, API casing (camelCase envelope vs plan §5 snake_case), `views_needed.prompt` absent from verdict v0, `/v1/healthz` missing.
- `internal/build/layout-decisions-ws-02.md` §2 (WS-02): `packetgen/checks.py` is the synthetic-truth checker and mostly not portable; `PacketReport.errors: list[str]` cannot feed the 422 body; spec §3.2 depth-on-every-keyframe vs `depth: null` in the spec's own example; spec typos (`obliqueCloseUp.still` purpose, `$id`, undefined role `compat`); hosted-architecture stale notes (`parentPacketId`, `capture_check` emitting `door_view_meter_side` before finalize, 422 before finalize, `flow` in API but not in packet); fixture layout `streams/<variant>/` differs from the spec layout; WS-04 reads `packet_id` instead of `packetId`.
- `internal/build/layout-decisions-ws-07.md` (WS-07): plan §6 cascade line must be rewritten to the B3 cascade; OWLv2 seed 0.3 / support 0.2; opening detection keeps GDINO; gate-decision §3 cost table superseded by B3; WS-00 `Settings` lacks `VISION_EXTERNAL_CALLS` and `OBJECTS_*`; WS-09's class map drops `other` and never maps bin/shrub/tree/gate/deck/stairs/eave/porch_roof; scene v0 enum lacks `water_heater` and `fence`.
- `internal/build/layout-decisions-ws-08.md`, `-ws-03.md`, `-ws-05.md`, `-ws-14.md`, `-geometry.md`, `-criteria.md`: read their inconsistency sections the same way; WS-01, WS-12, WS-13 files land later.

## Structural decisions the reconciler makes (record each in the report)

- Repo layout: the plan header's `packet/ recon/ scene/ criteria/ eval/` folders vs WS-00's `server/` package. WS-00 wins; the plan is rewritten to the `server/` layout with the breadth paths staying under `recon/<path>` as read-only experiment archives.
- Artifact and stage names: WS-00 is authoritative unless a sibling brief measured something WS-00 did not; then WS-00 is amended and the sibling kept.
- API casing: camelCase on the wire (matches `packet.json`), snake_case in Python; the plan's §5 examples are fixed.
- pycolmap 4.2.0 (harness, packetgen, A1/A6) vs 3.11.1 and Python 3.11 in the Modal image: pick one version for the server image and note the rebuild in WS-04 and WS-12.
- Environment variables: one scheme, `HSS_<AREA>_*` plus the shared `VISION_EXTERNAL_CALLS`, applied to every brief.
- Stage graph for objects (WS-03 item 1): `objects` runs as 2D detection in parallel with `poses` and `reads` right after `validate` (WS-06 and hosted-architecture ordering); 3D placement (`objects_3d`, `merge_objects`) runs after `scene`. Fix the plan mermaid, WS-00's stage table and WS-11's expected event order to that.
- `Pipeline` ownership (WS-03 item 2): WS-03 builds the coordinator and `Pipeline` on WS-00's `app`/`image` at M0; WS-12 owns deployment of it, not its construction.
- `StateStore`, `StageContext`, `StageRecord` (WS-03 item 3, WS-10 `set_status`): amend WS-00 with the additive fields the coordinator needs (per-run stage listing, blobs, spend, `heartbeat`, `deadline_at`, `set_status`) rather than leaving each brief to subclass.
- Cold start (WS-03 item 6): measured L4 cold start is 73 s against hosted-architecture's 60 s p95 budget, so prewarm is load-bearing; update hosted-architecture's latency budget and mark `benchmark.md` as being refreshed by the Modal agent.
- `capture_check` codes differ between hosted-architecture, WS-02 and WS-14: one code list, owned by WS-00, referenced by the others.

## From the Modal GPU benchmark (`internal/modal-pipeline/benchmark.md`, landed 22:28 UTC)

- Timing: L4 method time 148 s at 40 photos, 259 s at 60, 324 s at 70 (mapper 96 / 186 / 237 s dominates; π³ + coverage stay inside their bars); electro LiDAR packet 77 s; A10G facade-70 230 s. The 3 min p50 holds only at about 40 frames; the 8 min p95 holds everywhere (max client time 397 s). WS-04 and WS-03 must add a frame budget for the mapper (keyframe thinning to ~40–48 frames for SfM, remaining frames registered as fixed-ARKit or via image registrator), and hosted-architecture's latency table gets the measured numbers.
- Accuracy red flag: housepipe on the facade photo packets scores key-distance medians of 24–26 in and p90 39–47 in with camera RMS 0.19–0.83 m, against the 2–6 in bar, while A1/A6's tight-prior recipe (pycolmap 4.2.0) measured 0.9–1.9 in on openings. The electro LiDAR packet scores 1.38 in median. Treat housepipe's SfM path as scaffolding only: WS-04's port source for the prior pipeline is the A1/A6 recipe (`internal/breadth/a1-classical/`, `a6-iphone-quality/` code and their handoff notes), the acceptance stays the A6 LaMAR numbers, and the builder must explain the facade discrepancy (matching parameters 20/12 vs 40/20, largest-epoch-only, the 3.11.1 prior-flag C++ hack, subsampling) before porting.
- Toolchain: the Modal image runs Python 3.11.5, COLMAP 3.11.1 CUDA, pycolmap 3.11.1, numpy 1.26.4, torch 2.5.1; A1, A6 and the harness pin pycolmap 4.2.0. Decision: WS-12 pins the pycolmap 4.2.0 line with a matching COLMAP build for parity with the measured recipe (clean image compile ~609 s, prebuild and pin the image id), unless WS-04 proves the 3.11.1 path reproduces A6 within tolerance; record whichever wins in WS-04 and WS-12.
- Modal facts for WS-03 and WS-12: `scaledown_window=900` did not keep a container warm (every call booted, 30–76 s overhead), so prewarm needs `min_containers`/keep-warm or a scheduled ping; cold start after image pin is weight load 15–22 s; L4 memory ~7 GiB of 23 GiB; geometry-only L4 cost $0.05–0.15 per house before VLM; total benchmark spend $0.85.
- Scene export is `house-scene/v0-modal`, not scene v0; the WS-06 port must emit scene v0 (already in the queue via the geometry writer).

## Decisions for Hunter or Base surfaced by the briefs

- Does an unconfirmed meter number (pre-fill not yet tapped) force `manual_review`, or does the verdict go out with the meter criterion marked `pending_confirmation`? (WS-08 item 7)
- Licence stance: may ETH3D-derived JSON (extracted ground truth, fixtures) live in the private server repo with licence notes, or must it stay out of every repo and be rebuilt from the dataset by script? WS-00 defaults to the private repo with notes. (WS-00 §3)
- Photo-fact criteria (panel condition and recall, CL320, meter can size and mount, main panel count) currently "never pass" because the VLM judge is downgrade-only; with panel photos present that makes `eligible` unreachable without a human review. Proposed default for the reconciler to write in: a photo-fact criterion passes when both GPT-5.5 and Grok 4.7 agree on the read, neither flags a hazard, and the read is not on the recall list; any disagreement or single hazard flag goes to review; the judge remains downgrade-only for geometry criteria. Hunter/Base confirm whether this counts as an acceptable auto-pass. (WS-09 §5)
- Battery height: `criteria.yaml` v0.2 rule set C says 36 in while Base Core, PR #11, C2 and rule set S use 39.5 in; the reconciler standardises on 39.5 in and flags it for Base confirmation. (WS-09 §5)
