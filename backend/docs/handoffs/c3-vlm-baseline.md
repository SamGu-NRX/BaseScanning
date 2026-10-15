---
cursor:
  subagentId: "bc-45fa4069-e277-509c-9604-862dba534a93"
---

# C3 handoff: SSR prompts as the photo-only baseline and downgrade-only judge

## 1. Summary
Path C3 ran Base's 13 SSR prompts unchanged, one API call each, on photo packets. Models: Sonnet 5, Opus 5.5, GPT-5.5; 8 real houses; scored against `docs/house-labels.md` v0.2. Label agreement: Sonnet 87%, Opus 85%, GPT-5.5 76%. **Unsupported confident answers:** 11, 11 and 21. Review-like verdict share: 68–80%. Cost per house: $0.65 / $1.26 / $1.68. **Verdict: no-go as a decider.** It is the bar the pipeline must beat, and the model for a judge that may only downgrade. Report: `internal/breadth/C3-vlm-baseline.md`. Code: `internal/breadth/c3-vlm/`, not yet in any repo.

## 2. Environment
- Python 3.12, `pip install anthropic openai pillow numpy py7zr`. CPU only.
- API keys come from env: the Anthropic and OpenAI pooled keys, with standard `*_API_KEY` aliases also read.
- The Anthropic key ran **out of credit** mid-run. Check its balance before any batch run.
- For tools: ETH3D facade laser scan, from `bash internal/data/scripts/fetch_gt.sh eth3d facade ~/data/eth3d`. The laser geometry is cached at `~/cache/c3/eth3d-facade/laser_geometry.npz`, built in about 30 s on first load.

## 3. Reproduce (from `internal/breadth/c3-vlm/`)
- `python3 run_photo_only.py --models claude-sonnet-5,gpt-5.5,claude-opus-5-5`: all `ready` houses, about 30 s per house (13 prompts in parallel). Skips results that already exist.
- `python3 score.py`: prints per-house agreement, unsafe passes, wrong rejects, review share and cost, then writes `results/scores.json`.
- Facade variants:
  - `python3 meter_hint.py`: photo-only with the meter cross.
  - `PYTHONPATH=. python3 run_tools.py --models gpt-5.5 --prompts p06,p07,p08,p12`: laser tools.
  - `PYTHONPATH=. python3 probe.py --models gpt-5.5 --tools gpt-5.5`: distances vs laser truth.
- Output lands in `results/<house>/photo_only_verbatim/<model>/<prompt>.json`. Each file holds the raw text, parsed JSON, usage, cost, latency and normalised `verdicts`.

## 4. Prompt structure and parameters (no prompt text here; source is `internal/criteria/SSR_Prompts.md`)
- **The 13 prompts fall into four groups:**
  - 7 feature extractors: multiple mains, oversized can, CL320, meter number, meter–panel relationship, mount, type. Each returns a label plus an evidence or clean flag.
  - 3 placement decisions: A (disconnect placement), B (ComEd 3 ft opening rule), C (battery space). A and B return pass or review; C can also reject.
  - 2 panel decisions: main panel type and recall, each pass / review / reject.
  - 1 location prompt with four yes / no / review sub-verdicts, where "yes" means the problem is present.
- **Edits made (only these two):**
  - Few-shot image placeholders became a "reference image not available" marker. Base's reference photos are missing, which is a known fidelity gap.
  - Prompts that name fields but give no JSON shape get one appended output paragraph (p01–p04, p06).
- **Order:** the prompt goes first, then the photos labelled `Photo N:`, with no system prompt, because the prompts say the photos follow. This rules out cross-prompt caching. Putting photos first is 3–4× cheaper, but that deviates from Base's order.
- **Packets:** up to 16 frames at 1568 px on the long edge, with the dataset's tagged evidence frames first (a bias toward informative views). GPT reasoning effort: medium.

## 5. Dead ends
- A reconstructed prompt set (from the Notion digest) was used before the originals arrived. Its results are in `results/*/photo_only/`. They use a different schema: do not score or compare them.
- The store filesystem refuses directory renames, so old result folders stayed where they were.
- The Opus API rejects forced tool choice and returns thinking blocks. Round-trip the assistant content with `model_dump`.
- OpenAI tool images work inside `function_call_output` as `input_image` (Responses API with `previous_response_id`).
- The same input gave three different C verdicts from three models (reject / review / pass). A single run is not a stable verdict.

