---
cursor:
  subagentId: "bc-45fa4069-e277-509c-9604-862dba534a93"
---

# C3: vision-language judge baseline (Base's SSR prompts, as-is)

## 1. Verdict

**No-go as an auto-decider, go as the baseline to beat.** Run as-is, Base's own prompts give confident placement passes on 4 of the 6 real houses that have a meter. The draft labels expect review on all of them (one arguably leaks the answer; see §5). Giving the model 3D tools fixes depth estimates but not width estimates. It also adds a new failure: the model treats geometry the photos never showed as seen.

## 2. Inputs and outputs

- **Prompts:** the original `internal/criteria/SSR_Prompts.md` (all 13), loaded verbatim by `c3-vlm/ssr_prompts.py`. There are only two edits:
  - The 42 `{{image: …}}` few-shot placeholders become "[reference image not available]", because Base's reference photos aren't in the file.
  - p01–p04 and p06 name their output fields but give no JSON shape, so one "Output" paragraph lists exactly those fields.
  - Following the prompts' own wording ("the photos that follow after this prompt text"), the prompt comes first, then the photos labelled `Photo N:`, with no system prompt and one call per prompt.
  - The earlier reconstructed-prompt runs are kept in `results/*/photo_only/` and are **not** used in the numbers below.
- **Houses (all real imagery):** 8 in total.
  - `eth3d-facade` and `eth3d-electro`: laser GT, no real meter.
  - `tt-barn`: laser GT, no meter.
  - Five YouTube walkarounds: `yt-12Ks1idWj40` (a Base install time-lapse), `yt-5-M-7f35Tyg`, `yt-0gotY_syWWU`, `yt-4h8kyjnPo9s`, `yt-Qm4b1cw8pV4` (the last has no label yet).
  - Packets hold up to 16 frames, 1568 px on the long edge, with the dataset's tagged evidence frames first. That biases packets toward informative views, a bit like Base's photo checklist.
- **Models:** Claude Sonnet 5, Claude Opus 5.5 and GPT-5.5 on 6 houses. GPT-5.5 alone on `tt-barn` and `yt-Qm4b1cw8pV4`, because **the Anthropic pooled key ran out of credit at about 16:40 UTC**. That left one Sonnet prompt errored (p08 on `yt-5-M`) and most Claude tool and probe runs undone.
- **Tool variant (eth3d-facade only):** the same verbatim prompts plus tools (`locate`, `measure`, `facing_clearance`, `top_down`, `render_battery`, `view_photo`) over the ETH3D laser scan with COLMAP poses. The loop now runs on Claude and on OpenAI (Responses API).
  - PR #13's invented meter is drawn as a red cross, and a one-line note names it. The cross only falls in photo 8, since no other frame covers that spot. A "photo-only + same cross" variant keeps the comparison fair.
  - The backend itself is accurate: through the same `measure` tool, PR #13's hand-clicked jambs give 5.12, 5.31 and 4.86 ft for the window 4–5 gap (laser: 5.11 ft).

## 3. Metrics against ground truth

**Agreement with the draft labels** (`docs/house-labels.md` v0.2): 17 criteria per house, with placement.S excluded. Feature verdicts are matched on each prompt's primary label field plus `clean-decision`. An **unsafe pass** is a pass, a `no`, or a clean or definite read that the label doesn't accept.

| Model | Labelled houses | Agreement | Unsafe passes | Wrong rejects | Review-like share of verdict slots |
|---|---|---|---|---|---|
| Sonnet 5 | 6 | 87/100 (87%) | 11 | 2 | 80% |
| Opus 5.5 | 6 | 87/102 (85%) | 11 | 0 | 79% |
| GPT-5.5 | 7 | 91/119 (76%) | 21 | 2 | 68% |

The agreement figure is flattering. Most labels are "undeterminable, so review", and a model that always says review scores well on them. The signal is in the placement column and the unsafe passes.

**Placement (A = p06, B = p07 ComEd, C = p08 Battery Space); label expected is `manual_review` everywhere except yt-12Ks C (pass or review):**

