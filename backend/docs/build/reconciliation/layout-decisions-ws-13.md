---
cursor:
  subagentId: "bc-ccbc31a8-1ec3-57d0-8b15-270b20f62624"
---

# WS-13 layout decisions and inconsistencies

Companion to `docs/build/ws-13-real-house-and-data.md`. Every `[default chosen]` in the brief is listed here with its reason and who can override it. Section numbers refer to the brief. Written 2026-09-26 22:10 UTC against plan `docs/server-pipeline-build-plan.md` (21:13 UTC), template `internal/build/brief-template.md`, WS-00/04/06/08/09/11/14 briefs in `docs/build/`, `internal/breadth/B3-cheap-gate.md` (21:41 UTC) and `internal/breadth/E2E-criteria-on-recon.md` (22:02 UTC).

## 1. Defaults chosen (`[default chosen]` in the brief)

| # | Brief § | Decision | Why | Override by |
|---|---|---|---|---|
| D1 | 0 | Two PRs: `ws-13/meter-models-and-registry` (PR-A), `ws-13/real-house-and-data` (PR-B) | PR-A needs no capture day and unblocks WS-05's loader; PR-B waits on Hunter's schedule (plan §9). Template allows an exception when stated | coordinator |
| D2 | 3.3 | Table lives at `data/meter-models/meter_models.v1.json` (WS-13), WS-05 reads it via env `METER_MODELS_PATH` with that default | Plan WS-05 and WS-13 both name `meter_models.json`; splitting data (WS-13) from loader (WS-05) avoids two owners of one file in `server/scale/`. WS-12 must copy `data/meter-models/` into the image | WS-05 + WS-12 |
| D3 | 3.4 | Sessions `guided-nonpro` (`arkit`), `photo-nonpro` (`photo_only`), `guided-lidar` (`arkit_lidar`); one tar per session; manifest `packet.json` (spec layout) committed at `data/fixtures/packets/real-00N-<session>/packet.json`; replay without `--variant` | Real sessions do not share keyframes, so packetgen's shared-pixels variant layout does not apply. WS-00's `check-packets` glob `packet.*.json` would miss `packet.json`: request in the brief (§12 Q10) | WS-00, WS-11 |
| D4 | 3.4 | House ids `real-001`, `real-002`; no address, city or owner in any id, path or record; `location` = state | Assignment: addresses stay out of the repo; ids must not encode them | none |
| D5 | 3.4 | `house.json` `source_type: team_capture` (new enum value); `frames.dir: null`, frames never copied under `houses/<id>/frames/` | `internal/data/README.md` enum has no team-capture value; copying a private house's frames into `houses/` would duplicate personal data outside the scrubbed packets | dataset owner |
| D6 | 3.2 | Scrub removes `streams.location`, `stills[].location`, `scaleReference.ext.serviceAddress` and EXIF GPS (exiftool metadata edit, pixels untouched); frames showing a face, plate or house number are dropped, never edited | Spec §5 rule 1 forbids re-encoding keyframes; dropping is the only pixel-safe redaction. Location is never used for distances (spec §3.6) | WS-14 (spec) |
| D7 | 4-B1 | `packetgen --camera-model OPENCV_FISHEYE` for `yt-olz1uwI4vM8` if registration < 30 % | Manifest caveat: ultra-wide lens; packetgen has a single `SIMPLE_RADIAL` today | packetgen owner |
| D8 | 4-C0 | `packetgen photos --source-kind device --purposes CSV` | `photos.py` hard-codes `source.kind: synthetic` and derives purposes from manifest tags; real photos with real EXIF are `device` | packetgen owner |
| D9 | 4-C1 | R1 selection thresholds: clear span ≥ 1.5 m within 1 m of the meter; no opening or equipment within 0.91 m; gas ≥ 1.83 m; drivable surface ≥ 2.13 m from the wall; pool ≥ 3.05 m; eave ≥ 2.13 m; facing ≥ 1.52 m; slope ≤ 5°; envelope 0.91 × 0.76 m clear, overhead ≥ 1.83 m | Spec §4.2 thresholds (A/B 5 ft, C 3 ft + depth, B 3 ft openings, 7 ft headroom, 5 ft facing, P12 envelope) with a margin so the site passes every profile, giving the first `pass` labels under A, B, C and S (E2E §7: none exist) | Hunter |
| D10 | 4-C5, 8 | Minimum KDs: R1 12 (≥ 3 in 4.57–7.92 m, ≥ 2 full openings), R2 6 (its binding distances and both ends) | Plan says 5–10 check distances; 12 covers every harness KD family once | coordinator |
| D11 | 4-C5 | Declared truth bounds laser 0.010 m, tape 0.015 m | Instrument accuracy (±1.5 mm laser, ±3 mm tape) plus endpoint definition; ≥ 4× tighter than the 2–6 in bar | none |
| D12 | 4-D4 | Start-range label margin 0.15 m: `pass` bands clear every check by ≥ 0.15 m, `borderline` within 0.15 m of a threshold | 0.15 m ≈ 6 in, the loose end of the accuracy bar (gate decision) | Hunter |
| D13 | 4-D8 | `docs/real-house-validation.md` in the store (plan path), `docs/components/ws-13-real-house.md` in the repo | Plan header says plan paths are store paths; WS-00 requires a repo component page per WS | coordinator |
| D14 | 4-D7 | `packetgen.calibrate --session/--truth`; the real profile replaces `nonlidar_modern`, the assumed one is kept as `nonlidar_modern_assumed` with `n_sessions: 1` | packetgen README: "Replace `nonlidar_modern` with the first real non-LiDAR phone session"; keeping the old profile preserves reproducibility of A1/A9/E2E numbers | packetgen owner, WS-04 |
| D15 | 4-E2 | Per-house `approval {status, by, on, ref}` in `house-labels.json`; `export_labels_v0.py` writes the approval into `labeller` | labels.v0 schema has `additionalProperties: false`, so approval cannot be a new label field | Hunter |
| D16 | 4-F | Fold-in mechanics: `internal/build/fold-ins/<id>.md` (store), `docs/evidence-register.md` (repo), one issue per owning WS; plan never edited by the builder | Plan §0: contract and plan changes go through the coordinator; the builder must not touch other WS's code | coordinator |
| D17 | 4-G | LaMAR fetchers under `scripts/lamar/` (WS-13), `server/eval/lamar/` untouched (WS-11) | WS-11 owns `server/eval/lamar/**`; the coordinator assigned the fetcher rewrite to WS-13 | WS-11 |
| D18 | 8 gate 9 | LiDAR and photo-flow numbers are targets, not gates (LiDAR p90 ≤ 4.0 in, scale ≤ 1 %) | First real data; the plan's gates are "verdict matches" and "6 in p90" for the non-Pro guided flow only | coordinator |
| D19 | 10 | Spend caps: VLM ≤ $2 per run, ≤ 12 runs; Modal ≤ $30 | B3 cascade ≈ $0.30/house, WS-11 e2e ≈ $0.50 GPU per run; 12 runs cover two houses × three sessions × two iterations | Hunter |

