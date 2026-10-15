# WS-11 — Evaluation, replay client, regression gates

Store root `$S` = `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`. Plan: `docs/build/server-pipeline-build-plan.md` §6 WS-11, §8. Builds on the WS-00 brief (`docs/build/ws-00-skeleton-and-contracts.md`, as amended by `docs/build/decisions.md`, cited as `SD-nn`); every `[default chosen]` is listed in `$S/internal/build/layout-decisions-contracts.md` (resolutions in `$S/internal/build/decision-log-group-a.md`). Builder handoff notes cited below: `$S/internal/context-dumps/full/{c1-rules-3d,c2-volumetric,c3-vlm-baseline}.md`. Final pass 23:3x UTC: A7 (`$S/internal/breadth/A7-iphone-degradation.md` §5.4–5.5, interim) and A10 (`$S/internal/breadth/A10-sparse-photo-flow.md` §5.5) folded into §3.3, §4 step 14 and §8 as harness fixes and the re-baseline task (`$S/internal/build/final-pass-report.md`).

## 0. Card

- **WS-11 `eval-and-regression`.** Goal: the replay client that stands in for the iOS app, the regression gates every PR must pass (contract validation, geometry vs laser truth, verdicts vs labels including a positive-pass check, real-phone LaMAR check), and `make e2e`, which replays golden packets through the deployed stack and prints one gate table.
- Builder: Grok 4.7. Validator: a separate agent, never the builder.
- Inputs consumed: WS-00 `server/contracts`, `server/eval`, `data/fixtures`; WS-01 `/v1/*` API; WS-03 stage events and `run/timings.json`; WS-06 `scene/scene.json`; WS-09 `verdicts/verdict.{A,B,C,S}.json` and `server.criteria.run.evaluate_scene`; WS-10 `result/result.json`; WS-04 `server.poses.run` (LaMAR gate); WS-12 `CAPTURE_API_URL` for `dev`.
- Outputs produced: `server/eval/replay.py` (the one replay CLI, SD-36: WS-01 and WS-03 done-when, WS-12 deploy verify, WS-14 iOS test double), `server/eval/gates.py` + `server/eval/gates.yaml` (the gate table every WS pastes into its PR, plan §8), `server/eval/e2e.py` + `make e2e`, `.github/workflows/e2e.yml` (owned here, SD-37), `server/eval/adapters/criteria.py` (real-engine adapter), `server/eval/lamar/`, `data/fixtures/reference-scores/`, `docs/components/ws-11-eval-and-regression.md`.
- Repo `huntertcarver/house-scanning-server`; two PRs: `ws-11/replay-and-gates` (needs only WS-00 + the WS-01 API for the replay smoke) and `ws-11/e2e-and-lamar` (after M1: WS-04/06/09 produce scenes and verdicts).

## 1. Start gate

Stop and report if missing.
- WS-00 PRs merged; `make validate` green; `make fixtures ONLY=all` populates `.fixtures/` (tars, GT PLYs); `score-geometry --gt GT --recon GT` on the facade returns 55 KDs at 0 error.
- Store readable for: `internal/breadth/c1-rules-3d/{expected/,outputs/verdicts/,outputs/scenes/harness-gt-laser.json,outputs/sweep/summary.md}`, `internal/breadth/c2-volumetric/results/`, `internal/breadth/a6-iphone-quality/*.py`, the three context dumps above.
- PR 1 smoke: WS-01 API reachable at `$CAPTURE_API_URL` (`GET /v1/healthz` → 200 `[default chosen]`) with `$CAPTURE_API_TOKEN`, even if finalize still returns a stub run.
- PR 2: `server.criteria.evaluate` (WS-09), `server.poses.run` (WS-04), `server.scene` output (WS-06) exist and the `dev` deployment (WS-12) runs a facade packet to a verdict. LaMAR data downloadable via the A6 `setup.sh` recipe; if the download fails, ship the LaMAR runner with `SKIP` and report.

## 2. Files you own / must not touch

Own: `server/eval/{replay.py,gates.py,gates.yaml,e2e.py,report.py}`, `server/eval/adapters/criteria.py`, `server/eval/cli.py` (rename + new subcommands only), `server/eval/geomscore.py` (`by_wall` stats, §3.4), `server/eval/verdictscore.py` (`pass_recall`, §3.4), `server/eval/extract.py` (robustness items in §4 step 14), `server/eval/lamar/**`, `server/eval/tests/**`, `.github/workflows/e2e.yml`, `Makefile` targets `e2e e2e-local gates-offline lamar`, `data/fixtures/reference-scores/**`, `data/fixtures/verdicts/*/c1-3d/**`, `data/fixtures/manifest.json` (add entries), `docs/components/ws-11-eval-and-regression.md`.
Must not touch: `server/contracts/**` (schema changes go through a spec PR to WS-00), `server/api/**` (WS-01), `server/criteria/**` and `criteria.yaml`/`profiles.yaml` thresholds (WS-09), labels (WS-13), stage modules, store docs.

## 3. Interfaces

### 3.1 Replay client

```bash
python -m server.eval.replay --packet .fixtures/packets/eth3d-facade.tar --variant arkit-lidar \
  --api $CAPTURE_API_URL --token-env CAPTURE_API_TOKEN --rate 2.0 --concurrency 4 \
  --finalize-at last --until verdict --timeout 900 --out runs/replay/facade-lidar.json
```

