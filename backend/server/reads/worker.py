"""Early worker: one still in, a stored result and hints out. Idempotent on sha256."""

from __future__ import annotations

import argparse
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from server.contracts.api import HintData
from server.reads.barcode import read_barcodes
from server.reads.cache import (
    MemoryStorage,
    Storage,
    cache_key,
    get_json,
    identity_key,
    local_cache_path,
    put_json,
    read_cached,
    read_local_cache,
    still_key,
    write_cached,
    write_local_cache,
)
from server.reads.client import call_reader
from server.reads.config import ReadsConfig
from server.reads.hints import hints_for
from server.reads.image import open_rgb, prepare
from server.reads.panel_policy import decide_panel, should_escalate
from server.reads.policy import decide_meter
from server.reads.prompts import prompt_text
from server.reads.types import (
    MeterIdentity,
    MeterRead,
    PanelRead,
    ReaderAnswer,
    ReadsError,
    StillRef,
    StillResult,
)

READ_PURPOSES = {"meter_close", "meter_oblique", "panel_open", "panel_close", "main_disconnect"}
METER_PURPOSES = {"meter_close", "meter_oblique"}
PANEL_PURPOSES = {"panel_open", "panel_close", "main_disconnect"}


def _spent(storage: Storage, capture_id: str) -> float:
    prefix = f"captures/{capture_id}/early/reads/"
    total = 0.0
    for key in storage.list_prefix(prefix):
        if "/vlm-cache/" in key or not key.endswith(".json"):
            continue
        try:
            total += float(get_json(storage, key).get("cost_usd") or 0)
        except (KeyError, ValueError, TypeError):
            continue
    return total


def _cached_answer(
    storage: Storage,
    cfg: ReadsConfig,
    still: StillRef,
    model: str,
    prompt_id: str,
) -> ReaderAnswer | None:
    if not cfg.cache:
        return None
    key = cache_key(still.capture_id, still.sha256, model, prompt_id, cfg.max_side_px)
    found = read_cached(storage, key)
    if found is not None:
        return found
    if cfg.cache_dir:
        return read_local_cache(local_cache_path(cfg.cache_dir, still.sha256, model, prompt_id, cfg.max_side_px))
    return None


def _store_answer(storage: Storage, cfg: ReadsConfig, still: StillRef, answer: ReaderAnswer) -> None:
    if not cfg.cache or answer.cached:
        return
    key = cache_key(still.capture_id, still.sha256, answer.model, answer.prompt_id, cfg.max_side_px)
    write_cached(storage, key, answer)
    if cfg.cache_dir:
        write_local_cache(
            local_cache_path(cfg.cache_dir, still.sha256, answer.model, answer.prompt_id, cfg.max_side_px),
            answer,
        )


def _ask(
    storage: Storage,
    cfg: ReadsConfig,
    still: StillRef,
    model: str,
    prompt_id: str,
    data_url: str,
) -> ReaderAnswer:
    cached = _cached_answer(storage, cfg, still, model, prompt_id)
    if cached is not None:
        return cached
    answer = call_reader(
        model,
        prompt_id,
        prompt_text(prompt_id),
        data_url,
        still.sha256,
        cfg,
        capture_id=still.capture_id,
    )
    _store_answer(storage, cfg, still, answer)
    return answer


def _identity(answers: list[ReaderAnswer], image_id: str) -> MeterIdentity:
    wanted = [item for item in answers if item.prompt_id == "meter_model/v0" and item.answer]
    by_model = {item.model: item.answer or {} for item in wanted}
    gpt = by_model.get("gpt-5.5") or {}
    grok = by_model.get("grok-4.7") or {}
    agree = bool(gpt.get("model_family")) and gpt.get("model_family") == grok.get("model_family")
    printed = gpt.get("model_evidence") == "printed" or grok.get("model_evidence") == "printed"
    source = gpt or grok
    return MeterIdentity(
        manufacturer=source.get("manufacturer"),
        model_family=source.get("model_family"),
        model_evidence=source.get("model_evidence") if source.get("model_evidence") in {"printed", "visual"} else None,
        model_confidence=float(source.get("model_confidence") or 0),
        cover_material=source.get("cover_material"),
        ansi_socket_meter=source.get("ansi_socket_meter"),
        base_flange_visible=source.get("base_flange_visible"),
        socket_type=source.get("socket_type"),
        agree=agree and printed,
        images=[image_id],
    )


def _not_evaluated(model: str, prompt_id: str) -> ReaderAnswer:
    return ReaderAnswer(model=model, prompt_id=prompt_id, error="not_evaluated")


