# WS-02 builder brief: packet validation and normalization

Store root `$S` = `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2` for every `docs/...` and `internal/...` path; repo paths are relative to the checkout of `huntertcarver/house-scanning-server`. Plan section: `docs/server-pipeline-build-plan.md` §6 "WS-02". Contract names follow `docs/build/ws-00-skeleton-and-contracts.md` (WS-00, as amended by `internal/build/seam-decisions.md`, cited as `SD-nn`) verbatim. `[default chosen]` marks a choice the plan, spec or code left open; every one is listed in `internal/build/layout-decisions-ws-02.md` (resolutions in `internal/build/decision-log-group-a.md`).

## 0. Card

- **WS-02 `packet-validation`.** Goal: turn a finalized capture packet (spec v0.4) into (a) a `ValidationReport` whose errors carry JSON pointers the iOS app can act on (422 body), (b) a `NormalizedPacket` every later stage reads without re-parsing the spec (intrinsics for every image, one JPEG per image, poses re-orthonormalized and epoch-grouped, tier/branch/flow decided, derived flags), and (c) the `capture_check` checklist ("you can leave") with stable codes.
- Builder model: Grok 4.7. Validator: a separate agent (any model), never the builder.
- Inputs: `packet.json` + files under `captures/{captureId}/` (WS-01 ingest, after promotion from `uploads/`, SD-30); per-file `meta` records during capture (WS-01, for the partial checklist); `flow` from `CaptureRecord` (WS-03). Outputs (WS-00 §3.9 keys under `runs/{runId}/`, SD-02): `packet/validation.json` (`ValidationReport`, the WS-00 contract type) and `packet/normalized.json` (`NormalizedPacket`) → WS-03/04/05/06/07/08; `packet/capture_check.json` (`CaptureCheck`) → WS-03 events, WS-10 reviewer; transcoded stills `captures/{captureId}/normalized/stills/<id>.jpg` (capture prefix, idempotent across re-runs).
- Repo `huntertcarver/house-scanning-server`, branch `ws-02/packet-validation`, one PR.

## 1. Start gate

Stop and report (do not invent the missing piece) unless all of these exist on `main`:

