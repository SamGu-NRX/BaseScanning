---
cursor:
  subagentId: "bc-eda28841-649e-5080-bb24-53a1febc18a9"
---

# Layout decisions, WS-14 (iOS contract support)

Every `[default chosen]` in `docs/build/ws-14-ios-contract.md` (§1), the sibling-brief decisions it follows (§2), and the inconsistencies found in the docs and between sibling briefs (§3). Sibling files: `layout-decisions-contracts.md` (WS-00), `layout-decisions-geometry.md` (WS-04/06), `layout-decisions-criteria.md` (WS-09), `layout-decisions-ws-03.md`. Store root `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`; repo paths relative to `huntertcarver/house-scanning-server`. Written 2026-09-26 ~22:15 UTC against the sibling briefs present in `docs/build/` at that time (WS-00, 02, 03, 04, 05, 06, 08, 09, 10, 11).

## 1. Defaults chosen by WS-14

| # | Decision | Default | Why | Reconcile with |
|---|---|---|---|---|
| 1 | Kit location and shape | top-level `ios-kit/`: static deliverables (schemas, examples, prompts, mini fixtures, README) + `ios_kit/` tooling package run from the repo checkout (`uv run --project ..`) + vendored `packetgen/{__init__,conventions,validate}.py` with a kit-owned `packetgen/__main__.py` (`validate` only) | the zip must work on a Mac with `jsonschema` + `numpy` only (plan WS-14 done-when: `python -m packetgen validate`); tooling that needs `server.*` stays in the repo | WS-00 (repo layout, `pyproject` extra) |
| 2 | Replay ownership | `ios_kit replay` wraps `server.eval.replay.replay()` (WS-11); the kit never re-implements register/PUT/commit/finalize/poll; adds local `packetgen` pre-check, result validation, prompt printing, exit codes, env-only token | plan: WS-11 builds, WS-14 provides to iOS; one uploader | WS-11 (`amend_of`/`answers` request) |
| 3 | Contract fake | `ios_kit/fake_api.py` (FastAPI, ~200 lines, WS-00 `api.py` models, vendored `packetgen.validate`) | WS-01 waits on the SMP verdict; kit must self-validate; iOS needs an offline loop; `make e2e-local` (WS-11) is preferred once it exists | WS-01 (contract double only) |
| 4 | `contentType` per file kind | `image/jpeg`; `application/octet-stream` for `.f32 .u8 .bin .ply`; `application/gzip` for `.csv.gz` | unspecified anywhere | WS-01 |
| 5 | Amendment `formatVersion` | equals the parent packet's | spec silent | WS-02, spec owner |
| 6 | Compatibility window | current + previous `0.x` minor for 30 days after the kit release; validation by versioned schema file; rejection code `format_version_unsupported` (WS-02's) | spec only says 0.x may break | WS-02, spec owner |
| 7 | Result example geometry | ground 1.40 m below the session start (`y = −1.40`), scene origin `(0.9831, −1.40, −3.25)` from the Appendix B example; battery C/S `{0.7874, 1.0033, 0.5588}` from WS-10 | spec §2 says the ground is 1.2–1.6 m below | WS-10 |
| 8 | Catalogue schema and limits | JSON with `match` keys (`kind, band, cause, target_class, member_action, hint_code, capture_check_code, outcome`), `title ≤ 40`, `body ≤ 140` chars after rendering, `{side}` threshold ±0.3 m, class→plain-words map | WS-00 §3.7 fixes `prompt_id` + `prompt {title, body}` and names WS-14's catalogue; WS-10 planned 120-char single strings | WS-10 (`copy.py` loads the file; 120 vs 140) |
| 9 | Mini fixtures | three from `eth3d-electro` (`arkit-lidar`, `arkit-nonlidar` keyframes `k00020:k00025` incl. the tap on `k00023`; `pose-free` stills `s00020:s00025`), paths rewritten to the spec layout, committed under `ios-kit/fixtures/`, appended to `data/fixtures/manifest.json` as `kind: packet_mini` | 5 MB rule; iOS team needs something committed; WS-00 allows ETH3D-derived small fixtures in the private repo | WS-00 (manifest kind), WS-13 |
| 10 | Kit release | `VERSION = <spec minor>.<kit patch>` (`0.4.0`), tag `ios-kit-v<VERSION>`, `make ios-kit` → `dist/ios-kit-<VERSION>.zip` with copies of `server/contracts/schemas/*` | none existed | WS-00 (Makefile), WS-12 |
| 11 | Component doc and CI names | `docs/components/ws-14-ios-kit.md`, `.github/workflows/ws-14-ios-kit.yaml` | sibling convention; CI files are WS-00's | WS-00 |
| 12 | ARWorldMap | README recommends saving an `ARWorldMap` at session end so anchors can be shown later in `arkit_epoch`'s world | anchors are only valid in that world | iOS team |
| 13 | Exit codes | `0 2 3 4 5 6 7` as in brief §3.5 | none existed | none |

## 2. Decisions taken from sibling briefs (followed, not re-decided)

- WS-00: camelCase envelope (`runId, status, site, scale.relBound95, criteria[], viewsNeeded[], memberActions, verdictUrl, previewUrl, reviewUrl`), snake_case `outcome` (`kind, profile, message: str, arkit_epoch, frame_to_arkit, recommended_placement {…, box_arkit_world {pose, size}}, cable_route_polyline_arkit, views_needed: ViewNeededDevice[], reasons: RejectReason[]`), `verdict?` optional and omitted by default, `flow: guided|photo`, `pathPrefix = amendments/{amendmentId}/`, `ApiError` codes, `EventType` and typed `data`, `StageName` (10 incl. `reads`), `SHOW_REJECT_TO_HOMEOWNER=false`, `RULES_PROFILE_DEFAULT=C`, `CAPTURE_CHECK_TARGET_S=30`, `data/fixtures/manifest.json` + `.fixtures/` + `scripts/fetch_fixtures.py`, schema file names `capture-packet.v0.4 / result.v0 / api.v1`, `keyframe_image` rejected with no alias.
- WS-11: `server.eval.replay` CLI and `replay()` signature, `ReplayRun` fields and timings, `--finalize-at last|early`, `--dry-run`, `/healthz`, `make e2e-local`.
- WS-10: headline messages, battery dimensions per profile, camera-pose heights (wall 1.4 m, ground 1.3 m), hidden-reject behaviour (`manual_review` shown, `internal_decision` stored), `HSS_RESULT_*` config family.
- WS-03: closed hint code list and `capture_check.missing` code list; `capture_check` deadline 30 s.
- WS-02: `format_version_unsupported`, warning codes (`scale_no_keyframe_link`, `intrinsics_assumed`).

## 3. Inconsistencies found (reported, nothing edited)

Docs:
1. Assignment path `internal/data/packets/archives/index.json` does not exist; the index is `internal/data/packets/index.json` (`archives/` holds only tars). Plan WS-00 lists `archives/eth3d-facade.tar`, but facade is split into `eth3d-facade.tar.part-00` + `.part-01`.
2. `docs/hosted-architecture.md` §2.2 `files` example uses `role: keyframe_image`; spec Appendix A/D say `keyframe` (WS-00 rejects it; the doc fix is a coordinator spec PR).
3. `docs/hosted-architecture.md` `/result` example is camelCase and flattens verdict fields; plan §5 is snake_case ("verdict v0 plus an `outcome` block") and itself mixes `memberActions`/`previewUrl` with snake_case outcome fields. WS-00 settled it (camel envelope, snake `outcome`); the docs still disagree with each other.
4. `docs/hosted-architecture.md` §2.2 says "The spec owner may later add a `parentPacketId` field"; spec v0.4 already has it.
5. Spec Appendix A and `internal/packet-generator/schema/capture-packet.schema.json`: `scaleReference.ext.obliqueCloseUp.still` description says "(purpose meter_close)"; should be `meter_oblique` (validator is right; description typo).
6. Fixture archives list `streams/<variant>/<name>.csv.gz` and `features/<variant>/k.bin`; the spec's layout is `streams/<name>.csv.gz` and `keyframes/k.features.bin`. Both are legal `relPath`s; the iOS team must follow the spec layout (the minis are rewritten to it).
7. `docs/hosted-architecture.md` `files` example `meta.still` lacks the required still fields `timestamp` and `orientation` although `meta` is "the same objects `packet.json` will hold".
8. Plan WS-14 done-when: `python -m packetgen validate` needs pycolmap/OpenCV through `packetgen/__main__.py` imports; the kit's validate-only `__main__` keeps the command with slim deps.
9. `docs/scene-and-verdict-format.md` §3.1 asks the packet spec to add a matching capture prompt for the downward ground shot; v0.4 has none (the catalogue fills it; a spec PR could reference it).
10. Spec Appendix D step 4 omits the `Idempotency-Key` and `X-Packet-Sha256` finalize headers that hosted-architecture §2.2 shows.
11. `docs/project-context.md` "up to about 60 photos" vs plan §2 "~40–70"; brief embeds "up to ~60".
12. Spec §3.5 `arkitPoses` "30 Hz or more" vs schema `rateHz ≥ 10`.

Between sibling briefs (for the coordinator):
13. Result schema location: WS-00 exports `server/contracts/schemas/result.v0.schema.json`; WS-10 declares `server/result/schema/result.v0.schema.json` as "the only new contract" and tells WS-14 to consume it. The kit copies WS-00's; WS-10 should validate against WS-00's export or the two must be identical.
14. `RejectReason`: WS-00 camelCase (`criterion, valueFt, plusMinusFt, thresholdFt, comparison, text, evidenceViews[{image, boxPx}]`); WS-10 snake_case (`measured_ft, plus_minus_ft, threshold_ft`). Kit follows WS-00.
15. `ViewNeededDevice`: WS-00 §3.7 adopted WS-14's draft names (`prompt_id`, `prompt {title, body}`, `target_region_arkit {surface, polygon}`, `suggested_camera_pose_arkit: Mat4`, `reference_crop {image, box_px}`) and lists the WS-10 reconciliation as owed to the coordinator (WS-10: `prompt: str`, `target_region_arkit {kind, polygon}`). One ambiguity remains inside WS-00: `ResultResponse.viewsNeeded` is typed `list[ViewNeededDevice]` (snake_case) while the same row's note says the envelope repeats `id, kind, band, wallId, sM, outM, target, prompt` in camelCase. Kit follows the note (camelCase envelope repeat, full entries under `outcome.views_needed`) and its `check-spec` copies whatever `export_schemas` emits.
16. Customer copy ownership: WS-00 §3.7 says WS-10 renders from WS-14's catalogue; WS-10 §4 step 8 hard-codes its own `copy.py` table (≤ 120 chars, keyed by `(kind, band)`) and lists the catalogue publication as WS-14 consuming `copy.py`. Brief asks WS-10 to load `HSS_RESULT_PROMPTS_PATH`; headline texts kept identical to WS-10's.
17. Hidden-reject config name: WS-00 `SHOW_REJECT_TO_HOMEOWNER`; WS-10 `HSS_RESULT_SHOW_REJECT`. Kit documents WS-00's.
18. Event `data` casing: WS-00 `VerdictReadyData {runId}`, `RetakeRequestData {viewsNeeded, memberActions}` vs WS-03 `verdict_ready {run_id, status, outcome_kind}`, `retake_request {run_id, views_needed, member_actions}`; WS-03 also adds `capture_check.meter_read` and `hint.frames[]` not in WS-00's `CaptureCheckData`/`HintData`.
19. `capture_check.missing` codes: hosted-architecture example `ground_downward_w1`; WS-03 uses `ground_downward_left|right` (no wall ids before the scene). Kit follows WS-03.
20. WS-11's CLI takes `--token $CAPTURE_API_TOKEN` on argv; the kit reads the env var only (a flag value shows in process lists and shell history). Request to WS-11 to accept the env var too.
21. Replay `--rate`: WS-11 defines it as files/s; the plan's capture rule is ≥ 2 keyframes/s (a keyframe group is 1–4 files). Brief follows WS-11's definition; electro `arkit-nonlidar` (95 files) therefore takes ≈ 50 s at `--rate 2`, not 25 s.
22. `docs/build/ws-00-skeleton-and-contracts.md` did not exist when the first draft of this brief was written; the rewrite at ~22:15 UTC aligned to it. If WS-00 changes its result or api tables again, `check-spec` in the kit fails and the kit copies are refreshed with `make ios-kit`; the brief text itself may then lag.