def read_still(still: StillRef, storage: Storage, cfg: ReadsConfig) -> tuple[StillResult, list[HintData]]:
    if still.purpose not in READ_PURPOSES:
        raise ReadsError("purpose_not_readable")
    if still.purpose == "meter_oblique" and not cfg.read_oblique:
        raise ReadsError("purpose_not_readable")
    existing_key = still_key(still.capture_id, still.still_id)
    if storage.exists(existing_key):
        prior = StillResult.model_validate(get_json(storage, existing_key))
        if prior.still.sha256 == still.sha256:
            return prior, hints_for(prior)
    started = time.time()
    raw = storage.get_bytes(still.path)
    _jpeg, data_url, _w, _h = prepare(raw, cfg.max_side_px)
    image = open_rgb(raw)
    over_budget = _spent(storage, still.capture_id) > cfg.budget_usd_per_house
    answers: list[ReaderAnswer] = []
    barcodes: list[dict] = []
    meter: MeterRead | None = None
    panel: PanelRead | None = None

    if still.purpose in METER_PURPOSES:
        jobs = [("gpt-5.5", "meter/v0"), ("grok-4.7", "meter/v0")]
        if still.purpose == "meter_close":
            jobs += [("gpt-5.5", "meter_model/v0"), ("grok-4.7", "meter_model/v0")]
        if over_budget or not cfg.external_vision:
            if not cfg.external_vision:
                answers = [
                    ReaderAnswer(model=model, prompt_id=prompt_id, error="disabled") for model, prompt_id in jobs
                ]
            else:
                answers = [_not_evaluated(model, prompt_id) for model, prompt_id in jobs]
            barcodes = read_barcodes(image)
        else:
            with ThreadPoolExecutor(max_workers=len(jobs) + 1) as pool:
                answer_futures = [
                    pool.submit(_ask, storage, cfg, still, model, prompt_id, data_url) for model, prompt_id in jobs
                ]
                barcode_future = pool.submit(read_barcodes, image)
                answers = [future.result() for future in answer_futures]
                barcodes = barcode_future.result()
        gpt = next(item for item in answers if item.model == "gpt-5.5" and item.prompt_id == "meter/v0")
        grok = next(item for item in answers if item.model == "grok-4.7" and item.prompt_id == "meter/v0")
        meter = decide_meter(gpt, grok, barcodes, cfg)
        meter.images = [still.still_id]
    else:
        prompt_id = cfg.panel_prompt_id
        if over_budget:
            answers = [_not_evaluated("gpt-5.4-mini", prompt_id)]
        elif not cfg.external_vision:
            answers = [ReaderAnswer(model="gpt-5.4-mini", prompt_id=prompt_id, error="disabled")]
        else:
            mini = _ask(storage, cfg, still, "gpt-5.4-mini", prompt_id, data_url)
            answers = [mini]
            if should_escalate(mini.answer, force=cfg.panel_autopass) and mini.error is None:
                if _spent(storage, still.capture_id) + mini.cost_usd > cfg.budget_usd_per_house:
                    answers.append(_not_evaluated("gpt-5.5", prompt_id))
                else:
                    answers.append(_ask(storage, cfg, still, "gpt-5.5", prompt_id, data_url))
        mini_answer = next(item for item in answers if item.model == "gpt-5.4-mini")
        gpt_answer = next((item for item in answers if item.model == "gpt-5.5"), None)
        panel = decide_panel(mini_answer, gpt_answer)

    result = StillResult(
        still=still,
        answers=answers,
        barcodes=barcodes,
        meter=meter,
        panel=panel,
        cost_usd=round(sum(item.cost_usd for item in answers), 6),
        secs=round(time.time() - started, 2),
    )
    if over_budget:
        result.cost_usd = 0.0
    put_json(storage, existing_key, result.model_dump(mode="json"))
    if still.purpose == "meter_close":
        _write_identity(storage, still.capture_id, _identity(answers, still.still_id))
    return result, hints_for(result)


def _identity_useful(identity: MeterIdentity) -> bool:
    return bool(identity.manufacturer or identity.model_family or identity.cover_material or identity.socket_type)


def _write_identity(storage: Storage, capture_id: str, identity: MeterIdentity) -> None:
    """SD-46 always leaves a blob. A later empty close must not erase a useful model read."""
    key = identity_key(capture_id)
    if not _identity_useful(identity) and storage.exists(key):
        prior = MeterIdentity.model_validate(get_json(storage, key))
        if _identity_useful(prior):
            return
    put_json(storage, key, identity.model_dump(mode="json"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--capture", default="cap_local")
    parser.add_argument("--still", default="s1")
    args = parser.parse_args()
    path = Path(args.image)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    storage = MemoryStorage()
    storage.put_bytes(f"uploads/{args.capture}/{path.name}", path.read_bytes(), "image/jpeg")
    still = StillRef(
        capture_id=args.capture,
        still_id=args.still,
        path=f"uploads/{args.capture}/{path.name}",
        sha256=digest,
        purpose=args.purpose,
    )
    result, hint_list = read_still(still, storage, ReadsConfig.from_env())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "still.json").write_text(result.model_dump_json(indent=1))
    (out / "hints.json").write_text("[" + ",".join(item.model_dump_json() for item in hint_list) + "]")
    print(result.meter.status if result.meter else result.panel.hazard if result.panel else "empty", result.cost_usd)


if __name__ == "__main__":
    main()