- `--packet` = a tar, a split-part prefix (`eth3d-facade.tar.part-`), a folder, or a `packetgen export` zip; inside a folder `packet.json` wins when present (variant-less real sessions and `data/fixtures/packets/mini-arkit/`), else `--variant` picks `packet.<variant>.json` (default `arkit-nonlidar`); `--variant` on a variant-less folder is an error (SD-44).
- `--rate` files/s emulating capture (default 2.0 = the spec's ≥ 2 keyframes/s; `0` = as fast as possible); `--concurrency` parallel PUTs (default 4, hosted-architecture §2.1); `--finalize-at last|early` (`early` sends finalize when 50 % of files are committed to exercise `awaiting_files`); `--until verdict|processing` (default `verdict`; `processing` returns as soon as the capture status is `processing`, for deploy verification and stacks without an orchestrator); `--token <value>` or `--token-env <NAME>` (default `--token-env CAPTURE_API_TOKEN`; CI never passes the value); `--timeout` s to wait for `verdict_ready`/`failed` (default 900); `--dry-run` prints the upload plan. WS-01's interim `server/api/replay.py` and WS-12's deploy verify use exactly these flag names (SD-36).
- Python API: `replay(packet: Path, api: str, token: str, *, variant="arkit-nonlidar", rate=2.0, concurrency=4, finalize_at="last", until="verdict", timeout_s=900) -> ReplayRun`.
- `ReplayRun` (written as JSON): `captureId, runId, packetId, variant, tier, flow, files {registered, committed, mismatch}, timings {upload_s, last_commit_to_capture_check_s, finalize_to_verdict_s, total_s}, events: list[Event], result: ResultResponse | None, verdict_paths: dict[profile, Path], scene_path: Path | None, errors: list[str]`.

### 3.2 Harness CLI changes (`server/eval/cli.py`)

- `run-c1` is **renamed** `run-hs11`: it runs PR #11's single-chain solver through `adapters/hs11.py`, not the 3D rules engine (`internal/context-dumps/full/c1-rules-3d.md` §1: "Do not confuse it with harness `run-c1`… which runs PR #11's single-chain solver, not this engine"). `run-c1` remains as a hidden alias that prints a deprecation line and exits 2 `[default chosen]`. Its verdicts keep `evaluator.path: C1-rules-hs11`.
- New `run-criteria --scene S/scene.json --out DIR [--profiles A B C S public] [--ground-rule angle75|angle80|footprint3in] [--no-c2] [--no-c3]` → `server/eval/adapters/criteria.py`, a thin wrapper over the production stage entry point defined in the WS-09 brief (`server/criteria/run.py`):

```python
# WS-09 owns this; WS-11 only calls it. Flags map 1:1 onto CriteriaConfig fields.
def evaluate_scene(scene_dir: Path, out_dir: Path, *, reads: Path | None = None,
                   config: CriteriaConfig = CriteriaConfig()) -> dict[str, dict]:
    """scene_dir/scene.json (+assets) -> {profile: verdict v0}; writes out_dir/verdict.<p>.json,
    c1.<p>.raw.json, c2.sweep.json, c3.facts.json, c3.judge.json, criteria_report.json."""
```

  The adapter validates every returned verdict (`server.eval.sceneio.validate`) and asserts `evaluator.path ∈ {C1-rules-3d, C2-volumetric, C1C2}` and `rules_profile ∈ A, B, C, S, public` (the engine's `strictest` is emitted as `S`, c1 dump §7). `CriteriaConfig` must carry `profiles`, `ground_rule`, `c2: bool`, `c3: bool` `[default chosen; reconcile with WS-09]`; if WS-09 exposes them under other names the adapter maps them, never the gate config.
- New `gates --config server/eval/gates.yaml --inputs runs/e2e/<stamp>/ --out runs/e2e/<stamp>/gates.{json,md} [--strict]`; `e2e`, `lamar` as in §3.3.

### 3.3 Gates

`server/eval/gates.yaml` (excerpt; all thresholds literal, §4 step 6 lists the metric paths):

```yaml
sites:
  eth3d-facade:  {gt: data/fixtures/scenes/eth3d-facade/gt-laser/scene.json, labels: [A, B, C, S, demo]}
  eth3d-electro: {gt: data/fixtures/scenes/eth3d-electro/gt-laser/scene.json, labels: [A, B, C, S]}
  eth3d-delivery: {gt: data/fixtures/scenes/eth3d-delivery/gt-laser/scene.json, labels: [A, B, C, S]}
placement_excluded_sites:
  yt-12Ks1idWj40: "answer leak: frames after t0000.00 show the finished install (c3-vlm-baseline.md §6; house-labels v0.3)"
e2e_packets: [eth3d-facade:arkit-lidar, eth3d-facade:arkit-nonlidar, eth3d-electro:arkit-nonlidar]
geometry:   # provisional until the PR 1 re-baseline (§4 step 14 d): stated as measured on the current harness, commit recorded in harness_sha
  harness_sha: <server/eval commit the thresholds were measured on>
  eth3d-facade/arkit-lidar:    {by_wall.w1.p90_in_max: 3.0, false_observed_claims_max: 0, scale_error_pct_abs_max: 1.0}
  eth3d-facade/arkit-nonlidar: {by_wall.w1.p90_in_max: 5.5, false_observed_claims_max: 0}
  eth3d-electro/arkit-nonlidar: {on_truly_observed_elements.p90_in_max: 6.0, false_observed_claims_max: 0}
verdicts: {unsafe_passes_max: 0, false_rejections_max: 0, coverage_contract_errors_max: 0, c5_margin_errors_max: 0}
decidability:   # criteria_report.decidability.share per (site x profile), SD-49
  gt_laser_min: {eth3d-facade: 0.38, eth3d-electro: 0.75}       # FAIL below, on the laser GT scenes (E2E laser means)
  photo_warn_below: {eth3d-facade: 0.29}                          # WARN only, on colmap-nonlidar (fixtures predate WS-06 wall completion)
  baseline_photo: {eth3d-facade: [0.12, 0.13], eth3d-electro: [0.24, 0.25]}
  baseline_laser: {eth3d-facade: 0.38, eth3d-electro: 0.75}
positive_pass:  # chain s (metres from the meter), not C1's u; gravel-patched facade GT (SD-39)
  - {site: eth3d-facade, scene: gt-laser-gravel, profile: C, ground_rule: footprint3in, expect: pass, wall: w1, start_s_m: [-3.891, -3.383], route_ft_max: 9.0}
  - {site: eth3d-facade, scene: gt-laser-gravel, profile: C, ground_rule: angle80, expect: manual_review}
  - {site: eth3d-facade, scene: gt-laser, profile: C, ground_rule: footprint3in, expect: manual_review}   # unpatched GT: no ground material
  - {site: eth3d-facade, scene: e2e/arkit-lidar, profile: C, ground_rule: footprint3in, expect: pass, allow_warn: true}
safety: {no_coverage_no_pass: true, bounds_honest: true, veto_monotone: true, judge_monotone: true}
lamar: {openings_p90_in_max: 4.6, segments: [2022-06-17_s0, 2022-06-17_s1, 2022-02-10_s0, 2022-02-10_s1, 2021-06-02_s0]}
runtime: {e2e_wall_clock_min_max: 10, capture_check_s_max: 30, time_to_verdict_p50_s_max: 180}
```

Gate result JSON: `{"format": "hss-gates/v0", "stamp", "git_sha", "rows": [{id, group, target, metric, value, threshold, status: PASS|FAIL|WARN|SKIP, evidence_path, note}], "summary": {pass, fail, warn, skip}}`; exit code 1 on any FAIL (also on SKIP with `--strict`). The Markdown table has the same columns; every PR pastes it (plan §8).

### 3.4 Scorer extensions (`server/eval`)

- `geomscore.score_scene` rows gain `wall_id` (from the GT element: `wall_rect.wall_id`, corner walls, object `wall_id` or `Chain.nearest_wall`) and the output gains `key_distances.by_wall[<wall_id>]` = `err_stats` over scored rows of that wall `[default chosen]`. Existing fields are unchanged.
- `verdictscore.score` gains `headline.pass_recall` = `agree / (agree + over_caution + false_rejection)` over start-range positions and site criteria whose truth is `pass` (`None` when there are none) and `headline.labelled_pass_positions`; the site-decision row gains `pass_recall` too. Rationale: an engine that never passes scores 0 unsafe passes trivially (`c2-volumetric.md` §3: "C2 produced 0 passes on every scene… conservatism rather than a hard test of pass accuracy"; `c3-vlm-baseline.md` §7: "Agreement alone rewards always saying review").
- `gates.py` API: `evaluate(config: Path, inputs: Path) -> GateReport`; `report.markdown(GateReport) -> str`.

### 3.5 LaMAR runner (`server/eval/lamar/`)

`python -m server.eval.lamar run --segment 2022-02-10_s0 --data ~/lamar --out runs/lamar/<stamp>/` → builds a v0.4 `arkit` packet from the segment (`lamar_packet.py`: keyframes at 0.5 m / 15° plus all GT-posed frames, per-frame ARKit pose + intrinsics, `source.kind: replay`, no depth), runs `server.poses.run` (WS-04 recipe: σ 0.03 m, no robust loss, spatial + sequential matching), scores the sparse points against the laser with A6's alignment-free pair scorer (`pairscore.py`: `|P−P| − |Q−Q|` on laser twins, bins 0.5–2 m and 15–26 ft) and writes `lamar.json {segment, registered, frames, openings_median_in, openings_p90_in, far_median_in, far_p90_in, scale_error_pct}`.

## 4. Behaviour spec

1. **Upload plan** (`replay.py`): extract the packet to a temp dir (join split parts first); load `packet.json` via `server.contracts.packet.Packet`; `flow = guided` for ARKit tiers, `photo` for `photo_only` `[default chosen]`; `POST /v1/captures` with `Idempotency-Key = packetId` and body `{packetId, formatVersion, tier, flow, device {model: session.deviceModel, systemVersion, appVersion}, expected {keyframes, stills}}`; a repeat run with the same packet must get 200 and the same `captureId` (assert).
2. **Order** (spec Appendix D, hosted-architecture §2.1): `meter_close`, `meter_oblique`, `panel_*` stills first; then keyframes by `timestamp` within epoch order, each immediately followed by its `depth`, `confidence`, `features` files; then other stills; then `streams/*.csv.gz` and `geometry/mesh.ply`; `packet.json` last (finalize). Register in batches of ≤ 50 every `batch_interval = max(1, 50/rate)` s capped at 2 s; `md5` = base64 of the file's MD5; `contentType` = `image/jpeg` (`.jpg`), `application/gzip` (`.csv.gz`), `application/octet-stream` (everything else); `meta` = the keyframe / still / trueDepthFrame record from `packet.json`.
3. **PUT and commit**: PUT bytes with the returned headers; `MULTIPART` targets are split at `partBytes` and completed via `completeUrl`; commit in batches of ≤ 50 every 2 s; on `mismatch` re-upload once, then record the path in `errors`. Retries follow hosted-architecture §2.2 "Retries (client)": network/408/429/5xx → exponential backoff with full jitter 1 s → 60 s cap, at most 8 tries per call, honour `Retry-After`; storage 403 with an expired signature → re-register the path; 400 `BadDigest` → re-read and retry once; API 409 → stop, record `errors`, exit 3; API 422 on finalize → save the body to `<out>.422.json`, exit 4.
4. **Finalize**: body = `packet.json` gzip-compressed with `Content-Encoding: gzip`, `X-Packet-Sha256` = sha256 of the uncompressed bytes, `Idempotency-Key = packetId`. `--finalize-at early` finalizes after 50 % of files are committed and asserts `status == awaiting_files` with a non-empty `missing` list, then continues committing; `last` asserts `status == processing` and `missing == []`.
5. **Events and result**: long-poll `GET /events?after=<seq>&wait=25` until `verdict_ready` or `failed` or `--timeout` (or until status `processing` with `--until processing`); record every event with wall-clock; `last_commit_to_capture_check_s` = first `capture_check` event time minus the last keyframe commit time (plan WS-03 gate ≤ 30 s); then `GET /result`, download `verdictUrl` (and `verdicts/verdict.<profile>.json`, `verdicts/criteria_report.json`, `scene/scene.json` with its assets, `run/timings.json` from `captures/{id}/runs/{runId}/` when a `Storage` is configured for the run) into `<out_dir>/`; write `ReplayRun`.
6. **Gate metrics** (`gates.py`), each row = JSON path → threshold: geometry from `geomscore.json`: `key_distances.by_wall.<w>.p90_in`, `key_distances.on_truly_observed_elements.p90_in`, `len(key_distances.false_observed_claims)`, `abs(scale.scale_error_pct)`, plus `completeness.<region>.coverage.false_observed_by_rule ≤ 0.01` `[default chosen]`; verdicts from `score-verdicts` JSON per (verdict profile × matching labels file): `headline.unsafe_passes`, `start_range.positions.false_rejection + site rows with result false_rejection`, `len(coverage_contract_errors)`, `len(c5_margin_errors)`, and `headline.pass_recall` (reported; FAIL when `< 1.0` on GT scenes with labelled passes, SKIP when `None`); `audit-verdict` `decisive_on_false_claims == 0`; decidability from `verdicts/criteria_report.json` `decidability.share` (cells with outcome `pass|fail` over all cells per site × profile; WS-09 computes it): FAIL below `gt_laser_min` on the laser GT scenes (facade 0.38, electro 0.75), WARN below `photo_warn_below` on facade `colmap-nonlidar` (0.29), otherwise reported next to `unsafe_passes` against the baselines (E2E report: photo models 12–13 % facade, 24–25 % electro; laser 38 % / 75 %) (SD-49). Chain-solver cells count as decided but never lift a candidate (SD-48).
7. **Positive-pass rows** (the coordinator's requirement; truth source = the C1 hand-worked answers `internal/breadth/c1-rules-3d/expected/facade.json`, cases `facade-M1/C` and `facade-M3/C`, whose M1 meter is the harness GT meter: the best spot is w1 at u 31.00–33.58 ft from w1's left end, route 8.52 ft; `c1-rules-3d.md` §3–4). The candidate is compared in chain `s` (metres from the meter, the verdict's `start_s_m` coordinate), not in C1's `u`: w1's `s_range_m` starts at −12.832, so the passing spot is `start_s_m ∈ [−3.891, −3.383]` (`internal/breadth/c1-rules-3d/outputs/verdicts/facade-M1__C-c1rule.json`; SD-39). The scene is the gravel-patched facade GT, `.fixtures/scenes/eth3d-facade/gt-laser-gravel/scene.json`, derived at gate time by WS-09's `python -m server.criteria.tests.data.facade_gravel --gt data/fixtures/scenes/eth3d-facade/gt-laser --out .fixtures/scenes/eth3d-facade/gt-laser-gravel` (ETH3D-derived, never committed), because the unpatched GT has no ground material and the real engine's `eth3d-facade__gt-laser__C.json` is `manual_review` there (that is the third row). Run `run-criteria --scene <gravel>/scene.json --out DIR --profiles C --ground-rule footprint3in --no-c3` → `verdicts/verdict.C.json` with `site.decision == pass`, chosen candidate on `w1` with `start_s_m` overlapping `[−3.891, −3.383]`, `route_length.measured.value_ft ≤ 9.0`; under `angle80` the same scene must give `manual_review` (`c1-rules-3d.md` §4: "the facade verdict flips on the ground rule… don't change a ground rule silently"). On the e2e `arkit-lidar` facade scene the C/footprint3in row expects `pass`; `allow_warn: true` turns a `manual_review` with `unsure_cause: margin` into WARN (the engine keeps 11 of 12 GT passes at 2 in p90 and 6 of 12 at 6 in, `outputs/sweep/summary.md`), while `fail`/`reject` is FAIL. Copy the real-engine reference verdicts `internal/breadth/c1-rules-3d/outputs/verdicts/eth3d-facade__gt-laser__{A,B,C,S,public}.json` to `data/fixtures/verdicts/eth3d-facade/c1-3d/` as baselines. Never score C1's D1–D3 delivery scenes against `eth3d-delivery.*` labels (their meters differ; `c1-rules-3d.md` §6).
8. **Placement exclusion**: `gates.py` drops every placement, candidate and site-decision row for sites in `placement_excluded_sites` before scoring, whatever the labels say, and refuses `yt-12Ks1idWj40` in `e2e_packets` (`c3-vlm-baseline.md` §6: "the finished battery is in frame, so all models pass A, B and C citing the installed equipment. This house leaks the answer; exclude it from placement scoring"; `docs/house-labels.md` v0.3 already exports its placement as `unknown`). Site-scope meter/panel criteria of that house stay scorable.
9. **Safety rows** (fixtures only, no pipeline): `no_coverage_no_pass`: strip `coverage.bands`, `coverage.grid` and `keyframes` from the facade GT scene and run `run-criteria` for every profile, with and without `--no-c2` → `site.decision != pass`, 0 `pass` candidates, every criterion `unsure`/`not_evaluated` (`c1-rules-3d.md` §7 "a scene without coverage (must give all-unsure, never pass)"; `c2-volumetric.md` §7 "without a grid, C2 must emit every criterion as unsure / not_evaluated, never pass"). `bounds_honest`: for every pipeline scene, `frame.anchor.plus_minus_m > 0`, every wall and object `plus_minus_m ≥ 0.02 + rel_bound_95 × |distance from the meter|` (m, `rel_bound_95` from `scene/scale.json`), else FAIL (`c1-rules-3d.md` §7: the naive sweep "produced 80 unsafe passes"). `veto_monotone`: for each candidate, the outcome with C2 on ≤ the outcome with `--no-c2` on the order fail < unsure < pass, and no candidate improves (`c2-volumetric.md` §7 "C2 never upgrades a C1 fail"). `judge_monotone`: with WS-09's C3 judge on, `site.decision` never improves over `--no-c3` on the order reject < manual_review < pass (`c3-vlm-baseline.md` §8 `final = min(pipeline, judge)`); SKIP while the judge is not wired.
10. **`make e2e`**: `e2e.py` replays the three `e2e_packets` concurrently against `$CAPTURE_API_URL` (`dev`), downloads scenes and verdicts, runs `score-geometry` (the recon scene vs its site's GT), `score-verdicts` (each `verdict.<profile>.json` vs `data/fixtures/labels/<site>.<profile>.json`, plus `demo` for hs11 outputs), `audit-verdict`, `run-criteria --ground-rule footprint3in` for the positive rows, then `gates`, and prints the table. Whole run ≤ 10 min wall clock (plan WS-11); `capture_check_s_max` and `time_to_verdict_p50_s_max` come from the `ReplayRun` timings. `make e2e-local` does the same against an in-process API (`STORAGE_BACKEND=local`, `STATE_BACKEND=memory`, orchestrator in the same process) for laptops without Modal; `make gates-offline` runs only the fixture-based rows (verdict scoring on committed reference verdicts, safety rows) and is the PR-time CI job.
11. **Reference-score regression**: after each e2e, compare `geomscore.json` to `data/fixtures/reference-scores/<site>/<packet-variant>.json`: `median_in` within ±0.1, `p90_in` within ±0.2, `scale_error_pct` within ±0.05 pp, KDs `scored ≥ reference`; worse → FAIL, better → PASS with note "update reference" (the builder updates references only in a dedicated commit that pastes both tables).
12. **CI**: `ci.yml` job `gates-offline` (PR + main, < 3 min). `.github/workflows/e2e.yml`: `workflow_dispatch` + nightly `cron: "17 6 * * *"`, environment `dev`, secrets `CAPTURE_API_TOKEN`, variable `CAPTURE_API_URL`; runs `make fixtures ONLY=all` (from `FIXTURE_SOURCE`, a `gs://` mirror when WS-12 provides it), `make e2e`, uploads `runs/e2e/<stamp>/` as an artifact, fails on any FAIL; weekly (`cron: "37 7 * * 1"`) adds `make lamar`.
13. **LaMAR**: nightly-weekly only (15–25 min CPU per segment, `A6-iphone-quality.md` §5); all five segments; gate `max over segments of openings_p90_in ≤ 4.6` (the A6 handoff §7 gate; the measured per-segment openings p90 are 2.8 / 4.1 / 3.9 / 4.2 / 3.9 in, A6 §3.3, so 4.2 would leave no headroom; SD-40). Report `far_p90_in` (15–26 ft, ARKit scale; A6 reference 7.4–12.4 in) and `scale_error_pct` (A6: +2.6 % on `2022-02-10`) without gating.
14. **Harness fixes to carry** (plan WS-11; `internal/eval-harness/README.md` "Depth-phase TODOs"; E2E report `internal/breadth/E2E-criteria-on-recon.md`): rerun `server/eval/eth3d/run_validation.sh` end to end and refresh `data/fixtures/reference-scores/eth3d-facade/geomscore-*.json` (the last full run stopped at the since-fixed `best_fp = fp.min(1)` crash); two E2E bugs with regression tests in `server/eval/tests/`: (a) a viewing angle rounding to 90.2° failed the schema maximum of 90 and dropped C2 verdicts on π³-fused facade models → clamp `best_incidence_deg` to `[0, 90]` before writing, test feeds 90.2; (b) the reference extractor produced a reversed door span on facade w3 that crashed the PR #11 adapter → sort every span before the rules, test feeds a reversed span; dense-cloud extractor items (w1 far end and corner positions 12–17 in off on dense clouds, window-glass points behind walls; run dense scores one at a time to avoid the OOM); `score-cloud` `--max-points 400000` default kept but documented (12 min on 400 k points); keep `--align auto|centres|rotations`; the `views_min` default and mesh visibility fixes are already in the vendored code (verify with a test). Added by A7 and A10 (both in PR 1, before any gate number is trusted): (c) **perfect-click intrinsics** (A7 §5.4, A10 §5.5 a): `taps.py` projects the GT clicks through the DSLR camera and rescales by the width ratio only, so every input with a different field of view, crop or principal point (every phone frame) gets clicks on the wrong pixels, ≈ 20 in of error on every path; upstream `$S/internal/breadth/a7-iphone-degradation/harness-patch.diff` (one hunk: build `gt_cams` from the GT pose with the recon image's own `K`, `w`, `h`; a no-op for full-FoV inputs; A10's independent patch of the same defect is `$S/internal/breadth/a10-sparse-photo/vm-scripts/geomscore.patched.py`, keep whichever reads cleaner) with a test that scores a centre-cropped, re-intrinsic'd copy of the facade GT cameras identically to the full-FoV set on the GT-vs-GT self-check and differently before the fix; (d) **harness drift and re-baseline** (A7 §5.5): A1's stored non-LiDAR model now scores 1.73 / 4.11 / 4.76 in on the current harness against the published 0.84 / 2.90 / 5.20, and the facade door is now graded `observed`, so every acceptance number in the WS-04 brief §8 and in §3.3 here is provisional until PR 1 rescores the stored reference models and clouds (A1 facade/electro sparse, A2 windows, the E2E and `colmap-{oracle,nonlidar}` scenes, A7's `dslr_ref`) on the patched harness, writes them to `data/fixtures/reference-scores/<site>/rebaseline-<harness_sha>.json`, and confirms or amends `gates.yaml` `geometry` in a dedicated commit that pastes the old and new tables and records `harness_sha`; from then on every gate number is stated "as measured on `<harness_sha>`" and a scorer change re-runs the rescore; (e) **delivery extractor failure** (A10 §5.5 b): every delivery_area reconstruction raises `meter clicks hit no wall` in the extractor, so `run_validation.sh` on a delivery recon must end in a scene or a typed extractor error with a regression test on A10's delivery COLMAP model (`$S/internal/breadth/a10-sparse-photo/results/delivery_area/`); (f) **dense-cloud wall split** (A10 §3.4, §5.5 c; the A2/E2E instability above): on dense clouds the reference extractor splits the meter wall and the recon coverage grid then carries no view evidence (`views_min 0`), collapsing C1 decidability for a harness reason; regression test on a dense facade cloud (A10 `cda3` / `cpi3` result or a synthetic dense wall): one meter wall in `chain.wall_ids`, `coverage.bands` on it with `views ≥ 1`, decidability within 5 points of the sparse scene of the same run.
15. Determinism: the replay is deterministic apart from network timing; gates compare against literal thresholds; the LaMAR runner passes no seed of its own, lets WS-04's default `random_seed 0` (A6) apply, and reports `registered` plus the `random_seed` it reads back from `poses/registration_report.json.params` so a registration change is visible (SD-45).
16. Failure modes: API unreachable → exit 2; 409 → exit 3; 422 → exit 4 with the body saved; timeout → exit 5 with the event log; a gate input missing → row `SKIP` with `note`, exit 0 unless `--strict`.

## 5. Reuse

| Source | Destination | Keep / drop |
|---|---|---|
| `internal/eval-harness/evalharness/{geomscore,verdictscore,verdictcheck,cli}.py` (vendored by WS-00) | `server/eval/*` | extend per §3.4; keep every existing output field |
| `internal/eval-harness/evalharness/adapters/hs11.py` | `server/eval/adapters/hs11.py`, CLI `run-hs11` | keep unchanged; baseline only |
| `internal/breadth/c1-rules-3d/{to_verdict.py,from_base_scene.py,score_labels.py}` | WS-09 ports the engine; WS-11 reuses `score_labels.py`'s site-placement scoring as a test oracle | `score_labels.py` hard-codes `LABELS=/cursor/stores/...` → CLI arg |
| `internal/breadth/c1-rules-3d/{expected/*.json,outputs/verdicts/eth3d-facade__gt-laser__*.json,outputs/sweep/summary.md}` | `data/fixtures/{labels/c1-expected/,verdicts/eth3d-facade/c1-3d/,reference-scores/eth3d-facade/c1-sweep-summary.md}` | truth for the positive-pass rows; ETH3D-derived → private repo only |
| `internal/breadth/c2-volumetric/results/` verdicts (`gt.C2.{S,demo}.json` already in WS-00 fixtures), `rules_strictest.yaml` placeholders | gate notes | C2 slope 5° vs C1 10 % placeholder conflict recorded, not resolved here |
| `internal/breadth/a6-iphone-quality/{lamar_io,laser,segments,build_laser,pairscore,rescore,per_segment}.py`, `setup.sh` | `server/eval/lamar/` | keep the scorer and loaders; drop `a1_lamar.py`'s own SfM (WS-04's `server.poses.run` replaces it); licence LaMAR CC BY-SA (internal evaluation) |
| `internal/packet-generator/packetgen/export.py` | replay input handling | read `packet.json` folders/zips it writes; do not vendor |
| `docs/hosted-architecture.md` §2.1–2.2 | replay client behaviour | upload order, batch sizes, retry table |

## 6. Fixtures and data

- WS-00 fixtures (`data/fixtures/`, `.fixtures/`): GT scenes + PLYs for facade/electro/delivery; labels-v0 (28) + facade demo labels; hs11 reference verdicts; five packet tars. LiDAR branch = `eth3d-facade:arkit-lidar` and `eth3d-electro:arkit-lidar` (laser-rendered depth); image branch = `*:arkit-nonlidar`; photo flow = `*:pose-free` (replay smoke only, no gate).
- New in WS-11: `data/fixtures/verdicts/eth3d-facade/c1-3d/{A,B,C,S,public}.json` (real engine, contract mode, ~30–60 KB each), `data/fixtures/labels/c1-expected/{facade,electro-delivery}.json` (hand-worked decisions: facade 16 cases with passes `facade-M1/C` and `facade-M3/C`; electro/delivery 17 cases), `data/fixtures/reference-scores/eth3d-facade/c1-sweep-summary.md` (pass-keep curve: 12/12 at 0 in, 11/12 at 2 in, 8/12 at 4 in, 6/12 at 6 in, 0/12 at ≥ 12 in or any ARKit drift profile), e2e reference `geomscore` per packet after the first green run.
- LaMAR (`~/lamar`, fetched by the A6 recipe): sessions `2022-06-17_10.43.29` (depth recorded), `2022-02-10` (no depth, worst ARKit, +4.3 % scale), `2021-06-02` (no depth); 87–205 frames per 35 m segment at 1440 × 1920.
- Excluded from placement scoring and from `e2e_packets`: `yt-12Ks1idWj40` (§4 step 8). Excluded from geometry gates: `tt-barn`, `yt-Qm4b1cw8pV4`, `yt-LCPBf86oloE` (no laser GT; replay smoke and runtime rows only).

## 7. Commands

```bash
uv sync --frozen && make fixtures ONLY=all                       # WS-00 fetch, ≈ 300 MB
make gates-offline                                               # fixture-only rows, < 3 min CPU
uv run python -m server.eval.cli run-hs11 --scene data/fixtures/scenes/eth3d-facade/gt-laser/scene.json --out /tmp/hs11.json
uv run python -m server.eval.cli run-criteria --scene data/fixtures/scenes/eth3d-facade/gt-laser/scene.json \
  --out /tmp/crit/fp3 --profiles C --ground-rule footprint3in --no-c3                   # ≈ 3 s/profile + 16 s coverage query
uv run python -m server.eval.cli score-verdicts --verdict /tmp/crit/fp3/verdict.C.json \
  --labels data/fixtures/labels/eth3d-facade.C.json --out /tmp/vs
export CAPTURE_API_URL=https://<dev>.modal.run CAPTURE_API_TOKEN=...                       # names only
uv run python -m server.eval.replay --packet .fixtures/packets/eth3d-facade.tar --variant arkit-lidar \
  --api $CAPTURE_API_URL --token-env CAPTURE_API_TOKEN --out runs/replay/facade-lidar.json  # ≈ 4–8 min incl. pipeline
make e2e                                                          # 3 packets concurrently, ≤ 10 min, prints the gate table
make e2e-local                                                    # in-process API, CPU laptop, ≈ 20–40 min without a GPU
make lamar                                                        # 5 segments, ≈ 1.5–2 h CPU; weekly CI only
```

## 8. Done when

- `make gates-offline` exits 0 and its table shows: verdict rows for the hs11 references (facade demo: `unsafe_passes 0`, `start_agreement 1.0`) and the c1-3d references (facade A/B/C/S vs labels-v0: `unsafe_passes 0`, site agreement 1.0, `pass_recall SKIP` because labels-v0 has no scorable pass); the positive-pass rows `facade/gt-laser-gravel/C/footprint3in → pass` (candidate `start_s_m` within `[−3.891, −3.383]`), `…/angle80 → manual_review` and `facade/gt-laser/C/footprint3in → manual_review` PASS; `decidability.share` rows PASS on the facade (≥ 0.38) and electro (≥ 0.75) GT scenes from `run-criteria` output; `no_coverage_no_pass` PASS for every profile and engine; `veto_monotone` PASS; `judge_monotone` SKIP or PASS; the 90.2° clamp, reversed-span, perfect-click cropped-FoV, delivery-extractor and dense-wall-split tests green (§4 step 14 a–f); the re-baseline commit exists with `harness_sha` in `gates.yaml` and `data/fixtures/reference-scores/*/rebaseline-<harness_sha>.json`, and the PR pastes old vs new reference scores (A1 facade non-LiDAR 0.84 / 2.90 published vs 1.73 / 4.11 on today's harness, A7 §5.5).
- `make e2e` against `dev` finishes in ≤ 10 min with every row PASS or WARN and: facade `arkit-lidar` `by_wall.w1.p90_in ≤ 3.0`, `false_observed_claims == []`, `|scale_error_pct| ≤ 1.0`; facade `arkit-nonlidar` `by_wall.w1.p90_in ≤ 5.5`; every `verdicts/verdict.{A,B,C,S}.json` validates and scores `unsafe_passes 0`, `false_rejections 0`; `capture_check` within 30 s of the last committed frame; `bounds_honest` PASS on all three scenes; `time_to_verdict_p50_s ≤ 180` with the `poses` stage thinned to ≤ 48 SfM frames (SD-10).
- Failing a gate fails CI: `uv run pytest server/eval/tests/test_gates.py` includes a case that feeds a verdict with one unsafe pass and one that removes coverage and asserts exit code 1 and a FAIL row; a case that turns the facade GT C/footprint3in decision into `manual_review` and asserts the positive-pass row FAILs.
- `run-c1` exits 2 with the deprecation message; `run-hs11` on the facade GT reproduces `data/fixtures/verdicts/eth3d-facade/gt.C1.g80.json` apart from `evaluator.runtime_s` and `scene.sha256`.
- Replay idempotency: running the same packet twice yields the same `captureId` and `runId`; `--finalize-at early` yields `awaiting_files` then `processing`.
- `make lamar` (weekly) reports five segments with `max openings_p90_in ≤ 4.6`; a segment that fails to register ≥ 60 % of frames is a FAIL row.
- Regression gate that stays green from now on: `make gates-offline` on every PR; nightly `e2e.yml` green; no PR may lower `pass_recall` or raise `unsafe_passes` on any golden site (plan §8).

## 9. Validator checklist

Report at `internal/build/validation/ws-11.md` (store frontmatter; environment, commands with exit codes, artifacts inspected, hand checks, PASS/FAIL, defects).
1. Fresh clone: `make gates-offline` → exit 0; paste the table; confirm the three facade positive-pass rows (gravel pass, gravel angle80 review, unpatched review) and `no_coverage_no_pass` appear with PASS, and that no row references `yt-12Ks1idWj40` placement.
2. Corrupt a copy of `data/fixtures/verdicts/eth3d-facade/c1-3d/C.json` (flip one `unsure` candidate to `pass`) and rerun `gates` on it → FAIL row `unsafe_passes`, exit 1. Delete `coverage.bands` from a copy of the facade GT scene, run `run-criteria --profiles C --ground-rule footprint3in` → `manual_review`, 0 pass candidates.
3. `run-c1 …` → exit 2 with the deprecation text; `run-hs11` reproduces the reference verdict (`jq 'del(.evaluator.runtime_s, .scene.sha256)'` both sides, `diff` empty).
4. With `dev` reachable: `make e2e` → table with all PASS/WARN; open `runs/e2e/<stamp>/facade-arkit-lidar/geomscore.md` and check by hand that `by_wall.w1` exists and that `false_observed_claims` is `none`; open one `verdict.C.json` and confirm every `pass` criterion carries `coverage[].status == observed`.
5. Sample by hand: 3 `ReplayRun` JSONs → events in the SD-08 partial order (`validate` → {`poses`, `objects`, `reads`} → `scale` → `dense` → `coverage` → `scene` → `objects_3d` → `criteria` → `result`), `capture_check` present, `finalize_to_verdict_s` recorded.
6. Fail the WS if: any gate threshold is read from anywhere but `gates.yaml`; `pass_recall` is missing from the scorer output; the positive-pass rows are absent or hard-coded to PASS; the Houston house contributes to any placement metric; `run-c1` still runs the hs11 solver silently; e2e exceeds 10 min on two consecutive runs; any file > 5 MB or any tar is committed.

## 10. Constraints

- Vision models GPT-5.5 and Grok 4.7 only (cheap tiers from OpenAI/xAI only), no Anthropic; WS-11 code itself makes no model calls. Cost caps (SD-38): one `make e2e` triggers three pipeline runs (VLM ≈ $0.30 per house with the B3 cascade, hard cap $1.50 per run → ≤ $4.50 VLM worst case, plus measured geometry-only L4 $0.05–0.15 per house); nightly cap 1 run/day; `make lamar` is CPU-only; never run e2e on every PR.
- Never commit: real meter numbers, Base SSR prompt text, keys or tokens (env names `CAPTURE_API_URL`, `CAPTURE_API_TOKEN` only), files over 5 MB (tars, PLYs, LaMAR data, `runs/`), customer photos. `runs/` is gitignored; artifacts go to CI uploads.
- No customer tools in any gate assumption: the replay client never fakes a tape or a measured length; LaMAR truth is laser, not a homeowner measurement.
- Target device: gates run on `arkit` (non-Pro) and `arkit_lidar` variants; LiDAR rows are first when present, image rows must also pass. Privacy: fixtures are public datasets and synthetic metadata; LaMAR is CC BY-SA and stays out of git.
- Store docs are read-only; inconsistencies found go into the component page and the reply to the coordinator.

## 11. Out of scope and handoffs

Labels, start-range pass labels for A/B/C/S with a fixed meter, demo-profile labels for electro/delivery, Hunter's approval (WS-13; `c1-rules-3d.md` §9 asks for them); the criteria engines, the C2 veto join, C2/C1 placeholder unification (slope 5° vs 10 %), the C3 judge (WS-09); API handlers and storage/state backends (WS-01); Modal deploy, `deploy.yml`, the fixture `gs://` mirror (WS-12); scene extraction quality (WS-06); pose recipe (WS-04); the customer-safe prompt catalogue and the iOS-facing `packetgen` CLI (WS-14); contract changes (WS-00 via spec PR). WS-11 hands the gate table format to every WS and the `ReplayRun` format to WS-01/03/12/14.

## 12. Open questions and defaults

| Question | Default until answered |
|---|---|
| Which packets make up "3 golden packets" (plan) | `eth3d-facade:arkit-lidar`, `eth3d-facade:arkit-nonlidar`, `eth3d-electro:arkit-nonlidar` (the two sites with laser GT and labels; delivery has no packet) |
| Non-LiDAR facade geometry bar | `by_wall.w1.p90_in ≤ 5.5` (harness reference 5.09 in overall with drawn scale −1.66 %); LiDAR/correct scale `≤ 3.0` per plan WS-04 |
| Ground rule for the positive-pass row | `footprint3in` (the only rule under which the hand-worked facade C pass holds); Base's rule choice may retire this row |
| Positive-pass coordinate and scene (criteria writer's finding) | decided (SD-39): chain `s` `[−3.891, −3.383]` on the gravel-patched GT; the unpatched GT row expects `manual_review` |
| LaMAR gate 4.2 (plan) vs 4.6 (A6 handoff §7) | decided (SD-40): 4.6; measured 2.8 / 4.1 / 3.9 / 4.2 / 3.9 in reported as is |
| Acceptable review rate / E2E success-rate table (`E2E-criteria-on-recon.md` still pending) | report `manual_review_rate` per site, no gate until Base sets a target |
| `pass_recall` with no scorable labelled pass | SKIP with a note; the C1 hand-worked rows are the positive check until WS-13 lands start-range pass labels |
| `CriteriaConfig` field names for profiles / ground rule / C2 / C3 (WS-09 brief) | decided (SD-41): `profiles`, `ground_rule`, `c2`, `c3` as in §3.2; `gates.yaml` never changes |
| `/v1/healthz` (not in hosted-architecture §2.2) | decided (SD-36): WS-01 serves `{status, storage, state, wifTokenExpiresAt, version}`; the smoke checks `status == ok` |
| LaMAR unavailable in CI | weekly job SKIPs with a note; the runner still ships and runs locally |
| Harness drift since A1/A2 scored and phone-FoV perfect clicks (A7 §5.4–5.5, A10 §5.5) | PR 1 upstreams the `taps.py` patch, rescores every stored reference on the patched harness and confirms or amends the `geometry` thresholds in one commit; until then every WS-04 §8 and §3.3 number is provisional and labelled with `harness_sha`; phone-FoV inputs (LaMAR, WS-13) are scored only after the patch |
| A10 extractor defects (delivery `meter clicks hit no wall`; dense clouds split the meter wall) | regression tests in PR 1 (§4 step 14 e–f); delivery stays out of `e2e_packets` (no packet archive) and its C1/C2 scoring uses the GT scene until the extractor accepts its reconstructions |
