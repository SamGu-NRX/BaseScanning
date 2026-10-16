---
cursor:
  subagentId: "bc-d9b81738-b7a8-55a2-bded-6fcf0125651e"
---

# WS-08 layout decisions (defaults chosen where the plan or code was silent)

Companion to `docs/build/ws-08-meter-panel-reads.md`. Every `[default chosen]` tag in the brief is listed here with the reason and what would change it. Sources: plan `docs/server-pipeline-build-plan.md` §6 WS-08; `internal/breadth/B2-equipment-reading.md` (B2); `internal/breadth/B3-cheap-gate.md` (B3); `internal/context-dumps/full/b2-equipment-reading.md` (B2 dump); code in `internal/breadth/b2/`, `internal/breadth/b3-cheap-gate/`, `internal/breadth/a9-no-tape/meter_model.py`.

## 1. Defaults chosen

| # | Brief § | Decision | Why | Changes it |
|---|---|---|---|---|
| D1 | 1.2 | `OPENAI_API_KEY` / `XAI_API_KEY` primary (plan WS-12 names); pooled names `OpenAIChatGraphPooledProdApiKey` / `XAI_CHATGRAPH_PROD_API_KEY` accepted as local fallback | B2 code reads the pooled names; the repo's secrets use the plan's names | WS-12 secrets decision |
| D2 | 3 config | `READS_READ_OBLIQUE=on`: read `meter_oblique` as a second meter image | spec §3.4 requires both close-ups with a legible nameplate; second image gives `views_median 2` and a fallback; +≈ $0.045 per house, no wall time (parallel) | cost pressure, or a measured no-gain on real captures |
| D3 | 3 config | Grok 4.7 timeout 120 s, 3 attempts, 300 s per image | Grok p90 78 s (B2 §4); B2 dump §7 recommends ~120 s; hosted-architecture §3 says 90 s (inconsistency I6) | WS-03 hard-stop budget |
| D4 | 3 config | `READS_BUDGET_USD_PER_HOUSE=0.40` | ≈ 2.5× the expected $0.16 (2 meter photos × pair $0.088 + 3 panel photos $0.031 + make/model pair ≈ $0.045) | WS-12 cost report |
| D5 | 3 coverage | `region.element_ids` are class placeholders `["meter"]` / `["panel"]`; WS-09 remaps to scene ids | reads run before any scene exists | WS-09 merge rule |
| D6 | 3 hint | Hint codes `meter_plate_unreadable`, `meter_number_confirm`, `panel_label_unreadable` with a `confirm{}` block carried inside a `hint` event; answer channel proposed as `POST /captures/{id}/confirmations` | event types are fixed in hosted-architecture §2.2 and contain no confirm type; only `hint` fits | WS-01/WS-14 spec PR |
| D7 | 4.1 | `READ_PURPOSES = {meter_close, meter_oblique, panel_open, panel_close, main_disconnect}`; `panel_surround` not read | plan says `meter_close`/`panel_*`; `panel_surround` is a wide shot without a legible label (C3/WS-09 uses it); `main_disconnect` carries the service main amps (criteria spec §2) | WS-09 asks for a read on `panel_surround` |
| D8 | 4.2 | Result keys `captures/{id}/reads/{still_id}.json`, `captures/{id}/reads/reads.json`; cache key `vlm-cache/{sha256}/{model}.{prompt_id}.{max_side_px}.json` | content-hash cache survives amendments and retries and holds no capture id | WS-00 storage layout |
| D9 | 4.3 | `panel/v1` = B2 `PANEL_PROMPT` + `enclosures_visible`, `mains_visible`; ships only if the §8 panel gate still reproduces 5/5 hazards, 0 false alarms, brand/amps counts | the assignment asks for a panel count; B3 showed prompt changes can hurt reads, so the gate decides | gate result |
| D10 | 4.5 | `meter_model/v0` (A9 prompt) called by WS-08 on `meter_close` with both readers; WS-05 consumes `meter_identity` instead of calling again | A9 §3.1: "the model call can be added to B2's call"; avoids a duplicate call on the same image (plan WS-05 text implies its own call, inconsistency I5) | WS-05 brief |
| D11 | 4.7 | Confidence constants = measured rates: `auto_fill` 0.99, agreement 0.956 (65/68), GPT-5.5 alone 0.95 (69/73), Grok alone 0.90 (66/73), `choose` 0.5 | no calibration data beyond B2; literal constants are auditable | a larger labelled set |
| D12 | 4.7 | Exactly one reader matches a barcode while the other disagrees or failed → `prefill` with that value preselected (not auto-fill) | barcode-confirmed picks were 10/10 per reader (B2 dump §4) but n is small; the coordinator's rule is agreement + barcode for auto-fill | ≥ 75 more barcode-confirmed plates |
| D13 | 4.7 | `choose` order: GPT-5.5 top-1, Grok top-1, remaining union, trimmed to 3 | GPT-5.5 top-1 95 % vs Grok 90 %; top-3 holds 71/73 (B3 §3.1) | Base's identifier preference (Q8) |
| D14 | 4.7 | `implausible_length` (outside 6–14 chars or < 5 digits, `SPEC` regex hit, all zeros) demotes a candidate, never drops it; no check digit | PR #16 `locate.py` used these as ranking features only; no universal meter-number checksum exists | Base supplies a utility-specific format |
| D15 | 4.8 | `cl320`: both readers agree on `CL320` → `cl320_meter`; both give another CL or the same non-CL current rating → `not_cl320_meter`; else `cl320_unclear` | criteria spec P3: only readable CL320 text counts; non-CL ratings are non-US plates | CL320 positives collected (WS-13) |
| D16 | 4.9 | Hazard `confirmed` needs both mini and GPT-5.5 (Base list: FPE/Stab-Lok, Zinsco/Sylvania/Magnetrip, Challenger); one flag → `disputed` (review hint); single available reader that flags → `confirmed` | B3 §3.3 + B2 dump §4: 5/5 hazards with 0 false alarms vs 4 false alarms for GPT-5.5 alone; the coordinator asked the brief to follow this. Supersedes the plan's "either reader → review" (I2) | Base's review-load preference |
| D17 | 4.10 | `panel_count` is a hint in `reads.json`, not a criterion fact | P1/P9 are untested (gate decision §5.1); WS-09's brief owns `multiple_mains` / `main_panel_type` via C3 | a measured panel-count set |
| D18 | 4.11 | `panel_recall` with a legible non-recalled brand → `unsure/rule_requires_review` (`READS_PANEL_AUTOPASS=off`) | hazard recall 5/5 rests on 5 panels (Wilson low ≈ 57 %); mirrors WS-09's `CRITERIA_C3_AUTOPASS=off` | the 30–50 panel hazard set (B2 §7.3) |
| D19 | 4.15 | Amendment re-reads replace an `unreadable` result; a legible earlier result is kept unless the new one is `auto_fill` | never downgrade a confirmed/barcode read with a worse retake | WS-03 amendment semantics |
| D20 | 12 Q9 | Env prefix `READS_*` plus shared `VISION_EXTERNAL_CALLS` | matches WS-09's switch name; WS-04 uses `HSS_POSES_*` (I13) | WS-00 convention |

