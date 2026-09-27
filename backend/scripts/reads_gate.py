"""Offline replay of the cached meter and panel answers. Counts only: no cores, no payloads.

`--live` stays wired and does not call a provider. SD-62: the live gate and live timing
are not merge blockers while this Mac has no keys. The post-merge smoke runs on Modal
`dev` with the `hss-dev-vlm` secret once WS-12 deploys.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from server.reads.candidates import class_number, core, wilson  # noqa: E402
from server.reads.client import cost_usd  # noqa: E402
from server.reads.config import ReadsConfig  # noqa: E402
from server.reads.facts import coverage_contract_errors, to_criteria  # noqa: E402
from server.reads.panel_policy import b3_hazard, decide_panel, needs_escalation  # noqa: E402
from server.reads.policy import decide_meter  # noqa: E402
from server.reads.types import PanelBundle, PanelRead, ReaderAnswer, Reads  # noqa: E402

DIGIT_RUN = re.compile(r"[0-9]{6,}")
CHOOSE_IDS = ["m30", "m33", "m57", "m64", "m77"]
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


def _checks(meter: dict, panel: dict, extra: dict, sample: dict) -> dict[str, bool]:
    return {
        "gpt_top1": meter["gpt_top1"] == 69 and meter["labelled"] == 73,
        "grok_top1": meter["grok_top1"] == 66,
        "top3_union": meter["top3_union"] >= 71,
        "agree": meter["agree"] == 68 and meter["agree_correct"] == 65,
        "auto_fill": meter["auto_fill"] >= 9 and meter["auto_fill_wrong"] == 0,
        "choose": meter["choose_ids"] == CHOOSE_IDS and meter["choose_holds_label"] == len(CHOOSE_IDS),
        "us_cl": meter["us_cl_ok"] == 22 and meter["us_cl_n"] == 22,
        "false_cl320": meter["false_cl320_pair"] == 0 and meter["false_cl320_either_reader"] == 0,
        "panel_hazard": panel["hazard_ok"] == panel["hazard_n"] == 1 and panel["false_alarms"] == 0,
        "extra_hazard": extra["hazard_ok"] == extra["hazard_n"] == 4 and extra["false_alarms"] == 0,
        "hazard_recall": panel["routed_or_confirmed"] + extra["routed_or_confirmed"] == 5,
        "confirmed_recall": panel["confirmed_base_recall"] + extra["confirmed_base_recall"] == 4,
        "panel_brand": panel["brand_ok"] >= 16 and panel["brand_n"] == 19 and panel["brand_wrong"] <= 2,
        "extra_brand": extra["brand_ok"] >= 11 and extra["brand_n"] == 13 and extra["brand_wrong"] == 0,
        "panel_main": panel["main_ok"] >= 7 and panel["main_n"] == 9,
        "extra_main": extra["main_ok"] == extra["main_n"] == 3,
        "panel_rating": panel["rating_ok"] == panel["rating_n"] == 5,
        "extra_rating": extra["rating_ok"] == extra["rating_n"] == 1,
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", action="append", dest="sets", default=[])
    parser.add_argument("--out", default="/tmp/reads-gate")
    parser.add_argument("--raw", default="")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        print(
            "live gate deferred (SD-62): not a merge blocker while no keys are on this Mac. "
            "Post-merge smoke on Modal dev with the hss-dev-vlm secret once WS-12 deploys."
        )
        return 2
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
    gate_path = out / "gate.json"
    verdict_path = out / "verdict.sample.json"
    gate_path.write_text(json.dumps(report, indent=1) + "\n")
    verdict_path.write_text(json.dumps(sample["verdict"], indent=1) + "\n")
    for path in (gate_path, verdict_path):
        scrubbed = path.read_text().replace("000000001", "").replace("000000002", "").replace("000000000", "")
        if DIGIT_RUN.search(scrubbed):
            path.unlink()
            raise SystemExit(f"{path.name} contained a long digit run and was not kept")
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