## 6. Unsupported passes per house (the failure catalogue to beat)
- **yt-0gotY_syWWU** (meter unconfirmed): Opus and GPT treat an unconfirmed small device as the meter, pass C (GPT also B) and read the mount as clean. GPT rejected panel access against an "apparent" panel behind an AC unit.
- **yt-5-M-7f35Tyg** (360×480; gas about 1.5 ft from the meter): Sonnet passes B and claims gas is 5+ ft away. GPT passes B and C, Opus passes C, all on spots around a corner at unresolvable resolution.
- **tt-barn** (no meter per labels): GPT reports a clean standard meter and a panel, passes C, and says "no" to every access question.
- **yt-12Ks1idWj40** (Base install time-lapse): the finished battery is in frame, so all models pass A, B and C citing the installed equipment. **This house leaks the answer; exclude it from placement scoring.**
- **eth3d-facade with tools:** GPT passed B by placing the battery on the tower side wall, which the photos show only edge-on. The laser backend answered measurements there anyway. This is the origin of the coverage rule in `docs/scene-and-verdict-format.md` §3.1.
- **eth3d-electro:** Sonnet rejected C ("all pavement"), while the label says review. It is a wrong reject, not a pass.
- **What held up:** the meter number, CL320, oversized-can and multiple-mains prompts said unclear / not clean every time the evidence was missing.

## 7. Scoring procedure (`score.py`)
- Map each prompt's raw JSON into the label criteria:
  - Features: the raw JSON, matched on the primary label field plus `clean-decision`. A meter-number wildcard `<any>` requires a non-empty value.
  - Decisions: the decision string.
  - Location prompt: four verdict strings.
  - placement.S is skipped.
- **Agree** means the prediction matches any entry in `acceptable_verdicts`.
- **Unsafe pass** is a disagreeing pass or "no", a disagreeing `clean-decision: true`, or a definite (non-unclear) feature label with sufficient evidence.
- **Wrong reject** is a disagreeing reject or "yes".
- Site composite, using profile C: reject if any deciding check rejects or any access check says yes. Pass only if every check passes and every access answer is no, with clean mount and type reads. Otherwise review.
- Errored prompts (no JSON) are left unscored, not defaulted to review. Report agreement **and** unsafe counts. Agreement alone rewards always saying review.
- Known label disputes:
  - On yt-12Ks, frame `t0012.00` shows the panel open, outside, on the meter wall.
  - On the facade, a C pass is defensible on geometry.
  - Recheck both against newer label versions before trusting per-house numbers.

## 8. Integration: the judge may only downgrade
- The deterministic pipeline (C1 solver plus scene coverage) owns every pass. The VLM judge sees the same photos, with the pipeline's chosen spot and route drawn in, and may only move a verdict pass → manual_review, or pass/review → reject when a rule it can see is clearly violated. **It never upgrades.**
- Enforce this in code, not in the prompt: `final = min(pipeline, judge)` on the order reject < manual_review < pass. Log disagreements as `unsure_cause: rule_requires_review` or a judge-specific reason.
- **Allowed judge triggers:**
  - Missed objects or attributes: a gas meter, a drive or parked car, a pool or pool equipment, a window well, a door or gate swing, an overhang or deck.
  - The spot or meter is not actually visible.
  - The meter identity is uncertain.
  - Street-facing front.
  - Low resolution.
- **Never use the judge's numbers.** The probe shows widths off by 6–20% and depths 40–48% short without tools. Any distance must come from `measured` with `coverage_evidence` (§3.1).
- If tools are exposed to the judge, they must return coverage evidence and refuse grazing or unobserved regions.
- **Degrade gracefully:** judge timeout, API error or unparseable JSON leaves the pipeline verdict alone and flags `judge_unavailable`. A missing meter makes the judge force review on placement.
- **Budget:** one call per verdict, using Opus or Sonnet (Opus had zero wrong rejects). Run it only when the pipeline says pass, to cap cost.

## 9. Open risks and cheapest checks
- **No house has a legible meter face or open-panel close-up**, so feature prompts are untested on positives. Run them on 5 real Base SSR packets.
- The missing few-shot reference images may shift verdicts. Rerun with them once Base shares them.
- Claude reruns are pending: `yt-5-M` p08, the tool variant, and the probe. That needs credit on the Anthropic key.
- Low-light, cluttered non-Pro iPhone captures are untested. The expectation is more review verdicts, and the low-resolution pass risk (yt-5-M) stays.

## 10. What I'd do differently
- Freeze packets and labels first. Check for leakage, such as post-install frames, before running anything.
- Build the downgrade-only harness directly, rather than full SSR decisions.
- Put photos first with caching for exploration, and the verbatim order only for the final baseline.
- Attach coverage evidence to tool outputs from day one.