1. WS-00 PRs `ws-00/contracts` and `ws-00/skeleton` merged: `server/contracts/packet.py` (`Packet`, `Keyframe`, `Still`, `Tap`, `Epoch`, `ScaleReference`, `FileEntry`, `Streams`), `server/contracts/validation.py` (`ValidationIssue`, `ValidationReport`, `check_packet`, SD-14), `server/contracts/capture_check.py` (`CaptureCheckCode`, `HintCode`, SD-20), `server/contracts/api.py` (`ApiError`, `HintData`, `CaptureCheckData`), `server/contracts/jobs.py` (`StageContext`, `StageResult`, `Branch`, `StageName` containing `validate`), `server/contracts/schemas/{capture-packet.v0.4,validation-report.v0}.schema.json`, `server/common/geometry.py` (`from_column_major`, `column_major`, `pixel_ray_arkit`, `project_arkit`), `server/common/{config,logging,storage}.py`, `server/packet/{__init__.py,README.md,tests/__init__.py,validate_legacy.py}` stub, `make validate` green.
2. Fixtures: `data/fixtures/packets/examples/{example-packet,example-packet-photo-only}.json`, the 33 committed manifests (8 packetgen houses × 4 variants + `yt-12Ks1idWj40/packet.photo-only.json`) and `data/fixtures/packets/mini-arkit/`; `make fixtures ONLY=all` with `FIXTURE_SOURCE=$S/internal` fetches and extracts all nine store tars into `.fixtures/packets/<house>/` (WS-00's manifest carries `yt-0gotY_syWWU` 18 MB, `yt-12Ks1idWj40` 4.3 MB, `yt-4h8kyjnPo9s` 19 MB, `yt-5-M-7f35Tyg` 22 MB as well, SD-29; no manifest PR and no hand extraction from this WS). The plan's "all 33 fixture packets" is exactly that set.
3. Store sources readable: `internal/packet-generator/packetgen/{validate,conventions,exif,checks}.py`, `internal/modal-pipeline/housepipe/{validate,lidar,geometry}.py`.

## 2. Files you own / must not touch

Create or modify: `server/packet/__init__.py` (stage `run`), `server/packet/params.py`, `server/packet/report.py` (re-exports WS-00's `ValidationIssue`/`ValidationReport` and adds `PacketError`), `server/packet/validate.py`, `server/packet/integrity.py`, `server/packet/images.py`, `server/packet/exif_intrinsics.py`, `server/packet/poses.py`, `server/packet/streams.py`, `server/packet/conventions.py`, `server/packet/normalize.py`, `server/packet/capture_check.py`, `server/packet/cli.py`, `server/packet/schemas/{normalized-packet.v0,capture-check.v0}.schema.json` (exported from the models; `validation-report.v0` is WS-00's), `server/packet/tests/**` (incl. `corrupt.py`), `server/packet/README.md`, `docs/components/ws-02-packet-validation.md`; `pyproject.toml` only to add the `packet` extra (`pillow-heif>=1.0`, `pillow`, `numpy`, `jsonschema`).

Forbidden: `server/contracts/**` and `server/common/**` (WS-00; contract changes go through a spec PR reviewed by the WS-00 owner), `server/eval/**`, `server/api/**` (WS-01), `server/orchestrator/**` (WS-03 adds the one-line `validate_manifest` re-export itself, SD-23), every other stage package, `data/fixtures/**`, CI workflow files, store docs and `internal/**`. Import rule (WS-00 §4.10): `server.packet` imports only `server.contracts`, `server.common`, `server.eval`, never another stage.

## 3. Interfaces

Units: metres, seconds of device uptime, continuous pixels with (0,0) at the top-left corner; poses are ARKit camera-to-world `Mat4`, 16 numbers column-major, kept as such in every WS-02 output (WS-04 converts with `server.common.geometry`). Field names are the packet's wire names verbatim (camelCase), as WS-00 §3.1 requires. Pointers are RFC 6901 JSON pointers into `packet.json` (`/keyframes/12/pose`, `/files/3/sha256`, `/taps/0`).

```python
# server/contracts/validation.py (WS-00, SD-14) — re-exported by server/packet/report.py; shown for the reader
class ValidationIssue(Strict):   # one row of ApiError.errors (WS-00 §3.8) plus level
    code: str; level: Literal["error", "warning"]; pointer: str; message: str; value: Any | None = None
class ValidationReport(Strict):  # written as packet/validation.json; the only report type (there is no PacketReport)
    format: Literal["hss-validation-report/v0"] = "hss-validation-report/v0"
    packetId: str | None; formatVersion: str | None; tier: Tier | None; stage: Literal["manifest", "full"]
    ok: bool; errors: list[ValidationIssue]; warnings: list[ValidationIssue]
    keyframes: int; stills: int; taps: int; epochs: int; files: int; filesHashed: int
    maxTapReplayError: float | None; imagesUsable: bool; imagesDecodable: int; timingsS: dict[str, float]
    def to_api_error(self) -> ApiError: ...          # errors only, {code, pointer, message}
# server/packet/validate.py
def validate_packet(manifest: Path | dict, *, root: Path | None = None, stage: Literal["manifest", "full"] = "full",
                    check_hashes: bool = True, known_sha256: Mapping[str, str] | None = None,
                    params: PacketParams = PacketParams()) -> ValidationReport: ...
def validate_manifest(pk: dict) -> ValidationReport: ...   # = stage "manifest"; WS-03 re-exports it for WS-01's finalize
def validate_record(kind: Literal["keyframe", "still"], record: dict, session: dict | None = None) -> list[ValidationIssue]: ...
# server/packet/normalize.py
def normalize_packet(manifest: Path, *, flow: Literal["guided", "photo"] | None = None,
                     report: ValidationReport | None = None, params: PacketParams = PacketParams()) -> NormalizedPacket: ...
def load_normalized(path: Path, root: Path) -> NormalizedPacket: ...
def merge_amendment(parent: NormalizedPacket, amendment: NormalizedPacket, aid: str) -> NormalizedPacket: ...
# server/packet/capture_check.py
def capture_check(pk: NormalizedPacket | PartialPacket, *, mode: Literal["partial", "final"],
                  coverage_missing: Sequence[str] = ()) -> CaptureCheck: ...
def partial_from_metas(session: dict, metas: Iterable[dict]) -> PartialPacket: ...   # metas = FileRecord.meta dicts
```

Also: `server.packet.streams.read_stream(pk: NormalizedPacket, name: str) -> dict[str, np.ndarray]` (column → array, text columns as object arrays, `scaleToG` applied); `server.packet.report.PacketError(Exception)` with `code in {"invalid", "root_missing", "io"}` and `.report: ValidationReport | None`; `PartialPacket(Strict) {session: dict, keyframes: list[Keyframe], stills: list[Still]}` built by `partial_from_metas` from the `meta` records WS-01 stores at registration (`{keyframe: ...} | {still: ...}`); the one-call form WS-03's brief expects, `validate_and_normalize(manifest: Path, root: Path | None = None, *, flow=None) -> tuple[NormalizedPacket, ValidationReport]`, plus `NormalizedPacket.to_json(path: Path) -> None` and `NormalizedPacket.from_json(path: Path, root: Path) -> NormalizedPacket` (= `load_normalized`).

`NormalizedPacket(Packet)` is a valid v0.4 packet plus a typed `derived` block; on dump the block lives at `ext.hss` so `server.contracts.cli check` still accepts `normalized.json`; per-record derived data lives in `keyframes[i].ext.hss`, `stills[i].ext.hss`, `taps[i].ext.hss` (`derived_of(record) -> FrameDerived | TapDerived`). `root: Path` is excluded from JSON and set by `load_normalized`. Implementation: a declared field `derived` plus a wrap `model_serializer` that moves it to `ext["hss"]` and a `mode="before"` validator that pulls `ext.hss` back `[default chosen]`.

```python
class PacketDerived(Strict):     # NormalizedPacket.derived  <->  packet ext.hss
    format: Literal["hss-normalized-packet/v0"]; normalizedAt: IsoTime; validator: str      # "ws-02/<git sha>"
    flow: Literal["guided", "photo"]; flowSource: Literal["capture_record", "inferred"]
    branch: Branch; isAmendment: bool; evidence: bool                                     # evidence False when source.kind == synthetic
    primaryEpoch: str | None; epochs: list[EpochDerived]        # {id, keyframes, durationS, pathLengthM, trackingHistogram, primary}
    depth: DepthDerived            # {keyframesWithDepth, coverage, medianDepthM, confidenceShare: [low, med, high] | None}
    purposes: dict[str, list[str]] # purpose -> frame ids (stills, and keyframes carrying `purpose`)
    meterFrames: MeterFrames       # {close, oblique, tapKeyframe}: frame ids or None
    streams: dict[str, StreamDerived]   # {rows, rateHzMeasured, tMin, tMax, scaleToG}
    stats: PacketStats             # {keyframes, stills, taps, planes, imagesDecodable, durationS, keyframesPerS, medianSpacingM}
    notes: list[str]               # WS-06 copies into scene provenance.notes (e.g. HEIC transcodes)
    validation: ValidationSummary  # {ok, errors, warnings, reportPath}
    captureCheck: CaptureCheck | None
class FrameDerived(Strict):      # keyframes[i].ext.hss / stills[i].ext.hss
    kind: Literal["keyframe", "still"]; decodable: bool; format: Literal["jpeg", "heic->jpeg"]; bytes: int; sha256: Sha256Hex
    flags: list[str]; pitchDeg: float | None; rollDeg: float | None; gravityCam: Vec3 | None
    intrinsicsAssumed: bool; focalRelBound: float; originalImg: RelPath | None; originalOrientation: int | None
class TapDerived(Strict): replayError: float; epoch: str
class CaptureCheck(Strict):      # packet/capture_check.json; CaptureCheckData = {complete, missing, meterRead?, prompts?}; hints from items
    format: Literal["hss-capture-check/v0"]; mode: Literal["partial", "final"]; complete: bool; pendingFinal: bool
    missing: list[CaptureCheckCode]; unknown: list[CaptureCheckCode]; items: list[CheckItem]; stats: dict[str, float]
class CheckItem(Strict):
    code: CaptureCheckCode; status: Literal["present", "missing", "weak", "unknown", "not_applicable"]   # WS-00 closed list (SD-20)
    severity: Literal["required", "recommended"]; retake: str | None; message: str; evidence: dict[str, Any]
```

Stage entry (WS-00 §3.12): `server.packet.run(ctx: StageContext) -> StageResult`; `STAGE = "validate"`. It reads the packet from `ctx.inputs["packet_dir"]` (always set by WS-03, SD-17), else materializes `captures/{capture_id}/**` via `ctx.storage.list_prefix` + `get_bytes` into `ctx.work_dir/packet` `[default chosen]`; calls `ctx.heartbeat("<substep>")` between steps; writes `packet/validation.json`, `packet/normalized.json`, `packet/capture_check.json` under `captures/{capture_id}/runs/{run_id}/` and transcodes under `captures/{capture_id}/normalized/stills/`; returns `StageResult(artifacts={"packet/validation.json": key, …}, metrics={"validate_s", "normalize_s", "keyframes", "warnings"}, warnings=[distinct warning codes])`. Raises `PacketError("invalid")` when `report.ok` is false; WS-03 turns that into a `failed` event `{code: "packet_invalid", message}` and routes to `manual_review` when `report.imagesUsable`.

## 4. Behaviour spec

1. **Load.** `manifest` is `packet.json` (or a fixture's `packet.<variant>.json`); `root = manifest.parent` unless given. Parse with `Packet.model_validate`; every pydantic error becomes one `schema` issue (pointer from `loc`), tier-rule `ValueError`s become `tier_rule` at `/session/tier`. Continue past schema errors only for the checks whose inputs parsed. `formatVersion != "0.4"` → `format_version_unsupported` `[default chosen]`. `source.kind == synthetic` → warning `synthetic_source`, or error `synthetic_in_prod` when `Settings.SMP_ENVIRONMENT == "prod"` `[default chosen]`. All thresholds live in `PacketParams` (frozen dataclass, defaults below); no env overrides `[default chosen]`. `validate_record` runs steps 3–4 (keyframe) or the still checks of step 12 without link resolution on one `meta` record, so WS-03 can emit a `hint` on the first bad frame instead of a 422 at finalize.
2. **Files (stage `full`).** For every `files[i]`: exists (`file_missing`), `st_size == bytes` (`file_size_mismatch`), sha256 (`sha256_mismatch`; skipped when `known_sha256[path] == files[i].sha256`, counted in `filesHashed` otherwise), duplicate paths (`file_duplicate`), normalized path escaping `root` (`path_unsafe`). Every path a record references (`keyframes[].img`, `depth.file`, `depth.confidenceFile`, `features.file`, `stills[].img`, `streams.*.file`, `mesh.file`, TrueDepth `depth`/`image`) must be listed: `file_not_listed` at the referencing field. Unlisted files on disk are ignored (spec §6). Image headers (PIL lazy open, no decode): magic must be JPEG (`FF D8 FF`) for every keyframe and still; HEIC (`ftyp` brand `heic|heix|hevc|hevx|mif1|msf1`) only for `photo_only` stills, else `image_format`; header size ≠ `(w, h)` → `image_size_mismatch` (HEIC: `(h, w)` also accepted, see step 9).
3. **Poses.** `T = from_column_major(pose)`, `R = T[:3,:3]`: non-finite → `pose_non_finite`; last row ≠ `[0,0,0,1]` within 1e-6 → `pose_last_row`; `max|RᵀR − I| > 1e-3` or `|det R − 1| > 1e-3` → `pose_not_rigid` `[default chosen: housepipe's 1e-3; packetgen's 1e-4 becomes the warning pose_rotation_drift, and the normalizer replaces R by its SVD projection]`; `‖t‖ > 1000 m` → `pose_translation_absurd` `[default chosen]`. `worldAlignment`: `camera` → `world_alignment_camera`; `gravityAndHeading` → warning `world_alignment_heading` `[default chosen]`.
4. **Intrinsics.** `fx, fy > 0`, `0 < cx < w`, `0 < cy < h`, horizontal FOV `2·atan(w/2fx)` within 20–150° else `intrinsics_implausible`; warnings `fov_unusual` (outside 45–100°; iPhone wide camera ≈ 64–67°, `fx/w ≈ 0.75`), `focal_aspect` (`|fx/fy − 1| > 0.02`), `principal_point_offcentre` (`|cx − w/2| > 0.05 w` or `|cy − h/2| > 0.05 h`) `[default chosen]`. A `highResolution` keyframe whose `fx/w` differs by > 20 % from the session median `fx/w` of non-high-res keyframes carries unscaled `ARCamera.intrinsics` (spec §2): rescale all four by `w / videoFormat.imageResolution[0]`, flag `intrinsics_rescaled` `[default chosen]`. Non-high-res keyframes whose `(w, h)` ≠ `imageResolution` → warning `keyframe_resolution_differs`; long side < 640 px → `low_resolution` (A7/spec §5.1: 480p walks registered, so a warning only) `[default chosen]`.
5. **Epochs and time.** Keyframe epoch must exist (`unknown_epoch`); duplicate epoch ids → `epoch_duplicate`; tap `epoch` (when present) ≠ its keyframe's → `tap_epoch_mismatch`. Sort keyframes by `(epoch.startTime, timestamp, id)`; array order that differs → warning `keyframe_order` (spec §6 says timestamps increase; packetgen and housepipe only warn, this brief keeps the warning). Two keyframes in one epoch with `|Δt| < 1e-3 s`: identical pose → warning `duplicate_keyframe`; centres > 0.01 m apart → error `keyframe_timestamp_conflict` `[default chosen]`. Timestamp > 0.5 s before its epoch's `startTime` → `timestamp_before_epoch`; epochs not sorted by `startTime` → `epoch_order`; epoch without keyframes → `empty_epoch`. Primary epoch = most keyframes, ties → earliest `startTime` (WS-04 may re-anchor on the meter epoch).
6. **Motion sanity** (consecutive keyframes of one epoch, both `tracking == normal`, sorted): speed `‖Δc‖/Δt > 3.0 m/s` with `Δt ≥ 0.1 s` → `pose_jump` (flag `pose_jump_after` on the later frame); rotation `angle(R₁ᵀR₂)/Δt > 180°/s` → `pose_spin`; either on > 5 % of pairs → `tracking_unstable` `[default chosen]`. `tracking != normal` → per-frame flag `limited_tracking`; share > 0.30 → `limited_tracking_share`. Frames are never dropped here (spec §5.1 rule 6).
7. **Gravity.** ARKit tiers: gravity is world −y by construction; when `streams.motion` exists, for each keyframe take the motion row within 0.05 s and compare `grav` (device frame) with `g_pose = (R · DEVICE_FROM_CAMERAᵀ)ᵀ · (0, −1, 0)` (`internal/packet-generator/packetgen/checks.py` lines 108–117): median angle > 3° → warning `gravity_mismatch`; > 15° with ≥ 100 matched rows → error `gravity_conventions` `[default chosen]`. `photo_only`: `stills[].gravity` (device frame, g) → `gravityCam = DEVICE_FROM_CAMERAᵀ · g/‖g‖`, `pitchDeg`, `rollDeg`; `‖g‖` outside 0.9–1.1 → `gravity_magnitude`. Keyframes get `pitchDeg`/`rollDeg` from the pose (viewing axis −z vs the horizontal, up positive); flags `pitched_up` (> +30°), `pitched_down` (< −45°) `[default chosen]`.
8. **Depth, confidence, features.** `sceneDepthEnabled` and `depth` null: `highResolution == true` → warning `depth_missing_highres` `[default chosen; spec §3.2 has no exemption, see §12]`, else `depth_missing`. Sizes: depth `w·h·4` (`depth_size`), confidence `w·h` (`confidence_size`), features `20·count` (`features_size`). Normalize samples every 10th depth frame (`np.fromfile("<f4")`): finite share < 0.5 or median depth outside 0.2–10 m → `depth_values_odd`; no pixel with confidence ≥ 1 → `depth_confidence_all_low`; fill `derived.depth`.
9. **Images and HEIC (normalize).** Full decode of every keyframe and still (`Image.open(p).load()`, ≈ 6.5 ms per 1920×1440 JPEG measured) → `image_undecodable`; `decodable` per frame; `imagesUsable` = at least 60 % of the listed images decode and at least 10 of them (all of them when the packet lists fewer than 10) `[default chosen]`. Never rotate or re-encode a JPEG. HEIC (photo_only only): `pillow_heif.register_heif_opener()`, `Image.open`, save `normalized/stills/<id>.jpg` with `quality=92` and `exif=im.info["exif"]` `[default chosen]`. pillow-heif applies `irot`/`imir` and the EXIF orientation while opening and resets the tag to 1 (`im.info["original_orientation"]` keeps it; measured: a q50 HEIC of a 1920×1440 frame with Orientation 6 opens as 1440×1920, Orientation 1), so the transcoded still is upright: set `orientation = 1`, `w, h` = decoded size, `img` = the new path, append `{path, bytes, sha256, role: "compat"}` to `files[]`, keep `originalImg`/`originalOrientation` in `ext.hss`, append a note (`still s3: HEIC transcoded to JPEG q92, EXIF kept, orientation applied`), warnings `heic_transcoded` and, when the packet's `(w, h)` was the stored (swapped) size, `heic_orientation_applied`. A JPEG still whose file carries an EXIF `Orientation` tag that differs from `stills[].orientation` → `orientation_mismatch` (packet wins; no tag, no warning).
10. **EXIF intrinsics (`photo_only` stills; ARKit stills with `intrinsicsSource == arkit` are left alone).** Port `packetgen.exif.read_exif` (merge IFD0 and the Exif sub-IFD `0x8769`; `ISOSpeedRatings` may sit under `PhotographicSensitivity`). Focal source order: `stills[].intrinsics` with `intrinsicsSource in {arkit, calibration}` → keep; a `keyframe` link with no `intrinsics` → the keyframe's intrinsics, `intrinsicsSource = arkit`; file EXIF `FocalLenIn35mmFilm` (when the packet also carries one and it differs → `exif_mismatch`, file wins); else packet `exif.FocalLenIn35mmFilm`; else 26 mm with `intrinsicsAssumed = true`, warning `intrinsics_assumed` (photo_only default of `packetgen/photos.py`; iPhone 13–16 main camera is 24–26 mm). `K = K_from_focal35(f35, w, h)`: `f = f35 · hypot(w, h) / hypot(36, 24)`, `cx = w/2`, `cy = h/2`, `intrinsicsSource = exif_focal35` (also for the assumed case, as WS-04 expects; `intrinsicsAssumed` tells them apart). `focalRelBound` = 0.02 read (Apple rounds `FocalLenIn35mmFilm` to an integer), 0.15 assumed `[default chosen]`. Existing `exif_focal35` intrinsics that differ from the recomputation by > 1 % → replaced, warning `intrinsics_recomputed`. `f35` outside 10–200 mm → `exif_focal_out_of_range`; outside 22–30 (not the 1× camera) → `non_wide_lens`; `Make != "Apple"` → `non_apple_photo`; no EXIF at all → `exif_missing` `[default chosen]`.
11. **Taps.** Keyframe exists (`tap_unknown_keyframe`), pixel inside `[0, w] × [0, h]` (`tap_pixel_outside`), replay `pixel_ray_arkit(T, K, pixel)` vs `rayOrigin`/`rayDirection`, `max(|Δo|∞, |Δd|∞) ≤ 1e-3` else `tap_replay` (message `ray differs by 1.2e-02` style; measured: store fixtures reproduce to 3–5e-7, the spec example with its 4-decimal pose to 1.1e-5); `hit.distance` outside 0.2–30 m → `tap_hit_distance` `[default chosen]`; `TapDerived.replayError` always recorded.
12. **Stills and scale reference.** `stills[].keyframe` exists (`still_unknown_keyframe`) and shares `(w, h)` (`still_keyframe_size_mismatch`); purpose outside the 13 known values → `unknown_purpose` (kept as written, indexed as `other`). Port `check_scale_reference` from `packetgen/validate.py` with codes: errors `scale_still_unknown`, `door_view_unknown_frame`, `door_box_outside`, `truedepth_file_not_listed`, `truedepth_depth_size`; warnings `scale_purpose_mismatch`, `scale_no_keyframe_link`, `scale_distance` (outside 0.3–3.0 m), `scale_head_on_angle` (> 30°), `scale_oblique_angle` (outside 15–50°), `meter_identity_empty`, `door_on_meter_side_no_view`, `truedepth_relative`. Never copy `meterIdentity.rawText` into a message or log line (it can contain the meter number).
13. **Streams.** Each declared stream: file listed and present; gunzip + CSV header equal to `columns` (`stream_header`; unreadable → `stream_unreadable`; no rows → `stream_empty` `[default chosen]`); `t` non-decreasing else `stream_time_order`; measured rate `1/median(Δt)` < 0.5 × `rateHz` → `stream_rate_low`. `imuRaw` and `motion`: median `‖a‖` (or `‖grav‖`) in 0.9–1.1 g is fine; in 9.0–10.6 → the app wrote m/s²: set `scaleToG = 1/9.80665`, warning `imu_units_m_s2`; anything else → `imu_units_odd` `[default chosen]`. `arkitPoses`: every keyframe needs a row in its epoch within `2/rateHz` s whose position is within 0.05 m of the keyframe centre, else `arkit_pose_stream_gap` (count in the message) `[default chosen]`; `|‖q‖ − 1| > 1e-3` → `arkit_pose_stream_quat`. Streams are never rewritten; `read_stream` applies `scaleToG`.
14. **Tier, flow, branch.** `tier = session.tier`. `flow` = the argument when given (`flowSource: capture_record`), else `photo` for `photo_only` and `guided` otherwise (`inferred`; WS-00 §12 default). `branch = lidar` iff `lidarAvailable and sceneDepthEnabled` and ≥ 1 keyframe has depth and confidence files of the right size; else `image`. `sceneDepthEnabled` on tier `arkit` → `depth_in_non_lidar_tier`; `arkit_lidar` with `sceneDepthEnabled == false` → `lidar_available_not_enabled` (branch `image`); depth files with `lidarAvailable == false` → `depth_without_lidar_flag`, depth ignored `[default chosen]`. The 5 m LiDAR reach and per-region fallback belong to WS-05/06.
15. **Derived stats.** `pathLengthM` per epoch = Σ‖Δc‖ over sorted `normal` keyframes; `durationS`; `keyframesPerS`; `medianSpacingM`; `purposes` index (stills, plus keyframes with `purpose`); `meterFrames`; `stats`; frame flags `slow_exposure` (`exposure.duration` or `exif.ExposureTime` > 1/60 s, spec §5.1 rule 4), `high_iso` (ISO ≥ 1600; A7 `noise_iso1600` breaks registration) `[default chosen]`, `no_pose` (stills without a keyframe). `< 10` images → warning `few_images` (WS-04 owns `too_few_frames`).
16. **Capture check.** Items and rules (`severity` in brackets; `mode: partial` evaluates from `meta` records, items it cannot see are `unknown` and do not block): `meter_close` [required] a still or keyframe with purpose `meter_close` (final: `scaleReference.meterCloseUp.captured` and `still` resolves); `meter_oblique` [required; both flows per `docs/gate-decision.md` "Both flows (A9)"]; `meter_closeups_in_session` [recommended, ARKit] both close-ups link to a keyframe; `meter_tap` [required, guided, final only] a tap with label `meter`; `door_view_meter_side` [required, ARKit, final only; `not_applicable` when `ext.doorOnMeterSide == false`]; `panel_close`, `panel_open`, `main_disconnect` [recommended]; `keyframe_count` [recommended, ARKit] ≥ 40 keyframes else `weak`; `keyframe_cadence` [recommended, ARKit] `medianSpacingM ≤ 0.6` and `keyframesPerS ≥ 1.5` over the primary epoch else `weak`; `tracking_quality` [recommended] limited share ≤ 0.30; `photo_count` [recommended, `photo`] ≥ 40 stills `[default chosen]`. WS-03 appends the pose-frusta codes it computes (`wall_left_of_meter`, `wall_right_of_meter`, `ground_downward_left`, `ground_downward_right`, and `door_view_meter_side` when derived from poses; WS-00 `CaptureCheckCode`, SD-20) through `coverage_missing`. `missing` = codes with status `missing`; `complete` = no required item missing (final: and `report.ok`); `pendingFinal` = any required item `unknown`. Event data is `CaptureCheckData {complete, missing}` (WS-03 may add `meterRead`, `prompts`); each `missing`/`weak` item also yields one `HintData {code, message, retake}` with the same code (default customer-safe messages in `capture_check.DEFAULT_MESSAGES`, overridable by WS-10/14's catalogue); the full `CaptureCheck` goes to `packet/capture_check.json`.
17. **Amendments.** `parentPacketId` → `isAmendment = true`; validation is unchanged (own epochs). `merge_amendment` prefixes the amendment's keyframe, still, tap, plane and epoch ids with `<aid>/`, keeps its paths (already `amendments/<aid>/...`), concatenates lists, unions `files[]`, re-derives `purposes`, `meterFrames`, `stats`, and leaves `primaryEpoch` on the parent `[default chosen]`. Registration of the new frames is WS-04's.
18. **Determinism and budget.** No randomness. `validate` stage `manifest` ≤ 0.5 s, stage `full` ≤ 3 s for 150 keyframes / 120 MB with hashing on 2 vCPU; `normalize` ≤ 10 s (measured on the store fixtures: packetgen's validator 0.03–0.05 s per 47-keyframe packet, sha256 of 18 MB 0.01 s). Log one JSON line per issue with `code`, `pointer`, `capture_id`, never the message value when it could hold `rawText` or a service address.

Issue table (pointer targets in §4 steps): errors `schema, tier_rule, format_version_unsupported, synthetic_in_prod, world_alignment_camera, file_missing, file_size_mismatch, sha256_mismatch, file_duplicate, path_unsafe, file_not_listed, image_format, image_size_mismatch, image_undecodable, pose_non_finite, pose_not_rigid, pose_last_row, pose_translation_absurd, intrinsics_implausible, unknown_epoch, epoch_duplicate, tap_epoch_mismatch, keyframe_timestamp_conflict, depth_missing, depth_size, confidence_size, features_size, tap_unknown_keyframe, tap_pixel_outside, tap_replay, still_unknown_keyframe, still_keyframe_size_mismatch, scale_still_unknown, door_view_unknown_frame, door_box_outside, truedepth_file_not_listed, truedepth_depth_size, stream_unreadable, stream_header, stream_empty, gravity_conventions, exif_focal_out_of_range`. Codes shared with WS-00's `ApiError` list keep its spelling (`schema, tier_rule, format_version_unsupported, file_missing, sha256_mismatch, depth_size, tap_replay, stream_header`); the rest are additions WS-00 §3.8 allows. Warnings are the codes named in steps 1–15.

## 5. Reuse

| Store source | Keep | Drop / change |
|---|---|---|
| `internal/packet-generator/packetgen/validate.py` (`validate_packet`, `check_scale_reference`, `open_stream`, constants `CLOSE_UP_DISTANCE_M`, `HEAD_ON_MAX_ANGLE_DEG`, `OBLIQUE_ANGLE_DEG`) | every rule and threshold | string messages → `ValidationIssue`; jsonschema → `Packet.model_validate`; add pointers |
| `internal/packet-generator/packetgen/conventions.py` (`DEVICE_FROM_CAMERA`, `world_from_device`, `rotmat_to_quat_xyzw`, `qvec_to_rotmat`, `CM_FROM_ARKIT_WORLD`) | into `server/packet/conventions.py` | `from_column_major`, `pixel_ray_arkit`, `project_arkit` come from `server.common.geometry` (WS-00) |
| `internal/packet-generator/packetgen/exif.py` (`read_exif`, `K_from_focal35`, `focal35_from_K`, `EXIF_KEYS`) | all three functions | `synth_exposure`, `exif_datetime` (generator only) |
| `internal/packet-generator/packetgen/checks.py` | only the IMU-gravity-vs-pose snippet (lines 108–117) and depth stats (lines 154–165) | the rest needs `_truth/truth.json`, which pipeline code never reads |
| `internal/modal-pipeline/housepipe/validate.py` (= `server/packet/validate_legacy.py`) | tolerance 1e-3, `try/except` around `from_column_major` | delete after your tests cover it; it validates format 0.1 semantics |
| `internal/modal-pipeline/housepipe/lidar.py` `read_depth` size rule, `frame_depth_spec` | size checks only | scale/fuse/taps are WS-05/06/07 |
| `internal/modal-pipeline/housepipe/pipeline.py` `_load_frames` | the photo-only fallback `K = [w, w, w/2, h/2]` is replaced by EXIF intrinsics | never take the largest epoch only |
| `internal/breadth/a7-iphone-degradation/degrade.py` `_heic` (lines 221–227) | pillow-heif round trip pattern | everything else |

Licences: pillow-heif (BSD-3, bundles libheif LGPL-3 dynamically) `[default chosen]`, Pillow (MIT-CMU), jsonschema (MIT), numpy (BSD). No models, no GPU.

## 6. Fixtures and data

| Fixture | Where | Size | Exercises |
|---|---|---|---|
| `eth3d-facade` `arkit-lidar`, `eth3d-electro` `arkit-lidar` | `.fixtures/packets/<house>/` after `make fixtures ONLY=all` (facade: `cat eth3d-facade.tar.part-* \| tar -xf -`) | 93 MB (76 kf), 36 MB (45 kf) | branch `lidar`: depth + confidence sizes, `derived.depth`, tap replay on the auto `reference_point` tap |
| every `arkit-nonlidar`, `arkit-nonlidar-2018` | same five houses | 36–68 MB | branch `image`; `nonlidar-2018` has no keyframe `exif` (iOS 15) and more `limited.*` frames; both-epoch draws (`e1`, `e2`) appear where the noise profile reset fired (check `epochs` per manifest; synthesize a reset in tests if none did) |
| non-ETH3D `arkit-lidar` (`tt-barn`, `yt-*`) | same | | `lidarAvailable: true`, `sceneDepthEnabled: false`, `depth: null` → warning `lidar_available_not_enabled`, branch `image` |
| every `pose-free` | same | same pixels | `photo_only`: `exif_focal35` intrinsics recomputed from `FocalLenIn35mmFilm` (YouTube houses carry 39 mm → warning `non_wide_lens`), `orientation` 1 or 6, per-still `location` |
| `yt-12Ks1idWj40/packet.photo-only.json` | `.fixtures/packets/yt-12Ks1idWj40/` (WS-00 manifest, SD-29) | 4.3 MB, 6 stills 1920×1080 | photo_only with `meter_surround`/`panel_surround` purposes, `f35 = 26` → `fx = 1323.8` |
| `data/fixtures/packets/examples/example-packet.json`, `example-packet-photo-only.json` | committed (WS-00) | 11 KB, 2.4 KB | `--manifest-only`: meter tap replay to 1.1e-5 (4-decimal pose), two close-ups, door view, scale semantics; photo-only with EXIF + gravity + location (no files exist) |
| corrupted variants | generated by `server/packet/tests/corrupt.py` into `tmp_path`, never committed | | one case per error code in §8 |
| HEIC still | generated in tests from a fixture JPEG with `pillow_heif.from_pillow(im).save(path, quality=50, exif=...)` | ≈ 100 KB | transcode, orientation reset, `compat` file entry |

All store packets are `source.kind: synthetic`, carry no meter close-ups (`meterCloseUp.captured: false`), no `meter` tap and no door views, so their final `capture_check` lists `meter_close`, `meter_oblique`, `meter_tap`, `door_view_meter_side` as missing and `complete: false`; that is the expected result, not a defect. Fixture streams live under `streams/<variant>/<name>.csv.gz` and features under `features/<variant>/`: resolve every path through `files[]`/record fields, never through the spec's folder layout.

## 7. Commands

```bash
uv sync --frozen --extra packet                       # Python 3.12, pydantic, pillow-heif, jsonschema, numpy
export FIXTURE_SOURCE=/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal
make fixtures ONLY=all                                # WS-00: all nine store tars fetched and extracted into .fixtures/packets/<house>/
uv run python -m server.packet.cli validate .fixtures/packets/*/packet.*.json --json /tmp/ws02/validate.json   # 33 packets, < 20 s
uv run python -m server.packet.cli validate data/fixtures/packets/examples/*.json --manifest-only
uv run python -m server.packet.cli normalize .fixtures/packets/eth3d-electro/packet.arkit-lidar.json --flow guided --out /tmp/ws02/electro
uv run python -m server.packet.cli capture-check /tmp/ws02/electro/normalized.json --mode final
uv run python -m server.packet.cli corrupt .fixtures/packets/yt-0gotY_syWWU/packet.arkit-nonlidar.json --case tap_replay --out /tmp/ws02/bad
uv run pytest server/packet -q --cov=server.packet --cov-fail-under=90   # ≈ 60 s CPU
uv run ruff check server/packet && uv run mypy server/packet && uv run lint-imports
uv run python -m server.packet.cli export-schemas --check              # server/packet/schemas/{normalized-packet,capture-check}.v0 drift check
uv run python -m server.packet.cli validate data/fixtures/packets/mini-arkit/packet.json    # WS-00's ARKit fixture with meter stills: ok, meterFrames set
```

CLI exit codes: 0 all ok, 1 any error, 2 usage/IO. `validate` prints one JSON report per packet (`--json` writes the list); `--no-hashes` skips sha256; `--manifest-only` = stage `manifest`. Everything runs on a CPU laptop; no Modal, no GPU, no API key.

## 8. Done when

Paste the table below into the PR with observed values.

1. `validate` on the 33 store packets: 33 × `ok: true`, 0 errors; warnings expected from the set `synthetic_source`, `lidar_available_not_enabled`, `non_wide_lens`, `few_images`, `keyframe_resolution_differs`, `limited_tracking_share`, `fov_unusual`, `principal_point_offcentre`, `tracking_unstable` (any other code must be listed and explained in the PR: synthetic data may legitimately trip it, a rule bug may not); `maxTapReplayError < 1e-6` on every ARKit variant (measured 3.3–5.3e-7 on `yt-0gotY_syWWU`; packetgen README reports < 1e-6).
2. Both examples pass `--manifest-only` with 0 errors; `example-packet.json` yields `derived.meterFrames = {close: "s1", oblique: "s2", tapKeyframe: "k00002"}` and a final `capture_check` with `meter_close`, `meter_oblique`, `meter_tap`, `door_view_meter_side` all `present`.
3. `corrupt.py` cases, each failing with exactly the listed code and pointer and nothing else at error level: `sha256_mismatch` `/files/<i>/sha256` (one byte flipped in a JPEG); `file_size_mismatch` `/files/<i>/bytes` (truncated features file); `depth_size` `/keyframes/<i>/depth/file` (electro `arkit-lidar`, 4 bytes removed); `tap_replay` `/taps/0` (pixel shifted 5 px → error ≈ 1e-3–3e-3); `tap_unknown_keyframe` `/taps/0/keyframe`; `unknown_epoch` `/keyframes/<i>/epoch`; `pose_not_rigid` `/keyframes/<i>/pose` (one rotation entry ×1.1); `keyframe_timestamp_conflict`; `stream_header` `/streams/imuRaw/columns` (swapped header); `file_not_listed` `/keyframes/0/img`; `image_format` `/keyframes/0/img` (JPEG bytes replaced by a HEIC in an ARKit tier); `scale_still_unknown` `/scaleReference/meterCloseUp/still` (example packet); `tier_rule` `/session/tier` (photo_only with keyframes); `world_alignment_camera` `/session/worldAlignment`.
4. HEIC test: a `photo_only` packet whose still is a HEIC with Orientation 6 normalizes to a JPEG under `normalized/stills/`, `orientation == 1`, `(w, h)` swapped to the decoded size, one `compat` entry appended to `files[]`, `derived.notes` non-empty, and `server.contracts.cli check normalized.json` prints `ok`.
5. `normalized.json` for every fixture validates against `server/packet/schemas/normalized-packet.v0.schema.json` and against the packet schema (`server.contracts.cli check`); `load_normalized(path, root)` round-trips byte-identically after JSON canonicalisation; `branch == "lidar"` only for facade/electro `arkit-lidar`; `flow == "photo"` for every `pose-free`/`photo-only`.
6. Unit tests (≥ 30) green with line coverage ≥ 90 % of `server/packet` (excluding `cli.py`): each §4 threshold has a just-inside and a just-outside case (pose 1e-4/1e-3, FOV 45/100/20/150°, jump 3.0 m/s, gravity 3°/15°, m/s² IMU rescale, close-up 0.3/3.0 m and 30°/15°/50°, f35 22/30/10/200), `partial_from_metas` on the example's `files` metas gives `meter_close` present and `meter_tap` unknown, `merge_amendment` prefixes ids and keeps the parent's `primaryEpoch`, `check_packet(pk, root).ok == validate_packet(...).ok` on all 33 packets and all corrupt cases.
7. Runtime on 2 vCPU: `validate --manifest-only` ≤ 0.5 s and `validate` ≤ 3 s for `yt-LCPBf86oloE` (153 kf); `normalize` ≤ 10 s.
8. Regression gates untouched and green: `make validate`, `make schemas`, `make hygiene`, `uv run lint-imports`, WS-00's `score-verdicts` reference (0 unsafe passes); no file > 5 MB, no tar tracked.

## 9. Validator checklist

Fresh clone, own environment, never the builder's outputs; report at `internal/build/validation/ws-02.md` (validator `cursor.subagentId` frontmatter; sections: environment, commands with exit codes, §8 table with observed values, hand checks, verdict PASS/FAIL, defects).

1. Run §7 end to end; paste the 33-packet summary (`ok`, error and warning counts, `maxTapReplayError`).
2. Open three `validation.json` files (electro `arkit-lidar`, a `nonlidar-2018`, `yt-12Ks1idWj40`): every pointer resolves in the manifest (`jq 'getpath(...)'`), no message contains `rawText`, a street address or a file's bytes.
3. Open `normalized.json` for electro `arkit-lidar`: keyframes sorted by `(epoch, timestamp)`, every `ext.hss.flags` value is from the §4.15 list, `derived.depth.coverage == 1.0`, `derived.branch == "lidar"`; for a `pose-free` packet: every still has `intrinsics` and `intrinsicsSource == "exif_focal35"`, `fx == round(f35 · hypot(w,h) / 43.267, 3)`.
4. Re-run three `corrupt.py` cases by hand (`tap_replay`, `depth_size`, `image_format`) and confirm code + pointer; then craft one case the builder did not list (e.g. `cy = h + 1`) and confirm `intrinsics_implausible`.
5. Build a HEIC with Orientation 6 yourself (`pillow_heif.from_pillow`), run `normalize`, and open the transcoded JPEG: upright, `Orientation 1`, listed as `compat` in `files[]`.
6. Timing: `time uv run python -m server.packet.cli validate .fixtures/packets/yt-LCPBf86oloE/packet.arkit-nonlidar.json` ≤ 3 s.
7. `rg -n "rawText|serviceAddress" server/packet` → appears only in the scale-reference checks and tests, never in a log or message f-string; `rg -n "_truth" server/packet` → tests only.
8. Fail if any §8 item misses, a pointer does not resolve, a warning appears at error level (or vice versa) relative to §4, or a `[default chosen]` in this brief is absent from `internal/build/layout-decisions-ws-02.md`.

## 10. Constraints

- Vision models are GPT-5.5 and Grok 4.7 only (no Anthropic); this WS makes no model calls: VLM spend $0, Modal spend $0, no GPU, no API key.
- Never commit real meter numbers (`meterIdentity.rawText` is redacted in reports and logs), Base SSR prompt text, API keys or secret values, files over 5 MB (tars and HEIC samples are generated or fetched), or customer photos. `make hygiene` enforces the mechanical part.
- No customer tools in any capture assumption: no tape, no measured lengths; the checklist can only ask for photos, taps and the TrueDepth flip.
- Target device is the regular non-Pro iPhone: every path must work for `tier: arkit` and `photo_only`; LiDAR is optional and first when present (`branch`).
- Privacy: packets are personal data (photos, GPS, address). Log codes, pointers and ids only; `stills[].location`, `streams.location` and `serviceAddress` are passed through untouched and never printed. `imagesUsable` decides manual review, never the address.

## 11. Out of scope and handoffs

- Signed URLs, commit, finalize handlers, 202/409/422 responses: WS-01 (calls `validate_manifest` through WS-03's re-export at finalize; maps `ValidationReport.to_api_error()` to the 422 body).
- Stage execution, retries, `capture_check`/`hint` event emission, pose-frusta coverage codes, manual-review routing on `imagesUsable`: WS-03.
- Pose refinement, fragments, bridge check, `too_few_frames`: WS-04. LiDAR 5 m reach, scale, meter model reads: WS-05/08. Dense, coverage grid, scene provenance notes: WS-06. Meter detection without a tap: WS-07.
- Customer-safe copy for hints and `views_needed`: WS-10 table, WS-14 catalogue (WS-02 ships defaults only). Spec fixes found here (§12): WS-14 via spec PR.
- Any `server/contracts` change (incl. new `CaptureCheckCode` values): WS-00 owner via spec PR; the fixture manifest is already complete (SD-29).

## 12. Open questions and defaults

| Question | Default until answered |
|---|---|
| Spec §3.2 requires `depth` on every keyframe when `sceneDepthEnabled`, but `captureHighResolutionFrame` frames may lack `sceneDepth` (spec example k00003 has `depth: null`) | warning `depth_missing_highres` for `highResolution: true`, error otherwise; spec PR to WS-14 |
| Rotation tolerance (packetgen 1e-4 vs housepipe 1e-3) | error at 1e-3, warning at 1e-4, SVD re-orthonormalization in the normalized packet |
| Spec §6 lists "timestamps increase within an epoch" as a check; both validators only warn | warning `keyframe_order`; error only for conflicting duplicates (`keyframe_timestamp_conflict`) |
| The packet has no `flow`; `POST /captures` has `flow: guided\|photo` | `flow` comes from `CaptureRecord`; inferred from the tier when absent |
| HEIC in ARKit tiers (spec Appendix D allows it only for `photo_only` imports) | error `image_format` |
| `door_view_meter_side` before finalize (hosted-architecture example emits it during upload, but `doorViews` only travel in `packet.json`) | `unknown` in partial mode; WS-03 may fill it from pose coverage; WS-14 to decide whether `scaleReference` rides along in a `meta` |
| WS-00 `PacketReport.errors: list[str]` vs the 422 body's `{code, pointer, message}` | decided (SD-14): `ValidationReport` is the WS-00 contract type; `PacketReport` no longer exists |
| WS-00 `CaptureCheckData {complete, missing}` has no room for `unknown`/`weak` | decided (SD-19): event data `{complete, missing, meterRead?, prompts?}`; `unknown`/`weak` detail in `packet/capture_check.json` and `hint` events |
| Fixture stream layout `streams/<variant>/` vs spec `streams/<name>.csv.gz` | resolve through `files[]`; no layout assumption |
| Transcoded stills' storage key | decided (SD-02): `captures/{captureId}/normalized/stills/<id>.jpg`; WS-03 materializes the whole capture prefix |
| Photo-count and cadence thresholds (A10 pending) | 40 photos, 40 keyframes, 0.6 m spacing, 1.5 keyframes/s as `weak` (recommended) items; re-tune from WS-13's real capture |
| Digital zoom, `Make != Apple`, tele photos in the photo flow | accepted with warnings `non_wide_lens`, `non_apple_photo`; never rejected |
