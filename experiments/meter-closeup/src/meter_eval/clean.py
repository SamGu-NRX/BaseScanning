"""Q1: read every usable photo as downloaded, with three Vision configurations.

Writes results/clean_per_image.csv (booleans and check values, no recognized text) and
results/clean.md. Raw recognizer output, which contains meter numbers, stays in the data dir.
"""

import csv
import json

from PIL import Image

from meter_eval.locate import top_candidate
from meter_eval.match import class_read, number_boxes
from meter_eval.ocr import CONFIGS, PRIMARY, Reader
from meter_eval.paths import DATA_DIR, MANIFEST, RESULTS_DIR
from meter_eval.quality import device_checks, gray, region_checks, tallest_digit_line, union_box
from meter_eval.stats import share


def usable_rows() -> list[dict]:
    with MANIFEST.open() as handle:
        return [row for row in csv.DictReader(handle) if row["usable"] == "yes"]


def evaluate(row: dict, results: dict[str, dict]) -> dict:
    out = {
        "id": row["id"],
        "number_agreed": row["number_agreed"],
        "number_agreed_strict": row["number_agreed_strict"],
        "class_kind": row["class_kind"],
    }
    has_number = bool(row["number_hmac"])
    for config, result in results.items():
        lines = result["lines"]
        if has_number:
            length = int(row["number_len"])
            strict = number_boxes(lines, row["number_hmac"], length, lenient=False)
            lenient = number_boxes(lines, row["number_hmac_lenient"], length, lenient=True)
            out[f"number_{config}"] = int(strict is not None)
            out[f"number_lenient_{config}"] = int(lenient is not None)
            if config == PRIMARY and strict is not None:
                out["number_box"] = json.dumps([round(v, 5) for v in union_box(strict)])
                # Is the tallest line with 4+ digits, the phone's guess at the label, the number?
                tallest = tallest_digit_line(lines)
                out["tallest_is_number"] = int(tallest is not None and tallest["box"] in strict)
        if row["class_label"]:
            out[f"class_{config}"] = int(class_read(lines, row["class_label"]))
    return out


def run() -> list[dict]:
    raw_path = DATA_DIR / "ocr" / "clean.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with Reader() as reader, raw_path.open("w") as raw:
        for row in usable_rows():
            path = DATA_DIR / "images" / f"{row['id']}.jpg"
            # Barcodes feed the number-finding ranking that picks the phone-side box.
            results = {
                config: reader.read(path, config, barcodes=config == PRIMARY) for config in CONFIGS
            }
            for result in results.values():
                raw.write(json.dumps(result) + "\n")
            out = evaluate(row, results)
            with Image.open(path) as image:
                g = gray(image)
            guess = top_candidate(results[PRIMARY])
            checks = device_checks(g, guess and guess["box"])
            out |= {k: round(v, 4) for k, v in checks.items()}
            if "number_box" in out:
                located = region_checks(g, json.loads(out["number_box"]))
                out |= {f"label_{k}": round(v, 4) for k, v in located.items()}
            out["width"], out["height"] = g.shape[1], g.shape[0]
            rows.append(out)
            print(row["id"], out.get(f"number_{PRIMARY}", "-"), flush=True)
    return rows


def rate(rows: list[dict], key: str) -> str:
    values = [int(r[key]) for r in rows if r.get(key, "") != ""]
    return share(sum(values), len(values))


def table(rows: list[dict]) -> str:
    agreed = [r for r in rows if r["number_agreed"] == "yes"]
    strict = [r for r in rows if r["number_agreed_strict"] == "yes"]
    with_number = [r for r in rows if r["number_agreed"]]
    ansi = [r for r in rows if r["class_kind"] == "ansi_class"]
    rating = [r for r in rows if r["class_kind"] == "current_rating"]
    lines = [
        "| What was read | Photos | " + " | ".join(f"`{c}`" for c in CONFIGS) + " |",
        "|---|---|" + "---|" * len(CONFIGS),
    ]
    groups = [
        ("Meter number, exact", "number_{}", agreed, "both readers agree"),
        ("Meter number, O→0 and I/L→1 allowed", "number_lenient_{}", agreed, "both readers agree"),
        ("Meter number, exact", "number_{}", strict, "readers' main numbers identical"),
        ("Meter number, exact", "number_{}", with_number, "all labelled"),
        ("US class label (CL200, 200 CL, CL20)", "class_{}", ansi, "all labelled"),
        ("Current rating (e.g. 10(60)A)", "class_{}", rating, "all labelled"),
    ]
    for name, key, subset, which in groups:
        cells = [rate(subset, key.format(c)) for c in CONFIGS]
        lines.append(f"| {name} | {which} ({len(subset)}) | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = run()
    RESULTS_DIR.mkdir(exist_ok=True)
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k != "id", k))
    with (RESULTS_DIR / "clean_per_image.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)
    located = [r for r in rows if "tallest_is_number" in r]
    hits = sum(r["tallest_is_number"] for r in located)
    text = table(rows) + (
        f"\nOn {hits} of {len(located)} photos whose number was read, the tallest line with 4 or"
        " more digits was the meter number.\n"
    )
    header = "Generated by `make q1` (`uv run python -m meter_eval.clean`).\n\n"
    (RESULTS_DIR / "clean.md").write_text(header + text)
    print(text)


if __name__ == "__main__":
    main()