Unmarked interpretations (not defaults, listed for completeness): `real-house-truth/v0` reuses the harness KD ids so `truth_to_scene.py` is a direct map (brief §3.1); Measure Lab conversions are `source.kind: replay` per spec §3.1 and are never uploaded because spec §6 requires `imuRaw` and ≥ 10 Hz `arkitPoses` in ARKit tiers; the `generic-prior` table entry carries A9's assumed 6.35/6.50/6.60 in covers with `usable_for_scale: false` so the assumption stays visible but never counts as coverage; `TARGET_MODELS.md` seeds from the 16 families in `meter_model_check.csv` plus seven common US AMI families, each admitted only with a datasheet.

## 2. Instruction-driven changes (coordinator, not defaults)

- 21:47 UTC: B3 landed → §4-F row 1 folded now; only A7, A10, E2E (then) and the Modal benchmark pending. A9 handoff §4/§6 → per-entry provenance in the table, table on the critical path (§4-A). A6 handoff §3 → rewrite the VM-only LaMAR fetchers (§4-G).
- 22:03 UTC: E2E landed → the capture task now requires **two houses**: R1 measured and expected to pass under A and C with full labels (the pipeline's pass path has never been exercised: 102 photo-model verdicts, 0 unsafe passes, no labelled pass anywhere), R2 an expected reject; acceptance gate 10 requires at least one `eligible` verdict on R1 with `recommended_placement` inside a labelled `pass` band; `yt-LCPBf86oloE` (44/183 image-only, close-ups unregistered) cited in §4-C0 as evidence for in-app ARKit capture. The plan's WS-13 text still says "one real house": the coordinator should update it.

## 3. Inconsistencies found (reported, not fixed; the plan and existing docs were not edited)

| # | Where | Inconsistency | How the brief handles it |
|---|---|---|---|
| I1 | Plan §6 WS-13 | Packetize `yt-KSXy4LFwQU4`; the manifest id is `yt-KSXy6LFwQU4` | Uses `yt-KSXy6LFwQU4`, notes the typo (§4-B1) |
| I2 | Assignment, plan §0 | `internal/data/packets/archives/index.json`; the file is `internal/data/packets/index.json` (`archives/` holds only tars) | Correct path everywhere (§1 item 6) |
| I3 | Plan §6 WS-00 | Golden fixture `eth3d-facade.tar`; the archive is split into `eth3d-facade.tar.part-00/-01` | Split noted (§6) |
| I4 | Plan §1, §6 WS-07/08/13 | B3 and E2E listed as pending; both landed (21:41 and 22:02 UTC). B3 supersedes WS-07's "OWLv2 proposals → crops → cheap tier" (no crops, CLIP ∪ SigLIP top-2, OWLv2 no gain) and WS-08's "auto-fill when B3 confirms ≥ 95 %" (Wilson low 87.8 %, not confirmed); gate decision §3 VLM cost $0.75–1.65 → $0.30 | Folded in §4-F rows 1 and 4; WS-08's brief already agrees; WS-07 brief not yet written |
| I5 | `docs/house-labels.md` header; WS-00 §1, §6; WS-11 §6 | "7 ready houses", "28 labels-v0 files"; the manifest has 12 ready houses and `labels-v0/` holds 32 files (8 sites incl. `eth3d-delivery` × 4). Five manifest houses are unlabelled | §4-B4 labels them (→ 52 files) and fixes the header in v0.6; WS-00/WS-11 fixture counts should read 32 |
| I6 | Plan §6 WS-05 vs WS-13 | Both write `meter_models.json` (v0 CENTRON vs v1) | D2: WS-13 data + schema at `data/meter-models/`, WS-05 loader |
| I7 | Plan WS-13 vs assignment vs A9 | "Base calipers ~15 models" vs "20–30 models from public datasheets" vs A9 handoff §6: datasheets give a cover diameter for CENTRON only, Aclara I-210+ overall only, L+G FOCUS none | Table admits 20–30 entries but expects only 3–8 `usable_for_scale`; provenance per number; caliper rows from R1/R2 and Base (§4-A5) |
| I8 | Plan WS-11 vs A6 handoff §7 vs WS-11 `gates.yaml` | LaMAR openings gate 4.2 in (per-segment patches) vs 4.6 in (pooled points) | Both handed to WS-11 (§4-G); WS-04's brief flags the same |
| I9 | WS-11 brief §1 vs A6 handoff §3 | "LaMAR data downloadable via the A6 `setup.sh` recipe"; the handoff says `fetch_meta.py` / `fetch_par.py` are VM-only and not in `setup.sh` | §4-G rewrites them |
| I10 | Spec Appendix A | `scaleReference.ext.obliqueCloseUp.still` description says "purpose meter_close"; §3.4 says `meter_oblique` | Report only (WS-14 spec upkeep) |
| I11 | Spec §3.5/§6 vs the only team capture app | ARKit tiers require `imuRaw` and ≥ 10 Hz `arkitPoses`; PR #7 Measure Lab records neither, so no compliant guided packet exists without the iOS team's v0.4 app | Guided fallback scored offline as `replay` (§4-C0, §12 Q9); milestone M3 depends on the app |
| I12 | `internal/data/packets/index.json` | `not_built` lists `yt-olz1uwI4vM8` only; `yt-KSXy6LFwQU4` and `yt-Y0DSQekoV_g` are also unbuilt | §4-B builds all three, empties `not_built` |
| I13 | E2E §5 vs manifest / packetgen | `yt-LCPBf86oloE` registered 44/183 in 6 fragments (pycolmap 4.2, relaxed settings) vs 151/183 (COLMAP 3.9.1 CLI, relaxed mapper) in the manifest; packetgen kept 153 keyframes for its packet | Report to WS-04/WS-11: the LCPB golden packet's poses may embed a fold; registration is tool-version sensitive |
| I14 | E2E §7 vs WS-00 §4 | Harness bugs: coverage `worst_best_incidence_deg` 90.2 > schema max 90; reversed opening span crashes the PR #11 adapter. WS-00 allows only "two additive edits" to the schema copies, so the max-90 fix needs a spec PR | Fold-in row 4 assigns to WS-00/WS-11 |
| I15 | Plan §6 WS-13 vs coordinator 22:03 | "one real house" vs two houses (pass + reject) | Brief follows the coordinator; plan text to be updated |
| I16 | Plan §6 WS-13 | "report `docs/real-house-validation.md`": store or repo is ambiguous | D13 |
| I17 | E2E §1 vs plan §6 WS-09 | C1 rules-3d (the plan's C1 engine) decides 1–7 % of cells on photo models because of its evidence rule, PR #11 15–56 %; site decisions 100 % review on every photo model | Fold-in row 4 → WS-09 evidence-rule review; not a WS-13 change |

## 4. Alignment notes

- Repo names follow WS-00: `data/fixtures/` (committed, < 5 MB) + `.fixtures/` (fetched), `data/fixtures/manifest.json` items with `committed: false` for tars and LaMAR, `scripts/fetch_fixtures.py`, `make fixtures|validate|hygiene`, `uv run`, `docs/components/_template.md`, settings `RULES_PROFILE_DEFAULT=C`.
- WS-11 names: `server.eval.replay` flags (`--packet --api --token --finalize-at --timeout --out`), `server/eval/gates.yaml` `lamar` block, `make e2e|e2e-local|lamar`, `pass_recall` (SKIP until WS-13 pass labels).
- WS-08 names: `READS_AUTOFILL_NO_BARCODE` stays off for gate 10; reads are confirmed through WS-10's reviewer path.
- WS-04: the real capture is the only place σ sweeps may run (`scripts/score_poses.py`, WS-04 §12 Q10); the recalibrated profile goes to packetgen and WS-02.