## 2. Inconsistencies found (reported, not fixed; no existing doc was edited)

| # | Where | What | Brief's handling |
|---|---|---|---|
| I1 | assignment vs store | The assignment names `internal/breadth/b2-equipment-reading/`; the code lives at `internal/breadth/b2/` (the plan's `internal/breadth/b2/vlm.py` is right); B3's meter raw answers are under `b2/b3-raw/` | paths in the brief point at `b2/` |
| I2 | plan §6 WS-08 vs B3 §1/§3.3, B2 dump §4 | Plan: "any hazard flag from either reader → review" with GPT-5.5 + Grok 4.7 on panels. Measured: mini @2048 first, GPT-5.5 re-reads; hazard flagged by both → 5/5 with 0 false alarms; GPT-5.5 alone raises 4 false alarms | follows B3 (D16); plan line needs an update |
| I3 | plan §6 WS-08 vs B3 §1 | Plan puts Grok 4.7 on panels; B3's recommended panel tier is GPT-5.4-mini + GPT-5.5 escalation (Grok not used on panels) | brief uses B3's tier; Grok 4.7 stays the meter second reader |
| I4 | plan §6 WS-08 "Done when" | "≥ 96 % when agreeing" is a rounding of 65/68 = 95.6 % | gate uses the integer counts 68/73 and 65/68 with ±2 live tolerance |
| I5 | plan §6 WS-05 vs A9 §3.1 | WS-05 text runs `meter_model.py` (GPT-5.5 + Grok 4.7) on `meter_close`; A9 says the model field belongs in B2's call; two workstreams would call the same image twice | D10: WS-08 makes the call, hands `meter_identity` to WS-05 |
| I6 | hosted-architecture §3 vs B2 §4 / B2 dump §7 | 90 s Grok timeout vs measured p90 78 s and the dump's ~120 s recommendation | D3: 120 s |
| I7 | scene-and-verdict-format §3.1 vs `evalharness/sceneio.py::verdict_contract_errors` | Doc: "median views < 2 breaks the contract"; code applies `rule.min_views` from the evidence itself | brief sets `rule.min_views 1` for element-level reads (as WS-09's brief also assumes); one legible image can support `pass` under the code, not under the doc sentence |
| I8 | B2 dump §7 vs scene-and-verdict-format §4 | Dump maps `cl320` "other CL → fail"; contract maps a clean feature fact to `pass` with its value | brief follows the contract |
| I9 | hosted-architecture §2.2 | No route or event for the device to answer a one-tap confirm; the plan promises "one-tap confirm with top-3" | D6: `hint` event + proposed `POST /captures/{id}/confirmations` (WS-01/WS-14) |
| I10 | criteria spec §1 (two sentences) + WS-09 brief | "Feature prompts never change the site outcome" vs "manual_review if any needed feature is unclean"; WS-09 resolves with `CRITERIA_METER_NUMBER_REQUIRED`. With B3's rule (agreement ≠ auto-fill) ~80 % of houses carry an unclean `meter_number` until the confirm channel exists | flagged as Q2 for Hunter/Base |
| I11 | plan §6 WS-08 "Done when" vs `internal/data/packets/index.json` | Every golden packet has `stills: null`, so no fixture carries a meter or panel close-up | brief adds `scripts/reads_fixture_packet.py` (B2 photos, fetched not committed) |
| I12 | B2 dump §6 exclusion list | `inputs/meter_manifest.csv` is not listed, but its `title`/`page_url` columns carry Commons file names with digit runs (16 rows) | brief excludes the manifest from the repo; fetch reads it from the store |
| I13 | sibling briefs | Env prefixes differ (`HSS_POSES_*` WS-04, `CRITERIA_*` WS-09) | D20; WS-00 to settle |
| I14 | `verdict.v0.schema.json` criterion `id` description vs catalogue | Schema example says `meter_identity`; the catalogue id is `meter_number` | brief uses `meter_number` |
| I15 | plan §6 WS-08 "PR #16 `locate.candidates` ranking as fallback for top-3" vs gate decision (OCR no-go) | `locate.candidates` needs OCR lines with boxes; no OCR engine is ported | brief ports only the box-free features (`payload_tokens`, `SPEC`, `length_ok`, zeros) for demotion; VLM candidates fill the top-3 |
| I16 | WS-09 brief §4 step 9 | Still states "hazard flag from either reader → unsure" for `panel_recall` | WS-08 composes the `panel_recall` item itself (D16); WS-09 takes the outcome as given; reconcile wording |
