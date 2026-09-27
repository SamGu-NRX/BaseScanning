"""Offline replay of the cached meter and panel answers. Counts only: no cores, no payloads.

`--live` calls GPT-5.5, Grok 4.7, and GPT-5.4-mini when `OPENAI_API_KEY` and
`XAI_API_KEY` are both set. `--max-usd` (default 12) is a hard cap: each call
reserves the token-ceiling hold and settles to the measured `cost_usd` after it
returns. A hold that is not settled would stop the run early. The offline path
does not call a provider. `make reads-gate` stays offline.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from server.reads.barcode import read_barcodes, zxingcpp  # noqa: E402
from server.reads.candidates import class_number, core, wilson  # noqa: E402
from server.reads.client import call_reader, cost_usd  # noqa: E402
from server.reads.config import ReadsConfig  # noqa: E402
from server.reads.facts import coverage_contract_errors, to_criteria  # noqa: E402
from server.reads.image import open_rgb, prepare  # noqa: E402
from server.reads.panel_policy import b3_hazard, decide_panel, needs_escalation  # noqa: E402
from server.reads.policy import decide_meter  # noqa: E402
from server.reads.prompts import prompt_text  # noqa: E402
from server.reads.types import PanelBundle, PanelRead, ReaderAnswer, Reads  # noqa: E402

DIGIT_RUN = re.compile(r"[0-9]{6,}")
CHOOSE_IDS = ["m30", "m33", "m57", "m64", "m77"]
# Completion ceiling is `max_completion_tokens` in `complete_chat`. Input headroom
# covers the largest cached B2 prompt (about 4.1k). Settling to measured cost is
# what keeps a full replay under the default cap.
HOLD_INPUT_TOKENS = 4500
HOLD_OUTPUT_TOKENS = 8000
LIVE_TOLERANCE = 2
MAC_STORE = Path.home() / (
    "Library/Application Support/Cursor/AgentStores/cursor_agent_stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/files"
)
CLOUD_STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")


def _store() -> Path:
    named = os.environ.get("STORE") or os.environ.get("HSS_STORE")
    if named and (Path(named) / "internal/breadth/b2").exists():
        return Path(named)
    for candidate in (MAC_STORE, CLOUD_STORE):
        if (candidate / "internal/breadth/b2").exists():
            return candidate
    raise SystemExit("STORE is unset and the breadth cache was not found")


def _load_jsonl(path: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not path.exists():
        return found
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "error" in record:
            continue
        found[str(record["id"])] = record
    return found


def _csv_rows(path: Path) -> dict[str, dict]:
    with path.open(newline="") as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def _reader(record: dict | None, model: str, prompt_id: str) -> ReaderAnswer | None:
    if record is None or record.get("answer") is None:
        return None
    usage = record.get("usage") or {}
    return ReaderAnswer(
        model=model,
        prompt_id=prompt_id,
        answer=record.get("answer"),
        raw_text="",
        secs=float(record.get("secs") or 0),
        usage={"in": usage.get("in", 0), "out": usage.get("out", 0)},
        cost_usd=cost_usd(model, usage),
        cached=True,
    )


def _ranked(record: dict | None) -> list[str]:
    if not record or not record.get("answer"):
        return []
    answer = record["answer"]
    listed = [answer.get("meter_number")] + list(answer.get("number_candidates") or [])
    found: list[str] = []
    for item in listed:
        if not item:
            continue
        token = core(str(item))
        if token and token not in found:
            found.append(token)
    return found


def _barcodes(paddle: dict[str, dict], photo_id: str) -> list[dict]:
    record = paddle.get(photo_id) or {}
    return [item for item in (record.get("barcodes") or []) if isinstance(item, dict) and item.get("payload")]


def _meter(raw: Path, labels_dir: Path) -> dict:
    manifest = _csv_rows(raw.parent / "inputs" / "meter_manifest.csv")
    labels = _csv_rows(labels_dir / "meter_numbers_hand.csv")
    gpt = _load_jsonl(raw / "meter" / "vlm_gpt-5.5.jsonl")
    grok = _load_jsonl(raw / "meter" / "vlm_grok-4.7.jsonl")
    paddle = _load_jsonl(raw / "meter" / "paddle.jsonl")
    cfg = ReadsConfig()
    labelled = [photo_id for photo_id in labels if photo_id in gpt and photo_id in grok]
    gpt_top1 = grok_top1 = top3 = agree = agree_ok = 0
    auto_fill = auto_wrong = prefill = 0
    choose: list[str] = []
    choose_holds = 0
    for photo_id in labelled:
        label_core = labels[photo_id]["core"]
        gpt_cores = _ranked(gpt[photo_id])
        grok_cores = _ranked(grok[photo_id])
        gpt_top1 += bool(gpt_cores) and gpt_cores[0] == label_core
        grok_top1 += bool(grok_cores) and grok_cores[0] == label_core
        top3 += label_core in set(gpt_cores[:3] + grok_cores[:3])
        same = bool(gpt_cores and grok_cores and gpt_cores[0] == grok_cores[0])
        agree += same
        agree_ok += same and gpt_cores[0] == label_core
        decision = decide_meter(
            _reader(gpt[photo_id], "gpt-5.5", "meter/v0"),
            _reader(grok[photo_id], "grok-4.7", "meter/v0"),
            _barcodes(paddle, photo_id),
            cfg,
        )
        if decision.status == "auto_fill":
            auto_fill += 1
            auto_wrong += decision.core != label_core
        elif decision.status == "prefill":
            prefill += 1
        elif decision.status == "choose":
            choose.append(photo_id)
            choose_holds += label_core in {item.core for item in decision.candidates}
    us_ok = us_n = false_pair = false_either = photos = 0
    for photo_id, row in manifest.items():
        if photo_id not in gpt or photo_id not in grok:
            continue
        photos += 1
        gpt_cls = class_number((gpt[photo_id].get("answer") or {}).get("class_label"))
        grok_cls = class_number((grok[photo_id].get("answer") or {}).get("class_label"))
        false_either += gpt_cls == "CL320" or grok_cls == "CL320"
        decision = decide_meter(
            _reader(gpt[photo_id], "gpt-5.5", "meter/v0"),
            _reader(grok[photo_id], "grok-4.7", "meter/v0"),
            _barcodes(paddle, photo_id),
            cfg,
        )
        false_pair += decision.cl320 == "cl320_meter"
        if row.get("usable") == "yes" and row.get("class_kind") == "ansi_class" and row.get("class_agreed") == "yes":
            us_n += 1
            target = class_number(row.get("class_label"))
            us_ok += gpt_cls == target and grok_cls == target
    low, _high = wilson(agree_ok, agree)
    return {
        "labelled": len(labelled),
        "gpt_top1": gpt_top1,
        "grok_top1": grok_top1,
        "top3_union": top3,
        "agree": agree,
        "agree_correct": agree_ok,
        "agree_wilson_low": round(low, 4),
        "auto_fill": auto_fill,
        "auto_fill_wrong": auto_wrong,
        "prefill": prefill,
        "choose_ids": sorted(choose),
        "choose_holds_label": choose_holds,
        "us_cl_ok": us_ok,
        "us_cl_n": us_n,
        "false_cl320_pair": false_pair,
        "false_cl320_either_reader": false_either,
        "photos": photos,
        "paddle_with_barcodes": sum(1 for record in paddle.values() if record.get("barcodes")),
    }


def _panel(raw: Path, labels_path: Path, set_name: str) -> dict:
    labels = _csv_rows(labels_path)
    mini = _load_jsonl(raw / set_name / "vlm_gpt-5.4-mini.jsonl")
    gpt = _load_jsonl(raw / set_name / "vlm_gpt-5.5.jsonl")
    photo_ids = sorted(
        photo_id for photo_id in mini if photo_id in gpt and labels.get(photo_id, {}).get("hazard") != "duplicate"
    )
    brand_ok = brand_bad = brand_n = 0
    main_ok = main_n = rating_ok = rating_n = 0
    hazard_ok = hazard_n = false_alarm = confirmed_ok = routed_ok = 0
    escalated = 0
    spent = 0.0
    for photo_id in photo_ids:
        mini_answer = mini[photo_id].get("answer") or {}
        gpt_answer = gpt[photo_id].get("answer") or {}
        escalate = needs_escalation(mini_answer)
        escalated += escalate
        spent += cost_usd("gpt-5.4-mini", mini[photo_id].get("usage") or {})
        if escalate:
            spent += cost_usd("gpt-5.5", gpt[photo_id].get("usage") or {})
        mini_reader = _reader(mini[photo_id], "gpt-5.4-mini", "panel/v0")
        gpt_reader = _reader(gpt[photo_id], "gpt-5.5", "panel/v0") if escalate else None
        panel = decide_panel(mini_reader, gpt_reader)
        both = decide_panel(mini_reader, _reader(gpt[photo_id], "gpt-5.5", "panel/v0"))
        bit = b3_hazard(mini_answer, gpt_answer if escalate else None, escalate)
        lab = labels.get(photo_id)
        if lab and lab.get("brand"):
            brand_n += 1
            brand_ok += panel.brand == lab["brand"]
            brand_bad += bool(panel.brand) and panel.brand != lab["brand"]
        if lab and lab.get("main_amps"):
            main_n += 1
            main_ok += panel.main_breaker_amps == _int(lab["main_amps"])
        if lab and lab.get("rating_amps"):
            rating_n += 1
            rating_ok += panel.panel_rating_amps == _int(lab["rating_amps"])
        truth = lab["hazard"] if lab else ("no" if set_name == "panel" else "unverified")
        if truth == "yes":
            hazard_n += 1
            hazard_ok += bool(bit)
            confirmed_ok += both.hazard == "confirmed"
            routed_ok += both.hazard == "confirmed" or "pushmatic" in both.review_hints
        if truth == "no" and bit:
            false_alarm += 1
    photos = len(photo_ids)
    return {
        "photos": photos,
        "brand_ok": brand_ok,
        "brand_n": brand_n,
        "brand_wrong": brand_bad,
        "main_ok": main_ok,
        "main_n": main_n,
        "rating_ok": rating_ok,
        "rating_n": rating_n,
        "hazard_ok": hazard_ok,
        "hazard_n": hazard_n,
        "false_alarms": false_alarm,
        "confirmed_base_recall": confirmed_ok,
        "routed_or_confirmed": routed_ok,
        "escalated": escalated,
        "usd_per_photo": round(spent / photos, 4) if photos else 0.0,
    }


def _int(value: str) -> int | None:
    try:
        return int(str(value).strip().rstrip("Aa"))
    except ValueError:
        return None


def _sample_verdict(schema_path: Path) -> dict:
    cfg = ReadsConfig()
    number = "000000001"
    auto = Reads(
        capture_id="cap_sample",
        meter=_meter_read(number, number, [{"payload": number}], cfg, images=["s1"]),
    )
    prefill = Reads(
        capture_id="cap_sample",
        meter=_meter_read(number, number, [], cfg, images=["s1"]),
    )
    unread = Reads(
        capture_id="cap_sample",
        meter=_meter_read(None, None, [], cfg, images=["s1"]),
    )
    hazard = Reads(
        capture_id="cap_sample",
        panel=PanelBundle(
            merged=PanelRead(hazard="confirmed", hazard_line="challenger", brand="challenger", legibility="good"),
            per_image={"s3": PanelRead(hazard="confirmed")},
        ),
    )
    none = Reads(
        capture_id="cap_sample",
        panel=PanelBundle(
            merged=PanelRead(hazard="none", brand="eaton", brand_evidence="printed_text", legibility="good"),
            per_image={"s3": PanelRead(brand="eaton")},
        ),
    )
    criteria: list[dict] = []
    views: list[dict] = []
    for reads in (auto, prefill, unread, hazard, none):
        items, needed = to_criteria(reads, cfg)
        criteria.extend(items)
        views.extend(needed)
    verdict = {
        "format": "base-verdict/v0",
        "verdict_id": "v_reads_sample",
        "scene": {"scene_id": "sc_sample", "site": "sample"},
        "evaluator": {"path": "reads", "version": "reads/v0"},
        "site": {"decision": "manual_review", "reasons": []},
        "criteria": criteria,
        "views_needed": views,
    }
    schema = json.loads(schema_path.read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(verdict), key=lambda item: list(item.path))
    return {
        "verdict": verdict,
        "schema_errors": [error.message for error in errors],
        "contract_errors": coverage_contract_errors(verdict),
    }


def _meter_read(
    gpt_number: str | None, grok_number: str | None, barcodes: list[dict], cfg: ReadsConfig, images: list[str]
):
    meter = decide_meter(
        ReaderAnswer(model="gpt-5.5", prompt_id="meter/v0", answer={"meter_number": gpt_number, "legibility": "good"}),
        ReaderAnswer(
            model="grok-4.7", prompt_id="meter/v0", answer={"meter_number": grok_number, "legibility": "good"}
        ),
        barcodes,
        cfg,
    )
    meter.images = images
    return meter


def _eq(observed: int, expected: int, tol: int) -> bool:
    return abs(int(observed) - expected) <= tol


def _floor(observed: int, floor: int, tol: int) -> bool:
    return int(observed) >= floor - tol


def _checks(meter: dict, panel: dict, extra: dict, sample: dict, *, tol: int = 0) -> dict[str, bool]:
    choose_ids = meter["choose_ids"]
    choose_ok = choose_ids == CHOOSE_IDS and meter["choose_holds_label"] == len(CHOOSE_IDS)
    if tol:
        choose_ok = _eq(len(choose_ids), len(CHOOSE_IDS), tol) and meter["choose_holds_label"] >= max(
            0, len(choose_ids) - tol
        )
    return {
        "gpt_top1": _eq(meter["gpt_top1"], 69, tol) and _eq(meter["labelled"], 73, tol),
        "grok_top1": _eq(meter["grok_top1"], 66, tol),
        "top3_union": _floor(meter["top3_union"], 71, tol),
        "agree": _eq(meter["agree"], 68, tol) and _eq(meter["agree_correct"], 65, tol),
        "auto_fill": _floor(meter["auto_fill"], 9, tol) and meter["auto_fill_wrong"] == 0,
        "choose": choose_ok,
        "us_cl": _eq(meter["us_cl_ok"], 22, tol) and _eq(meter["us_cl_n"], 22, tol),
        "false_cl320": meter["false_cl320_pair"] == 0 and meter["false_cl320_either_reader"] == 0,
        "panel_hazard": _eq(panel["hazard_ok"], 1, tol)
        and _eq(panel["hazard_n"], 1, tol)
        and panel["false_alarms"] == 0,
        "extra_hazard": _eq(extra["hazard_ok"], 4, tol)
        and _eq(extra["hazard_n"], 4, tol)
        and extra["false_alarms"] == 0,
        "hazard_recall": _eq(panel["routed_or_confirmed"] + extra["routed_or_confirmed"], 5, tol),
        "confirmed_recall": _eq(panel["confirmed_base_recall"] + extra["confirmed_base_recall"], 4, tol),
        "panel_brand": _floor(panel["brand_ok"], 16, tol)
        and _eq(panel["brand_n"], 19, tol)
        and panel["brand_wrong"] <= 2 + tol,
        "extra_brand": _floor(extra["brand_ok"], 11, tol)
        and _eq(extra["brand_n"], 13, tol)
        and extra["brand_wrong"] <= tol,
        "panel_main": _floor(panel["main_ok"], 7, tol) and _eq(panel["main_n"], 9, tol),
        "extra_main": _eq(extra["main_ok"], 3, tol) and _eq(extra["main_n"], 3, tol),
        "panel_rating": _eq(panel["rating_ok"], 5, tol) and _eq(panel["rating_n"], 5, tol),
        "extra_rating": _eq(extra["rating_ok"], 1, tol) and _eq(extra["rating_n"], 1, tol),
        "panel_cost": panel["usd_per_photo"] <= 0.016,
        "extra_cost": extra["usd_per_photo"] <= 0.008,
        "verdict": not sample["schema_errors"] and not sample["contract_errors"],
    }


def _table(meter: dict, panel: dict, extra: dict) -> str:
    rows = [
        ("GPT-5.5 top-1", f"{meter['gpt_top1']}/{meter['labelled']}", "69/73"),
        ("Grok 4.7 top-1", f"{meter['grok_top1']}/{meter['labelled']}", "66/73"),
        ("top-3 union", f"{meter['top3_union']}/{meter['labelled']}", ">=71/73"),
        ("pair agree", f"{meter['agree']}/{meter['labelled']}", "68/73"),
        ("agree correct", f"{meter['agree_correct']}/{meter['agree']}", "65/68"),
        ("wilson low", f"{meter['agree_wilson_low']:.3f}", "0.878"),
        ("auto_fill", str(meter["auto_fill"]), ">=9"),
        ("auto_fill wrong", str(meter["auto_fill_wrong"]), "0"),
        ("choose ids", " ".join(meter["choose_ids"]), "m30 m33 m57 m64 m77"),
        ("US CL", f"{meter['us_cl_ok']}/{meter['us_cl_n']}", "22/22"),
        ("false CL320", f"{meter['false_cl320_pair']}/{meter['photos']}", "0/83"),
        ("panel hazard", f"{panel['hazard_ok']}/{panel['hazard_n']}", "1/1"),
        ("panel false alarms", str(panel["false_alarms"]), "0"),
        ("extra hazard", f"{extra['hazard_ok']}/{extra['hazard_n']}", "4/4"),
        ("extra false alarms", str(extra["false_alarms"]), "0"),
        ("panel brand", f"{panel['brand_ok']}/{panel['brand_n']}", ">=16/19"),
        ("panel brand wrong", str(panel["brand_wrong"]), "<=2"),
        ("extra brand", f"{extra['brand_ok']}/{extra['brand_n']}", ">=11/13"),
        ("extra brand wrong", str(extra["brand_wrong"]), "0"),
        ("panel main", f"{panel['main_ok']}/{panel['main_n']}", ">=7/9"),
        ("extra main", f"{extra['main_ok']}/{extra['main_n']}", "3/3"),
        ("panel rating", f"{panel['rating_ok']}/{panel['rating_n']}", "5/5"),
        ("extra rating", f"{extra['rating_ok']}/{extra['rating_n']}", "1/1"),
        ("panel usd/photo", f"{panel['usd_per_photo']:.4f}", "<=0.016"),
        ("extra usd/photo", f"{extra['usd_per_photo']:.4f}", "<=0.008"),
        (
            "hazard recall",
            f"{panel['routed_or_confirmed'] + extra['routed_or_confirmed']}/5",
            "5/5 routed-or-confirmed",
        ),
        (
            "confirmed",
            f"{panel['confirmed_base_recall'] + extra['confirmed_base_recall']}/5",
            "4/5",
        ),
    ]
    lines = ["| check | observed | threshold |", "|---|---|---|"]
    lines.extend(f"| {name} | {observed} | {threshold} |" for name, observed, threshold in rows)
    return "\n".join(lines)


def hold_usd(model: str) -> float:
    """Reserve input headroom plus the completion-token ceiling. Settle after the call."""
    return cost_usd(model, {"in": HOLD_INPUT_TOKENS, "out": HOLD_OUTPUT_TOKENS})


class LiveBudget:
    """Thread-safe cap. `reserve` fails closed; `settle` replaces the hold with measured cost."""

    def __init__(self, cap: float) -> None:
        self.cap = cap
        self.spent = 0.0
        self.held = 0.0
        self.skipped = 0
        self._lock = threading.Lock()

    def reserve(self, hold: float) -> bool:
        with self._lock:
            if self.spent + self.held + hold > self.cap + 1e-9:
                self.skipped += 1
                return False
            self.held += hold
            return True

    def settle(self, hold: float, cost: float) -> None:
        with self._lock:
            self.held -= hold
            self.spent += max(0.0, cost)


def _csv_ids(path: Path) -> list[str]:
    """First CSV field only. The rest of a meter label row is never stored."""
    ids: list[str] = []
    with path.open(newline="") as handle:
        header = handle.readline()
        if not header.startswith("id,"):
            raise SystemExit(f"{path.name} has no id column")
        for line in handle:
            comma = line.find(",")
            photo_id = (line[:comma] if comma >= 0 else line).strip()
            if photo_id:
                ids.append(photo_id)
    return ids


def _jsonl_ids(path: Path) -> list[str]:
    """Photo ids from cached jsonl. The rest of each line is not stored."""
    ids: list[str] = []
    if not path.exists():
        return ids
    for line in path.read_text().splitlines():
        match = re.search(r'"id"\s*:\s*"([^"]+)"', line)
        if match:
            ids.append(match.group(1))
    return ids


def _duplicate_ids(path: Path) -> set[str]:
    with path.open(newline="") as handle:
        return {row["id"] for row in csv.DictReader(handle) if row.get("hazard") == "duplicate"}


def _image_path(b2: Path, set_name: str, photo_id: str) -> Path:
    return b2 / set_name / "images" / f"{photo_id}.jpg"


def _empty_record(photo_id: str, model: str, *, skipped: bool) -> dict:
    return {
        "id": photo_id,
        "engine": model,
        "secs": 0.0,
        "usage": {"in": 0, "out": 0},
        "answer": None,
        "raw_text": "",
        "skipped": skipped,
    }


def _live_record(
    budget: LiveBudget,
    cfg: ReadsConfig,
    model: str,
    prompt_id: str,
    image: Path,
    photo_id: str,
) -> dict:
    hold = hold_usd(model)
    if not budget.reserve(hold):
        return _empty_record(photo_id, model, skipped=True)
    cost = 0.0
    wall = 0.0
    answer = None
    usage = {"in": 0, "out": 0}
    try:
        data = image.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        _jpeg, data_url, _width, _height = prepare(data, cfg.max_side_px)
        started = time.perf_counter()
        result = call_reader(model, prompt_id, prompt_text(prompt_id), data_url, digest, cfg)
        wall = time.perf_counter() - started
        cost = float(result.cost_usd)
        answer = result.answer
        usage = {
            "in": int((result.usage or {}).get("in", 0) or 0),
            "out": int((result.usage or {}).get("out", 0) or 0),
        }
    except Exception as exc:
        print(f"live call failed {photo_id} {model} {type(exc).__name__}", file=sys.stderr)
    finally:
        budget.settle(hold, cost)
    return {
        "id": photo_id,
        "engine": model,
        "secs": round(wall, 3),
        "usage": usage,
        "answer": answer,
        "raw_text": "",
        "skipped": False,
    }


def _barcode_record(image: Path, photo_id: str) -> dict:
    barcodes: list[dict] = []
    try:
        decoded = open_rgb(image.read_bytes())
        try:
            barcodes = read_barcodes(decoded)
        finally:
            decoded.close()
    except Exception as exc:
        print(f"live barcode failed {photo_id} {type(exc).__name__}", file=sys.stderr)
    return {"id": photo_id, "engine": "zxing", "barcodes": barcodes}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kept: list[str] = []
    for row in sorted(rows, key=lambda item: str(item["id"])):
        if "barcodes" in row:
            body = {"id": row["id"], "engine": row["engine"], "barcodes": row["barcodes"]}
        else:
            body = {
                "id": row["id"],
                "engine": row["engine"],
                "secs": row.get("secs") or 0,
                "usage": row.get("usage") or {"in": 0, "out": 0},
                "answer": row.get("answer"),
                "raw_text": "",
            }
        kept.append(json.dumps(body, separators=(",", ":")))
    path.write_text(("\n".join(kept) + "\n") if kept else "")


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))
    return round(ordered[index], 3)


def _timing_ok(live: dict) -> bool:
    gpt = live.get("gpt_median_s")
    grok = live.get("grok_median_s")
    grok_p90 = live.get("grok_p90_s")
    pair = live.get("meter_pair_p95_s")
    if gpt is None or grok is None or grok_p90 is None or pair is None:
        return False
    return gpt <= 12 and grok <= 60 and grok_p90 <= 120 and pair <= 130


def _missing_keys() -> list[str]:
    return [name for name in ("OPENAI_API_KEY", "XAI_API_KEY") if not os.environ.get(name)]


def _live_ids(store: Path, labels: Path, wanted: set[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    raw = store / "internal/breadth/b2/raw"
    if "meter" in wanted:
        manifest = store / "internal/breadth/b2/inputs/meter_manifest.csv"
        label_path = labels / "meter_numbers_hand.csv"
        if not manifest.is_file() or not label_path.is_file():
            raise SystemExit("meter manifest or labels are missing; no provider call was made")
        ids = _csv_ids(manifest)
        seen = set(ids)
        for photo_id in _csv_ids(label_path):
            if photo_id not in seen:
                ids.append(photo_id)
                seen.add(photo_id)
        found["meter"] = ids
    for set_name, labels_name in (("panel", "panel_hand.csv"), ("extra", "panel_extra_hand.csv")):
        if set_name not in wanted:
            continue
        source = raw / set_name / "vlm_gpt-5.4-mini.jsonl"
        if not source.is_file():
            raise SystemExit(f"{set_name} cached ids are missing; no provider call was made")
        duplicates = _duplicate_ids(ROOT / "data/fixtures/reads" / labels_name)
        found[set_name] = [photo_id for photo_id in _jsonl_ids(source) if photo_id not in duplicates]
    return found


def _require_images(b2: Path, ids: dict[str, list[str]]) -> int | None:
    for set_name, photo_ids in ids.items():
        missing = sum(1 for photo_id in photo_ids if not _image_path(b2, set_name, photo_id).is_file())
        if missing:
            print(f"live gate missing {missing} {set_name} images under {b2}; no provider call was made")
            return 2
        if not photo_ids:
            print(f"live gate has no {set_name} ids; no provider call was made")
            return 2
    return None


def _run_pool(workers: int, jobs: list) -> list:
    if not jobs:
        return []
    found: list = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(job) for job in jobs]
        for future in as_completed(futures):
            found.append(future.result())
    return found


def _collect_live(args: argparse.Namespace, cfg: ReadsConfig, ids: dict[str, list[str]], b2: Path) -> dict:
    budget = LiveBudget(args.max_usd)
    meter_gpt: list[dict] = []
    meter_grok: list[dict] = []
    meter_bars: list[dict] = []
    panel_mini: dict[str, list[dict]] = {"panel": [], "extra": []}
    jobs = []
    if "meter" in ids:
        for photo_id in ids["meter"]:
            image = _image_path(b2, "meter", photo_id)
            jobs.append(lambda image=image, photo_id=photo_id: ("bar", _barcode_record(image, photo_id)))
            jobs.append(
                lambda image=image, photo_id=photo_id: (
                    "gpt-5.5",
                    _live_record(budget, cfg, "gpt-5.5", "meter/v0", image, photo_id),
                )
            )
            jobs.append(
                lambda image=image, photo_id=photo_id: (
                    "grok-4.7",
                    _live_record(budget, cfg, "grok-4.7", "meter/v0", image, photo_id),
                )
            )
    for set_name in ("panel", "extra"):
        for photo_id in ids.get(set_name, []):
            image = _image_path(b2, set_name, photo_id)
            jobs.append(
                lambda image=image, photo_id=photo_id, set_name=set_name: (
                    set_name,
                    _live_record(budget, cfg, "gpt-5.4-mini", "panel/v0", image, photo_id),
                )
            )
    for kind, record in _run_pool(args.workers, jobs):
        if kind == "bar":
            meter_bars.append(record)
        elif kind == "gpt-5.5":
            meter_gpt.append(record)
        elif kind == "grok-4.7":
            meter_grok.append(record)
        else:
            panel_mini[kind].append(record)
    panel_gpt: dict[str, list[dict]] = {"panel": [], "extra": []}
    escalate_jobs = []
    for set_name in ("panel", "extra"):
        for record in panel_mini[set_name]:
            answer = record.get("answer")
            image = _image_path(b2, set_name, record["id"])
            if record.get("skipped") or not needs_escalation(answer):
                panel_gpt[set_name].append(_empty_record(record["id"], "gpt-5.5", skipped=False))
                continue
            escalate_jobs.append(
                lambda image=image, photo_id=record["id"], set_name=set_name: (
                    set_name,
                    _live_record(budget, cfg, "gpt-5.5", "panel/v0", image, photo_id),
                )
            )
    for set_name, record in _run_pool(args.workers, escalate_jobs):
        panel_gpt[set_name].append(record)
    return {
        "budget": budget,
        "meter_gpt": meter_gpt,
        "meter_grok": meter_grok,
        "meter_bars": meter_bars,
        "panel_mini": panel_mini,
        "panel_gpt": panel_gpt,
    }


def _live_summary(collected: dict) -> dict:
    gpt_secs = [row["secs"] for row in collected["meter_gpt"] if not row.get("skipped")]
    for rows in collected["panel_gpt"].values():
        gpt_secs.extend(row["secs"] for row in rows if row.get("answer") is not None and not row.get("skipped"))
    grok_secs = [row["secs"] for row in collected["meter_grok"] if not row.get("skipped")]
    by_id_gpt = {row["id"]: row["secs"] for row in collected["meter_gpt"]}
    by_id_grok = {row["id"]: row["secs"] for row in collected["meter_grok"]}
    pair = [max(by_id_gpt[photo_id], by_id_grok[photo_id]) for photo_id in by_id_gpt if photo_id in by_id_grok]
    budget: LiveBudget = collected["budget"]
    return {
        "ran": True,
        "max_usd": budget.cap,
        "spent_usd": round(budget.spent, 4),
        "skipped": budget.skipped,
        "gpt_median_s": _percentile(gpt_secs, 50),
        "grok_median_s": _percentile(grok_secs, 50),
        "grok_p90_s": _percentile(grok_secs, 90),
        "meter_pair_p95_s": _percentile(pair, 95),
    }


def _link_inputs(out: Path, store: Path) -> Path:
    raw = out / "raw"
    if raw.is_symlink():
        raw.unlink()
    elif raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)
    link = out / "inputs"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(store / "internal/breadth/b2/inputs")
    return raw


def _authorize_live(cfg: ReadsConfig) -> ReadsConfig | None:
    """`--live` proceeds when the switch is unset or exactly ``on`` (SD-67, SD-77).

    ``yes``, ``true``, ``1``, and ``off`` refuse. Unset still proceeds: ``--live``
    is the authorized run, and the process default stays off everywhere else.
    """
    raw = os.environ.get("VISION_EXTERNAL_CALLS")
    if raw is not None and raw.strip().lower() != "on":
        return None
    return replace(cfg, external_vision=True)


def run_live(args: argparse.Namespace) -> int:
    missing = _missing_keys()
    if missing:
        print("live gate needs OPENAI_API_KEY and XAI_API_KEY; no provider call was made")
        return 2
    cfg = _authorize_live(ReadsConfig.from_env())
    if cfg is None:
        print("VISION_EXTERNAL_CALLS is off; live gate makes no calls")
        return 2
    if args.max_usd <= 0:
        print("live gate max-usd must be positive; no provider call was made")
        return 2
    store = _store()
    labels = Path(os.environ.get("HSS_READS_LABELS_DIR") or store / "internal/breadth/b2/labels")
    wanted = set(args.sets or ["meter", "panel", "extra"])
    if "meter" in wanted and zxingcpp is None:
        print("zxingcpp is not installed; live gate makes no calls")
        return 2
    ids = _live_ids(store, labels, wanted)
    b2 = Path(os.environ.get("B2_DATA") or "/tmp/b2data")
    refused = _require_images(b2, ids)
    if refused:
        return refused
    collected = _collect_live(args, cfg, ids, b2)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = _link_inputs(out, store)
    if collected["meter_gpt"]:
        _write_jsonl(raw / "meter" / "vlm_gpt-5.5.jsonl", collected["meter_gpt"])
        _write_jsonl(raw / "meter" / "vlm_grok-4.7.jsonl", collected["meter_grok"])
        _write_jsonl(raw / "meter" / "paddle.jsonl", collected["meter_bars"])
    for set_name in ("panel", "extra"):
        if collected["panel_mini"][set_name]:
            _write_jsonl(raw / set_name / "vlm_gpt-5.4-mini.jsonl", collected["panel_mini"][set_name])
            _write_jsonl(raw / set_name / "vlm_gpt-5.5.jsonl", collected["panel_gpt"][set_name])
    meter = _meter(raw, labels) if "meter" in wanted else {}
    panel = _panel(raw, ROOT / "data/fixtures/reads/panel_hand.csv", "panel") if "panel" in wanted else {}
    extra = _panel(raw, ROOT / "data/fixtures/reads/panel_extra_hand.csv", "extra") if "extra" in wanted else {}
    sample = _sample_verdict(ROOT / "server/contracts/schemas/verdict.v0.schema.json")
    checks = _checks(meter, panel, extra, sample, tol=LIVE_TOLERANCE) if meter and panel and extra else {}
    summary = _live_summary(collected)
    summary["workers"] = max(1, args.workers)
    passed = bool(checks) and all(checks.values()) and summary["skipped"] == 0 and _timing_ok(summary)
    report = {
        "prompt_id": "panel/v0",
        "live": summary,
        "meter": meter,
        "panel": panel,
        "extra": extra,
        "schema_errors": len(sample["schema_errors"]),
        "contract_errors": sample["contract_errors"],
        "checks": checks,
        "pass": passed,
    }
    _write_report(out, report, sample["verdict"])
    if meter and panel and extra:
        print(_table(meter, panel, extra))
    print(
        "live",
        f"spent_usd={summary['spent_usd']}",
        f"max_usd={summary['max_usd']}",
        f"skipped={summary['skipped']}",
        f"gpt_median_s={summary['gpt_median_s']}",
        f"grok_median_s={summary['grok_median_s']}",
        f"grok_p90_s={summary['grok_p90_s']}",
        f"meter_pair_p95_s={summary['meter_pair_p95_s']}",
    )
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


def _write_report(out: Path, report: dict, verdict: dict) -> None:
    gate_path = out / "gate.json"
    verdict_path = out / "verdict.sample.json"
    gate_path.write_text(json.dumps(report, indent=1) + "\n")
    verdict_path.write_text(json.dumps(verdict, indent=1) + "\n")
    for path in (gate_path, verdict_path):
        scrubbed = path.read_text().replace("000000001", "").replace("000000002", "").replace("000000000", "")
        if DIGIT_RUN.search(scrubbed):
            path.unlink()
            raise SystemExit(f"{path.name} contained a long digit run and was not kept")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline replay, or --live provider calls. A --live run writes real meter "
            "numbers into --out. Never commit or upload that folder."
        )
    )
    parser.add_argument("--set", action="append", dest="sets", default=[])
    parser.add_argument(
        "--out",
        default="/tmp/reads-gate",
        help=(
            "Report directory (default /tmp/reads-gate). --live writes barcode payloads "
            "and reader answers here. Never commit or upload this folder."
        ),
    )
    parser.add_argument("--raw", default="")
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Call providers. Writes real meter numbers under --out; that folder must never be committed or uploaded."
        ),
    )
    parser.add_argument("--max-usd", dest="max_usd", type=float, default=12.0)
    parser.add_argument("--workers", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.live:
        return run_live(args)
    store = _store()
    raw = Path(args.raw) if args.raw else store / "internal/breadth/b2/raw"
    labels = Path(os.environ.get("HSS_READS_LABELS_DIR") or store / "internal/breadth/b2/labels")
    wanted = set(args.sets or ["meter", "panel", "extra"])
    meter = _meter(raw, labels) if "meter" in wanted else {}
    panel_labels = ROOT / "data/fixtures/reads/panel_hand.csv"
    extra_labels = ROOT / "data/fixtures/reads/panel_extra_hand.csv"
    panel = _panel(raw, panel_labels, "panel") if "panel" in wanted else {}
    extra = _panel(raw, extra_labels, "extra") if "extra" in wanted else {}
    schema = ROOT / "server/contracts/schemas/verdict.v0.schema.json"
    sample = _sample_verdict(schema)
    checks = _checks(meter, panel, extra, sample) if meter and panel and extra else {}
    report = {
        "prompt_id": "panel/v0",
        "live": "not_run",
        "meter": meter,
        "panel": panel,
        "extra": extra,
        "schema_errors": len(sample["schema_errors"]),
        "contract_errors": sample["contract_errors"],
        "checks": checks,
        "pass": bool(checks) and all(checks.values()),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    _write_report(out, report, sample["verdict"])
    table = _table(meter, panel, extra)
    print(table)
    print("schema_errors", len(sample["schema_errors"]))
    if sample["schema_errors"]:
        print("schema", "; ".join(sample["schema_errors"][:8]))
    print("contract_errors", sample["contract_errors"])
    print("PASS" if report["pass"] else "FAIL")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
