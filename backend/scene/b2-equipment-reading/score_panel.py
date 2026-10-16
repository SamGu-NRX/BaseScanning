"""Score panel reading (brand, main / panel amperage, hazard line) on PR #17's 67 real photos.

    B2_RAW=/tmp/b2raw python score_panel.py [panel|extra]

panel: labels/panel_hand.csv (PR #17's legibility screen and notes, plus eye checks). Photos not
in that file have no legible maker, amperage or hazard line: any claim there is "unsupported"
(it may be a correct guess from breaker style, but has no printed evidence) and hazard claims
count as false alarms.
extra: labels/panel_extra_hand.csv (61 screened Openverse photos; only title- or text-confirmed
fields labelled). Claims on unlabelled photos are listed as "unverified" for an eye check.
OCR engines are scored with a plain keyword rule over their text (brand names, "<n> A" / "<n>
AMP" near MAIN, STAB-LOK / ZINSCO / SYLVANIA / MAGNETRIP / PUSHMATIC).
"""

import csv
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))
from meter_eval.stats import share  # noqa: E402

RAW = Path(os.environ.get("B2_RAW", HERE / "raw"))
LABELS = {"panel": "panel_hand.csv", "extra": "panel_extra_hand.csv"}
BRANDS = {
    "federal_pacific": r"federal\s*(?:pacific|pioneer)|\bfpe\b|stab-?lok",
    "zinsco": r"zinsco|sylvania|\bgte\b|magnetrip",
    "square_d": r"square\s*d|schneider|\bqo\b|homeline|multi-?breaker",
    "eaton": r"eaton|cutler|\bch\b",
    "ge": r"\bge\b|general\s*electric",
    "siemens": r"siemens|\bite\b|murray",
    "westinghouse": r"westinghouse",
    "crouse_hinds": r"crouse",
    "frank_adam": r"frank\s*adam",
    "indoasian": r"indo\s*asian",
    "pushmatic": r"pushmatic|bulldog",
    "challenger": r"challenger",
    "other": r".",
}
HAZARD_LINES = {"federal_pacific_stab_lok", "zinsco_sylvania_magnetrip", "challenger", "pushmatic"}
HAZARD_TEXT = re.compile(r"stab-?lok|federal\s*pacific|zinsco|sylvania|magnetrip|pushmatic|challenger", re.I)
AMPS = re.compile(r"\b(\d{2,3})\s*(?:a\b|amp)", re.I)


def brand_of(text: str | None) -> str | None:
    if not text:
        return None
    for key, pattern in BRANDS.items():
        if re.search(pattern, text, re.I):
            return key
    return None


def as_int(x) -> int | None:
    try:
        return int(str(x).strip().rstrip("Aa"))
    except (TypeError, ValueError):
        return None


def vlm_claims(a: dict) -> dict:
    return {
        "brand": brand_of(" ".join(str(a.get(k) or "") for k in ("manufacturer", "product_line")).strip()),
        "main": as_int(a.get("main_breaker_amps")),
        "rating": as_int(a.get("panel_rating_amps")),
        "hazard": a.get("hazard_line") in HAZARD_LINES,
    }


def ocr_claims(record: dict) -> dict:
    text = " ".join(line["text"] for line in record["lines"])
    brand = None
    for key, pattern in BRANDS.items():
        if key != "other" and re.search(pattern, text, re.I):
            brand = key
            break
    amps = [int(m.group(1)) for m in AMPS.finditer(text) if 15 <= int(m.group(1)) <= 400]
    return {"brand": brand, "main": amps[0] if amps else None, "rating": amps[0] if amps else None,
            "hazard": bool(HAZARD_TEXT.search(text))}


def main() -> None:
    set_name = sys.argv[1] if len(sys.argv) > 1 else "panel"
    with (HERE / "labels" / LABELS[set_name]).open() as h:
        labels = {r["id"]: r for r in csv.DictReader(h)}
    legible = sum(1 for lab in labels.values() if lab["brand"])
    hazards = sum(1 for lab in labels.values() if lab["hazard"] == "yes")
    mains = sum(1 for lab in labels.values() if lab["main_amps"])
    ratings = sum(1 for lab in labels.values() if lab["rating_amps"])
    print(f"| Engine | photos | brand right (of {legible} legible) | brand wrong | brand claimed, none legible |"
          f" main amps right (of {mains}) | panel rating right (of {ratings}) | wrong amp claims | hazard caught (of {hazards}) | false hazard alarms |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    engines = sorted((RAW / set_name).glob("*.jsonl"), key=lambda p: (not p.stem.startswith("vlm_"), p.stem))
    detail = {}
    for path in engines:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        results = {r["id"]: r for r in rows if "error" not in r}
        if not results:
            continue
        is_vlm = path.stem.startswith("vlm_")
        c = dict(n=0, b_ok=0, b_bad=0, b_uns=0, m_ok=0, r_ok=0, amp_bad=0, hz_ok=0, hz_false=0)
        misses = []
        for i, r in sorted(results.items()):
            if labels.get(i, {}).get("hazard") == "duplicate":
                continue
            claims = vlm_claims(r.get("answer") or {}) if is_vlm else ocr_claims(r)
            lab = labels.get(i)
            c["n"] += 1
            if lab and lab["brand"]:
                if claims["brand"] == lab["brand"]:
                    c["b_ok"] += 1
                elif claims["brand"]:
                    c["b_bad"] += 1
                    misses.append(f"{i}:brand={claims['brand']}")
            elif claims["brand"] and not (lab and lab["hazard"] == "unknown"):
                c["b_uns"] += 1
            truth_amps = {as_int(lab[k]) for k in ("main_amps", "rating_amps")} - {None} if lab else set()
            if lab and lab["main_amps"] and claims["main"] == as_int(lab["main_amps"]):
                c["m_ok"] += 1
            if lab and lab["rating_amps"] and claims["rating"] == as_int(lab["rating_amps"]):
                c["r_ok"] += 1
            for k in ("main", "rating"):
                if claims[k] and not lab and set_name == "extra":
                    misses.append(f"{i}:{k}={claims[k]}?")
                elif claims[k] and claims[k] not in truth_amps:
                    c["amp_bad"] += 1
                    misses.append(f"{i}:{k}={claims[k]}")
            hz = lab["hazard"] if lab else ("no" if set_name == "panel" else "unverified")
            if hz == "unverified" and claims["hazard"]:
                misses.append(f"{i}:hazard?")
            if hz == "yes" and claims["hazard"]:
                c["hz_ok"] += 1
            if hz == "no" and claims["hazard"]:
                c["hz_false"] += 1
                misses.append(f"{i}:hazard")
        name = path.stem.removeprefix("vlm_")
        print(f"| {name} | {c['n']} | {share(c['b_ok'], legible)} | {c['b_bad']} | {c['b_uns']} | {c['m_ok']}/{mains} |"
              f" {c['r_ok']}/{ratings} | {c['amp_bad']} | {c['hz_ok']}/{hazards} | {c['hz_false']} |")
        detail[name] = misses
    print()
    for name, misses in detail.items():
        print(f"- {name}: {' '.join(misses)}")


if __name__ == "__main__":
    main()
