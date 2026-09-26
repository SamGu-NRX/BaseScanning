# WS-08 builder brief: meter and panel reads (agreement pair, zxing, one-tap confirm, early worker)

Store root for every `internal/...` and `docs/...` path: `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`. Repo paths are relative to the checkout of `huntertcarver/house-scanning-server`. Plan section: `docs/server-pipeline-build-plan.md` §6 "WS-08". Evidence: `internal/breadth/B2-equipment-reading.md` (B2), `internal/breadth/B3-cheap-gate.md` (B3), `internal/context-dumps/full/b2-equipment-reading.md` (B2 dump). `[default chosen]` marks a choice the plan or code left open; every one is listed in `internal/build/layout-decisions-ws-08.md`. Reconciliation decisions applied on 2026-09-26 (storage keys, `HSS_READS_*`, hint and confirmation contracts, photo-fact pass rule) are logged in `internal/build/decision-log-group-c.md`.

## 0. Card

- **WS-08, equipment reads.** Goal: read the meter nameplate (meter number, ANSI form, class label, CL320 flag, make/model for scale) and the panel photos (brand, main breaker and panel rating amps, recall match, condition flags, enclosure/mains counts) with GPT-5.5 and Grok 4.7 as an agreement pair, confirm meter numbers with zxing barcodes, return a one-tap confirm to the device when the pair is not barcode-confirmed, and emit contract-valid `fact` criteria; all of it as an early worker that finishes during the walk, plus the `reads` stage that assembles the result at finalize.
- Builder model: Grok 4.7. Validator: a separate agent (any model), never the builder.
- Inputs: committed stills (`role: still`, or keyframes carrying `purpose`) with purpose in `meter_close`, `meter_oblique`, `panel_open`, `panel_close`, `main_disconnect`, delivered by WS-03 (`dispatch_early`, `CommittedFile.key`) from WS-01 storage; `scaleReference.meterCloseUp.viewAngleDeg` when present (WS-02 packet). Outputs (SD-01, SD-07): `captures/{id}/early/reads/<stillId>.json` (per image, early worker), `captures/{id}/early/meter_identity.json` (for WS-05's `scale` stage), `captures/{id}/runs/{runId}/reads/reads.json` (`base-reads/v0`, the `reads` stage artifact; consumed by WS-09 criteria, WS-10 result and reviewer view), `ReadsStatus` for WS-03's `capture_check`, `Hint` objects for WS-03 to publish as `hint` events.
- Repo `huntertcarver/house-scanning-server`, branch `ws-08/meter-panel-reads`, one PR.

## 1. Start gate

Stop and report (do not invent the missing piece) unless all of these exist on `main`:

1. WS-00 merged: `server/reads/` stub + README, `server/common/` (config with `Settings.VISION_EXTERNAL_CALLS`, logging, the `Storage` protocol `put_bytes(key, data, content_type)`, `get_bytes(key) -> bytes`, `exists(key) -> bool`, `head`, `list_prefix` — JSON helpers are WS-08's own in `cache.py`), `server/contracts/jobs.py` (`StageContext`, `StageResult`, `StageName` includes `reads`; `CommittedFile`), `server/contracts/api.py` (`HintData`, `CaptureCheckData`, `ConfirmationRequest`, SD-19/SD-21), `server/eval/` = vendored `internal/eval-harness/evalharness/` with `server/contracts/schemas/verdict.v0.schema.json`, CI running `pytest` and `python -m server.eval.cli validate`.
2. Secrets available as env vars where the worker runs: `OPENAI_API_KEY`, `XAI_API_KEY` (names only in the repo). Local runs from the store VM may fall back to `OpenAIChatGraphPooledProdApiKey` / `XAI_CHATGRAPH_PROD_API_KEY` `[default chosen]`.
3. Python 3.12 repo-wide (WS-00 D-16), `uv sync --frozen --extra reads` with the `pyproject.toml` optional group `reads` = `openai pillow zxing-cpp numpy pydantic jsonschema` (zxing-cpp 3.1.1, Apache-2.0; no torch, no OCR engine).
4. B2 photos fetchable: `python internal/breadth/b2/fetch.py meter --manifest internal/breadth/b2/inputs/meter_manifest.csv` (83 photos, ~3 min), `... panel --manifest inputs/panel_screened.csv` (67), `... extra --manifest inputs/panels_extra.csv` (61) into `$B2_DATA` (default `/tmp/b2data`). Labels stay in the store (§6).

## 2. Files you own / must not touch

Create or modify: `server/reads/{__init__,config,types,prompts,client,image,barcode,candidates,policy,panel_policy,worker,aggregate,facts,cache,hints}.py`, `server/reads/tests/**`, `server/reads/README.md`, `scripts/reads_gate.py`, `scripts/reads_fixture_packet.py`, `scripts/reads_seed_cache.py`, `docs/components/ws-08-reads.md`, `Makefile` target `reads-gate` (append only), `data/fixtures/reads/**` (§6 only).

Forbidden: `server/eval/**` (WS-00/WS-11), `server/api/**`, `server/orchestrator/**` (WS-01/WS-03: you return hints and status, they publish), `server/scale/**` (WS-05), `server/criteria/**` (WS-09), `server/result/**` (WS-10), `docs/*.md` contracts, schemas, CI config, other `data/fixtures/**`. In the store: never edit `internal/**`. Never copy into the repo (they hold real meter numbers, excluded from repo): `internal/breadth/b2/labels/meter_numbers_hand.csv`, `inputs/hand_numbers_raw.txt`, `inputs/meter_locate_per_image.csv`, `inputs/meter_clean_per_image.csv`, `inputs/meter_manifest.csv` (its `title`/`page_url` columns carry file names with digit runs), `mosaics/num_*.png`, `raw/meter/*.jsonl`, `b3-raw/**`, `results/meter_per_image.csv`, `results/meter_analysis.md`, `results/meter_table.md`.

## 3. Interfaces

Units: pixels for boxes (normalised `[x, y, w, h]`, top-left origin, as in `meter_eval.locate`), seconds for timings, USD for cost, degrees for `viewAngleDeg`. Strings compare through `normalize` (A–Z, 0–9 only) and `core` (leading letters before the first digit stripped), ported unchanged from `internal/breadth/b2/vendor/meter_eval/match.py`.

```python
# server/reads/config.py  (per-area knobs HSS_READS_*, SD-11; the shared switch is WS-00 Settings.VISION_EXTERNAL_CALLS)
@dataclass(frozen=True)
class ReadsConfig:
    external_vision: bool = True        # Settings.VISION_EXTERNAL_CALLS=on|off (shared with WS-07/09)
    autofill_without_barcode: bool = False  # HSS_READS_AUTOFILL_NO_BARCODE; on only after §12 Q1
    panel_autopass: bool = False        # HSS_READS_PANEL_AUTOPASS; the photo-fact pass rule, awaiting Base (§4 step 11)
    read_oblique: bool = True           # HSS_READS_READ_OBLIQUE [default chosen]
    max_side_px: int = 2048             # never lower (B3 §3.3: 1024 px loses 2 meter top-1, 3 brands, 2 amps)
    gpt_timeout_s: float = 60.0         # median 7.2 s, p90 9.1 s per photo (B2 §4)
    grok_timeout_s: float = 120.0       # median 41 s, p90 78 s (B2 §4; B2 dump §7) [default chosen]
    mini_timeout_s: float = 30.0        # median 1.4 s, p90 1.6 s
    attempts: int = 3                   # backoff 10 s, 20 s (vlm.py: 10*(attempt+1))
    still_deadline_s: float = 300.0     # per image, all calls [default chosen]
    budget_usd_per_house: float = 0.40  # HSS_READS_BUDGET_USD_PER_HOUSE [default chosen]
    cache: bool = True                  # HSS_READS_CACHE=on|off, key = image sha256 (§4 step 2)
    models: tuple[str, str, str] = ("gpt-5.5", "grok-4.7", "gpt-5.4-mini")
```

```python
# server/reads/types.py (pydantic v2)
class StillRef(BaseModel):   # built by WS-03 from the files-register meta (still or keyframe record)
    capture_id: str; still_id: str; path: str; sha256: str; purpose: str
    w: int; h: int; keyframe: str | None = None; view_angle_deg: float | None = None
class ReaderAnswer(BaseModel):
    model: str; prompt_id: str; answer: dict | None; raw_text: str; secs: float; usage: dict
    cost_usd: float; cached: bool = False; error: str | None = None  # vlm_timeout|vlm_parse_error|provider_quota|provider_error|disabled
class Candidate(BaseModel):
    value: str; core: str; sources: list[str]; rank: int; flags: list[str]   # sources ⊆ {gpt-5.5, grok-4.7, barcode}
class MeterRead(BaseModel):
    status: Literal["auto_fill","prefill","choose","unreadable","single_reader","disabled"]
    value: str | None; core: str | None; candidates: list[Candidate]; confidence: float; agree: bool; barcode_match: bool
    class_label: str | None; class_number: str | None; cl320: Literal["cl320_meter","not_cl320_meter","cl320_unclear"]
    form: str | None; manufacturer: str | None; legibility: dict[str, str]; images: list[str]
class PanelRead(BaseModel):  # one per image, then merged in aggregate.merge_panels
    equipment: str | None; brand: str | None; brand_evidence: str | None; product_line: str | None
    main_breaker_amps: int | None; panel_rating_amps: int | None
    hazard: Literal["confirmed","disputed","none","unknown"]; hazard_line: str | None; review_hints: list[str]
    rust: str | None; damage_or_scorching: bool | None; enclosures_visible: int | None; mains_visible: int | None
    text_seen: list[str]; legibility: str | None
class StillResult(BaseModel):
    still: StillRef; answers: list[ReaderAnswer]; barcodes: list[dict]; meter: MeterRead | None; panel: PanelRead | None; cost_usd: float; secs: float
class ReadsStatus(BaseModel):   # WS-03 copies .meter into CaptureCheckData.meterRead (SD-19)
    meter: Literal["ok","pending","unreadable","absent"]; panel: Literal["ok","pending","unreadable","absent"]
```

```python
# server/reads/worker.py            (called by WS-03's dispatch_early per CommittedFile; idempotent on (capture_id, path, sha256))
def read_still(still: StillRef, storage: Storage, cfg: ReadsConfig) -> tuple[StillResult, list[Hint]]: ...
# server/reads/aggregate.py         (after every still, and once more in the `reads` stage with the packet's stills[])
def build_reads(capture_id: str, storage: Storage, cfg: ReadsConfig, packet: dict | None = None) -> Reads: ...
def reads_status(capture_id: str, storage: Storage) -> ReadsStatus: ...
# server/reads/__init__.py          (stage `reads`, SD-24; runs build_reads and writes runs/{runId}/reads/reads.json)
def run(ctx: StageContext) -> StageResult: ...
# server/reads/facts.py
def to_criteria(reads: Reads) -> tuple[list[dict], list[dict]]   # (criterion items, top-level views_needed entries)
# server/reads/policy.py
def decide_meter(gpt: ReaderAnswer | None, grok: ReaderAnswer | None, barcodes: list[dict], cfg: ReadsConfig) -> MeterRead: ...
def confirm(reads: Reads, still_id: str, choice: str | None, typed: str | None, by: Literal["device", "reviewer"],
            field_id: Literal["meter_number", "panel_brand", "main_breaker_amps"] = "meter_number") -> Reads: ...
```

`reads.json` (`Reads` model, the shape WS-09's brief already assumes):

```json
{"format": "base-reads/v0", "capture_id": "cap_…", "status": "complete | partial | disabled",
 "facts": ["<verdict-v0 criterion items from to_criteria, scope site>"],
 "views_needed": [{"id": "rd1", "kind": "confirm_attribute | close_up", "target": "…", "criteria": ["meter_number"], "why": "…"}],
 "meter": "<MeterRead>",
 "meter_identity": {"manufacturer": "…", "model_family": "…", "model_evidence": "printed | visual | null", "model_confidence": 0.0,
                    "cover_material": "…", "ansi_socket_meter": true, "base_flange_visible": true, "socket_type": "…", "agree": true, "images": ["s1"]},
 "panel": {"merged": "<PanelRead>", "per_image": {"s3": "<PanelRead>"}},
 "panel_count": {"enclosures_max": 1, "mains_max": 1, "agree": true},
 "confirm": {"still_id": "s1", "mode": "prefill | choose", "candidates": ["<Candidate>"], "preselected": 0, "answered_by": null, "answer": null},
 "cost_usd": 0.0, "timings_s": {"s1": 0.0}, "warnings": []}
```

- Criterion item: `id` (`meter_number` | `cl320` | `panel_recall`), `scope: "site"`, `outcome`, `unsure_cause` when unsure, `reason`, `measured: null`, `threshold: null`, `fact {value, clean, raw, coverage[]}`, `evidence {element_ids, views, coverage}`, `views_needed: [<ids>]`. Validate against `server/contracts/schemas/verdict.v0.schema.json` (`additionalProperties: false` everywhere; the schema example id `meter_identity` is stale, the catalogue id is `meter_number`; the coordinator's spec PR fixes the example).
- `fact.coverage[]` = one entry: `region {element_ids: ["meter"] | ["panel"], images: [<still ids>], description}`, `status`, `probes` (images sent), `observed_share`, `seen_share`, `views_min`, `views_median` (legible images used), `best_incidence_deg` = `viewAngleDeg` or null, `worst_best_incidence_deg: null`, `rule {min_views: 1, surface: "free", max_incidence_deg: null, observed_share: 1.0}`, `method: "reads/v0"`. `element_ids` are class placeholders (no scene exists yet); WS-09 remaps `[default chosen]`.
- `Hint` (returned to WS-03, published as event `type: hint` with WS-00's `HintData {code, message, retake?, promptId?, frames?}`, SD-19; WS-08's codes `meter_plate_unreadable | meter_number_confirm | panel_label_unreadable` are in WS-00's `HintCode` verbatim, SD-20): `{code, message: <customer-safe, rendered by WS-10 from WS-14's catalogue>, promptId: hint.<code>, retake: meter_close | panel_open | null, confirm: {imageId, fieldId: meter_number, mode: prefill | choose, candidates[], preselected, allowNone: true} | null}`. `confirm` is **not** in `HintData` today: WS-08 proposes it as an additive field in the spec PR WS-14 files for SD-21, and until it lands the device takes the candidates from `MeterBlock.candidates` in `GET /captures/{id}/result` (WS-10). The answer channel is WS-01's `POST /captures/{id}/confirmations` (SD-21): `ConfirmationRequest {fieldId: meter_number | panel_brand | main_breaker_amps, kind: confirmed | corrected | none, value?, imageId, clientAt}`; WS-03's `on_confirmation` hook calls `policy.confirm(reads, still_id=imageId, choice=value if kind == confirmed else None, typed=value if kind == corrected else None, by="device", field_id=fieldId)`; the WS-10 reviewer form posts the same body and arrives with `by="reviewer"`.

## 4. Behaviour spec

1. **Trigger and idempotency.** WS-03 calls `read_still` for every committed file whose `meta.still.purpose` or `meta.keyframe.purpose` is in `READ_PURPOSES = {meter_close, meter_oblique, panel_open, panel_close, main_disconnect}` (`panel_surround` is not read: no legible label; C3/WS-09 uses it `[default chosen]`). `still_id` is the still record's id, or the keyframe id when only the keyframe carries the purpose. Bytes are read from `CommittedFile.key` (`uploads/…` before promotion, `captures/…` after, SD-30). If `captures/{id}/early/reads/{still_id}.json` exists with the same `sha256`, return it. Unknown purpose → `ReadsError("purpose_not_readable")`.
2. **Image prep (`image.py`).** Load bytes, `ImageOps.exif_transpose`, RGB, downscale so the long side ≤ `max_side_px` (never upscale), JPEG q90, base64 `data:image/jpeg`. **No crop, sharpening, tiling or contrast change** (B2 dump §4; B3 §3.3: crops cut identifiers and the p07 Stab-Lok label). Cache key `captures/{id}/early/reads/vlm-cache/{sha256}/{model}.{prompt_id}.{max_side_px}.json` (`sha256` = committed file digest; the early prefix of SD-01) `[default chosen]`; a hit skips the call with `cost_usd 0`, `cached: true`.
3. **Prompts (`prompts.py`).** `meter/v0` = `METER_PROMPT` and `panel/v0` = `PANEL_PROMPT` from `internal/breadth/b2/vlm.py`, byte-identical (our wording; every number below was measured on them). `meter_model/v0` = `PROMPT` from `internal/breadth/a9-no-tape/meter_model.py`, byte-identical. `panel/v1` = `panel/v0` plus two keys `"enclosures_visible": int|null, "mains_visible": int|null` (separate service enclosures / independent main breakers visible) `[default chosen]`; §8 item 4 decides whether v1 ships. Do **not** port `internal/breadth/b3-cheap-gate/vlm_ids.py` (the identifier-context prompt dropped GPT-5.5 top-1 from 69 to 65 and pair precision to 63/67, B3 §3.1). Never paste text or examples from `internal/criteria/SSR_Prompts.md`.
4. **Calls (`client.py`).** OpenAI-compatible `chat.completions`: `gpt-5.5` and `gpt-5.4-mini` on OpenAI, `grok-4.7` on `base_url https://api.x.ai/v1`; message = text prompt then `image_url` with `detail: "high"`; `max_completion_tokens 8000`; no `temperature` (reasoning models reject it; run-to-run drift ±1–2 photos on 73, B2 dump §3). Parse the first `{…}` block (`vlm.py::parse`); none → `vlm_parse_error`. Timeouts per model (§3), `attempts` tries with 10 s then 20 s sleep; HTTP 429/402 → `provider_quota`. `cost_usd` from `tiers.PRICES` (USD per M tokens in/out: `gpt-5.5` 5.0/30.0, `gpt-5.4-mini` 0.75/4.5, `grok-4.7` 2.0/6.0; Grok's reported completion tokens may exclude reasoning, so its cost is a lower bound). Log with WS-00's keys `vlm_provider, vlm_model, tokens_in, tokens_out, vlm_cost_usd, duration_ms` (SD-13) plus `prompt_id` and the sha256 prefix; **never log candidate strings or meter numbers**.
5. **Meter images, both readers in parallel.** For `meter_close` (and `meter_oblique` when `read_oblique`): `meter/v0` on `gpt-5.5` and `grok-4.7` concurrently (one thread each), plus `meter_model/v0` on both concurrently for `meter_close` only (one call each, instead of a second WS-05 call on the same image; A9 §3.1) `[default chosen]`. Ranked list per reader = `[meter_number] + number_candidates`, deduplicated on `core`, nulls and empty strings dropped (`score_meter.vlm_view`). No cheap first tier for meters: mini + Grok 4.20 agreement is 61/73 agreed with 56 correct and still left 5 wrong auto-fills after escalation (B3 §3.3). Cost per meter photo: GPT-5.5 ≈ $0.036, Grok 4.7 ≥ $0.009.
6. **Barcode (`barcode.py`).** Port `engines.py::barcodes`: `zxingcpp.read_barcodes` on the grayscale image at scale 1.0 and 0.5, unique payloads with normalised boxes; payload cores = `core(normalize(part))` for each `re.split(r"[\s;*,{}]+", payload)` part with ≥ 4 digits (`locate.payload_tokens`). Runs on every meter image while the VLM calls are in flight; precision source only (decodes on 20/83 photos; confirms the first pick on 10/73 per reader, 10/10 correct, B2 dump §4).
7. **Meter decision (`policy.decide_meter`).** `g` = GPT-5.5 top-1 core, `x` = Grok top-1 core, `B` = payload cores. Confidence constants live in `policy.CONFIDENCE` and are the measured rates `[default chosen]`:
   - `auto_fill`: `g == x` and `g ∈ B`. Agreement alone is 65/68 correct (95.6 %, Wilson low 87.8 %), under the 95 % bar; with the barcode match it was 10/10 (B3 §3.1, B2 dump §4). `confidence 0.99`.
   - `prefill`: `g == x`, no barcode match → one-tap confirm with the agreed value preselected, `confidence 0.956`. With `autofill_without_barcode` on, this branch becomes `auto_fill` at `0.956`.
   - `prefill` when exactly one reader matches a barcode and the other disagrees or failed: that reader's value preselected, `confidence 0.95` `[default chosen]`.
   - `choose`: `g != x`, both non-null → candidates = GPT-5.5 top-1, Grok top-1, then the remaining union in reader order, trimmed to 3 (top-3 holds 71/73, B3 §3.1); `preselected: null`, `confidence 0.5` `[default chosen]`. No third voter: all three agreed errors (m38, m61, m66) are the same wrong identifier picked by both models, so voting cannot remove them (B3 §3.1, §5).
   - `single_reader`: one reader errored or timed out → that reader's top-3 in `prefill` mode, never auto-fill (hosted-architecture §3 failure table); `confidence 0.95` for GPT-5.5 alone (69/73), `0.90` for Grok alone (66/73).
   - `unreadable`: both top-1 null on every meter image (the flagship readers return null under stickers or off-frame, B2 §3.1) → hint `meter_plate_unreadable` with `retake: meter_close` while the capture is open; after finalize an `unsure/unobserved` fact plus `views_needed` `close_up`.
   - `meter_oblique` corroborates: its agreed core equal to the `meter_close` pick → `views_median 2` and the image joins `coverage.region.images`; `meter_close` unreadable and the oblique reads → use the oblique pick with `warnings += ["value_from_oblique"]`.
   - `value` = GPT-5.5's `meter_number` string as printed (fallback Grok's), whitespace-trimmed; `core` for comparisons. No check digit exists: apply no checksum. Plausibility flag `implausible_length` when `not (6 <= len(core) <= 14 and digits >= 5)` (`locate.py length_ok`), the `SPEC` regex hits, or the core is all zeros; it demotes the candidate in `choose` order and never drops it `[default chosen]`.
8. **Class, form, make/model.** `class_number()` from `score_meter.py` (`\bCL\s*[-.:]?\s*(\d{2,3})\b|\b(\d{2,3})\s*CL\b`) on `class_label`; `cl320 = cl320_meter` if both readers give `CL320`, `not_cl320_meter` if both give another CL or the same non-CL current rating (e.g. `30(200)A`), else `cl320_unclear` `[default chosen]`. `form` and `manufacturer`: both agree (normalised) → clean, else GPT-5.5's with `agree: false`. `meter_identity` = `meter_model/v0` answers: `model_family` clean when both agree and one says `model_evidence: printed` (GPT-5.5 was 21/21 on checkable plates, A9 §3.1), else GPT-5.5's with `agree: false`; WS-05 owns the datasheet lookup. Any `cl320_meter` adds `warnings += ["cl320_positive_unmeasured"]` (no CL320 plate in any dataset; false-CL320 rate 0/83, B2 §3.2).
9. **Panels: cheap tier, then GPT-5.5 (`panel_policy.py` = B3 `tiers.py::verify_risky_amps_legible`).** Per image:
   - `gpt-5.4-mini` with `panel/v1` at 2048 px first (≈ $0.002, median 1.4 s).
   - Escalate the same image to `gpt-5.5` when mini reports `hazard_line ∈ HAZARD_LINES`, or a brand with `brand_evidence != "printed_text"`, or a non-null `main_breaker_amps` or `panel_rating_amps`, or `legibility == "good"` with both amps null.
   - Merge: brand = GPT-5.5's, else mini's; `main_breaker_amps` and `panel_rating_amps` = GPT-5.5's only (mini invents amps: 4 wrong claims alone vs 1); `hazard = confirmed` only when **both** models return a `hazard_line` in `BASE_RECALL = {federal_pacific_stab_lok, zinsco_sylvania_magnetrip, challenger}`: 5/5 hazards (p07, x0007, x0012, x0013, x0678) with 0 false alarms on 127 panel photos, against 4 false alarms for GPT-5.5 alone (B3 §3.3, B2 dump §4). Exactly one model flags → `disputed`; only one model answered (error/timeout) and it flags → `confirmed` (fail-safe) `[default chosen]`. This "both models" rule supersedes the plan's "any hazard flag from either reader → review" (see `internal/build/layout-decisions-ws-08.md`).
   - `review_hints` (never `confirmed`): `pushmatic` (not on Base's list), `westinghouse`, `federal_pioneer` text, `rust == "heavy"` from both models, `damage_or_scorching` from both.
   - Text cross-check: `HAZARD_TEXT` and `BRANDS` regexes from `score_panel.py` over `manufacturer`, `product_line`, `text_seen[]` → `hazard_evidence: printed | style_only` in `raw`.
   - Measured cost: $0.0137 / $0.0067 per panel photo (PR #17 set / extra set) vs $0.0294 / $0.0241 for GPT-5.5 alone; escalation on 26/67 and 14/60 photos.
10. **Panel merge across images (`aggregate.merge_panels`).** Images `panel_open`, `panel_close`, `main_disconnect`. Hazard: `confirmed` on any image wins. Brand: printed-evidence brands must agree across images, else `brand: null` and `warnings += ["brand_conflict"]`. Amps: all non-null `main_breaker_amps` must agree, else null with the list in `raw`. `panel_count = {enclosures_max, mains_max, agree}`, `agree` = mini and GPT-5.5 counts equal on the image that gave the max; a hint for WS-09's `multiple_mains` only, untested, never a fact here `[default chosen]`.
11. **Facts (`facts.to_criteria`).** Coverage `status`: `observed` if ≥ 1 image where the deciding readers report `legibility != "poor"`, `partial` if only one reader does, `unobserved` otherwise; every item must pass `server.eval.sceneio.verdict_contract_errors` inside a verdict (`pass` needs `observed` and `views_median ≥ rule.min_views = 1`).
   - `meter_number`: `auto_fill` or confirmed → `pass`, `clean: true`. `prefill` / `choose` / `single_reader` → `unsure`, `unsure_cause: unknown_attribute`, `clean: false`, `value` = best effort (criteria spec P4: an unclean suggestion is never a trusted id), plus `views_needed {kind: confirm_attribute, target: "confirm the meter number", why: "readers disagree or are not barcode-confirmed"}`. `unreadable` → `unsure/unobserved`, `value: null`, `views_needed {kind: close_up, target: "meter label close-up", why: "meter label not legible"}`. No meter still → no item.
   - `cl320`: both agree → `pass`, `clean: true`, `value: cl320_meter | not_cl320_meter`, `raw` = label as printed (contract §4: a clean feature fact is `pass` with its value; eligibility effect is WS-09's). Disagree or one legible → `unsure/unknown_attribute`, `value: cl320_unclear`. None legible → `unsure/unobserved`.
   - `panel_recall`: `hazard confirmed` → `unsure`, `rule_requires_review`, `clean: true`, `value: "hazard:<line>"`. No hazard and a printed brand → `unsure/rule_requires_review` while `panel_autopass` is off, `value: "none_identified"`. With `HSS_READS_PANEL_AUTOPASS=on` (the reconciler's photo-fact pass rule, **awaiting Base confirmation**; WS-09 brief §4 step 8): `pass` only when GPT-5.5 has read the image (force the escalation), both mini and GPT-5.5 agree on a `printed_text` brand, neither returns a `hazard_line`, and the brand is not in `BASE_RECALL`; any disagreement or single flag stays `unsure/rule_requires_review`. The proposal names GPT-5.5 + Grok 4.7 as the pair; panels keep B3's measured mini + GPT-5.5 tier (0 false alarms) until Base says otherwise. Brand missing or unreadable → `unsure/unknown_attribute`, `clean: false`. `disputed` or any `review_hints` → `unsure/rule_requires_review`. No panel stills → no item. `raw` = JSON string of `{hazard_line_gpt, hazard_line_mini, hazard_evidence, brand, brand_evidence, main_breaker_amps, panel_rating_amps, text_seen, images, review_hints}`.
12. **Confirm (`policy.confirm`).** For `field_id = meter_number`: `choice` must equal one candidate `value` (`kind: confirmed`), or `typed` is given (`kind: corrected`), or both are null (`kind: none`, "none of these"). Sets `meter.status = auto_fill`, `confidence 1.0`, `raw: "confirmed:<by>"` (or `unreadable` on "none"), rebuilds `facts`; a second answer for the same `still_id` and field is rejected (`409 confirmation_conflict` at the route) unless `by == "reviewer"`. `panel_brand` / `main_breaker_amps` set the merged panel value with `raw: "confirmed:<by>"` and rebuild `panel_recall` the same way `[default chosen]`.
13. **Early-worker timing and the `reads` stage.** Grok 4.7 (median 41 s, p90 78 s per photo) is the latency floor of the whole VLM line (B3 §4), so reads start at commit, before finalize, in the device upload order `meter_close, meter_oblique, panel_*` (hosted-architecture §2.1). Per house 45–80 s wall time with every call in parallel; target `read_still` p50 ≤ 60 s, p95 ≤ 130 s for `meter_close`. The `reads` stage (`server.reads.run`, right after `validate`, parallel with `poses` and `objects`, SD-08) runs `build_reads`: it re-runs any read-purpose still listed in `packet.json` without an early result (blocking, `still_deadline_s` each; cache hits otherwise), writes `runs/{runId}/reads/reads.json` and `early/meter_identity.json`; `scale` and `criteria` depend on it, geometry never does. `reads_status().meter` feeds `CaptureCheckData.meterRead` (`ok` = an `auto_fill | prefill | choose` result exists; `absent` = no meter still yet).
14. **Disable switch.** `Settings.VISION_EXTERNAL_CALLS=off`: no network call, zxing still runs, every reader answer is `error: disabled`, `reads.json.status = "disabled"`, facts `unsure/not_evaluated`, no hints. Verified with the network unplugged (§8 item 6).
15. **Budget and failures.** Stop new calls when the capture's summed `cost_usd` exceeds `budget_usd_per_house` (`warnings += ["budget_exhausted"]`, remaining images `not_evaluated`). Both providers down for 10 min → `provider_down`; WS-03 routes to manual review. Amendment stills (`retake_meter_closeups`) re-run and replace an `unreadable` result; a legible earlier result is kept unless the new one is `auto_fill` `[default chosen]`.

## 5. Reuse

- Port unchanged: `internal/breadth/b2/vlm.py` (`METER_PROMPT`, `PANEL_PROMPT`, `encode` → `image.py`, `parse`, the OpenAI/xAI call shape; drop the Anthropic branch and the pooled key names), `engines.py::barcodes` (zxing only; drop docTR, PaddleOCR, Tesseract: server OCR top-1 59–68 %, no-go), `vendor/meter_eval/match.py` (`normalize`, `core`, `normalize_class`, `digest` with `METER_HMAC_KEY` for label scoring), `vendor/meter_eval/locate.py` (`payload_tokens`, `SPEC`, `KEYWORD`, the `length_ok` rule; the OCR line ranking is not needed), `vendor/meter_eval/stats.py` (`wilson`, `share`), `score_meter.py` (`class_number`, `vlm_view`, `evaluate`, `summarize` → `scripts/reads_gate.py`), `score_panel.py` (`BRANDS`, `HAZARD_LINES`, `HAZARD_TEXT`, `AMPS`, `brand_of`, `as_int`, `vlm_claims` → `panel_policy.py` and the gate), `internal/breadth/b3-cheap-gate/tiers.py` (`verify_risky_amps_legible`, `PRICES`, `cost` → `panel_policy.py`, `client.cost_usd`), `internal/breadth/a9-no-tape/meter_model.py::PROMPT`.
- Drop: `analyze_meter.py` (its Opus agreement rows; keep the rule "accept when top-1 cores agree" as `policy.py`), `b3-cheap-gate/vlm_ids.py`, `crop.py`, `reading_gate.py` (checklist purposes replace the gate), `score_can.py` (can type and rust from close-ups are no-go, B2 §3.3), `merge_raw.py` (store-sync artefact).
- Licences: zxing-cpp Apache-2.0; OpenAI and xAI API terms (photos leave the device: Base's decision, §12 Q10). No Anthropic SDK anywhere in the package.

## 6. Fixtures and data

| Item | Path | Size | Exercises |
|---|---|---|---|
| Meter photos (80 usable of 83, Commons CC BY / BY-SA / PD) | fetched to `$B2_DATA/meter/images/m*.jpg` via `b2/fetch.py meter` | ~150 MB, not committed | pair agreement, barcode path (20 photos decode), class labels (22 US CL) |
| Panel photos: PR #17 set 67 (CC), extra set 61 (mostly NC/ND, internal evaluation only) | `$B2_DATA/{panel,extra}/images/` via `b2/fetch.py panel|extra` | ~200 MB, not committed | cheap tier + escalation, 5 hazard panels (p07, x0007, x0012, x0013, x0678) |
| Meter labels (73, plaintext, **excluded from repo**) | `internal/breadth/b2/labels/meter_numbers_hand.csv`, read at run time via `HSS_READS_LABELS_DIR` | 8 KB | gate scoring; repo gets `data/fixtures/reads/meter_labels.hmac.csv` (HMAC digests via `match.digest`, key in `METER_HMAC_KEY`, never the plaintext) |
| Panel labels (no meter numbers) | copy `labels/panel_hand.csv`, `labels/panel_extra_hand.csv` → `data/fixtures/reads/` | 8 KB | brand, amps, hazard truth |
| Stored reader answers | `internal/breadth/b2/raw/{meter,panel,extra}/vlm_{gpt-5.5,grok-4.7,gpt-5.4-mini}.jsonl` (meter files hold numbers: seed a local cache only) | 3 MB | offline replay with `scripts/reads_seed_cache.py` → `/tmp/reads-cache` |
| Reads demo packet | `scripts/reads_fixture_packet.py --meter m01 --oblique m03 --panel p02 --disconnect p12 --out data/fixtures/reads/packet-reads-demo/` builds a v0.4 `photo_only` packet with stills `s1..s4` (purposes `meter_close, meter_oblique, panel_open, main_disconnect`) from the fetched photos | images not committed; `packet.json` committed with sha256s | early-worker E2E through WS-03/WS-11 replay with real plates; the golden ETH3D packets have `stills: null` |
| Shared stills fixture (SD-29) | `data/fixtures/packets/mini-arkit/packet.json` (WS-00, synthetic: 4 keyframes, stills `s1` `meter_close`, `s2` `meter_oblique`, meter tap) | ≈ 60 KB, committed | the commit-hook → `read_still` → `reads` stage path in CI with `VISION_EXTERNAL_CALLS=off` (`status: disabled`) or with the seeded cache; synthetic plates read as `unreadable` live |
| Synthetic unit-test answers | `server/reads/tests/data/*.json`, `"meter_number": "000000000"`-style values only | < 50 KB | every policy branch |

## 7. Commands

```sh
export STORE=/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2 B2_DATA=/tmp/b2data
export HSS_READS_LABELS_DIR=$STORE/internal/breadth/b2/labels VISION_EXTERNAL_CALLS=on   # Python 3.12, `uv sync --frozen --extra reads` done
python $STORE/internal/breadth/b2/fetch.py meter --manifest $STORE/internal/breadth/b2/inputs/meter_manifest.csv
python $STORE/internal/breadth/b2/fetch.py panel --manifest $STORE/internal/breadth/b2/inputs/panel_screened.csv
python $STORE/internal/breadth/b2/fetch.py extra --manifest $STORE/internal/breadth/b2/inputs/panels_extra.csv
python -m pytest server/reads/tests -q                                   # CPU laptop, < 60 s, no network
python -m server.reads.worker --image $B2_DATA/meter/images/m01.jpg --purpose meter_close --out /tmp/r   # 45–80 s, live
python scripts/reads_seed_cache.py --raw $STORE/internal/breadth/b2/raw --images $B2_DATA --cache /tmp/reads-cache
HSS_READS_CACHE_DIR=/tmp/reads-cache python scripts/reads_gate.py --set meter --set panel --set extra --out /tmp/reads-gate   # offline, ~1 min
python scripts/reads_gate.py --live --set meter --set panel --set extra --workers 12 --out /tmp/reads-gate-live           # ~15 min, ≈ $12
python scripts/reads_fixture_packet.py --out data/fixtures/reads/packet-reads-demo/
python -m server.eval.cli validate /tmp/reads-gate/verdict.sample.json
```

`reads_gate.py` prints the B2 tables (`score_meter.summarize` columns, the agreement row, the barcode row, `score_panel` columns for the shipped policy) and writes `gate.json`. Everything runs on a CPU machine; Modal is not needed (early workers are CPU functions that WS-03/WS-12 wrap).

## 8. Done when

Offline replay (`--cache /tmp/reads-cache`, exact counts) and live (`--live`, tolerance ±2 photos) both satisfy, on the 73 labelled meter photos and 127 panel photos:

1. GPT-5.5 top-1 69/73, Grok 4.7 top-1 66/73, top-3 union ≥ 71/73 (B2 §3.1).
2. Pair agreement 68/73 (93 % coverage) with 65/68 correct (95.6 %, Wilson low 87.8 %); `auto_fill` = agreed picks with a barcode match ≥ 9 (expect 10) with 0 wrong; `prefill` = the other agreed picks; `choose` = the 5 disagreements (m30, m33, m57, m64, m77), every one routed to one-tap confirm; 0 wrong auto-fills in every run.
3. US CL labels 22/22, false CL320 claims 0/83.
4. Panels (`verify_risky_amps_legible` policy, `panel/v1`): hazard 5/5 with 0 false alarms on both sets; brand ≥ 16/19 and ≥ 11/13, brand wrong ≤ 2 and 0; main amps ≥ 7/9 and 3/3; panel rating 5/5 and 1/1; cost ≤ $0.016 and ≤ $0.008 per photo. If `panel/v1` misses any of these, ship `panel/v0` (drop the count fields) and say so in the PR.
5. `python -m server.eval.cli validate` passes on `verdict.sample.json` (a minimal verdict wrapping `to_criteria` output for `auto_fill`, `prefill`, `unreadable`, `hazard confirmed`, `none_identified`); `verdict_contract_errors` is empty; every non-barcode, unconfirmed meter read is `unsure`.
6. Unit tests ≥ 30 and green: every `decide_meter` branch, `implausible_length`, oblique corroboration, `merge_panels` conflicts, one test per escalation trigger, both-flag hazard rule, fail-safe single-reader hazard, `confirm` twice, cache hit (0 cost), `VISION_EXTERNAL_CALLS=off` opens no socket (monkeypatch `socket.socket` to raise), budget stop, idempotent `read_still`.
7. Live timing: GPT-5.5 median ≤ 12 s, Grok 4.7 median ≤ 60 s and p90 ≤ 120 s per photo; `read_still` for `meter_close` ≤ 130 s p95; the demo packet through the replay client writes every `early/reads/<stillId>.json` before finalize and the `reads` stage writes `reads.json` from cache hits only (`cost_usd == 0` in the stage, `stage` event `reads done` before `scale`).
8. Leak check: `rg -n "[0-9]{6,}" server/reads scripts/reads_* data/fixtures/reads` returns only sha256 hex, timestamps and `000000000`-style test values; none of the §2 excluded files appears in the diff.

## 9. Validator checklist

File the report at `internal/build/validation/ws-08.md` with sections: commands run, gate table (offline and live) against §8, 5 hand-checked items, verdict.

- Run §7 in order on a clean checkout; paste `gate.json` numbers next to the §8 thresholds. Fail on any miss outside the ±2 live tolerance, any wrong `auto_fill`, any false hazard alarm, or any `pass` meter fact without a barcode match or confirmation.
- Inspect `reads.json` for the demo packet: `facts[]` ids exactly `meter_number`, `cl320`, `panel_recall`; every `fact.coverage[0].rule.min_views == 1`; `views_needed` entries have no `prompt` key; `meter_identity.images == ["s1"]`.
- Sample by hand: three `choose` cases (m30, m33, m57) hold the labelled core within their 3 candidates (compare `core`; never write the string into the report); p20 (a modern Eaton label printing "Challenger") has `hazard != confirmed`; p07 has `hazard == confirmed` with `hazard_evidence: printed`.
- Timeouts: one meter photo with `HSS_READS_GROK_TIMEOUT_S=1` → `single_reader`, `prefill`, GPT-5.5's top-3, no auto-fill.
- Privacy: build `/tmp/cores.txt` locally from the store labels, `rg -f /tmp/cores.txt` over the run logs and the PR text → 0 hits; delete the file.
- The repo diff contains none of the §2 excluded files and no `anthropic` import.

## 10. Constraints

- Vision models: GPT-5.5 and Grok 4.7 as the agreement pair, GPT-5.4-mini as the panel cheap tier; OpenAI and xAI endpoints only; no Anthropic (B2's Opus pair, 65/65, is the retired baseline and must not be reintroduced).
- Never commit real meter numbers (labels, raw answers, mosaics, manifests with file titles), Base SSR prompt text (`internal/criteria/SSR_Prompts.md`, including its barcode-wrapper example), API keys, or files over 5 MB; fixtures are fetched, not committed. Never print a meter number in logs, PR text or the validation report.
- Never send images below 2048 px long side, cropped or enhanced; never add a cheap first tier or a third voter for meter numbers; never auto-fill without a barcode match until §12 Q1 is answered.
- No customer tools (no tape) in any capture assumption; the only member actions are a retake (`meter_close`) or a tap.
- Cost ≈ $0.16 per house at list prices (2 meter photos × pair ≈ $0.088, 3 panel photos ≈ $0.031, make/model pair ≈ $0.045); hard stop `HSS_READS_BUDGET_USD_PER_HOUSE=0.40` inside the run-wide $1.50 cap (SD-38); no GPU and no Modal spend beyond the CPU early worker.
- Privacy: customer photos go to OpenAI and xAI only while `VISION_EXTERNAL_CALLS=on`; every read artifact stays under `captures/{id}/` (`early/reads/`, `runs/{runId}/reads/`); the cache key is a content hash and the cache entry holds no capture id.

## 11. Out of scope and handoffs

- Storage, signed URLs, promotion `uploads/` → `captures/`, the events table and the `POST /captures/{id}/confirmations` route (`ConfirmationRequest`/`ConfirmationResponse`, SD-21): WS-01; the spec PR adding the route to `docs/hosted-architecture.md` and the proposed `HintData.confirm` field: WS-14.
- Spawning `read_still` per `CommittedFile` (`dispatch_early`), the `on_confirmation` hook, publishing hints and `capture_check` (`meterRead`), scheduling the `reads` stage, retries and the 20 min hard stop: WS-03.
- Datasheet lookup, `meter_models.json`, contour fit and the `scale` block: WS-05 (consumes `early/meter_identity.json`).
- `multiple_mains`, `main_panel_type`, `oversized_can`, `meter_type`, `meter_mount`, P12 flags, site composition, and whether an unclean `meter_number` routes a house to review (`HSS_CRITERIA_METER_NUMBER_REQUIRED`): WS-09 (consumes `facts`, `views_needed`, `panel_count`).
- Customer copy for hints and `views_needed`, the reviewer confirm UI, the result payload: WS-10.
- Replay client, CI gate wiring, `make e2e`: WS-11. Modal function wrappers, secrets, cost logs: WS-12. CL320 and hazard-panel data collection, real-house captures: WS-13.

## 12. Open questions and defaults

1. Auto-fill on pair agreement without a barcode? Default **no** (`HSS_READS_AUTOFILL_NO_BARCODE=off`) until ≥ 150 labelled plates show a Wilson lower bound ≥ 95 % (about 75 more agreed photos with 0 errors, B2 §3.1); today 87.8 %.
2. How does the device answer a one-tap confirm? Resolved: `POST /captures/{id}/confirmations` (SD-21, WS-01) → `on_confirmation` → `policy.confirm(..., by="device")`; the reviewer form (WS-10) posts the same body. Still open for Base: roughly 80 % of houses carry an unclean `meter_number` until the tap lands (agreement without barcode), so Base decides whether that alone routes to review (`HSS_CRITERIA_METER_NUMBER_REQUIRED`, default on) or the verdict goes out with the criterion pending confirmation.
3. Hazard from exactly one model (`disputed`): review or ignore? Default: `rule_requires_review` via `review_hints`, both answers shown to the reviewer; `confirmed` needs both models.
4. Panel `pass` on a legible non-recalled brand? Default off (`HSS_READS_PANEL_AUTOPASS=off`): hazard recall is 5/5 on 5 panels only; the reconciler's photo-fact pass rule (§4 step 11) is the on-state, awaiting Base confirmation and the 30–50 panel hazard set (B2 §7.3).
5. CL320's role in eligibility and what a `cl320_meter` read triggers? Default: fact only plus `warnings: cl320_positive_unmeasured`; WS-09 composes (criteria spec #19); another CL rating is `not_cl320_meter`, a `pass` with its value, never a `fail` (contract §4 over the B2 handoff).
6. Read `meter_oblique` too? Default yes (`HSS_READS_READ_OBLIQUE=on`): +≈ $0.045 per house, no wall-time cost, a second legible image for `views_median 2`.
7. Grok 4.7 timeout 90 s (hosted-architecture §3) or 120 s (B2 dump §7, p90 78 s)? Default 120 s, 3 attempts, 300 s per image; WS-03 owns the hard stop.
8. Which identifier does Base want on multi-identifier plates (utility number, manufacturer serial, barcode string)? Default: the B2 prompt's rule (utility-facing number first); every remaining pair error is this choice, so the confirm UI always shows the alternatives.
9. Env-var convention? Resolved (SD-11): `HSS_READS_*` for every knob here, `VISION_EXTERNAL_CALLS` as a WS-00 `Settings` field.
10. May customer photos go to OpenAI and xAI (gate decision §10, Base 8)? Default yes for the demo; production ships with `VISION_EXTERNAL_CALLS` set by Base.