| House | Sonnet 5 A/B/C | Opus 5.5 A/B/C | GPT-5.5 A/B/C |
|---|---|---|---|
| eth3d-facade (no meter shown) | R/R/R | R/R/R | R/R/R |
| eth3d-electro | R/R/**reject** | R/R/R | R/R/R |
| yt-4h8kyjnPo9s (no meter in walk) | R/R/R | R/R/R | R/R/R |
| yt-0gotY_syWWU (meter unconfirmed) | R/R/R | R/R/**pass** | R/**pass**/**pass** |
| yt-5-M-7f35Tyg (gas 1.5 ft from meter, 360×480) | R/**pass**/(error) | R/R/**pass** | R/**pass**/**pass** |
| yt-12Ks1idWj40 (Base install) | **pass/pass**/pass | **pass/pass**/pass | **pass/pass**/pass |
| tt-barn (no meter) | – | – | R/R/**pass** |

**Measurement probe (eth3d-facade, laser truth, GPT-5.5 only; Claude runs blocked by credit):**

| Quantity (truth) | Photo-only, run 1 / run 2 | With laser tools |
|---|---|---|
| Window 4–5 gap (5.11 ft) | 4.5 / 4.8 | 4.7 |
| Window 4 width (5.56) | 5.2 / 4.9 | 6.5 |
| Window 4 sill height (3.32) | 2.8 / 2.8 | 3.8 |
| Meter to tower corner (9.9) | 8.6 / 10.2 | 9.5 |
| Tower projection, depth (11.6) | **7.0 / 6.0** | 11.4 |
| Door width (7.18) | 6.3 / 7.0 | 8.6 |
| Mean absolute error | 17% / 15% | 11% |

- The prompts' warning that "models over-read these gaps by ~2×" does **not** hold here. GPT-5.5 slightly *under*-reads widths (6–12% short) on a brick facade photographed head-on.
- Its big photo-only error is depth along the view axis, 40–48% short.
- Tools fix depth. They don't fix widths (errors of +17% to +20%), because the model's own pointing at jambs is the error source; the measuring backend is accurate to about 0.2 ft.

**eth3d-facade placement: photo-only vs meter cross vs tools:**

| Prompt | Photo-only (S / O / G) | + meter cross (S / O / G) | + cross + laser tools |
|---|---|---|---|
| p06 A | R / R / R | R / R / R | Opus R, GPT R |
| p07 B, ComEd (truth: no 8 ft gap on w1, so review) | R / R / R | R / R / R | Sonnet R, **GPT pass** |
| p08 C (label: review) | R / R / R | **reject** / R / **pass** | **GPT pass** |

## 4. Runtime and cost per house (13 prompts, API, no local hardware)

| Model | Cost per house, verbatim order (no cache reuse possible) | Median / p90 latency per prompt |
|---|---|---|
| Sonnet 5 | $0.65 mean ($0.26–0.93) | 4.7 s / 18 s |
| Opus 5.5 | $1.26 ($0.46–1.87) | 8.4 s / 16 s |
| GPT-5.5 | $1.68 ($0.82–2.24) | 7.2 s / 28 s |

- Cost scales with packet resolution. The 360–640 px walkarounds are cheapest.
- Putting the photos *first* lets all 13 prompts share one cached image prefix. That cut cost 3–4× in the reconstructed run ($0.16–0.65 per house), at the price of deviating from Base's stated order.
- The tool variant costs $0.14–0.68 per placement prompt and takes 3–38 tool calls; GPT-5.5 took up to about 150 s.
- All 13 prompts run in parallel, so a house takes about 30 s end to end.

## 5. Failure modes (all observed on real images)

1. **Confident passes on unconfirmed evidence.**
   - yt-0gotY: Opus and GPT name an unconfirmed small device as the meter, then pass placement. GPT also reads its mount as clean and rejects panel access, citing an "apparent main breaker box" behind an AC unit.
   - tt-barn: GPT reports a clean standard meter and a panel on a barn the labels say has none. I couldn't find one at contact-sheet scale either.
2. **Low-resolution placement passes.** On yt-5-M (360×480), Sonnet passes the ComEd rule and puts the gas meter "5+ ft" away; the labels put it about 1.5 ft from the electric meter. GPT passes B and C.
3. **Answer leakage on yt-12Ks.** The packet is the install time-lapse, so the finished battery is in frame, and all three models pass A, B and C citing the installed equipment. Placement results for this house are not a fair test.
4. **Label disputes, for the label owner (not edited):**
   - yt-12Ks: the grey enclosure is open with breakers visible in `t0012.00`, on the exterior wall beside the meter. The models' `panelInHouse = no` and `notSameWall = no` look right, and the v0.2 label ("closed, undeterminable") looks wrong. Correcting it would turn about 2–3 "unsafe passes" per model into agreements.
   - eth3d-facade p08: GPT's pass is defensible on geometry (the label's own profile C note says "the geometry passes"). The label's review rests on drivability and street-facing, which the model weighed differently.
5. **Tools create an unseen-geometry leak.** With laser tools, GPT-5.5 passed p07 by placing the battery on the tower's side wall (w2). The photos show that wall only edge-on, and PR #13 counts it as unobserved; it also has an unannotated narrow window. The laser scan covers it, so `locate`, `measure` and `facing_clearance` answered cleanly, and the model took that as seeing the wall. Any 3D backend must return coverage and view angle with every measurement, and refuse or flag grazing or unobserved surfaces.
6. **Instability.** The same p08 input with the meter cross came back reject, review and pass from the three models. A single photo-only run is not a stable verdict.
7. **Wrong rejects.** Sonnet rejected p08 on eth3d-electro ("all pavement"), and on the facade once the meter was marked. The prompts reserve reject for near-certainty.
8. **What works:** meter number, CL320, oversized can and multiple mains came back "unclear / not clean" whenever the evidence was missing, on every house and model. Opus was the only model with no wrong rejects.

## 6. Licence

- The API models (Anthropic, OpenAI) are fine for commercial use under standard API terms. Photos go to third-party APIs, so customer-data terms need a check before production.
- ETH3D is CC BY-NC-SA (non-commercial). Tanks and Temples is research use.
- The YouTube frames are for internal evaluation only.
- Base's prompts are Base's.

## 7. What going deeper would take

- **Labels and data:**
  - Real Base SSR packets (close-ups, open panels), so the meter and panel prompts get tested at all. None of our houses has a legible meter face.
  - Resolve the label disputes above.
  - Base's few-shot reference images, which are missing from every run here.
  - Rerun Claude when the Anthropic key is topped up: `yt-5-M` p08, the tool variant (p07/p08 on Sonnet, p07/p08/p12 on Opus) and the probe.
- **Tool variant on Track A output:**
  - Write a loader from the Track A scene format into `Geometry`.
  - Add per-measurement coverage and view-angle fields, and have the prompt treat unobserved as unsure (failure 5).
  - Let a deterministic solver own widths and clearances, with the VLM only naming objects.
- **Cost:** photos-first caching (3–4× cheaper), or Sonnet for the feature prompts with Opus for placement only.

## Files (all under `internal/breadth/c3-vlm/`)

| Path | What |
|---|---|
| `ssr_prompts.py` | Verbatim loader + `normalize()` (new) |
| `ssr_prompts_reconstructed.py` | Earlier reconstruction (renamed from `ssr_prompts.py`) |
| `run_photo_only.py`, `vlm.py` | Photo-only runner; `prompt_first` call order |
| `run_tools.py`, `geometry.py` | Tool loop (Claude + OpenAI), laser geometry |
| `meter_hint.py` | Meter-cross packet + photo-only-with-cross variant (new) |
| `probe.py` | Measurement probe (new) |
| `score.py` | Scoring vs labels v0.2 (new); writes `results/scores.json` |
| `results/<house>/photo_only_verbatim/<model>/` | Main results |
| `results/eth3d-facade/{photo_only_meter_hint_verbatim,tools_laser_gt_verbatim,probe}/` | Facade variants |
| `logs/*verbatim*.log`, `logs/probe_run*.log` | Run logs |
