---
cursor:
  subagentId: "bc-54a553c8-de64-590b-a1f9-0edbf2e408d3"
---

# Decision log, group A (WS-00, WS-01, WS-02, WS-03, WS-11, WS-12)

Fast-mode application of `internal/build/seam-decisions.md` (`SD-nn`) plus the group's own inconsistency resolutions, edited in place in `docs/build/` on 2026-09-26 (22:45–23:10 UTC). This file replaces the originally assigned `applied-group-a.md`. Template section order kept in every brief; no code bodies added; measured numbers copied verbatim. Line counts after editing: WS-00 394, WS-01 269, WS-02 220, WS-03 236, WS-11 201, WS-12 309.

Sources of the local resolutions: `layout-decisions-contracts.md` §2a/§3, `layout-decisions-ws-01.md` I-01…I-22, `layout-decisions-ws-02.md` §2 items 1–24, `layout-decisions-ws-03.md` §2 items 1–25, `layout-decisions-ws-12.md` B.1–B.15, `layout-decisions-criteria.md` §5 item 2, `layout-decisions-geometry.md` §6 item 10, `reconciliation-queue.md` (E2E and Modal-benchmark sections).

## WS-00 `ws-00-skeleton-and-contracts.md` (authority; amended where siblings measured more)

| Where | Decision applied |
|---|---|
| header, §2 | Pointer to `seam-decisions.md`; `scripts/make_mini_packet.py` added to owned files (SD-29) |
| §3.2 | `check_packet` returns `ValidationReport` from new `server/contracts/validation.py`; `PacketReport` dropped (SD-14; WS-02 §2 item 4) |
| §3.4 | `ViewNeeded.view_angle_deg?: Span` noted as pending spec PR 2 (SD-27; E2E consequence "wall id + viewing-angle band") |
| §3.7 | Whole result payload camelCase incl. `Outcome`, `RecommendedPlacement`, `ViewNeededDevice` (`viewingAngleDeg`, `promptId`, `prompt {title, body}`, `targetRegionArkit {surface}`, `referenceCrop {image, boxPx}`), `RejectReason`; single schema location; envelope `viewsNeeded` = `outcome.viewsNeeded`; the "reconciliation still owed" paragraph replaced by the settled shape (SD-25/26/27; contracts §2a item 1, §3 item 14/15) |
| §3.8 | `ApiError` codes gain `format_version_unsupported` (SD-22); `EventType` = 10 with `confirmation`; typed `data` models incl. `FilesCommittedData`, `ConfirmationData`, `StageData.runId/gpu/durationS/errorCode`, `VerdictReadyData.kind`, `FailedData.next`, `HintData.promptId/frames`, `CaptureCheckData.meterRead/prompts` (SD-19; WS-01 I-05, WS-03 decision 14, criteria §5 item 8); `ConfirmationRequest/Response` rows and route (SD-21; WS-01 W1-29); `HealthResponse` + `GET /v1/healthz` (D-26, SD-36) |
| §3.9 | `CaptureStatus` = single device-visible status via `set_status` (SD-18); `StageName` 11 with `objects_3d`, dependency list, optional stages (SD-08); ids incl. `run_id = "run_" + sha256(packet)[:16]` (SD-09; WS-01 I-03 vs D-09); `CaptureRecord.owner_principal`, `FileRecord.key`, `CommittedFile` (SD-17/30); `RunRecord`, `StageRecord`, `EventRecord`, `StageContext` additive fields (SD-17; WS-03 decisions 2/12/19); storage keys `uploads/` + promotion, `early/`, transcoded stills under the capture prefix, no attempt folders (SD-01/02/30; WS-01 I-01); artifact table rewritten per stage folder: `packet/capture_check.json`, `poses/` names, `objects/objects.json` (2D) + `objects/objects_3d.json` + `merge_objects`, `dense/`, `coverage/`, `scene/` copies, `verdicts/` sidecars, `result/result.internal.json` + `preview.ply`, `run/` files with `enter_s` (SD-02…SD-07, SD-33) |
| §3.10 | `Storage` gains the six WS-01 methods, `ObjectMeta.md5_b64` nullable (SD-16); `StateStore` rewritten as the one protocol with the WS-01 kv/CAS/capture methods and the WS-03 run/stage/heartbeat/blob/spend methods, expressed as a grouped signature list (code block was over 25 lines); backends in `server/common/backends/` with Dict key prefixes (SD-15); `Settings` naming scheme (bare platform + shared knobs, `HSS_<AREA>_*` per area), new names `STATE_DICT_NAME`, `MODAL_APP_NAME`, `GCP_AUTH_MODE`, `VOLUME_ROOT`, `CAPTURE_API_PUBLIC_URL`, `R2_*`, `UPLOAD_URL_HMAC_SECRET`, `BASE_WEBHOOK_URL`, `GIT_SHA`, `VISION_EXTERNAL_CALLS`; `load()` factory (SD-11/12; contracts §2a item 7 resolved by keeping WS-00's bare shared names because WS-03/06/09/10 already adopted them); logging key union (SD-13); `CaptureCheckCode` (16) and `HintCode` lists (SD-20; WS-03 item 14) |
| §3.12 | `server.objects.run_3d` for `objects_3d`; orchestrator entry points `start_run(capture_id, packet_sha256, packet_uri, *, parent_run_id)`, `prewarm`, `dispatch_early`, `on_confirmation`, `cancel_capture`, `sweep`, `validate_manifest` re-export; no adapters (SD-23/24; WS-01 I-10, WS-03 decision 23) |
| §4 step 9 | `Pipeline` built by WS-03 on WS-00's `app`, composed by WS-12 (SD-32; WS-03 item 4) |
| §4 step 13 | `fetch_fixtures --item`, tar extraction, `make_mini_packet.py` (SD-28/29) |
| §5 | `colmap_prior.py` reference only with the housepipe facade numbers (SD-35); `pycolmap==4.2.0` `[provisional: WS-04]`, Python 3.12, image says 3.11.5 (SD-34; D-16 amended) |
| §6 | 33 committed manifests (8 houses × 4 + photo-only), `mini-arkit` row, nine tars incl. the four extra `yt-*` with sizes, stills-fixture note (SD-29; WS-02 decision 34, WS-01 W1-40, WS-03 item 25) |
| §7, §8 | `make fixtures ITEM=`; `make validate` counts 36 packet manifests; mini-arkit file check |
| §12 | pins row updated (provisional); decidability row (E2E report) |

## WS-01 `ws-01-ingest-api.md`

| Where | Decision applied |
|---|---|
| header, §0 | Seam pointer; inputs name the SD-23 entry points and `server/common/backends/`; outputs include the backends (SD-15/16/23) |
| §1 | Start gate lists WS-00's `validation.py`, `CommittedFile`, confirmation models, `HealthResponse`, full `StateStore`, `resolve_*`, backends, orchestrator stubs; fixtures via `make fixtures ONLY=all` (incl. `yt-12Ks1idWj40`) and committed `mini-arkit`, no hand `tar` (SD-29); `HSS_INGEST_HOOKS=null` interim (SD-11) |
| §2 | Backends move to `server/common/backends/` as additive commits; `server/api/locks.py` holds the in-process lock; contracts additive commit dropped (models are WS-00's); `server/contracts/**` forbidden (SD-15/16/21) |
| §3.2 | Confirmation/`FilesCommittedData` models labelled as WS-00's; `EventType` 10 |
| §3.3 | `Storage` six methods are protocol members implemented here (SD-16) |
| §3.4 | `IngestStore` removed; WS-00 `StateStore` used directly; `ModalDictState` shared with WS-03; DDL aligned to WS-00 `RunRecord`/`StageRecord` (+ `blobs`, `spend`) (SD-15/17; I-04, I-19) |
| §3.5 | `Hooks` bind 1:1 to the SD-23 entry points; `CommittedFile` is WS-00's; `HSS_INGEST_HOOKS=auto` |
| §3.6 | Env table: platform names from WS-00 (`STATE_DICT_NAME`, `MODAL_APP_NAME`, `GCP_AUTH_MODE`, `VOLUME_ROOT`, …), `HSS_INGEST_HOOKS` (SD-11; I-06) |
| §3.7 | Module interfaces marked authoritative for WS-12's root stack (SD-31; WS-12 B.1, A.17 resolved in WS-01's favour: pool per environment `modal-<env>`) |
| §4.8–4.10 | Events via `store.events_after`/`append_event` with typed data; status = `CaptureRecord.status`, no run-status mapping (SD-18; I-04, W1-25 superseded); result read through `run.artifacts`, `verdicts/` and `result/` keys, validated against WS-00 `ResultResponse` with a `result.schema_drift` log, never blocking (SD-25/26; I-07) |
| §4.11–4.12 | `CaptureRecord.status` wording; `answers` ⊂ `viewsNeeded ∪ memberActions` (one shape, SD-26) |
| §4.18 | `GCP_AUTH_MODE=wif` (SD-11) |
| §4.19 | Label `capture-api` kept (WS-12 adopted it), URL stated, `min_containers` from `HSS_API_MIN_CONTAINERS`, `max_containers=1`, standalone interim deploy with `HSS_INGEST_HOOKS=null` (SD-32) |
| §6, §7, §8 | `yt-12Ks1idWj40` from the manifest; `mini-arkit` from WS-00 (tests copy to `tmp_path`); replay flags `--token|--token-env`, `--until` (SD-36); `HSS_INGEST_HOOKS=null` in commands and done-when |
| §10, §11 | `GCP_AUTH_MODE=sa_key`; handoff to WS-03 through the SD-23 entry points and WS-00's `StateStore` |

## WS-02 `ws-02-packet-validation.md`

| Where | Decision applied |
|---|---|
| header, §0 | Seam pointer; outputs named with WS-00 keys `packet/validation.json`, `packet/normalized.json`, `packet/capture_check.json`; stills path decided (SD-02); inputs read `captures/` after promotion (SD-30) |
| §1 | Start gate lists `server/contracts/validation.py`, `capture_check.py`, `validation-report.v0` schema; 33 manifests + `mini-arkit` committed; nine tars via `make fixtures ONLY=all`; the 4-entry manifest PR withdrawn (SD-14/20/29; WS-02 decision 34) |
| §2 | `report.py` re-exports WS-00 types; `validation-report.v0` schema is WS-00's; `data/fixtures/**` fully forbidden; WS-03 adds the `validate_manifest` re-export (SD-23; WS-01 I-10) |
| §3 | `ValidationIssue`/`ValidationReport` shown as WS-00's; `CaptureCheck.missing/unknown` and `CheckItem.code` typed `CaptureCheckCode` (SD-20); stage entry uses `packet/…` keys, `ctx.heartbeat`, `packet_dir` always present (SD-17) |
| §4.16 | Pose-frusta codes `wall_left_of_meter`, `wall_right_of_meter`, `ground_downward_left`, `ground_downward_right` replace `ground_downward_<wallId>`/`wall_band_<wallId>` (SD-20; WS-03 item 14); `CaptureCheckData` may carry `meterRead`, `prompts` (SD-19) |
| §4 issue table | `format_version_unsupported` listed among the shared WS-00 codes (SD-22) |
| §6, §7 | `yt-12Ks1idWj40` from the manifest; hand-tar loop removed; `export-schemas` covers two schemas; `mini-arkit` validate command |
| §11, §12 | Manifest handoff closed; `PacketReport`, `CaptureCheckData`, transcoded-stills rows marked decided (SD-14/19/02) |

## WS-03 `ws-03-orchestrator.md`

| Where | Decision applied |
|---|---|
| header, §0 | Seam pointer; eleven stages and the `merge_objects` join; `run/{timings,cost,stages}.json`; `Pipeline` on WS-00's `app`; no contracts PR (SD-08/17/32) |
| §1 | Start gate lists WS-00's 11-stage enums, `CommittedFile`, `StageContext` fields, `capture_check.py`, full `StateStore`, backends, SD-23 stubs, `mini-arkit`; WS-02 `validate_and_normalize` + `partial_from_metas` + `validate_manifest`; WS-06 `merge_objects`; fallback additive commit for `state_modal_dict.py` (SD-15; WS-02 item 23) |
| §2 | `models.py`/`state.py` dropped, eleven stubs, `HSS_ORCH_STAGE_STUBS`; `server/contracts/**` and `server/common/**` forbidden; `server/modal_app.py` import-only (`app, GPU_IMAGE, CPU_IMAGE, volume, KNOBS`) (SD-11/17/32) |
| §3 | `RunControl`/`StageControl` folded into `RunRecord`/`StageRecord`; coordinator-private blob `blob:run_control:{run_id}`; `OrchestratorState` block removed; Dict prefixes limited to WS-03's (SD-15/17; WS-03 decisions 2, 12); stage table rewritten with WS-00 folder keys (`packet/`, `poses/cameras.json`…, `objects/objects.json`, `dense/`, `coverage/`, `scene/`, `verdicts/`, `result/`) and the `objects_3d` row; `merge_objects` + re-validate before `criteria` (SD-01…SD-08; WS-03 items 2, 17); `HSS_POSES_SFM_MAX_FRAMES=48` frame budget (SD-10); `OrchestratorStageContext` block replaced by WS-00's `StageContext` (SD-17; item 19); entry points block = SD-23 incl. `on_confirmation` and the `validate_manifest` re-export (WS-01 I-10); events camelCase with `runId`, `kind`, `next` (SD-19; criteria §5 item 8); codes from WS-00 lists, `too_few_keyframes` dropped (SD-20); config renamed `HSS_ORCH_*`, `STATE_DICT_NAME`, `MODAL_APP_NAME`, `VISION_EXTERNAL_CALLS` from `Settings` (SD-11; item 16, WS-01 I-06) |
| §4.1 | `HSS_ORCH_*` knobs; direct `Pipeline().warm.spawn()`; `enter_s` recorded; warm-hit-rate rule (SD-33; Modal benchmark 22:28) |
| §4.2 | `early_reads(capture_id, file: CommittedFile)` reading `file.key` (SD-30; WS-01 W1-03) |
| §4.3 | `start_run` signature, `set_status(processing)`, eleven `StageRecord`s, `parent_run_id` in `RunRecord`, `parent/scale/scale.json` (SD-09/17/18/23) |
| §4.4 | `StageRecord.worker_call_id`/`error_code`; `vlm_objects` includes `objects_3d`; step (i) `merge_objects` + validate; `run/timings.json` with `enter_s`, `merge_objects_s`, `run/cost.json`, `run/stages.json` (SD-06/07) |
| §4.5, §4.7 | `HSS_ORCH_*` names; storage keys without attempt folders; measured L4 $0.05–0.15 per house (SD-01/38) |
| §4.8 | `packet/validation.json imagesUsable == false`; `poses/registration_report.json`; `scale/scale.json`; `objects_3d` among optional stages; `scene/scene.json` after `merge_objects`; `verdicts/verdict.<P>.json`; `set_status` and `RunRecord.failure_code`/`degraded` (SD-03/04/07/17/18) |
| §5 | `Pipeline` and every function on WS-00's `app`, registered by WS-12's import; `images.py` interim; `KNOBS` (SD-32, WS-12 design adopted); `bench` is WS-12's |
| §6, §7, §8 | `yt-12Ks1idWj40` via `make fixtures ITEM=`; `mini-arkit` replaces the conftest synthetic packet (SD-29); env renames; `SMP_ENVIRONMENT=dev` deploy line; done-when partial order with `objects_3d`, WS-00 keys, `run/*.json` |
| §11, §12 | WS-12 composition wording; `state_postgres.py`; Pipeline, objects order, additive fields and default-profile rows marked decided (SD-08/11/17/32); WS-04 timeout row cites `HSS_POSES_TIMEOUT_S` 400 s (the WS-04 brief's value) and SD-10 |

## WS-11 `ws-11-eval-and-regression.md`

| Where | Decision applied |
|---|---|
| header, §0 | Seam pointer; inputs/outputs use WS-00 keys (`run/timings.json`, `scene/scene.json`, `verdicts/`, `result/result.json`), `evaluate_scene`; `e2e.yml` ownership stated (SD-37) |
| §3.1 | Replay flags `--until verdict|processing`, `--token|--token-env` (default env), Python API `until=` (SD-36; WS-12 Q6) |
| §3.3 | `gates.yaml`: `decidability` report-only block with the E2E baselines; positive-pass rows in chain `s` `[−3.891, −3.383]` on `gt-laser-gravel`, unpatched GT row expects `manual_review`, e2e row kept; LaMAR `openings_p90_in_max: 4.6` (SD-39/40; criteria §5 item 2, geometry §6 item 10) |
| §4.5–4.7 | `--until processing`; `run/timings.json` downloaded; `decided_share` metric; positive-pass paragraph rewritten with the `u` vs `s` explanation and the gravel derivation command (SD-39) |
| §4.13, §4.14 | LaMAR 4.6 with the measured per-segment numbers; the two E2E harness bugs (90.2° clamp, reversed door span) with regression tests (queue WS-11 item) |
| §7 | `--token-env CAPTURE_API_TOKEN`, `--until verdict` |
| §8 | Gates-offline expectations for the three positive-pass rows, `decided_share`, the two bug tests; `verdicts/` key; `time_to_verdict_p50_s ≤ 180` tied to the 48-frame budget (SD-10); LaMAR 4.6 |
| §9.5 | Event partial order `validate → {poses, objects, reads} → … → scene → objects_3d → criteria → result` (SD-42; WS-03 item 2) |
| §10 | Cost references replaced by SD-38 (VLM ≈ $0.30 baseline, $1.50 cap, L4 $0.05–0.15) |
| §12 | Rows added/marked decided: positive-pass coordinate and scene, LaMAR gate, `/v1/healthz` shape, `CriteriaConfig` names (SD-36/39/40/41) |

## WS-12 `ws-12-deploy-and-infra.md`

The writer had already re-based the brief (after 22:15 UTC) on WS-01's module interfaces, the per-environment WIF pool, `SMP_ENVIRONMENT`, `Pipeline` in WS-03's `modal_functions.py`, `e2e.yml` under WS-11 and the `gs://hss-fixtures` mirror; only the remaining deltas were applied.

| Where | Decision applied |
|---|---|
| header | Seam pointer |
| §1 | `HSS_INGEST_HOOKS=null` (SD-11) |
| §3 | `STATE_DICT_NAME`, `MODAL_APP_NAME` in the `hss-<env>-gcp` row; `run_stage(run_id, stage, attempt)` (SD-11/32) |
| §4.1.2 | COLMAP build tag moves from `3.11.1` to the release matching `pycolmap==4.2.0`; extra `gpu` pins `pycolmap==4.2.0` (cp312 wheel or in-image build) (SD-34, provisional on WS-04) |
| §4.1.3, §4.1.6 | Clean compile 609 s measured, prebuild and pin the image id; `enter_s` 15–22 s after the pin; warm-hit-rate rule for `HSS_PIPELINE_MIN_CONTAINERS=1` (SD-33; Modal benchmark 22:28) |
| §4.3 step 5 | Secret contents use `STATE_DICT_NAME`, `MODAL_APP_NAME` |
| §4.5 | `modal-check` uses `SMP_ENVIRONMENT=dev`; deploy verify replay uses `--token-env CAPTURE_API_TOKEN --until processing` with identical interim flags (SD-36) |
| §4.8 | Cost references: L4 $0.05–0.15, VLM ≈ $0.30, cap $1.50 (SD-38) |
| §6 | `yt-12Ks1idWj40` now in the manifest (`ITEM=`); mirror ≈ 430 MB for nine tars; golden E2E packets = WS-11's D-23 set; bench-baseline row carries the 22:28 timings (148 / 259 / 324 s at 40 / 60 / 70 photos, electro LiDAR 77 s, A10G facade-70 230 s, max client 397 s, ≈ 7 GiB, $0.85) (SD-29/37/38) |
| §7, §8 | Docker check expects `4.2.0`; nine tars in the mirror listing; done-when A.4 provisional wording (SD-34) |
| §12 | Q5 → `make fixtures ITEM=` (SD-28); Q6 → replay flags identical (SD-36) |

## Cross-brief seam corrections made while applying

1. **SD-32 revised**: my first draft composed sub-apps (`orchestrator_app`, label `house-scanning-capture-api`); the WS-12 writer's single-`app` design (WS-03 registers on WS-00's `app` by import, WS-01's `api_app` included, label `capture-api`, `KNOBS` for deploy knobs) is simpler and was adopted; WS-03 §2/§3/§4.1/§5/§7/§11/§12 and WS-01 §4.19 were re-edited to match and `seam-decisions.md` SD-32 rewritten. `MODAL_APP_NAME` survives only for out-of-app lookups.
2. **Env scheme vs contracts §2a item 7**: kept WS-00's bare shared names (`RULES_PROFILE_DEFAULT`, `SHOW_REJECT_TO_HOMEOWNER`, `GROUND_RULE_DEFAULT`, `HARD_STOP_MIN`, `CAPTURE_CHECK_TARGET_S`) instead of renaming them to `HSS_CRITERIA_*`/`HSS_RESULT_*`, because WS-03, WS-06, WS-09 and WS-10 already adopted them; the `HSS_<AREA>_*` rule applies to per-area knobs only (SD-11).
3. **Casing (D-06 withdrawn)**: the queue's "camelCase on the wire; the plan's §5 examples are fixed" was read as covering the `outcome` block too. WS-10 and WS-14 (group B) must rename their `outcome` fields; the embedded verbatim `verdict` stays snake_case as a document.

## Ambiguities and conflicts recorded (not resolvable inside group A)

- **`objects_3d` stage semantics**: the queue names the stage and `merge_objects` but neither WS-06 nor WS-07 defines what 3D placement adds beyond the scene builder's plane lifting. The seam fixes only the file (`objects/objects_3d.json`, scene-frame `Object`s), the dependency (`scene`, `objects`), optionality and the coordinator's `merge_objects` + re-validate step; WS-07 (group B) fills the body.
- **`view_angle_deg` in verdict v0**: needs spec PR 2 (coordinator) because WS-00 copies the schema verbatim; WS-10 derives the band meanwhile (SD-27).
- **pycolmap 4.2.0** is provisional on WS-04; the matching COLMAP release tag is left for WS-12's build to determine (the wheel metadata), with a forbidden combination stated (3.11.1 binary under a 4.x wheel).
- **Positive-pass numbers** (`[−3.891, −3.383]`, `s_range_m` start −12.832) come from the criteria writer's reading of `facade-M1__C-c1rule.json`; WS-11's builder must re-derive them from that file before hard-coding `gates.yaml`.
- **`CaptureRecord.owner_principal`** was promoted from WS-01's `capturex` extras into the WS-00 record so `StateStore` needs no extras for auth; WS-01's DDL was updated, the `capturex` key keeps the rest.
- **WS-12's `KNOBS["pipeline_timeout_s"]` (1200) vs its own §4.10 Cloud Run job timeout 1500 s**: left as is; the Cloud Run figure is a migration note, not the Modal `Pipeline` timeout.
- **WS-01 §4.18 `copy` semantics** (source_generation vs `if_generation_match`) were edited by the WS-01 writer after my read; left untouched.
- Group B rows in `seam-decisions.md` (SD-03…SD-08, SD-10, SD-11, SD-17, SD-19…SD-22, SD-24…SD-29, SD-34, SD-35, SD-39…SD-41) are not applied to WS-04…WS-10, WS-13, WS-14 by this group.

## Addendum (23:1x UTC): group B/C ambiguities settled as SD-43…SD-59

Inputs: `decision-log-group-b.md` (five ambiguities + "items handed on") and `decision-log-group-c.md` §3 (twelve items). Settled in `seam-decisions.md` §10; `STATUS: complete` re-emitted as the last line. Applied here to the group A briefs:

| Brief | Rows applied | Where |
|---|---|---|
| WS-00 | SD-43 (`mini-arkit/packet.json`, manifest id/kind), SD-44 (`packet.json`-first resolution in `fetch_fixtures`/`check-packets`), SD-46 (early blob writers: WS-08 `reads/` + `meter_identity.json`, WS-03 `qa/` + `capture_check.json`), SD-47 (`objects.json.ground_type` votes), SD-51 (`HintData.confirm`), SD-52 (`by` on `ConfirmationResponse`, `ConfirmationData`, `on_confirmation(..., *, by)`, reviewer supersedes), SD-55 (`c1.<p>.chain.json` sidecar; `evaluator.path` hybrid value) | §3.8, §3.9, §3.12, §4 step 13 |
| WS-01 | SD-52 (`by` from the principal: `reviewer*` token label or JWT `role == reviewer`; reviewer answer supersedes with 201, device conflict 409; `by` in the event, the CAS record and the hook; `field_id` passed to WS-08's `confirm`) | §3.2, §3.5, §4.1, §4.12 |
| WS-02 | none needed (SD-43 path already explicit; SD-44 compatible with `validate_packet(manifest)`) | – |
| WS-03 | SD-46 (`identify_meter` call removed; WS-08 writes `early/meter_identity.json`; WS-05 lookup-only), SD-51 (`meter_number_confirm` hint relayed with `confirm`), SD-52 (`on_confirmation(..., *, by)`), SD-56 (early-read `cost_usd` summed into `spend_usd.vlm_reads` at `start_run`; lower-of-two cap rule; `HSS_READS_BUDGET_USD_PER_HOUSE` for early workers), SD-59 (`HSS_DENSE_WEIGHTS` default `/vol/hf`) | §0, §1, §3, §4.1, §4.2, §4.7, §11 |
| WS-11 | SD-44 (folder resolution rule in `--packet`/`--variant`), SD-45 (no own seed; WS-04's `random_seed 0` read back from `registration_report.json.params`), SD-48 (chain cells decided, never lift a candidate), SD-49 (`gates.yaml` `decidability {gt_laser_min: facade 0.38 / electro 0.75 (FAIL), photo_warn_below: facade 0.29 (WARN), baselines}`; `criteria_report.decidability.share` as the metric; gates-offline rows on the GT scenes) | §3.1, §3.3, §4.6, §4.15, §8 |
| WS-12 | SD-59 (pipeline knob row `HSS_POSES_COLMAP_BIN`, `HSS_DENSE_WEIGHTS`, `HSS_DENSE_GEOMETRY`, `HSS_SCALE_METER_MODELS_PATH` via `secrets_for("pipeline")`; `add_local_dir("data/meter-models", …)` on `GPU_IMAGE`) | §3, §4.1.2 |

Line counts after the addendum: WS-00 394, WS-01 269, WS-02 220, WS-03 236, WS-11 203, WS-12 310.

### Rows the other groups' briefs still need edited (follow-up pass)

| Brief | Rows | Edit |
|---|---|---|
| WS-04 | SD-43, SD-45 | name `data/fixtures/packets/mini-arkit/packet.json` instead of an `ls packet*.json` glob; state that WS-11's runner reads `random_seed` back from `registration_report.json.params` (WS-04 keeps default 0) |
| WS-05 | SD-43, SD-46 | `packet.json` path; confirm the identity source is `reads/reads.json` / `early/meter_identity.json` written by WS-08 and drop any residual `identify_meter` entry-point wording for WS-03 |
| WS-06 | SD-47, SD-57, SD-59 | read `objects.json.ground_type` votes (mode, ≥ 2 agreeing, else `unknown`); keep `view_angle_deg` only in `coverage_report.json` with `[0, 30]` / `[20, 60]`; `HSS_DENSE_WEIGHTS` default `/vol/hf` |
| WS-07 | SD-43, SD-47, SD-54, SD-56 | `packet.json` path; write `ground_type` votes into `objects/objects.json`; movable obstacles blocking in A/S recorded as `[default chosen, awaiting Base]`; lower-of-two VLM cap wording |
| WS-08 | SD-43, SD-46, SD-50, SD-51, SD-52, SD-56, SD-58 | `packet.json` path; `read_still` returns the identity and writes `early/meter_identity.json`; panel pair mini + GPT-5.5 in the Base rule text; `HintData.confirm` as the walk-time channel (hint `meter_number_confirm`); `policy.confirm(..., by=by, field_id=)` with reviewer override; `HSS_READS_BUDGET_USD_PER_HOUSE` for early workers; `HSS_READS_PANEL_AUTOPASS` owns panel `pass` |
| WS-09 | SD-47, SD-48, SD-49, SD-50, SD-54, SD-55, SD-57, SD-58 | ground material only via WS-06's scene; chain `fail` propagates to the candidate, chain `pass` never lifts it; decidability targets as in `gates.yaml`; Base rule names the measured pair per fact; A/S blocking obstacles; `c1.<p>.chain.json` listed; omit `view_angle_deg` until the spec PR; copy WS-08's panel outcome |
| WS-10 | SD-51, SD-52, SD-57 | `HintData.confirm` rendered as the one-tap card; reviewer form posts to the SD-21 route and gets `by == reviewer` from its token; derive `viewingAngleDeg` `[0, 30]` / `[20, 60]` |
| WS-13 | SD-44, SD-49, SD-59 | real sessions are `packet.json` folders with ids `<site>-<yyyymmdd>-<n>`; decidability targets in the per-session rows; `data/meter-models/` travels into the image via `add_local_dir`, path `HSS_SCALE_METER_MODELS_PATH` |
| WS-14 | SD-43, SD-44, SD-51, SD-52, SD-53 | copy of `mini-arkit/packet.json` named `example-packet-meter-stills.json`; `packet.json` for real sessions; `HintData.confirm` in the spec PR and the iOS one-tap card; `by` in `ConfirmationResponse`/event; keep `ios-kit/fixtures/mini-arkit/` with `ios-kit-mini-*` ids (no rename) |
| coordinator | SD-51, SD-57 | spec PRs 1 and 2 (`docs/hosted-architecture.md` confirmations body with `by` and `HintData.confirm`; verdict `view_angle_deg`); `layout-decisions-criteria.md` item 13 (wall camera 1.3 m) is superseded by group C's C-24 (1.4 m), note only |
