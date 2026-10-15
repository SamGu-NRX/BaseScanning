"""Tiered-VLM policies for meter and panel reading, scored against B2's labels.

    python tiers.py [--meter-raw DIR] [--panel-raw DIR] [--extra-raw DIR] [--tag full|crop]

Reads per-photo VLM answers (B2's jsonl format) and simulates each cascade policy: which models
are called, the final answer, cost and latency. Prints markdown tables and writes
results/tiers_<tag>.json. Meter numbers are only compared, never printed or written: per-image
output holds booleans only.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
B2 = HERE.parent / "b2"
sys.path.insert(0, str(B2))
sys.path.insert(0, str(B2 / "vendor"))
from meter_eval.match import core  # noqa: E402
from meter_eval.stats import wilson  # noqa: E402
from score_meter import class_number, normalize_class  # noqa: E402
from score_panel import HAZARD_LINES, as_int, brand_of  # noqa: E402

# USD per million tokens (input, output), list prices as used in B1/B2.
PRICES = {
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4-mini": (0.75, 4.5),
    "grok-4.20-0309-non-reasoning": (1.25, 2.5),
    "grok-4.7": (2.0, 6.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
SHORT = {"m": "gpt-5.4-mini", "g4": "grok-4.20-0309-non-reasoning", "G": "gpt-5.5", "O": "claude-opus-5-5", "X": "grok-4.7"}


def load(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if "error" not in r and r.get("answer") is not None:
                    out[r["id"]] = r
    return out


def cost(record: dict, model: str) -> float:
    pin, pout = PRICES[model]
    u = record.get("usage") or {}
    return u.get("in", 0) / 1e6 * pin + u.get("out", 0) / 1e6 * pout


def meter_ranked(record: dict | None) -> list[str]:
    if not record:
        return []
    a = record["answer"]
    listed = [a.get("meter_number")] + list(a.get("number_candidates") or [])
    return list(dict.fromkeys(core(str(x)) for x in listed if x and core(str(x))))


def top1(record):
    r = meter_ranked(record)
    return r[0] if r else None


def meter_policies():
    """Each policy: fn(answers by short name) -> (final ranked list, autofill bool, class text, calls, stages)."""

    def cls_of(a):
        return a["G"]["answer"].get("class_label")

    def agree(*picks):
        return bool(picks[0]) and all(p == picks[0] for p in picks)

    def pair(first):
        """GPT-5.5 + second reader in parallel; auto-fill only when their top-1 cores agree."""
        def fn(a):
            return meter_ranked(a["G"]), agree(top1(a["G"]), top1(a[first])), cls_of(a), ["G", first], [["G", first]]
        return fn

    def triple(a):
        """GPT-5.5 + Grok 4.7 + mini in parallel; auto-fill only when all three agree."""
        ok = agree(top1(a["G"]), top1(a["X"]), top1(a["m"]))
        return meter_ranked(a["G"]), ok, cls_of(a), ["G", "X", "m"], [["G", "X", "m"]]

    def gx_top2_consistent(a):
        """GPT-5.5 + Grok 4.7 agree on top-1 and neither lists a second identifier (no which-identifier doubt)."""
        g, x = meter_ranked(a["G"]), meter_ranked(a["X"])
        ok = agree(top1(a["G"]), top1(a["X"])) and len(g) == 1 and len(x) == 1
        return g, ok, cls_of(a), ["G", "X"], [["G", "X"]]

    def cheap_pair_then_GX(a):
        """mini + Grok 4.20 first; auto-fill needs GPT-5.5 + Grok 4.7 anyway, so escalate always unless both cheap readers are null."""
        m, g4 = top1(a["m"]), top1(a["g4"])
        if not m and not g4:
            return [], False, a["m"]["answer"].get("class_label"), ["m", "g4"], [["m", "g4"]]
        g, x = top1(a["G"]), top1(a["X"])
        return meter_ranked(a["G"]), agree(g, x), cls_of(a), ["m", "g4", "G", "X"], [["m", "g4"], ["G", "X"]]

    def mini_G_then_X(a):
        """mini + GPT-5.5 in parallel; auto-fill needs Grok 4.7 to agree too, asked only when mini and GPT-5.5 agree."""
        g, m = top1(a["G"]), top1(a["m"])
        if agree(g, m):
            return meter_ranked(a["G"]), agree(g, top1(a["X"])), cls_of(a), ["m", "G", "X"], [["m", "G"], ["X"]]
        return meter_ranked(a["G"]), False, cls_of(a), ["m", "G"], [["m", "G"]]

    def cheap_pair_only(a):
        """Reference only: mini + Grok 4.20 agreement as the auto-fill rule (no flagship)."""
        return meter_ranked(a["m"]), agree(top1(a["m"]), top1(a["g4"])), a["m"]["answer"].get("class_label"), ["m", "g4"], [["m", "g4"]]

    def gx_not_ambiguous(a):
        """GPT-5.5 + Grok 4.7 agree and neither flags another identifier as a plausible meter number."""
        amb = any((a[k]["answer"] or {}).get("ambiguous") is True for k in ("G", "X"))
        return meter_ranked(a["G"]), agree(top1(a["G"]), top1(a["X"])) and not amb, cls_of(a), ["G", "X"], [["G", "X"]]

    return {
        "old baseline GPT-5.5 + Opus (B2; Anthropic now excluded)": pair("O"),
        "GPT-5.5 + Grok 4.7 agree, neither flags ambiguous": gx_not_ambiguous,
        "new baseline GPT-5.5 + Grok 4.7": pair("X"),
        "GPT-5.5 alone": lambda a: (meter_ranked(a["G"]), False, cls_of(a), ["G"], [["G"]]),
        "GPT-5.5 + Grok 4.7 + mini, all three agree": triple,
        "GPT-5.5 + Grok 4.7 agree, single identifier listed by both": gx_top2_consistent,
        "GPT-5.5 + mini agree (ref)": pair("m"),
        "GPT-5.5 + Grok 4.20 agree (ref)": pair("g4"),
        "mini + GPT-5.5, Grok 4.7 only when they agree": mini_G_then_X,
        "mini + Grok 4.20 agree, no flagship (ref)": cheap_pair_only,
    }


def score_meters(raw: dict[str, dict], labels: dict, manifest: dict) -> list[dict]:
    ids = [i for i in manifest if manifest[i]["usable"] == "yes" and all(i in raw[k] for k in ("m", "g4", "G"))]
    out = []
    for name, fn in meter_policies().items():
        need = [k for k, w in (("O", "Opus"), ("X", "Grok 4.7")) if w in name]
        pool = [i for i in ids if all(i in raw[k] for k in need)]
        n = t1 = t3 = auto = auto_ok = cl_us = cl_us_n = cl_all = cl_all_n = 0
        c = lat = 0.0
        esc = 0
        for i in pool:
            a = {k: raw[k].get(i) for k in raw}
            ranked, autofill, cls, calls, stages = fn(a)
            c += sum(cost(a[k], SHORT[k]) for k in calls)
            lat += sum(max(a[k]["secs"] for k in st) for st in stages)
            esc += len(stages) > 1
            lab = labels.get(i)
            if lab:
                n += 1
                t1 += bool(ranked) and ranked[0] == lab["core"]
                t3 += lab["core"] in ranked[:3]
                if autofill:
                    auto += 1
                    auto_ok += ranked[0] == lab["core"]
            m = manifest[i]
            if m["class_agreed"] == "yes" and m["class_label"]:
                if m["class_kind"] == "ansi_class":
                    cl_us_n += 1
                    cl_us += class_number(cls) == class_number(m["class_label"])
                cl_all_n += 1
                cl_all += bool(cls) and normalize_class(m["class_label"]) in normalize_class(str(cls))
        k = len(pool)
        if not k:
            continue
        low = wilson(auto_ok, auto)[0] if auto else 0.0
        out.append({"policy": name, "photos": k, "labelled": n, "top1": t1, "top3": t3, "auto": auto, "auto_ok": auto_ok,
                    "auto_cov": f"{auto / n:.0%}" if n else "-", "auto_prec_wilson_low": f"{low:.1%}",
                    "cl_us": f"{cl_us}/{cl_us_n}", "cl_all": f"{cl_all}/{cl_all_n}", "escalated": esc,
                    "usd_per_photo": c / k, "secs_per_photo": lat / k})
    return out


def panel_claims(record):
    a = (record or {}).get("answer") or {}
    return {
        "brand": brand_of(" ".join(str(a.get(k) or "") for k in ("manufacturer", "product_line")).strip()),
        "main": as_int(a.get("main_breaker_amps")),
        "rating": as_int(a.get("panel_rating_amps")),
        "hazard": a.get("hazard_line") in HAZARD_LINES,
        "evidence": a.get("brand_evidence"),
        "legibility": a.get("legibility"),
    }


def panel_policies():
    def base(model):
        return lambda a: (panel_claims(a[model]), [model], [[model]])

    def claim_escalate(a):
        """Mini first; any claim (brand, amps, hazard) is re-read by GPT-5.5, whose answer is final."""
        m = panel_claims(a["m"])
        if m["brand"] or m["main"] or m["rating"] or m["hazard"]:
            return panel_claims(a["G"]), ["m", "G"], [["m"], ["G"]]
        return m, ["m"], [["m"]]

    def verify_risky(a):
        """Mini first; hazard claims and brands not backed by printed text go to GPT-5.5; amps kept."""
        m = panel_claims(a["m"])
        if m["hazard"] or (m["brand"] and m["evidence"] != "printed_text"):
            g = panel_claims(a["G"])
            m = dict(m, brand=g["brand"], hazard=g["hazard"])
            return m, ["m", "G"], [["m"], ["G"]]
        return m, ["m"], [["m"]]

    def verify_risky_amps(a):
        """As verify_risky, and amp claims also go to GPT-5.5 (mini invents amps)."""
        m = panel_claims(a["m"])
        if m["hazard"] or (m["brand"] and m["evidence"] != "printed_text") or m["main"] or m["rating"]:
            g = panel_claims(a["G"])
            return dict(m, brand=g["brand"], hazard=g["hazard"], main=g["main"], rating=g["rating"]), ["m", "G"], [["m"], ["G"]]
        return m, ["m"], [["m"]]

    def legible_escalate(a):
        """Mini first; escalate unless mini says legibility poor and claims nothing."""
        m = panel_claims(a["m"])
        if m["legibility"] == "poor" and not (m["brand"] or m["main"] or m["rating"] or m["hazard"]):
            return m, ["m"], [["m"]]
        return panel_claims(a["G"]), ["m", "G"], [["m"], ["G"]]

    def claim_escalate_merge(a):
        """Mini first; any claim goes to GPT-5.5; GPT-5.5's non-null fields override mini's, hazard from GPT-5.5."""
        m = panel_claims(a["m"])
        if m["brand"] or m["main"] or m["rating"] or m["hazard"]:
            g = panel_claims(a["G"])
            merged = {k: (g[k] if g[k] else m[k]) for k in ("brand", "main", "rating")}
            return dict(m, **merged, hazard=g["hazard"]), ["m", "G"], [["m"], ["G"]]
        return m, ["m"], [["m"]]

    def verify_risky_amps_legible(a):
        """verify hazard + unprinted brand + amps, and also escalate when mini rates the text legible but reads no amps."""
        m = panel_claims(a["m"])
        if (m["hazard"] or (m["brand"] and m["evidence"] != "printed_text") or m["main"] or m["rating"]
                or (m["legibility"] == "good" and not (m["main"] or m["rating"]))):
            g = panel_claims(a["G"])
            return dict(m, brand=g["brand"] or m["brand"], hazard=g["hazard"], main=g["main"], rating=g["rating"]), ["m", "G"], [["m"], ["G"]]
        return m, ["m"], [["m"]]

    return {
        "baseline GPT-5.5": base("G"),
        "mini, escalate any claim, merge": claim_escalate_merge,
        "mini, verify hazard/brand/amps + legible-no-amps": verify_risky_amps_legible,
        "mini alone": base("m"),
        "mini, escalate any claim to GPT-5.5": claim_escalate,
        "mini, verify hazard + unprinted brand with GPT-5.5": verify_risky,
        "mini, verify hazard + unprinted brand + amps with GPT-5.5": verify_risky_amps,
        "mini, escalate unless poor & no claim": legible_escalate,
    }


def score_panels(raw: dict[str, dict], labels: dict, set_name: str) -> list[dict]:
    ids = sorted(i for i in raw["m"] if i in raw["G"] and labels.get(i, {}).get("hazard") != "duplicate")
    legible = sum(1 for i, lab in labels.items() if lab["brand"] and i in ids)
    hazards = sum(1 for i, lab in labels.items() if lab["hazard"] == "yes" and i in ids)
    out = []
    for name, fn in panel_policies().items():
        s = dict(b_ok=0, b_bad=0, b_uns=0, m_ok=0, r_ok=0, amp_bad=0, hz_ok=0, hz_false=0, esc=0)
        c = lat = 0.0
        for i in ids:
            a = {k: raw[k].get(i) for k in raw}
            cl, calls, stages = fn(a)
            c += sum(cost(a[k], SHORT[k]) for k in calls)
            lat += sum(max(a[k]["secs"] for k in st) for st in stages)
            s["esc"] += len(calls) > 1
            lab = labels.get(i)
            if lab and lab["brand"]:
                s["b_ok"] += cl["brand"] == lab["brand"]
                s["b_bad"] += bool(cl["brand"]) and cl["brand"] != lab["brand"]
            elif cl["brand"] and not (lab and lab["hazard"] == "unknown"):
                s["b_uns"] += 1
            truth = {as_int(lab[k]) for k in ("main_amps", "rating_amps")} - {None} if lab else set()
            if lab and lab["main_amps"] and cl["main"] == as_int(lab["main_amps"]):
                s["m_ok"] += 1
            if lab and lab["rating_amps"] and cl["rating"] == as_int(lab["rating_amps"]):
                s["r_ok"] += 1
            for k in ("main", "rating"):
                if cl[k] and (lab or set_name == "panel") and cl[k] not in truth:
                    s["amp_bad"] += 1
            hz = lab["hazard"] if lab else ("no" if set_name == "panel" else "unverified")
            s["hz_ok"] += hz == "yes" and cl["hazard"]
            s["hz_false"] += hz == "no" and cl["hazard"]
        k = len(ids)
        out.append(dict(policy=name, photos=k, brand=f"{s['b_ok']}/{legible}", brand_wrong=s["b_bad"], brand_unsupported=s["b_uns"],
                        main_ok=s["m_ok"], rating_ok=s["r_ok"], amp_wrong=s["amp_bad"], hazard=f"{s['hz_ok']}/{hazards}",
                        false_hazard=s["hz_false"], escalated=s["esc"], usd_per_photo=c / k, secs_per_photo=lat / k))
    return out


def table(rows):
    keys = list(rows[0])
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    for r in rows:
        lines.append("| " + " | ".join(f"{v:.4f}" if isinstance(v, float) else str(v) for v in r.values()) + " |")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--meter-raw", default=str(B2 / "raw/meter"))
    p.add_argument("--panel-raw", default=str(B2 / "raw/panel"))
    p.add_argument("--extra-raw", default=str(B2 / "raw/extra"))
    p.add_argument("--fallback-raw", default="", help="B2 raw root used for models missing from the crop dirs (Opus)")
    p.add_argument("--tag", default="full")
    args = p.parse_args()

    def raws(d, set_name):
        out = {}
        for k, model in SHORT.items():
            path = Path(d) / f"vlm_{model}.jsonl"
            if not path.exists() and args.fallback_raw:
                path = Path(args.fallback_raw) / set_name / f"vlm_{model}.jsonl"
            out[k] = load(path)
        return out

    with (B2 / "inputs/meter_manifest.csv").open() as h:
        manifest = {r["id"]: r for r in csv.DictReader(h)}
    with (B2 / "labels/meter_numbers_hand.csv").open() as h:
        labels = {r["id"]: r for r in csv.DictReader(h)}
    result = {"meter": score_meters(raws(args.meter_raw, "meter"), labels, manifest)}
    for set_name, d, lab_file in (("panel", args.panel_raw, "panel_hand.csv"), ("extra", args.extra_raw, "panel_extra_hand.csv")):
        with (B2 / "labels" / lab_file).open() as h:
            plabels = {r["id"]: r for r in csv.DictReader(h)}
        result[set_name] = score_panels(raws(d, set_name), plabels, set_name)
    for k, rows in result.items():
        print(f"\n### {k} ({args.tag})\n")
        print(table(rows))
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"tiers_{args.tag}.json").write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
